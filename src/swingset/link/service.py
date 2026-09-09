"""Atomic event-scoped identity linking."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tomllib
from collections import Counter
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from swingset.clock import Clock
from swingset.normalize.names import normalize_name
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY_SOURCE
from swingset.state.db import Database
from swingset.state.work import WorkUnit, bump_revision, complete

from .assign import ScoredPair, assign
from .candidates import Candidate, DancerRecord, Subject, generate_candidates
from .points import expected_points
from .score import Weights, score_candidate

if TYPE_CHECKING:
    from swingset.state.inputs import InputBundle

LINKER_VERSION = "2"


def _source_ids(database: Database, event_id: str) -> dict[str, int]:
    """Read scoring.dance cell attributes without making projection own links."""
    result: dict[str, int] = {}
    rows = database.connection.execute(
        "SELECT o.payload_json FROM observations o JOIN source_event_map m ON m.source_ref=o.scope_id WHERE o.scope_kind='source_event' AND m.event_id=? AND m.source='scoringdance'",
        (event_id,),
    )

    def visit(value: object) -> None:
        if isinstance(value, dict):
            attributes = value.get("attributes")
            if isinstance(attributes, list):
                pairs = {
                    str(item[0]): str(item[1])
                    for item in attributes
                    if isinstance(item, list) and len(item) == 2
                }
                raw_name = value.get("text")
                if isinstance(raw_name, str) and pairs.get("data-wsdc", "").isdigit():
                    result[normalize_name(raw_name).value] = int(pairs["data-wsdc"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for row in rows:
        visit(json.loads(str(row[0])))
    return result


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


def _update_registry_points(db: sqlite3.Connection, event_id: str) -> None:
    rows = db.execute(
        "SELECT p.placement_id,p.place,p.leader_entry_id,p.follower_entry_id,p.couple_entry_id,c.division,c.wsdc_points_eligible,(SELECT max(entry_count) FROM rounds r WHERE r.contest_id=p.contest_id AND r.round_type='prelim') FROM placements p JOIN contests c USING(contest_id) WHERE p.event_id=?",
        (event_id,),
    ).fetchall()
    event = db.execute(
        "SELECT series_id,end_date FROM events WHERE event_id=?", (event_id,)
    ).fetchone()
    if event is None:
        return
    for row in rows:
        points: list[int | None] = []
        for role, entry_id in (("leader", row[2]), ("follower", row[3])):
            linked = (
                db.execute("SELECT wsdc_id FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
                if entry_id
                else None
            )
            registry = (
                db.execute(
                    "SELECT points FROM registry_placements WHERE wsdc_id=? AND role=? AND series_id=? AND division=? AND substr(event_month,1,7)=substr(?,1,7) AND result=?",
                    (linked[0], role, event[0], row[5], event[1], str(row[1])),
                ).fetchone()
                if linked and linked[0] is not None
                else None
            )
            points.append(int(registry[0]) if registry else None)
        confirmed = all(value is not None for value in points)
        field_size = int(row[7]) if row[7] is not None else 0
        expected = expected_points(field_size, int(row[1]))
        matches = (
            None
            if not any(value is not None for value in points) or not bool(row[6])
            else all(value is None or value == expected for value in points)
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
    database: Database, event_id: str, bundle: InputBundle, clock: Clock, run_id: str
) -> bool:
    """Replace every link and candidate for an event in one transaction."""
    conn = database.connection
    dancer_rows = conn.execute(
        "SELECT wsdc_id,first_name,last_name,primary_role,recent_year,is_pro,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,follower_highest_level FROM dancers WHERE merged_into_wsdc_id IS NULL"
    ).fetchall()
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
    event = conn.execute("SELECT year FROM events WHERE event_id=?", (event_id,)).fetchone()
    event_year = int(event[0]) if event else None
    source_ids = _source_ids(database, event_id)
    entry_rows = conn.execute(
        "SELECT e.entry_id,e.name_raw,e.role,e.wsdc_id,c.division,e.bib FROM entries e JOIN contests c USING(contest_id) WHERE e.event_id=?",
        (event_id,),
    ).fetchall()
    judge_rows = conn.execute(
        "SELECT judge_id,name_raw,wsdc_id FROM judges WHERE event_id=?", (event_id,)
    ).fetchall()
    subjects = [
        Subject(
            "entry",
            str(row[0]),
            str(row[1] or ""),
            str(row[2]),
            str(row[4]),
            event_year,
            source_ids.get(normalize_name(str(row[1] or "")).value),
            str(row[5]) if row[5] is not None else None,
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
    overrides = {row["entry_id"]: row for row in bundle.csv("identity_overrides.csv")}
    registry_confirmations: dict[str, set[int]] = {}
    for subject in subjects:
        if subject.subject_kind != "entry" or subject.division is None:
            continue
        rows = conn.execute(
            "SELECT rp.wsdc_id FROM registry_placements rp JOIN events e ON e.series_id=rp.series_id WHERE e.event_id=? AND rp.role=? AND rp.division=? AND substr(rp.event_month,1,7)=substr(e.end_date,1,7)",
            (event_id, subject.role, subject.division),
        )
        registry_confirmations[subject.subject_id] = {int(row[0]) for row in rows}
    scored: dict[str, list[tuple[Candidate, float]]] = {}
    surname_counts = Counter(
        d.name_raw.casefold().split()[-1] for d in dancers if d.name_raw.split()
    )
    bib_counts = Counter((subject.role, subject.bib) for subject in subjects if subject.bib)
    source_id_groups: dict[tuple[str, int], set[str]] = {}
    for subject in subjects:
        if subject.source_wsdc_id is not None:
            source_id_groups.setdefault((subject.role, subject.source_wsdc_id), set()).add(
                subject.bib or subject.subject_id
            )
    for subject in subjects:
        pool = [
            d
            for d, row in zip(dancers, dancer_rows, strict=True)
            if subject.subject_kind == "entry"
            or d.is_pro
            or str(row[10]).casefold() in {"allstar", "als", "champion", "chmp"}
            or str(row[11]).casefold() in {"allstar", "als", "champion", "chmp"}
        ]
        scored[subject.subject_id] = [
            (candidate, score_candidate(candidate, weights))
            for candidate in generate_candidates(subject, pool, nicknames)
        ]
    assignments: dict[str, ScoredPair] = {}
    for role in {subject.role for subject in subjects}:
        pairs = [
            ScoredPair(
                f"{subject.role}:{subject.bib or subject.subject_id}",
                candidate.dancer.wsdc_id,
                score,
            )
            for subject in subjects
            if subject.role == role
            for candidate, score in scored[subject.subject_id]
        ]
        group_assignments = assign(pairs)
        for subject in subjects:
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
        subject_ids = [subject.subject_id for subject in subjects]
        if subject_ids:
            marks = ",".join("?" for _ in subject_ids)
            db.execute(f"DELETE FROM link_candidates WHERE subject_id IN ({marks})", subject_ids)
            db.execute(f"DELETE FROM identity_links WHERE subject_id IN ({marks})", subject_ids)
        for subject in subjects:
            ranked = sorted(
                scored[subject.subject_id], key=lambda item: (-item[1], item[0].dancer.wsdc_id)
            )
            override = overrides.get(subject.subject_id)
            chosen = assignments.get(subject.subject_id)
            if override is not None:
                wsdc_id = (
                    None if override["wsdc_id"].upper() == "NONE" else int(override["wsdc_id"])
                )
                method, status, confidence = "manual", "confirmed", 1.0
            elif (
                subject.source_wsdc_id is not None
                and len(source_id_groups[(subject.role, subject.source_wsdc_id)]) == 1
            ):
                wsdc_id, method, status, confidence = (
                    subject.source_wsdc_id,
                    "source_id",
                    "confirmed",
                    1.0,
                )
            elif confirmed := [
                candidate.dancer.wsdc_id
                for candidate, _score in ranked
                if candidate.dancer.wsdc_id in registry_confirmations.get(subject.subject_id, set())
            ]:
                wsdc_id, method, status, confidence = (
                    confirmed[0],
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
                if subject.bib is not None and bib_counts[(subject.role, subject.bib)] > 1:
                    method = "bib_reuse"
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
                    json.dumps(["event_role_unique"]),
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
                        int(subject.role == candidate.dancer.primary_role),
                        int(
                            event_year is not None
                            and candidate.dancer.recent_year >= event_year - 3
                        ),
                        None,
                        int(
                            subject.bib is not None
                            and bib_counts[(subject.role, subject.bib)] > 1
                            and wsdc_id == candidate.dancer.wsdc_id
                        ),
                        int(
                            candidate.dancer.wsdc_id
                            in registry_confirmations.get(subject.subject_id, set())
                        ),
                        int(subject.source_wsdc_id == candidate.dancer.wsdc_id),
                        rank,
                        int(wsdc_id == candidate.dancer.wsdc_id),
                        run_id,
                    ),
                )
            if subject.subject_kind == "entry":
                published_id = wsdc_id if status in {"confirmed", "probable"} else None
                db.execute(
                    "UPDATE entries SET wsdc_id=?,link_status=?,link_confidence=? WHERE entry_id=?",
                    (published_id, status, confidence, subject.subject_id),
                )
            else:
                db.execute(
                    "UPDATE judges SET wsdc_id=? WHERE judge_id=?",
                    (wsdc_id if status in {"confirmed", "probable"} else None, subject.subject_id),
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

    complete(database, WorkUnit("link", "event", event_id), write)
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
    event = db.execute("SELECT end_date FROM events WHERE event_id=?", (event_id,)).fetchone()
    if event is None or date.fromisoformat(str(event[0])) + timedelta(days=30) < now.date():
        return
    ids = db.execute(
        "SELECT DISTINCT e.wsdc_id FROM placements p JOIN entries e ON e.entry_id IN (p.leader_entry_id,p.follower_entry_id,p.couple_entry_id) WHERE p.event_id=? AND e.wsdc_id IS NOT NULL",
        (event_id,),
    )
    for row in ids:
        spec = REGISTRY_SOURCE.watch(int(row[0]))
        upsert_watch(db, spec, now)
        db.execute(
            "UPDATE watches SET notes=?,priority=5,next_check_at=? WHERE watch_id=?",
            (f"confirmation:{event_id}", now.isoformat(), spec.watch_id),
        )
