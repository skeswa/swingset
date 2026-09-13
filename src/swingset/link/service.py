"""Atomic event-scoped identity linking."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tomllib
from calendar import monthrange
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from swingset.clock import Clock
from swingset.normalize.names import normalize_name, paired_names
from swingset.project.materialization import helper_recipe, materializing
from swingset.schedule.confirmation import pending_confirmation_events
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY_SOURCE
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.identity_journal import token as journal_token
from swingset.state.identity_references import ReferenceReader, retain_binding
from swingset.state.work import WorkUnit, bump_revision, complete

from .assign import ScoredPair, assign
from .candidates import Candidate, DancerRecord, Subject, generate_candidates
from .decisions import DecisionResolver, retain_resolution
from .points import expected_points
from .score import Weights, score_candidate

if TYPE_CHECKING:
    from swingset.state.derivations import Selection
    from swingset.state.inputs import InputBundle

LINKER_VERSION = "9"


class StaleIdentityResolution(RuntimeError):
    """A newly accepted decision keeps this work queued for recomputation."""


def _link_id(kind: str, subject_id: str) -> str:
    return hashlib.sha256(f"{kind}|{subject_id}".encode()).hexdigest()[:16]


def _nicknames(bundle: InputBundle) -> dict[str, str]:
    return {
        row["nickname"].casefold(): row["canonical"].casefold()
        for row in bundle.csv("nicknames.csv")
    }


def _weights(bundle: InputBundle) -> Weights:
    raw = tomllib.loads(bundle.files.get("link/weights.toml", b"").decode() or "")
    return Weights(**{key: float(value) for key, value in raw.items()})


def _candidate_pools(
    dancers: list[DancerRecord], judge_ids: set[int]
) -> tuple[dict[str, list[DancerRecord]], dict[str, list[DancerRecord]]]:
    """Apply the generator's surname-initial block once for an event."""
    entries: dict[str, list[DancerRecord]] = {}
    judges: dict[str, list[DancerRecord]] = {}
    for dancer in dancers:
        surname = normalize_name(dancer.name_raw).last_token
        if not surname:
            continue
        entries.setdefault(surname[0], []).append(dancer)
        if dancer.wsdc_id in judge_ids:
            judges.setdefault(surname[0], []).append(dancer)
    return entries, judges


