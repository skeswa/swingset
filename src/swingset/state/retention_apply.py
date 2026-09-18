"""Act on one written retention plan, and give the freed pages back.

The removal half of plan step 3 of
[the bounded state plan](../../../docs/plans/bounded-state-and-archive.md).
[`retention`](retention.py) decides and writes nothing down but the plan; this
module is the only thing in the tree that removes anything, and it removes only
what a plan named, under both of the pipeline's locks.

Two commands live here:

- `gc --apply <plan digest>` recomputes the plan while holding the writer lock
  and the control lock, stops if the digest moved, commits a note of what it
  intends to remove, removes it, and writes one receipt.
- `gc --reclaim` rewrites the database file in place so the pages a removal
  freed stop counting against the size cap.

**Why the note.** SQLite can undo a row it deleted. Nothing can undo a file it
unlinked. So the note, which names every file and every payload this apply
intends to remove, commits *before* the first unlink. If the process dies
between that commit and the last unlink, the note is the record of what was
planned, and the next apply from a fresh plan finishes the files that plan still
calls eligible and says so in its own receipt.

**Why a note is never replayed on its own.** An unfinished note says what was
eligible when it was written, and the state moves on after a crash: a rollback
can point the baseline back at a candidate, a publication can start on one, and
the age floor can be raised. So a note is finished only through the plan this
apply just recomputed under both locks. Files that plan no longer calls eligible
are left where they are and named under `skipped_files` in the note's receipt,
because removal happens only from a written plan of the current state
([D-0151](../../../journal/decisions/0151-a-resumed-note-removes-only-what-the-fresh-plan-still-names.md)).

**Why no payload goes yet.** Removing payload bytes is only safe once those bytes
are archived somewhere else, which is plan step 4. The code path is here and is
gated on the fact step 4 establishes: no generation is eligible until an
`archived_generations` table exists and names it. Until that table exists the
gate returns nothing, so an apply today removes files and no row data at all
([D-0149](../../../journal/decisions/0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md)).
The plan reads that table and writes the answer down, so the digest covers it
([D-0155](../../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.build.files import canonical_json, durable_write, fsync_dir

from .control_lock import control_lock
from .db import Database
from .derivations import interned
from .retention import Plan, RetentionError, plan, usage

APPLY_RECEIPT_FORMAT = "retention-apply-receipt-v1"
RECLAIM_RECEIPT_FORMAT = "retention-reclaim-receipt-v1"

#: Why the payload delete gate was opened. The grant row carries it, and
#: `_revoke_stale_payload_removal` logs it if one ever outlives its transaction.
REMOVAL_REASON = "gc --apply of plan "

#: Rewriting the file in place needs room for SQLite's temporary copy and its
#: journal of the rewrite, so about twice the current file size.
RECLAIM_FREE_MULTIPLE = 2


def receipt_directory(state_dir: Path) -> Path:
    """Where receipts land. Under `gc/`, so backups leave them out.

    The note in the database is the durable record that survives a restore; a
    receipt is the operator's copy of it
    ([D-0148](../../../journal/decisions/0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md)).
    """
    return state_dir / "gc" / "receipts"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_plan(
    database: Database,
    *,
    plan_digest: str,
    max_database_bytes: int,
    recent_window: int,
    collect_older_than: float,
    now: datetime,
    timeout: float = 60,
) -> dict[str, Any]:
    """Remove exactly what one plan named, under the writer and control locks.

    The caller holds the writer lock already, because that is what opening the
    state database for writing takes. This adds the control lock, in that
    documented order, and holds it from the final recompute through the last
    unlink. Creating a hold and admitting a candidate take the control lock too,
    so no new starting point can appear in that window: a hold creator that
    arrives waits, and then plans against a state this apply has finished with.
    """
    if not database.holds_writer_lock:
        raise RetentionError(
            "gc --apply needs the writer lock; open the state database for writing"
        )
    state_dir = database.state_dir
    conn = database.connection
    with control_lock(state_dir, timeout=timeout):
        recorded = _note(conn, plan_digest)
        if recorded is not None and recorded["receipt_json"] is not None:
            # Already applied. Return the receipt as recorded, remove nothing.
            # The file is written after the note, so a process that died between
            # the two left the note without its operator copy; writing it here
            # is the only thing that ever fills that gap, and the contents come
            # from the note, never from state that has since moved on.
            return _write_receipt(
                state_dir, f"{plan_digest}.json", dict(json.loads(str(recorded["receipt_json"])))
            )
        fresh = plan(
            conn,
            state_dir,
            max_database_bytes=max_database_bytes,
            recent_window=recent_window,
            collect_older_than=collect_older_than,
        )
        if fresh.digest != plan_digest:
            raise RetentionError(
                f"plan {plan_digest} is not the plan of this state, which is {fresh.digest}; "
                "nothing was removed. Write a new plan with gc --plan and apply that one"
            )
        eligible = eligible_files(fresh, now=now)
        planned_files = eligible
        planned_payloads = eligible_payloads(conn, fresh)
        unfinished = _unfinished_notes(conn, exclude=plan_digest)
        removed_payload_bytes = _payload_bytes(conn, planned_payloads)
        if recorded is None:
            with database.transaction() as txn:
                txn.execute(
                    "INSERT INTO retention_applies"
                    "(plan_digest,planned_files_json,planned_payloads_json,"
                    "removed_payload_bytes,started_at) VALUES (?,?,?,?,?)",
                    (
                        plan_digest,
                        canonical_json(list(planned_files)).decode(),
                        canonical_json(list(planned_payloads)).decode(),
                        removed_payload_bytes,
                        now.isoformat(),
                    ),
                )
                _remove_payloads(txn, planned_payloads, plan_digest=plan_digest, now=now)
        else:
            # The note committed and the process died before the files went. The
            # digest matched, so this is the same plan and its files are in this
            # apply's own list; the union only covers a file whose age floor had
            # passed then. The payload lists come back from the note, because
            # its transaction already removed those bytes and nothing can count
            # them a second time.
            planned_files = tuple(
                dict.fromkeys([*json.loads(str(recorded["planned_files_json"])), *planned_files])
            )
            planned_payloads = tuple(json.loads(str(recorded["planned_payloads_json"])))
            removed_payload_bytes = int(recorded["removed_payload_bytes"])
        removed_files, already_gone = _remove_planned_files(state_dir, planned_files)
        finished = _finish_unfinished(
            database,
            unfinished,
            eligible=frozenset(eligible),
            plan_digest=plan_digest,
            now=now,
        )
        receipt = {
            "format": APPLY_RECEIPT_FORMAT,
            "plan_digest": plan_digest,
            "started_at": str(recorded["started_at"]) if recorded is not None else now.isoformat(),
            "files_completed_at": now.isoformat(),
            "planned_files": list(planned_files),
            "removed_files": removed_files,
            # Planned files this apply found already gone. Only a resumed note
            # has any: the apply that crashed unlinked them before it died, and
            # leaving them out of every list would drop them from the account.
            "already_gone_files": already_gone,
            "planned_payloads": list(planned_payloads),
            "removed_payload_bytes": removed_payload_bytes,
            "resumed": finished,
            "totals": dict(fresh.content["totals"]),
        }
        _finish_note(database, plan_digest, receipt=receipt, now=now)
    return _write_receipt(state_dir, f"{plan_digest}.json", receipt)


def eligible_files(value: Plan, *, now: datetime) -> tuple[str, ...]:
    """Every removable file the plan named whose age floor has passed.

    The floor comes from the plan, which read it from the directory's own
    modification time, so a build that is still writing its candidate is never
    removed by an apply of a plan written while it was writing.
    """
    moment = now.timestamp()
    return tuple(
        str(row["path"])
        for row in value.content["files"]
        if row["list"] == "removable" and float(row.get("eligible_after", moment + 1)) <= moment
    )


def eligible_payloads(conn: sqlite3.Connection, value: Plan) -> tuple[str, ...]:
    """Payload digests this apply may remove. Empty until step 4 exists.

    A generation's bytes may go only once they are somewhere else, and the only
    record that says so is the `archived_generations` table step 4 adds. The
    plan reads that table and carries the answer per generation, so the digest
    an operator reviewed covers it and archiving something after the review
    stops the apply instead of widening it
    ([D-0155](../../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).
    Until the table exists nothing is archived, so this returns nothing and the
    apply removes no row data. A payload goes only when every generation naming
    it is both archivable and archived: one shared payload kept alive by a
    single local generation keeps all of them.

    Before schema 32 there are no payloads to remove at all: a row's bytes are
    the row, rows are permanent, and the tables this reads do not exist. So this
    returns nothing there too, and an apply on such a database removes files
    only.
    """
    if not interned(conn):
        return ()
    eligible = sorted(
        str(row["generation_id"])
        for row in value.content["generations"]
        if row["list"] == "archivable" and row["archived"]
    )
    if not eligible:
        return ()
    marks = ",".join("?" * len(eligible))
    rows = conn.execute(
        f"SELECT DISTINCT payload_sha256 FROM derivation_row_refs WHERE generation_id IN ({marks}) "
        f"AND payload_sha256 NOT IN (SELECT payload_sha256 FROM derivation_row_refs "
        f"WHERE generation_id NOT IN ({marks}))",
        (*eligible, *eligible),
    )
    return tuple(sorted(str(row[0]) for row in rows))


def _remove_payloads(
    txn: sqlite3.Connection,
    payloads: tuple[str, ...],
    *,
    plan_digest: str,
    now: datetime,
) -> None:
    """Open the delete gate, remove the bytes, and close it before the commit.

    The permission row's deferred foreign key points at an always-empty table,
    so a transaction still holding it cannot commit. Closing the gate is not
    politeness; it is the only way this transaction ever commits.
    """
    if not payloads:
        return
    txn.execute(
        "INSERT INTO derivation_payload_removal_authority(singleton,reason,granted_at) "
        "VALUES (1,?,?)",
        (REMOVAL_REASON + plan_digest, now.isoformat()),
    )
    for digest in payloads:
        txn.execute("DELETE FROM derivation_payloads WHERE payload_sha256=?", (digest,))
    txn.execute("DELETE FROM derivation_payload_removal_authority")


def _remove_planned_files(state_dir: Path, planned: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Remove what the note named. Safe to rerun.

    Returns what this call unlinked and what it found already gone, so a receipt
    can account for every planned file rather than silently dropping the ones a
    crashed apply had already removed.
    """
    root = state_dir.resolve()
    removed: list[str] = []
    absent: list[str] = []
    parents: set[Path] = set()
    for relative in planned:
        path = state_dir / relative
        if not path.exists():
            absent.append(relative)
            continue
        if not path.resolve().is_relative_to(root):
            raise RetentionError(f"planned path leaves the state directory: {relative}")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(relative)
        parents.add(path.parent)
    for parent in parents:
        if parent.is_dir():
            fsync_dir(parent)
    return removed, absent


def _payload_bytes(conn: sqlite3.Connection, payloads: tuple[str, ...]) -> int:
    """The bytes those payloads hold, read before they go. Zero while the gate is shut."""
    total = 0
    for digest in payloads:
        row = conn.execute(
            "SELECT octet_length(payload_json) FROM derivation_payloads WHERE payload_sha256=?",
            (digest,),
        ).fetchone()
        if row is not None:
            total += int(row[0])
    return total


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def _note(conn: sqlite3.Connection, plan_digest: str) -> sqlite3.Row | None:
    if not _has_table(conn, "retention_applies"):
        return None
    found: sqlite3.Row | None = conn.execute(
        "SELECT * FROM retention_applies WHERE plan_digest=?", (plan_digest,)
    ).fetchone()
    return found


def _unfinished_notes(conn: sqlite3.Connection, *, exclude: str) -> list[sqlite3.Row]:
    """Notes whose apply committed and whose file removal never finished."""
    if not _has_table(conn, "retention_applies"):
        return []
    return list(
        conn.execute(
            "SELECT * FROM retention_applies WHERE files_completed_at IS NULL AND plan_digest<>? "
            "ORDER BY plan_digest",
            (exclude,),
        )
    )


def _finish_unfinished(
    database: Database,
    notes: list[sqlite3.Row],
    *,
    eligible: frozenset[str],
    plan_digest: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Close the notes of applies that crashed before their files went.

    A note is never replayed on its own. `eligible` is what the plan this apply
    just recomputed under both locks calls removable, and this runs after that
    plan's own files went. A file of the note in `eligible` is finished: this
    apply removed it, or it was already gone. A file the fresh plan no longer
    names stays where it is and is recorded under `skipped_files`,
    because the state can move between the crash and the next apply and a stale
    note is not a plan of the state it runs against (D-0151).

    The receipt accounts for every file the note planned. `removed_files` is
    every planned file that is no longer on disk, which covers both the files
    this apply unlinked and the files the crashed apply had already unlinked
    before it died. Counting only this apply's own unlinks would drop the
    latter from the note's receipt altogether, and the receipt is the operator's
    account of what that apply did.

    The note is then closed with its own receipt, saying which apply finished it,
    rather than staying open forever and being reconsidered by every later apply.
    A skipped file is not lost work: if it becomes removable again, the next plan
    names it again.
    """
    finished: list[dict[str, Any]] = []
    for note in notes:
        planned = tuple(str(item) for item in json.loads(str(note["planned_files_json"])))
        present = {path for path in planned if (database.state_dir / path).exists()}
        skipped = [path for path in planned if path not in eligible and path in present]
        done = [path for path in planned if path not in present]
        receipt = {
            "format": APPLY_RECEIPT_FORMAT,
            "plan_digest": str(note["plan_digest"]),
            "started_at": str(note["started_at"]),
            "files_completed_at": now.isoformat(),
            "planned_files": list(planned),
            "removed_files": done,
            "skipped_files": skipped,
            "planned_payloads": json.loads(str(note["planned_payloads_json"])),
            "removed_payload_bytes": int(note["removed_payload_bytes"]),
            "finished_by": plan_digest,
        }
        _finish_note(database, str(note["plan_digest"]), receipt=receipt, now=now)
        _write_receipt(database.state_dir, f"{note['plan_digest']}.json", receipt)
        finished.append(
            {
                "plan_digest": str(note["plan_digest"]),
                "removed_files": done,
                "skipped_files": skipped,
            }
        )
    return finished


def _finish_note(
    database: Database, plan_digest: str, *, receipt: dict[str, Any], now: datetime
) -> None:
    with database.transaction() as txn:
        txn.execute(
            "UPDATE retention_applies SET files_completed_at=?, receipt_json=? "
            "WHERE plan_digest=? AND files_completed_at IS NULL",
            (now.isoformat(), canonical_json(receipt).decode(), plan_digest),
        )


def _write_receipt(state_dir: Path, name: str, receipt: dict[str, Any]) -> dict[str, Any]:
    """One receipt per apply, written once. A rerun reads the note, not this file."""
    path = receipt_directory(state_dir) / name
    if not path.is_file():
        durable_write(path, canonical_json(receipt))
    return receipt


# ---------------------------------------------------------------------------
# Reclaim
# ---------------------------------------------------------------------------


def reclaim(database: Database, *, now: datetime, timeout: float = 60) -> dict[str, Any]:
    """Rewrite the database file in place so freed pages leave the file.

    Deleting rows, and dropping a table, return pages to SQLite's free list and
    leave the file the size it was. The cap in the plan's section 9 and every
    backup measure the file, so the gap has to be closed on purpose.

    In place, on the one writer connection: no second database file, no swap and
    no second install path. SQLite's own journal makes the rewrite
    all-or-nothing, so a crash or a kill at any point rolls back to the original
    file the next time it is opened. Readers keep reading a consistent view. A
    pending write on another connection makes `VACUUM` fail, and then this
    reports it and stops with nothing changed
    ([D-0150](../../../journal/decisions/0150-reclaim-rewrites-in-place-on-the-one-writer-connection.md)).
    """
    if not database.holds_writer_lock:
        raise RetentionError(
            "gc --reclaim needs the writer lock; open the state database for writing"
        )
    state_dir = database.state_dir
    conn = database.connection
    before = usage(conn, state_dir)
    free = shutil.disk_usage(state_dir).free
    needed = int(before["file_bytes"]) * RECLAIM_FREE_MULTIPLE
    if free < needed:
        raise RetentionError(
            f"reclaim needs about {needed} free bytes on the state volume and there are {free}; "
            "nothing was touched. Free space or move the state directory first"
        )
    if conn.in_transaction:
        raise RetentionError("reclaim cannot run inside a transaction; nothing was touched")
    with control_lock(state_dir, timeout=timeout):
        try:
            # Start the rewrite from a file with no pending changes, as backup
            # creation already does, then rewrite it.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        except sqlite3.Error as exc:
            message = str(exc).lower()
            if "lock" in message or "busy" in message:
                raise RetentionError(
                    "another connection holds a pending write, so the file was not rewritten; "
                    "nothing was changed"
                ) from exc
            raise RetentionError(
                f"reclaim did not finish and the file rolled back to what it was: {exc}"
            ) from exc
        _check_after_rewrite(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = usage(conn, state_dir)
    receipt = {
        "format": RECLAIM_RECEIPT_FORMAT,
        "at": now.isoformat(),
        "before": before,
        "after": after,
        "file_bytes_freed": int(before["file_bytes"]) - int(after["file_bytes"]),
    }
    name = _receipt_name(receipt_directory(state_dir), now)
    durable_write(receipt_directory(state_dir) / name, canonical_json(receipt))
    return receipt


def _check_after_rewrite(conn: sqlite3.Connection) -> None:
    """The same three checks a checkpoint is verified with, on the live file."""
    from swingset.backup.checkpoint import _check_database_schema

    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if integrity is None or integrity[0] != "ok" or foreign_keys:
        raise RetentionError(
            f"the database failed its checks after the rewrite: integrity={integrity}, "
            f"foreign_keys={foreign_keys[:5]}"
        )
    # Read the supported version when the check runs, not when this module was
    # imported, so a caller that pins the schema is checked against its own pin.
    from .db import SCHEMA_VERSION

    _check_database_schema(conn, SCHEMA_VERSION)


def _receipt_name(directory: Path, now: datetime) -> str:
    """One receipt per reclaim, named by when it ran, never overwriting one."""
    stamp = now.strftime("reclaim-%Y%m%dT%H%M%SZ")
    name = f"{stamp}.json"
    suffix = 2
    while (directory / name).exists():
        name = f"{stamp}-{suffix}.json"
        suffix += 1
    return name


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )
