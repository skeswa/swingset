"""Exact, operator-authorized new-source fixture quarantine; no admission activation."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import signal
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from swingset.clock import SystemClock
from swingset.config import load_config
from swingset.fetch.archive import canonical, digest

MANIFEST_SHA = "c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93"
APPROVAL = "approved_new_source_fixture_exception_only"
HOST = "web.archive.org"


class FixtureStopped(RuntimeError):
    """A prerequisite or hard acquisition boundary was not satisfied."""


def read_manifest(path: Path, repository: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_bytes())
    if digest(canonical(manifest)) != MANIFEST_SHA:
        raise FixtureStopped("manifest differs from the exact reviewed proposal")
    for target in manifest["targets"]:
        evidence = target["evidence"]
        body = (repository / evidence["path"]).read_bytes()
        if digest(body) != evidence["sha256"] or evidence["row"] not in json.loads(body):
            raise FixtureStopped("retained CDX evidence does not match")
        if headers := target.get("retained_headers"):
            if digest((repository / headers["path"]).read_bytes()) != headers["sha256"]:
                raise FixtureStopped("retained header evidence does not match")
    return cast(dict[str, Any], manifest)


def authorize(path: Path | None, *, state: Path, output: Path, now: datetime) -> dict[str, Any]:
    if path is None:
        raise FixtureStopped("explicit owner authorization record is required")
    record = json.loads(path.read_bytes())
    validate_authorization(record, state=state, output=output, now=now)
    return cast(dict[str, Any], record)


def validate_authorization(
    record: dict[str, Any], *, state: Path, output: Path, now: datetime
) -> None:
    required = {
        "approval": APPROVAL,
        "manifest_canonical_sha256": MANIFEST_SHA,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "published_stages": ["V2", "V4"],
    }
    if any(record.get(key) != value for key, value in required.items()):
        raise FixtureStopped("authorization does not match this fixture-only execution")
    for field in ("approved_by", "owner_decision_reference", "publication_receipt_reference"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise FixtureStopped(f"authorization requires {field}")
    try:
        approved = datetime.fromisoformat(record["approved_at"])
        window = record["execution_window"]
        starts = datetime.fromisoformat(window["starts_at"])
        expires = datetime.fromisoformat(window["expires_at"])
        valid = approved <= starts <= now < expires <= starts + timedelta(days=1)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise FixtureStopped(
            "execution window must be current, at most 24 hours, and after approval"
        )
    state, output = state.resolve(), output.resolve()
    if output == state or output.is_relative_to(state) or state.is_relative_to(output):
        raise FixtureStopped("quarantine must be separate from production state")
    if output.exists():
        raise FixtureStopped("quarantine already exists; fixture intake is single-use, no retry")


def _accounting_only(action: int, table: str | None, column: str | None, *_: Any) -> int:
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE):
        allowed = table == "host_budget" or (
            table == "hosts"
            and (
                action == sqlite3.SQLITE_INSERT
                or column in {"next_allowed_at", "paused_until", "pause_reason", "pause_streak"}
            )
        )
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    if action in {
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


@contextlib.contextmanager
def accounting_connection(state: Path) -> Iterator[sqlite3.Connection]:
    """Use the production writer lock and existing schema without migration or run rows."""
    if (state / "RESTORE_PENDING").exists():
        raise FixtureStopped("restore verification is pending")
    with (state / "state.lock").open("r+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FixtureStopped("production writer lock is held") from exc
        conn = sqlite3.connect(
            (state / "state.sqlite").resolve().as_uri() + "?mode=rw",
            uri=True,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.set_authorizer(_accounting_only)
            yield conn
        finally:
            conn.close()
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def wall_limit(seconds: float) -> Iterator[None]:
    """POSIX CLI deadline interrupts blocked network reads as well as between-read checks."""

    def expired(signum: int, frame: object) -> None:
        raise FixtureStopped("elapsed-time ceiling")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--execute", action="store_true", help="Default is a read-only dry run")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    manifest = read_manifest(args.manifest, args.repo)
    if not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "requests_made": 0,
                    "manifest_canonical_sha256": MANIFEST_SHA,
                    "targets": [target["replay_url"] for target in manifest["targets"]],
                    "metadata_queries": manifest["metadata_queries"],
                    "limits": manifest["limits"],
                    "authorization_required": True,
                    "authorization_path_supplied": args.authorization is not None,
                },
                indent=2,
            )
        )
        return
    from research.fixture_transport import FixtureRunner

    clock = SystemClock()
    authorization = authorize(
        args.authorization, state=args.state, output=args.quarantine, now=clock.now()
    )
    with accounting_connection(args.state) as conn:
        runner = FixtureRunner(
            conn,
            load_config(args.repo / "config"),
            clock,
            state=args.state,
            output=args.quarantine,
            manifest=manifest,
            authorization=authorization,
        )
        with wall_limit((runner.deadline - clock.now()).total_seconds()):
            receipt = runner.run()
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