def _update_registry_points(db: sqlite3.Connection, event_id: str) -> None:
    rows = db.execute(
        "SELECT p.placement_id,p.place,p.leader_entry_id,p.follower_entry_id,p.couple_entry_id,c.division,c.dance_style,c.wsdc_points_eligible,p.contest_id FROM placements p JOIN contests c USING(contest_id) WHERE p.event_id=?",
        (event_id,),
    ).fetchall()
    event = db.execute(
        "SELECT series_id,end_date FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        return
    for row in rows:
        incomplete_prelim = db.execute(
            "SELECT 1 FROM findings f JOIN rounds r ON r.round_id=f.subject_id WHERE f.kind='missing_identity' AND f.subject_kind='round' AND f.closed_at IS NULL AND r.contest_id=? AND r.round_type='prelim' LIMIT 1",
            (row[8],),
        ).fetchone()
        points: list[int | None] = []
        field_sizes: list[int | None] = []
        for role, entry_id in (("leader", row[2]), ("follower", row[3])):
            linked = (
                db.execute("SELECT wsdc_id FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
                if entry_id
                else None
            )
            registry = (
                db.execute(
                    "SELECT points FROM registry_placements WHERE wsdc_id=? AND role=? AND event_id=? AND division=? AND dance_style=? AND result=?",
                    (linked[0], role, event_id, row[5], row[6], str(row[1])),
                ).fetchone()
                if bool(row[7]) and linked and linked[0] is not None
                else None
            )
            points.append(int(registry[0]) if registry else None)
            field = (
                None
                if incomplete_prelim
                else db.execute(
                    "SELECT max(field_size) FROM (SELECT count(*) AS field_size FROM rounds r JOIN entries e ON e.contest_id=r.contest_id AND e.role=? WHERE r.contest_id=? AND r.round_type='prelim' AND EXISTS (SELECT 1 FROM json_each(e.rounds_danced) WHERE value=r.round_type) GROUP BY r.round_id)",
                    (role, row[8]),
                ).fetchone()
            )
            field_sizes.append(int(field[0]) if field and field[0] is not None else None)
        confirmed = all(value is not None for value in points)
        available = [index for index, value in enumerate(points) if value is not None]
        matches = (
            None
            if not available
            or not bool(row[7])
            or any(field_sizes[index] is None for index in available)
            else all(
                points[index] == expected_points(field_sizes[index] or 0, int(row[1]))
                for index in available
            )
        )
        db.execute(
            "UPDATE placements SET registry_points_leader=?,registry_points_follower=?,registry_confirmed=?,points_matches_expected=? WHERE placement_id=?",
            (
                points[0],
                points[1],
                int(confirmed),
                None if matches is None else int(matches),
                row[0],
            ),
        )


def link_event(
    database: Database,
    event_id: str,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    *,
    selection: Selection | None = None,
) -> bool:
    """Replace every link and candidate for an event in one transaction."""
    conn = database.connection
    delegated = selection is not None
    from swingset.state import derivations

    if selection is None and derivations.available(conn):
        with database.transaction():
            selection = derivations.capture(
                conn,
                WorkUnit("link", "event", event_id),
                now=clock.now(),
                recipe=helper_recipe(conn, bundle.files, "link"),
            )
    resolver = DecisionResolver(conn)
    event = conn.execute("SELECT year FROM events WHERE event_id=?", (event_id,)).fetchone()
    event_year = int(event[0]) if event else None
    entry_rows = conn.execute(
        "SELECT e.entry_id,e.name_raw,e.role,e.wsdc_id,c.division,e.bib,e.contest_id FROM entries e JOIN contests c USING(contest_id) WHERE e.event_id=?",
        (event_id,),
    ).fetchall()
    judge_rows = conn.execute(
        "SELECT judge_id,name_raw,wsdc_id FROM judges WHERE event_id=?", (event_id,)
    ).fetchall()
    dancer_rows = (
        conn.execute(
            "SELECT wsdc_id,first_name,last_name,primary_role,recent_year,is_pro,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,follower_highest_level FROM dancers WHERE merged_into_wsdc_id IS NULL"
        ).fetchall()
        if entry_rows or judge_rows
        else []
    )
    dancers = [
        DancerRecord(
            int(row[0]),
            f"{row[1]} {row[2]}",
            str(row[3]),
            int(row[4]),
            bool(row[5]),
            str(row[6]),
            str(row[7]),
            str(row[8]),
            str(row[9]),
        )
        for row in dancer_rows
    ]
    eligible_judges = {
        int(row[0])
        for row in dancer_rows
        if bool(row[5])
        or str(row[10]).casefold() in {"allstar", "als", "champion", "chmp"}
        or str(row[11]).casefold() in {"allstar", "als", "champion", "chmp"}
    }
    entry_pools, judge_pools = _candidate_pools(dancers, eligible_judges)
    subjects = [
        Subject(
            "entry",
            str(row[0]),
            str(row[1] or ""),
            str(row[2]),
            str(row[4]),
            event_year,
            None,
            str(row[5]) if row[5] is not None else None,
            str(row[6]),
        )
        for row in entry_rows
    ]
    subjects += [
        Subject(
            "judge",
            str(row[0]),
            str(row[1] or ""),
            "unknown",
            event_year=event_year,
            source_wsdc_id=None,
        )
        for row in judge_rows
    ]
    nicknames = _nicknames(bundle)
    weights = _weights(bundle)
    registry_confirmations: dict[str, set[int]] = {}
    dancer_names = {d.wsdc_id: normalize_name(d.name_raw).value for d in dancers}
    for subject in subjects:
        if subject.subject_kind != "entry" or subject.division is None:
            continue
        rows = conn.execute(
            """SELECT DISTINCT rp.wsdc_id
            FROM placements p
            JOIN contests c USING(contest_id)
            JOIN registry_placements rp
              ON rp.event_id=p.event_id
             AND rp.role=?
             AND rp.division=c.division
             AND rp.dance_style=c.dance_style
             AND rp.result IN (CAST(p.place AS TEXT),'F')
            WHERE p.event_id=?
              AND c.wsdc_points_eligible=1
              AND ? IN (p.leader_entry_id,p.follower_entry_id,p.couple_entry_id)""",
            (subject.role, event_id, subject.subject_id),
        )
        registry_confirmations[subject.subject_id] = {
            int(row[0])
            for row in rows
            if dancer_names.get(int(row[0])) == normalize_name(subject.name_raw).value
        }
    previous_default_ids = {str(row[0]): row[3] for row in entry_rows}
    previous_default_ids.update({str(row[0]): row[2] for row in judge_rows})
    reference_reader = ReferenceReader(conn)
    bindings = {
        subject.subject_id: reference_reader.for_subject(subject.subject_kind, subject.subject_id)
        for subject in subjects
    }
    printed_ids = {
        identifier: {
            int(binding.locator["source_wsdc_id"])
            for binding in references
            if binding.locator.get("source_wsdc_id") is not None
        }
        for identifier, references in bindings.items()
    }
    subjects = [
        replace(subject, source_wsdc_id=next(iter(printed_ids[subject.subject_id])))
        if len(printed_ids[subject.subject_id]) == 1
        else subject
        for subject in subjects
    ]
    decisions = {
        subject.subject_id: resolver.resolve(
            tuple(binding.reference for binding in bindings[subject.subject_id]),
            source_wsdc_id=subject.source_wsdc_id,
            registry_wsdc_ids=frozenset(registry_confirmations.get(subject.subject_id, set())),
            legacy_subject_id=subject.subject_id,
            reference_problem="contradictory_printed_source_identities"
            if len(printed_ids[subject.subject_id]) > 1
            else resolver.binding_problem(
                bindings[subject.subject_id],
                subject_kind=subject.subject_kind,
                subject_id=subject.subject_id,
            ),
        )
        for subject in subjects
    }
    claims: dict[tuple[str | None, str, int], list[Subject]] = {}
    for subject in subjects:
        policy = decisions[subject.subject_id]
        if subject.subject_kind != "entry" or policy.hold_subject:
            continue
        strong_ids = (
            registry_confirmations.get(subject.subject_id, set())
            | ({subject.source_wsdc_id} if subject.source_wsdc_id is not None else set())
            | ({policy.positive_wsdc_id} if policy.positive_wsdc_id is not None else set())
        )
        for number in strong_ids:
            if policy.allows(number):
                claims.setdefault((subject.contest_id, subject.role, number), []).append(subject)
    for claimants in claims.values():
        if len({subject.bib or subject.subject_id for subject in claimants}) <= 1:
            continue
        for subject in claimants:
            policy = decisions[subject.subject_id]
            decisions[subject.subject_id] = replace(
                policy,
                hold_subject=True,
                review_required=True,
                contradictions=tuple(
                    sorted(set(policy.contradictions) | {"identity_claimed_by_distinct_bibs"})
                ),
            )
    scored: dict[str, list[tuple[Candidate, float]]] = {}
    surname_counts = Counter(
        d.name_raw.casefold().split()[-1] for d in dancers if d.name_raw.split()
    )
    bib_counts = Counter(
        (subject.contest_id, subject.role, subject.bib) for subject in subjects if subject.bib
    )
    source_id_groups: dict[tuple[str | None, str, int], set[str]] = {}
    for subject in subjects:
        if subject.source_wsdc_id is not None:
            source_id_groups.setdefault(
                (subject.contest_id, subject.role, subject.source_wsdc_id), set()
            ).add(subject.bib or subject.subject_id)
    for subject in subjects:
        pools = entry_pools if subject.subject_kind == "entry" else judge_pools
        initial = normalize_name(subject.name_raw).last_token[:1]
        pool = pools.get(initial, [])
        generated = {}
        variants = [
            replace(subject, source_wsdc_id=printed)
            for printed in sorted(printed_ids[subject.subject_id])
        ] or [subject]
        for variant in variants:
            for candidate in generate_candidates(variant, pool, nicknames):
                if candidate.dancer.wsdc_id in printed_ids[subject.subject_id]:
                    candidate = replace(
                        candidate,
                        subject=replace(subject, source_wsdc_id=candidate.dancer.wsdc_id),
                    )
                generated[candidate.dancer.wsdc_id] = candidate
        scored[subject.subject_id] = [
            (candidate, score_candidate(candidate, weights))
            for candidate in sorted(
                generated.values(),
                key=lambda item: (-item.name_similarity, item.dancer.wsdc_id),
            )
        ]
    assignments: dict[str, ScoredPair] = {}
    for contest_id, role in {(subject.contest_id, subject.role) for subject in subjects}:
        scoped_subjects = [
            subject
            for subject in subjects
            if subject.contest_id == contest_id and subject.role == role
        ]
        pairs = [
            ScoredPair(
                f"{subject.role}:{subject.bib or subject.subject_id}",
                candidate.dancer.wsdc_id,
                score,
            )
            for subject in scoped_subjects
            for candidate, score in scored[subject.subject_id]
            if decisions[subject.subject_id].allows(candidate.dancer.wsdc_id)
        ]
        group_assignments = assign(pairs)
        for subject in scoped_subjects:
            group = f"{subject.role}:{subject.bib or subject.subject_id}"
            if pair := group_assignments.get(group):
                assignments[subject.subject_id] = ScoredPair(
                    subject.subject_id, pair.wsdc_id, pair.score
                )
    now = clock.now().isoformat()
    old = [
        tuple(row)
        for row in conn.execute(
            "SELECT subject_kind,subject_id,wsdc_id,method,status,confidence FROM identity_links WHERE subject_id IN (SELECT entry_id FROM entries WHERE event_id=? UNION SELECT judge_id FROM judges WHERE event_id=?) ORDER BY subject_kind,subject_id",
            (event_id, event_id),
        )
    ]
    old += [
        ("placement", *tuple(row))
        for row in conn.execute(
            "SELECT placement_id,leader_wsdc_id,follower_wsdc_id,registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE event_id=? ORDER BY placement_id",
            (event_id,),
        )
    ]

    def write(db: sqlite3.Connection) -> None:
        if journal_token(db) != resolver.token:
            raise StaleIdentityResolution("identity decisions changed during linking")
        findings: list[Finding] = []
        subject_ids = [subject.subject_id for subject in subjects]
        if subject_ids:
            marks = ",".join("?" for _ in subject_ids)
            db.execute(f"DELETE FROM link_candidates WHERE subject_id IN ({marks})", subject_ids)
            db.execute(f"DELETE FROM identity_links WHERE subject_id IN ({marks})", subject_ids)
        for subject in subjects:
            ranked = sorted(
                scored[subject.subject_id], key=lambda item: (-item[1], item[0].dancer.wsdc_id)
            )
            decision = decisions[subject.subject_id]
            chosen = assignments.get(subject.subject_id)
            mixed_person = subject.role == "couple" or bool(paired_names(subject.name_raw))
            if mixed_person:
                wsdc_id, method, status, confidence = None, "none", "unmatched", 0.0
                findings.append(
                    Finding(
                        kind="paired_name",
                        subject_kind=subject.subject_kind,
                        subject_id=subject.subject_id,
                        severity="warning",
                        summary="Individual identity withheld for a paired subject",
                        evidence={
                            "name_raw": subject.name_raw,
                            "reason": "paired_name_ownership_unresolved",
                            "source_wsdc_id": subject.source_wsdc_id,
                            "has_override": bool(decision.decision_ids),
                        },
                    )
                )
            elif decision.hold_subject:
                wsdc_id, method, status, confidence = None, "none", "unmatched", 0.0
            elif decision.positive_wsdc_id is not None:
                wsdc_id = decision.positive_wsdc_id
                method, status, confidence = "manual", "confirmed", 1.0
            elif (
                subject.source_wsdc_id is not None
                and decision.allows(subject.source_wsdc_id)
                and len(
                    source_id_groups[(subject.contest_id, subject.role, subject.source_wsdc_id)]
                )
                == 1
            ):
                wsdc_id, method, status, confidence = (
                    subject.source_wsdc_id,
                    "source_id",
                    "confirmed",
                    1.0,
                )
            elif (
                len(
                    confirmed := {
                        candidate.dancer.wsdc_id
                        for candidate, _score in ranked
                        if candidate.dancer.wsdc_id
                        in registry_confirmations.get(subject.subject_id, set())
                        and decision.allows(candidate.dancer.wsdc_id)
                    }
                )
                == 1
            ):
                wsdc_id, method, status, confidence = (
                    next(iter(confirmed)),
                    "registry_placement",
                    "confirmed",
                    1.0,
                )
            elif chosen is None:
                wsdc_id, method, status, confidence = None, "none", "unmatched", 0.0
            else:
                wsdc_id, confidence = chosen.wsdc_id, chosen.score
                close = len(ranked) > 1 and ranked[1][1] >= confidence - 0.05
                status = (
                    "probable"
                    if confidence >= 0.9 and not close
                    else "possible"
                    if confidence >= 0.7
                    else "ambiguous"
                )
                method = "name_unique" if len(ranked) == 1 else "assignment"
                if (
                    subject.bib is not None
                    and bib_counts[(subject.contest_id, subject.role, subject.bib)] > 1
                ):
                    method = "bib_reuse"
            if decision.review_required:
                findings.append(
                    Finding(
                        kind="identity_decision",
                        subject_kind=subject.subject_kind,
                        subject_id=subject.subject_id,
                        severity="warning",
                        summary="Identity requires decision-aware review",
                        evidence={
                            "decision_ids": decision.decision_ids,
                            "reference_ids": decision.ref_ids,
                            "migration_ids": decision.migration_ids,
                            "contradictions": decision.contradictions,
                            "blocked_wsdc_ids": sorted(decision.blocked_wsdc_ids),
                            "printed_wsdc_ids": sorted(printed_ids[subject.subject_id]),
                            "hold_subject": decision.hold_subject,
                            "journal_digest": decision.token.digest,
                            "policy_version": decision.policy_version,
                        },
                    )
                )
            db.execute(
                "INSERT INTO identity_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _link_id(subject.subject_kind, subject.subject_id),
                    subject.subject_kind,
                    subject.subject_id,
                    wsdc_id,
                    method,
                    status,
                    confidence,
                    json.dumps(
                        ["paired_name_ownership_unresolved"]
                        if mixed_person
                        else ["contest_role_unique"]
                    ),
                    now,
                    run_id,
                    LINKER_VERSION,
                ),
            )
            for rank, (candidate, score) in enumerate(ranked, 1):
                surname = candidate.dancer.name_raw.casefold().split()[-1]
                rarity = 1.0 / surname_counts[surname]
                db.execute(
                    "INSERT INTO link_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        subject.subject_kind,
                        subject.subject_id,
                        candidate.dancer.wsdc_id,
                        score,
                        candidate.name_similarity,
                        rarity,
                        None if candidate.division_ok is None else int(candidate.division_ok),
                        None
                        if subject.subject_kind == "judge" and subject.role == "unknown"
                        else int(subject.role == candidate.dancer.primary_role),
                        int(
                            event_year is not None
                            and candidate.dancer.recent_year >= event_year - 3
                        ),
                        None,
                        int(
                            subject.bib is not None
                            and bib_counts[(subject.contest_id, subject.role, subject.bib)] > 1
                            and wsdc_id == candidate.dancer.wsdc_id
                        ),
                        int(
                            candidate.dancer.wsdc_id
                            in registry_confirmations.get(subject.subject_id, set())
                        ),
                        int(candidate.dancer.wsdc_id in printed_ids[subject.subject_id]),
                        rank,
                        int(wsdc_id == candidate.dancer.wsdc_id),
                        run_id,
                    ),
                )
            if subject.subject_kind == "entry":
                published_id = wsdc_id if status == "confirmed" else None
                db.execute(
                    "UPDATE entries SET wsdc_id=?,link_status=?,link_confidence=? WHERE entry_id=?",
                    (published_id, status, confidence, subject.subject_id),
                )
            else:
                db.execute(
                    "UPDATE judges SET wsdc_id=? WHERE judge_id=?",
                    (wsdc_id if status == "confirmed" else None, subject.subject_id),
                )
            for binding in bindings[subject.subject_id]:
                retain_binding(db, binding, now=now)
            previous_id = previous_default_ids[subject.subject_id]
            accepted = wsdc_id is not None and status == "confirmed"
            retain_resolution(
                db,
                subject.subject_kind,
                subject.subject_id,
                decision,
                state="accepted"
                if accepted
                else "revoked"
                if previous_id is not None
                else "unresolved",
                reason=",".join(decision.contradictions) or "subject_hold"
                if decision.hold_subject
                else "paired_name_ownership_unresolved"
                if mixed_person
                else method,
                now=now,
            )
        replace_findings(
            db,
            owner_kind="link",
            owner_id=event_id,
            findings=tuple(findings),
            opened_at=now,
            run_id=run_id,
        )
        db.execute(
            "UPDATE placements SET leader_wsdc_id=(SELECT wsdc_id FROM entries WHERE entry_id=leader_entry_id),follower_wsdc_id=(SELECT wsdc_id FROM entries WHERE entry_id=follower_entry_id) WHERE event_id=?",
            (event_id,),
        )
        _update_registry_points(db, event_id)
        _seed_confirmation_watches(db, event_id, clock.now())
        current = [
            tuple(row)
            for row in db.execute(
                "SELECT subject_kind,subject_id,wsdc_id,method,status,confidence FROM identity_links WHERE subject_id IN (SELECT entry_id FROM entries WHERE event_id=? UNION SELECT judge_id FROM judges WHERE event_id=?) ORDER BY subject_kind,subject_id",
                (event_id, event_id),
            )
        ]
        current += [
            ("placement", *tuple(row))
            for row in db.execute(
                "SELECT placement_id,leader_wsdc_id,follower_wsdc_id,registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE event_id=? ORDER BY placement_id",
                (event_id,),
            )
        ]
        if old != current:
            bump_revision(db, "links")

    def generation_write(db: sqlite3.Connection) -> None:
        with materializing(
            db,
            WorkUnit("link", "event", event_id),
            now=clock.now(),
            run_id=run_id,
            selection=selection,
            recipe=helper_recipe(db, bundle.files, "link"),
        ):
            write(db)

    try:
        complete(database, WorkUnit("link", "event", event_id), generation_write)
    except (StaleIdentityResolution, SupersededWorkError) as exc:
        if delegated:
            raise SupersededWorkError("identity inputs changed during linking") from exc
        return False
    new = [
        tuple(row)
        for row in conn.execute(
            "SELECT subject_kind,subject_id,wsdc_id,method,status,confidence FROM identity_links WHERE subject_id IN (SELECT entry_id FROM entries WHERE event_id=? UNION SELECT judge_id FROM judges WHERE event_id=?) ORDER BY subject_kind,subject_id",
            (event_id, event_id),
        )
    ]
    new += [
        ("placement", *tuple(row))
        for row in conn.execute(
            "SELECT placement_id,leader_wsdc_id,follower_wsdc_id,registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE event_id=? ORDER BY placement_id",
            (event_id,),
        )
    ]
    return old != new


