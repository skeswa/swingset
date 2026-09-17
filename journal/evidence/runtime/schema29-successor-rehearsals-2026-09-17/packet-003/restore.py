"""Restore the pinned H16 checkpoint into disposable held state, then migrate it.

Only public-repository reads are allowed. This never runs a cycle, accepts inputs,
publishes, activates repairs, changes live state or removes the operator hold.
The helper may be frozen separately from the runtime; pin both inventories.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
CANDIDATE = "cand_8f31cad7226643ae"
PRODUCTION = Path("/var/lib/swingset")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def guards() -> dict[str, str]:
    require((PRODUCTION / "operator-hold").is_file(), "live operator hold is absent")
    require(not (PRODUCTION / "RESTORE_PENDING").exists(), "live restore is pending")
    result = {}
    for kind in ("cycle", "backup", "summary"):
        for suffix in ("service", "timer"):
            unit = f"swingset-{kind}.{suffix}"
            state = subprocess.check_output(
                ["systemctl", "show", unit, "--property=ActiveState", "--value"], text=True
            ).strip()
            require(state == "inactive", f"ordinary unit is active: {unit}")
            result[unit] = state
    return result


def frozen_util(source: Path, receipt: Path, expected: str) -> Any:
    require(sha(receipt) == expected, "runtime receipt differs")
    name = "journal/tools/runtime/rehearse_extension_migration.py"
    inventory = json.loads(receipt.read_bytes())["files"]
    recorded = inventory[name]
    require(
        sha(source / name) == (recorded["sha256"] if isinstance(recorded, dict) else recorded),
        "migration helper differs",
    )
    spec = importlib.util.spec_from_file_location("frozen_migration_rehearsal", source / name)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import frozen migration helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_source(source, receipt, expected)
    return module


class ReadOnlyHeldHub:
    """Check the actual public baseline while the restore barrier and hold exist."""

    def __init__(self, hub: Any, state: Path, *, hold_sha: str, baseline: str) -> None:
        self.hub, self.state, self.hold_sha, self.baseline = hub, state, hold_sha, baseline
        self.heads: list[str] = []
        self.remote: dict[str, Any] | None = None

    def held(self) -> None:
        require(
            sha(self.state / "operator-hold") == self.hold_sha, "restored operator hold changed"
        )

    def head(self) -> str:
        from swingset.backup.checkpoint import _verify_candidate_remote

        self.held()
        require(
            (self.state / "RESTORE_PENDING").is_file(),
            "restore barrier cleared before remote check",
        )
        value = self.hub.head()
        require(
            value == self.baseline,
            f"public baseline differs: expected {self.baseline!r}, actual {value!r}",
        )
        self.heads.append(value)
        if self.remote is None:
            remote = self.hub.inspect(value)
            require(remote.commit == value, "remote inspection returned another commit")
            _verify_candidate_remote((self.state / "baseline").resolve(), remote)
            self.remote = asdict(remote)
        self.held()
        return str(value)

    def inspect(self, commit: str) -> Any:
        raise ValueError("pinned acknowledged checkpoint must have no pending publication")

    def is_initial_head(self, commit: str) -> bool:
        raise ValueError("initial publication is outside restore rehearsal")

    def create_commit(self, **kwargs: Any) -> str:
        raise ValueError("restore rehearsal forbids remote writes")


def exercise_restore(
    checkpoint: Path,
    state: Path,
    manifest: dict[str, Any],
    hub: Any,
    clock: Any,
    *,
    maximum_schema: int,
) -> ReadOnlyHeldHub:
    from swingset.backup.checkpoint import restore_from_checkpoint

    require(not state.exists(), "restore target must be new")
    checked = ReadOnlyHeldHub(
        hub, state, hold_sha=manifest["files"]["operator-hold"]["sha256"], baseline=BASELINE
    )
    restore_from_checkpoint(
        checkpoint, state, checked, clock, lock_timeout=0, maximum_schema_version=maximum_schema
    )
    checked.held()
    require(not (state / "RESTORE_PENDING").exists(), "restore activation did not finish")
    require(
        len(checked.heads) == 2 and checked.remote is not None, "remote restore checks incomplete"
    )
    return checked


class HeldStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, finish: Callable[[], None]) -> None:
        self.stream, self.finish = stream, finish

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self.stream
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.finish()


class SerialReadTransport(httpx.BaseTransport):
    """Keep one request in flight through body close; spacing starts at completion."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        user_agent: str,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner, self.user_agent, self.monotonic, self.sleep = (
            inner,
            user_agent,
            monotonic,
            sleep,
        )
        self.lock = threading.Lock()
        self.completed: dict[str, float] = {}
        self.requests: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.lock.acquire()
        finished = False
        host = request.url.host

        def finish() -> None:
            nonlocal finished
            if not finished:
                finished = True
                self.completed[host] = self.monotonic()
                self.lock.release()

        try:
            require(request.method in {"GET", "HEAD"}, "remote writes are forbidden")
            require(len(self.requests) < 256, "public verification request budget exhausted")
            self.sleep(max(0.0, self.completed.get(host, -5.0) + 5.0 - self.monotonic()))
            request.headers["User-Agent"] = self.user_agent
            self.requests.append(
                dict(method=request.method, host=host, issued_at=datetime.now(UTC).isoformat())
            )
            response = self.inner.handle_request(request)
            if not isinstance(response.stream, httpx.SyncByteStream):
                raise ValueError("unexpected asynchronous response")
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                extensions=response.extensions,
                stream=HeldStream(response.stream, finish),
            )
        except BaseException:
            finish()
            raise

    def close(self) -> None:
        self.inner.close()


