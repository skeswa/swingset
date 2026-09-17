"""Load the evidence and version basis used for an event's identity resolution."""

from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from swingset.normalize.names import normalize_name
from swingset.state.identity_journal import JournalToken
from swingset.state.identity_references import ReferenceReader

from .candidates import DancerRecord, Subject
from .decisions import DecisionResolver
from .model import EventEvidence, LinkingRules, SubjectEvidence
from .score import Weights

if TYPE_CHECKING:
    from swingset.state.derivations import Selection
    from swingset.state.inputs import InputBundle


@dataclass(frozen=True)
class ResolutionBasis:
    """Input versions against which a computed result may be committed.

    ``selection`` identifies the selected derivation inputs, when available;
    ``journal`` identifies the accepted review journal. A newly accepted
    rejection of WSDC 100 makes an older answer based on that journal stale.
    """

    selection: Selection | None
    journal: JournalToken


@dataclass(frozen=True)
class LinkingSnapshot:
    """Loaded event evidence, matching rules, and their commit-time version guards.

    This snapshot is an input bundle for linking, distinct from an archived
    HTTP response snapshot. The resolver consumes evidence and rules; the
    writer checks the basis before committing the resulting conclusions.
    """

    evidence: EventEvidence
    rules: LinkingRules
    basis: ResolutionBasis


def load_linking_snapshot(
    conn: sqlite3.Connection, event_id: str, bundle: InputBundle, selection: Selection | None
) -> LinkingSnapshot:
    """Assemble a LinkingSnapshot from retained database records.

    For each entry or judge, gather its source locations, printed identity
    claims, matching registry results, continuity problems, and previous
    default identity. Also load the registry pool, reviewed-decision policy,
    scoring weights, nicknames, and input versions. This gathers existing
    evidence without fetching source pages or choosing a final identity.
    """
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
            wsdc_id=int(row["wsdc_id"]),
            name_raw=f"{row['first_name']} {row['last_name']}",
            primary_role=str(row["primary_role"]),
            recent_year=int(row["recent_year"]),
            is_pro=bool(row["is_pro"]),
            leader_required_level=str(row["leader_required_level"]),
            leader_allowed_level=str(row["leader_allowed_level"]),
            follower_required_level=str(row["follower_required_level"]),
            follower_allowed_level=str(row["follower_allowed_level"]),
        )
        for row in dancer_rows
    ]
    eligible_judges = {
        int(row["wsdc_id"])
        for row in dancer_rows
        if bool(row["is_pro"])
        or str(row["leader_highest_level"]).casefold() in {"allstar", "als", "champion", "chmp"}
        or str(row["follower_highest_level"]).casefold() in {"allstar", "als", "champion", "chmp"}
    }
    subjects = [
        Subject(
            subject_kind="entry",
            subject_id=str(row["entry_id"]),
            name_raw=str(row["name_raw"] or ""),
            role=str(row["role"]),
            division=str(row["division"]),
            event_year=event_year,
            bib=str(row["bib"]) if row["bib"] is not None else None,
            contest_id=str(row["contest_id"]),
        )
        for row in entry_rows
    ]
    subjects += [
        Subject(
            subject_kind="judge",
            subject_id=str(row["judge_id"]),
            name_raw=str(row["name_raw"] or ""),
            event_year=event_year,
        )
        for row in judge_rows
    ]
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
    previous_default_ids = {str(row["entry_id"]): row["wsdc_id"] for row in entry_rows}
    previous_default_ids.update({str(row["judge_id"]): row["wsdc_id"] for row in judge_rows})
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
    evidence = []
    for subject in subjects:
        references = bindings[subject.subject_id]
        problem = (
            "contradictory_printed_source_identities"
            if len(printed_ids[subject.subject_id]) > 1
            else resolver.binding_problem(
                references, subject_kind=subject.subject_kind, subject_id=subject.subject_id
            )
        )
        evidence.append(
            SubjectEvidence(
                subject=subject,
                bindings=references,
                printed_ids=frozenset(printed_ids[subject.subject_id]),
                registry_ids=frozenset(registry_confirmations.get(subject.subject_id, set())),
                reference_problem=problem,
                previous_reference_ids=resolver.previous_reference_ids(subject.subject_id)
                if problem
                else (),
                previous_default_id=previous_default_ids[subject.subject_id],
            )
        )
    raw_weights = tomllib.loads(bundle.files.get("link/weights.toml", b"").decode() or "")
    rules = LinkingRules(
        weights=Weights(**{key: float(value) for key, value in raw_weights.items()}),
        nicknames=MappingProxyType(
            {
                row["nickname"].casefold(): row["canonical"].casefold()
                for row in bundle.csv("nicknames.csv")
            }
        ),
    )
    return LinkingSnapshot(
        EventEvidence(
            event_id, tuple(evidence), tuple(dancers), frozenset(eligible_judges), resolver.policy
        ),
        rules,
        ResolutionBasis(selection, resolver.token),
    )