def _seed_confirmation_watches(db: sqlite3.Connection, event_id: str, now: datetime) -> None:
    event = db.execute(
        "SELECT end_date,event_month FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        return
    if event[0] is None:
        if not event[1]:
            return
        year, month = map(int, str(event[1]).split("-"))
        end = date(year, month, monthrange(year, month)[1])
    else:
        end = date.fromisoformat(str(event[0]))
    if end + timedelta(days=30) < now.date():
        return
    ids = db.execute(
        """SELECT DISTINCT e.wsdc_id FROM placements p
        JOIN entries e ON e.entry_id IN
          (p.leader_entry_id,p.follower_entry_id,p.couple_entry_id)
        WHERE p.event_id=? AND e.wsdc_id IS NOT NULL""",
        (event_id,),
    )
    for row in ids:
        wsdc_id = int(row[0])
        pending_events = pending_confirmation_events(db, wsdc_id, now)
        if not pending_events:
            continue
        spec = REGISTRY_SOURCE.watch(wsdc_id)
        upsert_watch(db, spec, now)
        watch = db.execute(
            "SELECT last_checked_at,next_check_at FROM watches WHERE watch_id=?",
            (spec.watch_id,),
        ).fetchone()
        due = str(watch[1] or now.isoformat())
        if watch[0] is not None:
            last_checked = datetime.fromisoformat(str(watch[0]).replace("Z", "+00:00"))
            due = (last_checked + timedelta(days=1)).isoformat()
        db.execute(
            "UPDATE watches SET notes=?,priority=5,next_check_at=? WHERE watch_id=?",
            (f"confirmation:{pending_events[0]}", due, spec.watch_id),
        )