def remote_reader(repo: str, cache: Path) -> Any:
    # Keep metadata and file reads serial, including redirects, with the project
    # UA and five-second spacing. Disable Xet's separate download worker pool.
    require(
        "huggingface_hub.constants" not in sys.modules,
        "remote client must initialize with disposable cache controls",
    )
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_HUB_CACHE"] = str(cache / "hub")
    from huggingface_hub import set_client_factory

    from swingset.fetch.client import USER_AGENT
    from swingset.publish.huggingface import HuggingFaceHub

    transport = SerialReadTransport(httpx.HTTPTransport(), USER_AGENT)
    set_client_factory(lambda: httpx.Client(transport=transport, follow_redirects=True, timeout=60))
    hub: Any = HuggingFaceHub(repo, token=os.environ["HF_TOKEN"])
    hub.rehearsal_requests = transport.requests
    return hub


def runtime_bound(source: Path) -> None:
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name == "swingset" or name.startswith("swingset."):
            require(
                path is not None and Path(path).resolve().is_relative_to(source / "src"),
                f"runtime import escaped frozen source: {name}",
            )


def expected_restore(conn: sqlite3.Connection, util: Any) -> dict[str, Any]:
    expected = util.table_receipts(conn)
    rows = [list(row) for row in conn.execute("SELECT * FROM event_pressure_state ORDER BY singleton")]
    require(expected["event_pressure_state"]["columns"] == ["singleton", "epoch", "sequence"]
            and len(rows) == 1 and rows[0][0] == 1, "restore pressure fence shape differs")
    rows[0][1] += 1
    body = json.dumps(rows[0], separators=(",", ":"), ensure_ascii=True).encode()
    expected["event_pressure_state"] = dict(expected["event_pressure_state"],
        sha256=hashlib.sha256(len(body).to_bytes(8, "big") + body).hexdigest())
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--archive-commit", required=True)
    parser.add_argument("--target-schema", type=int, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--helper-sha256", required=True)
    parser.add_argument("--public-repo", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require(
        len(args.checkpoint_sha256) == 64
        and all(c in "0123456789abcdef" for c in args.checkpoint_sha256),
        "invalid checkpoint digest",
    )
    require(
        len(args.archive_commit) == 40
        and all(c in "0123456789abcdef" for c in args.archive_commit),
        "invalid archive commit",
    )
    require(args.public_repo == "skeswa/swingset", "public repository differs")
    require(sha(Path(__file__)) == args.helper_sha256, "restore helper differs")
    source, checkpoint = args.source.resolve(strict=True), args.checkpoint.resolve(strict=True)
    destination = args.destination.resolve()
    require(not destination.exists(), "destination must be new")
    require(
        not any(
            destination.is_relative_to(p) or p.is_relative_to(destination)
            for p in (PRODUCTION, source, checkpoint)
        ),
        "destination overlaps protected state or inputs",
    )
    util = frozen_util(source, args.source_receipt, args.source_receipt_sha256)
    require(
        sha(checkpoint / "checkpoint.json") == args.checkpoint_sha256, "checkpoint identity differs"
    )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    require(
        manifest["schema_version"] == 28
        and manifest["pending_candidate"] is None
        and manifest["baseline_candidate"] == CANDIDATE,
        "checkpoint is not the acknowledged H16 baseline",
    )
    require("operator-hold" in manifest["files"], "checkpoint omitted the hold")
    before_units = guards()
    from swingset.clock import SystemClock
    from swingset.state.db import SCHEMA_VERSION, open_database

    require(
        SCHEMA_VERSION == args.target_schema and args.target_schema == 29,
        "runtime schema differs from reviewed target",
    )
    runtime_bound(source)
    destination.mkdir()
    hub = remote_reader(args.public_repo, destination / "remote-cache")
    state = destination / "restored-state"
    report: dict[str, Any] = dict(
        format="extension-operational-restore-rehearsal-v1",
        started_at=datetime.now(UTC).isoformat(),
        passed=False,
        checkpoint=str(checkpoint),
        checkpoint_sha256=args.checkpoint_sha256,
        acknowledged_archive_commit=args.archive_commit,
        source=str(source),
        source_receipt_sha256=args.source_receipt_sha256,
        helper_sha256=args.helper_sha256,
        public_repo=args.public_repo,
        baseline=BASELINE,
        state=str(state),
        units_before=before_units,
        target_schema=args.target_schema,
        source_requests=0,
        public_writes=0,
        input_acceptance=False,
        workers_started=False,
        repairs_activated=False,
    )
    try:
        report["stage"] = "checkpoint_table_receipts"
        with sqlite3.connect(
            (checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
        ) as conn:
            report["before"] = util.table_receipts(conn)
            report["expected_restored"] = expected_restore(conn, util)
            report["restore_change_contract"] = "event_pressure_state_singleton_epoch_plus_one_only"
        report["stage"] = "restore_protocol"
        checked = exercise_restore(
            checkpoint, state, manifest, hub, SystemClock(), maximum_schema=SCHEMA_VERSION
        )
        report["remote"] = checked.remote
        report["restore_heads"] = checked.heads
        with open_database(state, read_only=True) as database:
            require(
                database.schema_version == 28, "restore unexpectedly migrated before activation"
            )
            require(
                util.table_receipts(database.connection) == report["expected_restored"],
                "restoration changed retained application state",
            )
        report["schema28_activated_under_hold"] = True
        report["stage"] = "held_migration"
        checked.held()
        with open_database(state) as database:
            after = util.table_receipts(database.connection)
            report["changed_existing_tables"] = util.compare(report["expected_restored"], after)
            report["after"] = after
            report["schema"] = database.schema_version
            require(
                not report["changed_existing_tables"] and database.schema_version == SCHEMA_VERSION,
                "migration changed retained state",
            )
            require(
                [tuple(row) for row in database.connection.execute("PRAGMA integrity_check")]
                == [("ok",)],
                "migrated integrity failure",
            )
            require(
                database.connection.execute("PRAGMA foreign_key_check").fetchone() is None,
                "migrated foreign-key failure",
            )
        checked.held()
        with open_database(state) as database:
            require(
                util.table_receipts(database.connection) == after,
                "reopening changed migrated state",
            )
        report["stage"] = "preservation_and_remote_recheck"
        for name, record in manifest["files"].items():
            if name != "state.sqlite":
                require(
                    sha(state / name) == record["sha256"], f"restored private file changed: {name}"
                )
        require(
            sha(checkpoint / "checkpoint.json") == args.checkpoint_sha256
            and sha(checkpoint / "state.sqlite") == manifest["files"]["state.sqlite"]["sha256"],
            "checkpoint changed during rehearsal",
        )
        require(hub.head() == BASELINE, "public head changed after migration")
        report["units_after"] = guards()
        runtime_bound(source)
        util.verify_source(source, args.source_receipt, args.source_receipt_sha256)
        report["passed"] = True
        report["stage"] = "finished_held_without_input_acceptance"
    except BaseException as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["restore_pending"] = (state / "RESTORE_PENDING").exists()
        report["operator_hold_present"] = (state / "operator-hold").is_file()
        report["public_requests"] = hub.rehearsal_requests
        with (destination / "restore-receipt.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")


if __name__ == "__main__":
    raise SystemExit("use the sealed packet runner")
