"""Rehearse the current schema on a disposable copy of a verified H16 checkpoint.

This performs no source requests, public writes, live migration, input acceptance,
or repair activation. It compares every pre-existing application table, retaining
only the expected schema-version metadata exception. It is not a restore drill.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_source(source: Path, receipt: Path, expected_sha: str) -> None:
    if sha(receipt) != expected_sha:
        raise ValueError("source receipt differs")
    inventory = json.loads(receipt.read_bytes())["files"]
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("empty source inventory")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("frozen source cannot contain unlisted symlink targets")
    actual = {
        p.relative_to(source).as_posix()
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.resolve() != receipt.resolve()
    }
    if actual != set(inventory):
        raise ValueError("source inventory is incomplete or contains extra paths")
    for name, expected in inventory.items():
        relative = Path(name)
        path = source / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.resolve().is_relative_to(source)
        ):
            raise ValueError("source inventory path escapes root")
        if sha(path) != (expected["sha256"] if isinstance(expected, dict) else expected):
            raise ValueError(f"source differs: {name}")
    for required in ("src/swingset/state/db.py", "src/swingset/backup/checkpoint.py"):
        if required not in inventory:
            raise ValueError("source omits runtime")


def table_receipts(conn: sqlite3.Connection) -> dict[str, Any]:
    """Hash stable row order with bounded Python memory; never emit row contents."""
    receipts = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        columns = list(conn.execute(f"PRAGMA table_info({quoted})"))
        digest = hashlib.sha256()
        count = 0
        query = f"SELECT * FROM {quoted}"
        if name == "meta":
            query += " WHERE key!='schema_version'"
        primary = sorted((r[5], r[1]) for r in columns if r[5])
        order = ",".join('"' + column.replace('"', '""') + '"' for _, column in primary)
        query += " ORDER BY " + (order or "rowid")
        for row in conn.execute(query):
            data = json.dumps(
                list(row),
                separators=(",", ":"),
                ensure_ascii=True,
                default=lambda value: (
                    {"bytes": value.hex()} if isinstance(value, bytes) else str(value)
                ),
            ).encode()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            count += 1
        receipts[name] = dict(
            rows=count, sha256=digest.hexdigest(), columns=[r[1] for r in columns]
        )
    return receipts


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [name for name in before if before[name] != after.get(name)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-receipt", required=True, type=Path)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    source = args.source.resolve(strict=True)
    if Path(__file__).resolve() != source / "journal/tools/runtime/rehearse_extension_migration.py":
        raise ValueError("rehearsal helper must be part of the frozen source")
    checkpoint = args.checkpoint.resolve(strict=True)
    destination = args.destination.resolve()
    if destination.exists() or destination.is_relative_to(Path("/var/lib/swingset")):
        raise ValueError("destination must be new disposable state outside production")
    if destination.is_relative_to(checkpoint) or destination.is_relative_to(source):
        raise ValueError("destination overlaps immutable inputs")

    verify_source(source, args.source_receipt, args.source_receipt_sha256)
    from swingset.backup.checkpoint import verify_checkpoint
    from swingset.state.db import SCHEMA_VERSION, open_database

    for function in (verify_checkpoint, open_database):
        if not Path(inspect.getfile(function)).resolve().is_relative_to(source):
            raise ValueError("runtime differs from frozen source")
    if sha(checkpoint / "checkpoint.json") != args.checkpoint_sha256:
        raise ValueError("checkpoint identity differs")
    manifest = verify_checkpoint(checkpoint, maximum_schema_version=SCHEMA_VERSION)
    database_sha = manifest["files"]["state.sqlite"]["sha256"]
    if manifest["schema_version"] != 14 or manifest["pending_candidate"] is not None:
        raise ValueError("expected acknowledged schema14 baseline checkpoint")
    if "operator-hold" not in manifest["files"]:
        raise ValueError("checkpoint omitted the operator hold")
    report: dict[str, Any] = dict(
        format="event-extension-migration-rehearsal-v1",
        started_at=datetime.now(UTC).isoformat(),
        checkpoint=str(checkpoint),
        checkpoint_sha256=args.checkpoint_sha256,
        source=str(source),
        source_receipt_sha256=args.source_receipt_sha256,
        helper_sha256=sha(Path(__file__)),
        old_schema=14,
        target_schema=SCHEMA_VERSION,
        passed=False,
        scope="migration_only_without_runtime_input_acceptance",
    )
    destination.mkdir()
    try:
        # A database-only copy makes migration effects explicit. Artifacts remain
        # in the verified checkpoint; no pipeline command opens this specimen.
        with sqlite3.connect(
            (checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
        ) as before:
            report["before"] = table_receipts(before)
        shutil.copyfile(checkpoint / "state.sqlite", destination / "state.sqlite")
        if sha(destination / "state.sqlite") != database_sha:
            raise ValueError("copied checkpoint database changed")
        shutil.copyfile(checkpoint / "operator-hold", destination / "operator-hold")
        with open_database(destination) as database:
            after = table_receipts(database.connection)
            report["changed_existing_tables"] = compare(report["before"], after)
            report["new_tables"] = sorted(set(after) - set(report["before"]))
            report["after"] = after
            report["schema"] = database.schema_version
            report["integrity"] = [
                list(r) for r in database.connection.execute("PRAGMA integrity_check")
            ]
            report["foreign_key_failure"] = (
                database.connection.execute("PRAGMA foreign_key_check").fetchone() is not None
            )
            if report["changed_existing_tables"] or report["schema"] != SCHEMA_VERSION:
                raise ValueError("migration changed protected state or missed target schema")
            if report["integrity"] != [["ok"]] or report["foreign_key_failure"]:
                raise ValueError("migrated specimen failed integrity checks")
        with open_database(destination) as database:
            if table_receipts(database.connection) != after:
                raise ValueError("reopening changed migrated state")
        if sha(checkpoint / "checkpoint.json") != args.checkpoint_sha256:
            raise ValueError("checkpoint manifest changed during rehearsal")
        if sha(checkpoint / "state.sqlite") != database_sha:
            raise ValueError("checkpoint database changed during rehearsal")
        verify_source(source, args.source_receipt, args.source_receipt_sha256)
        report["passed"] = True
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        with (destination / "migration-receipt.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    main()
