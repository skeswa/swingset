"""Append-only reviewed decisions, accepted atomically with their relink work."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date

from swingset.fetch.archive import digest
from swingset.state.identity_references import SourceReference, fingerprint, references_for_subject
from swingset.state.work import WorkUnit, bump_revision, enqueue

FIELDS = (
    "decision_id",
    "source",
    "source_event",
    "contest",
    "round",
    "participant",
    "wsdc_id",
    "decision",
    "evidence",
    "reason",
    "author",
    "date",
    "supersedes",
)
LEGACY_FIELDS = ("entry_id", "wsdc_id", "reason", "author", "date")
KINDS = frozenset({"same_person", "different_person", "insufficient_evidence", "hold_unlinked"})


@dataclass(frozen=True)
class Decision:
    decision_id: str
    source: str
    source_event: str
    contest: str
    round: str
    participant: str
    wsdc_id: str
    decision: str
    evidence: str
    reason: str
    author: str
    date: str
    supersedes: str

    @property
    def reference(self) -> SourceReference:
        return SourceReference(
            self.source, self.source_event, self.contest, self.round, self.participant
        )


@dataclass(frozen=True)
class JournalToken:
    digest: str
    generation: int


def token(conn: sqlite3.Connection) -> JournalToken:
    return JournalToken(
        str(
            conn.execute("SELECT value FROM meta WHERE key='identity_journal_digest'").fetchone()[0]
        ),
        int(
            conn.execute("SELECT value FROM revisions WHERE name='identity_decisions'").fetchone()[
                0
            ]
        ),
    )


def parse_journal(body: bytes) -> tuple[Decision, ...]:
    if not body:
        return ()
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    if (
        reader.fieldnames is None
        or len(reader.fieldnames) != len(set(reader.fieldnames))
        or set(reader.fieldnames) != set(FIELDS)
    ):
        raise ValueError(
            "identity journal needs the decision columns; convert legacy overrides first"
        )
    rows = []
    seen = set()
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"identity journal line {number}: malformed CSV row")
        decision = Decision(**row)
        if any(
            not row[field].strip()
            for field in (
                "decision_id",
                "source",
                "participant",
                "wsdc_id",
                "decision",
                "evidence",
                "reason",
                "author",
                "date",
            )
        ):
            raise ValueError(f"identity journal line {number}: missing decision provenance")
        if decision.source != "legacy" and not decision.source_event:
            raise ValueError("identity decision requires its original source event")
        date.fromisoformat(decision.date)
        if decision.decision not in KINDS:
            raise ValueError(f"unknown identity decision: {decision.decision}")
        if decision.wsdc_id != "NONE" and (
            not decision.wsdc_id.isdecimal() or int(decision.wsdc_id) < 1
        ):
            raise ValueError("decision wsdc_id must be positive or NONE")
        if decision.decision in {"same_person", "different_person"} and decision.wsdc_id == "NONE":
            raise ValueError("pair decisions require a WSDC number")
        if decision.decision == "hold_unlinked" and decision.wsdc_id != "NONE":
            raise ValueError("hold_unlinked is a subject decision and requires NONE")
        if decision.decision_id in seen:
            raise ValueError(f"duplicate decision_id: {decision.decision_id}")
        seen.add(decision.decision_id)
        rows.append(decision)
    by_id = {row.decision_id: row for row in rows}
    for item in rows:
        visited = {item.decision_id}
        prior = item.supersedes
        while prior:
            if prior not in by_id or prior in visited:
                raise ValueError("identity supersession is missing or cyclic")
            visited.add(prior)
            previous = by_id[prior]
            prior = previous.supersedes
    return tuple(rows)


def encode_journal(rows: tuple[Decision, ...]) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(asdict(row) for row in rows)
    return target.getvalue().encode()


def convert_legacy(conn: sqlite3.Connection, body: bytes) -> bytes:
    reader = csv.DictReader(io.StringIO(body.decode()))
    if reader.fieldnames is not None and set(reader.fieldnames) == set(FIELDS):
        parse_journal(body)
        return body
    if reader.fieldnames is None or tuple(reader.fieldnames) != LEGACY_FIELDS:
        raise ValueError("unrecognized legacy identity override columns")
    decisions = []
    for number, row in enumerate(reader, 2):
        if None in row or any(value is None or not value.strip() for value in row.values()):
            raise ValueError("malformed legacy identity override")
        bindings = references_for_subject(conn, "entry", row["entry_id"])
        exact = len(bindings) == 1
        ref = (
            bindings[0].reference
            if exact
            else SourceReference("legacy", "", "", "", f"unmapped-entry:{row['entry_id']}")
        )
        evidence = json.dumps(
            {
                "legacy_row": row,
                "line": number,
                "legacy_digest": digest(body),
                "mapping": "exact" if exact else "ambiguous" if bindings else "unmapped",
                "references": [
                    {
                        "snapshot": b.snapshot_id,
                        "source_reference": asdict(b.reference),
                        "locator": b.locator,
                    }
                    for b in bindings
                ],
            },
            sort_keys=True,
        )
        decisions.append(
            Decision(
                "legacy-" + fingerprint((digest(body), number, row))[:24],
                ref.source,
                ref.source_event,
                ref.contest,
                ref.round,
                ref.participant,
                row["wsdc_id"],
                "same_person"
                if exact and row["wsdc_id"] != "NONE"
                else "hold_unlinked"
                if exact
                else "insufficient_evidence",
                evidence,
                row["reason"],
                row["author"],
                row["date"],
                "",
            )
        )
    converted = encode_journal(tuple(decisions))
    parse_journal(converted)
    return converted


def accept_journal(conn: sqlite3.Connection, body: bytes, *, bundle_digest: str, now: str) -> bool:
    """Caller owns the input-acceptance transaction, including invalidation."""
    if not conn.in_transaction:
        raise ValueError("identity journal acceptance requires an input transaction")
    rows = parse_journal(body)
    by_id = {row.decision_id: row for row in rows}
    old = {
        str(r[0]): str(r[1])
        for r in conn.execute("SELECT decision_id,row_sha256 FROM identity_decisions")
    }
    if any(
        identifier not in by_id or fingerprint(asdict(by_id[identifier])) != sha
        for identifier, sha in old.items()
    ):
        raise ValueError("identity journal removes or alters an accepted decision_id")
    from swingset.state.identity_references import approved_reference_path

    for row in rows:
        if row.decision_id in old or not row.supersedes:
            continue
        previous = by_id[row.supersedes]
        if (
            previous.source != "legacy"
            and previous.reference != row.reference
            and not approved_reference_path(conn, previous.reference.ref_id, row.reference.ref_id)
        ):
            raise ValueError("cross-reference supersession requires a recorded reference migration")
    journal_digest = digest(body)
    if token(conn).digest == journal_digest:
        return False
    pending = {key: row for key, row in by_id.items() if key not in old}
    while pending:
        for identifier, row in list(pending.items()):
            if row.supersedes and row.supersedes in pending:
                continue
            values = asdict(row)
            ref = row.reference
            conn.execute(
                "INSERT OR IGNORE INTO identity_source_refs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ref.ref_id,
                    ref.source,
                    ref.source_event,
                    ref.contest,
                    ref.round,
                    ref.participant,
                    "judge" if ref.participant.startswith("judge:") else "entry",
                    json.dumps(
                        {"decision_id": row.decision_id, "evidence": row.evidence}, sort_keys=True
                    ),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO identity_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *[
                        values[field] or None if field == "supersedes" else values[field]
                        for field in FIELDS
                    ],
                    json.dumps(values, sort_keys=True),
                    fingerprint(values),
                    journal_digest,
                    now,
                ),
            )
            pending.pop(identifier)
    bump_revision(conn, "identity_decisions")
    generation = token(conn).generation
    conn.execute(
        "INSERT OR IGNORE INTO identity_journal_acceptances VALUES (?,?,?,?,?)",
        (journal_digest, bundle_digest, now, len(rows), generation),
    )
    conn.execute("UPDATE meta SET value=? WHERE key='identity_journal_digest'", (journal_digest,))
    scopes = {
        str(row[0])
        for row in conn.execute(
            "SELECT event_id FROM events UNION SELECT event_id FROM identity_reference_bindings"
        )
    }
    enqueue(conn, (WorkUnit("link", "event", event) for event in sorted(scopes)), enqueued_at=now)
    return True


def active_decisions(conn: sqlite3.Connection) -> tuple[Decision, ...]:
    return tuple(
        Decision(**json.loads(row[0]))
        for row in conn.execute(
            "SELECT row_json FROM identity_decisions WHERE decision_id NOT IN (SELECT supersedes FROM identity_decisions WHERE supersedes IS NOT NULL) ORDER BY decision_id"
        )
    )


def main() -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Convert legacy overrides offline; no input acceptance or linking."
    )
    parser.add_argument("convert", choices=["convert"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = args.state if args.state.suffix in {".sqlite", ".db"} else args.state / "state.sqlite"
    with sqlite3.connect(f"{state.resolve().as_uri()}?mode=ro", uri=True) as conn:
        conn.execute("BEGIN")
        converted = convert_legacy(conn, args.input.read_bytes())
    args.output.write_bytes(converted)
    print(
        json.dumps(
            {
                "decisions": len(parse_journal(converted)),
                "output": str(args.output),
                "accepted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
