"""Phased, offline input acceptance and ordinary derivation on disposable state.

This is not an operational restore, production activation or clean rebuild audit.
Prepare copies every verified checkpoint artifact; accept records exact frozen
inputs; drain runs bounded ordinary parse/project/link attempts without fetching.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

SOURCE_RECEIPT = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
CHECKPOINT_SHA = "22102b3cc07b07b49800c702396a748bbbe733e148bbca9b426bff370792223d"
ARCHIVE_COMMIT = "da38556935ee18ee26a93e121ef461514ac2f423"
BASELINE = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
CANDIDATE = "cand_8f31cad7226643ae"
MARKER = "extension-input-scratch.json"
PROTECTED = ("hosts", "host_budget", "operator_pauses", "control_state", "control_events")


class ReplayLimit(Exception):
    pass


@contextmanager
def selection_window(conn: sqlite3.Connection, deadline: float) -> Iterator[None]:
    from swingset.state.write_deadline import WRITE_SECONDS

    def expired() -> bool:
        return monotonic() >= deadline - WRITE_SECONDS - 5

    conn.set_progress_handler(lambda: int(expired()), 1000)
    try:
        if expired():
            raise ReplayLimit()
        yield
        if expired():
            raise ReplayLimit()
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error) and expired():
            raise ReplayLimit() from error
        raise
    finally:
        conn.set_progress_handler(None, 0)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_new(path: Path, value: dict[str, Any]) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def scratch_path(path: Path, checkpoint: Path, source: Path) -> Path:
    path = path.resolve()
    require(
        not any(
            path == root or path.is_relative_to(root)
            for root in (
                Path("/var/lib/swingset").resolve(),
                checkpoint.resolve(),
                source.resolve(),
            )
        ),
        "scratch overlaps production or immutable input",
    )
    require(
        "checkpoints" not in path.parts and not (path / "checkpoint.json").exists(),
        "checkpoint is not scratch",
    )
    return path


def external_inputs(source: Path, config: Path, overrides: Path) -> dict[str, str]:
    expected = {
        "config/" + name: sha(source / "config" / name) for name in ("hosts.toml", "sources.toml")
    }
    expected.update(
        {"overrides/" + path.name: sha(path) for path in (source / "overrides").glob("*.csv")}
    )
    actual = {"config/" + name: sha(config / name) for name in ("hosts.toml", "sources.toml")}
    actual.update({"overrides/" + path.name: sha(path) for path in overrides.glob("*.csv")})
    require(
        overrides.is_dir() and actual == expected,
        "external configuration or override inventory differs from frozen inputs",
    )
    require(
        not os.environ.get("SWINGSET_REVISION"),
        "environment repository identity override is not reviewed",
    )
    return actual


def query_hash(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> dict[str, Any]:
    digest, count = hashlib.sha256(), 0
    for row in conn.execute(query, params):
        body = json.dumps(list(row), separators=(",", ":"), default=str).encode()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
        count += 1
    return dict(rows=count, sha256=digest.hexdigest())


def protected(
    conn: sqlite3.Connection, admission_highwater: int, policy_highwater: int = 0
) -> dict[str, Any]:
    result = {
        name: query_hash(conn, f'SELECT * FROM "{name}" ORDER BY rowid') for name in PROTECTED
    }
    result["original_execution_admissions"] = query_hash(
        conn,
        "SELECT * FROM execution_admissions WHERE rowid<=? ORDER BY rowid",
        (admission_highwater,),
    )
    result["request_admissions"] = query_hash(
        conn, "SELECT * FROM execution_admissions WHERE action_kind='request' ORDER BY rowid"
    )
    result["admission_policies"] = query_hash(
        conn,
        "SELECT * FROM admission_policies WHERE rowid<=? OR mode<>'shadow' ORDER BY rowid",
        (policy_highwater,),
    )
    return result


def named_judges(conn: sqlite3.Connection) -> dict[str, list[Any]]:
    return {
        str(row[0]): list(row[1:])
        for row in conn.execute(
            "SELECT judge_id,name_raw,wsdc_id FROM judges WHERE name_raw IS NOT NULL AND name_raw<>'' ORDER BY judge_id"
        )
    }


def judge_continuity(conn: sqlite3.Connection, original: dict[str, list[Any]]) -> dict[str, Any]:
    current = named_judges(conn)
    differences = [
        identifier for identifier, value in original.items() if current.get(identifier) != value
    ]
    return dict(
        original_named=len(original),
        original_null_ids=sum(value[1] is None for value in original.values()),
        preserved=len(original) - len(differences),
        changed_or_missing=len(differences),
        examples=differences[:20],
        requires_review=bool(differences),
    )


def verify_files(scratch: Path, marker: dict[str, Any], *, check_original: bool = False) -> None:
    checkpoint = Path(marker["checkpoint"])
    require(
        sha(checkpoint / "checkpoint.json") == marker["checkpoint_sha256"],
        "checkpoint manifest changed",
    )
    for name, record in marker["retained_files"].items():
        target = scratch / name
        require(
            target.is_file()
            and not target.is_symlink()
            and target.stat().st_nlink == 1
            and sha(target) == record["sha256"],
            f"retained scratch evidence changed or linked: {name}",
        )
        if check_original:
            require(sha(checkpoint / name) == record["sha256"], "origin evidence changed")
    baseline = scratch / "baseline"
    require(
        baseline.is_symlink() and baseline.resolve() == scratch / "candidates" / CANDIDATE,
        "scratch baseline differs",
    )
    for path in scratch.rglob("*"):
        require(not path.is_symlink() or path == baseline, "unexpected scratch symlink")
    require(not (scratch / "RESTORE_PENDING").exists(), "scratch restore remains pending")


def prepare(checkpoint: Path, scratch: Path, source: Path, util: Any) -> dict[str, Any]:
    from swingset.backup.checkpoint import verify_checkpoint
    from swingset.state.db import open_database

    scratch = scratch_path(scratch, checkpoint, source)
    require(
        not scratch.exists() and sha(checkpoint / "checkpoint.json") == CHECKPOINT_SHA,
        "new scratch or reviewed checkpoint required",
    )
    manifest = verify_checkpoint(checkpoint, maximum_schema_version=28)
    require(
        manifest["schema_version"] == 14
        and manifest["baseline_candidate"] == CANDIDATE
        and manifest["pending_candidate"] is None
        and "operator-hold" in manifest["files"],
        "checkpoint is not held acknowledged schema14 predecessor",
    )
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as conn:
        require(
            conn.execute("SELECT 1 FROM execution_admissions WHERE state<>'settled'").fetchone()
            is None,
            "checkpoint has unsettled admissions",
        )
        before = util.table_receipts(conn)
    scratch.mkdir(parents=True)
    for name in manifest["files"]:
        target = scratch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkpoint / name, target)
        require(sha(target) == manifest["files"][name]["sha256"], "copied evidence differs")
    (scratch / "baseline").symlink_to(Path("candidates") / CANDIDATE)
    published = json.loads((scratch / "baseline/PUBLISHED").read_bytes())
    require(
        published["commit"] == BASELINE and published["candidate_id"] == CANDIDATE,
        "checkpoint publication differs",
    )
    with open_database(scratch) as db:
        require(
            db.schema_version == 28
            and util.compare(before, util.table_receipts(db.connection)) == [],
            "scratch migration changed predecessor tables",
        )
        highwater = db.connection.execute(
            "SELECT coalesce(max(rowid),0) FROM execution_admissions"
        ).fetchone()[0]
        policy_highwater = db.connection.execute(
            "SELECT coalesce(max(rowid),0) FROM admission_policies"
        ).fetchone()[0]
        marker = dict(
            format="extension-input-scratch-v1",
            scratch=str(scratch),
            checkpoint=str(checkpoint),
            checkpoint_sha256=CHECKPOINT_SHA,
            archive_commit=ARCHIVE_COMMIT,
            source=str(source),
            source_receipt_sha256=SOURCE_RECEIPT,
            helper_sha256=sha(Path(__file__)),
            schema=28,
            admission_highwater=highwater,
            protected=protected(db.connection, highwater),
            retained_files={
                name: value for name, value in manifest["files"].items() if name != "state.sqlite"
            },
            origin_database_sha256=manifest["files"]["state.sqlite"]["sha256"],
            prepared_at=datetime.now(UTC).isoformat(),
        )
        marker["named_judges"] = named_judges(db.connection)
        marker["policy_highwater"] = policy_highwater
        marker["protected"] = protected(db.connection, highwater, policy_highwater)
    verify_files(scratch, marker, check_original=True)
    write_new(scratch / MARKER, marker)
    return marker


def drain(
    db: Any,
    bundle: Any,
    clock: Any,
    *,
    max_seconds: float,
    max_units: int,
    selection_window: Any,
    verify_turn: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.schedule.fairness import next_offline, record_offline_service
    from swingset.state.attempts import recover_interrupted
    from swingset.state.control_scopes import unit_allowed
    from swingset.state.controls import recover_admissions
    from swingset.state.work import recover_parse_hints
    from swingset.state.write_deadline import WRITE_SECONDS

    require(60 <= max_seconds <= 3600 and 1 <= max_units <= 10000, "invalid drain bounds")
    require(
        db.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE state<>'settled' AND action_kind NOT IN ('parse','project','link')"
        ).fetchone()
        is None,
        "non-derivation admission requires separate reconciliation",
    )
    started = monotonic()
    deadline = started + max_seconds
    verify_turn()
    with db.transaction() as conn:
        recovered = recover_admissions(conn, now=clock.now())
        interrupted = recover_interrupted(db, now=clock.now())
    hints = recover_parse_hints(db, now=clock.now(), limit=100, wall_seconds=2)
    run_id = db.start_run(clock.now())
    counts: Counter[str] = Counter()
    attempted: set[Any] = set()
    report: dict[str, Any] = dict(
        run_id=run_id,
        recovered_admissions=recovered,
        interrupted_attempts=interrupted,
        parse_hint_recovery=hints,
        status="bounded_stop",
        max_seconds=max_seconds,
        max_units=max_units,
        time_limit_basis="selection_budget_not_hard_wall_deadline",
        transaction_limit_seconds=WRITE_SECONDS,
        worker_overrun_possible=True,
    )
    try:
        while len(attempted) < max_units and monotonic() < deadline - WRITE_SECONDS - 5:
            with selection_window(db.connection, deadline):
                unit = next_offline(
                    db.connection,
                    now=clock.now(),
                    allowed=lambda value: unit_allowed(db.connection, value, now=clock.now()),
                    exclude=attempted,
                )
            if unit is None:
                report["status"] = "no_more_runnable_units_this_turn"
                break
            verify_turn()
            attempted.add(unit)
            outcome = derive_one(db, Archive(db.state_dir), unit, bundle, clock, run_id)
            verify_turn()
            counts[unit.stage + "/" + (outcome.reason or "output_committed")] += 1
            with db.transaction() as conn:
                record_offline_service(conn, unit, now=clock.now())
        return report
    except ReplayLimit:
        report["status"] = "selection_time_limit"
        return report
    finally:
        with db.transaction() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=? WHERE run_id=?", (clock.now().isoformat(), run_id)
            )
        elapsed = monotonic() - started
        report.update(
            attempted=len(attempted),
            outcomes=dict(counts),
            elapsed_seconds=elapsed,
            selection_budget_overrun_seconds=max(0.0, elapsed - max_seconds),
            pending_queue_by_stage={
                row[0]: row[1]
                for row in db.connection.execute(
                    "SELECT stage,count(*) FROM pending_work GROUP BY stage"
                )
            },
            fleet_derivation_completeness="not_assessed_by_queue_count",
            all_work_current=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "accept", "drain"))
    for name in ("source", "checkpoint", "scratch", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--helper-sha256", required=True)
    parser.add_argument("--marker-sha256")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--max-units", type=int, default=100)
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    require(sha(Path(__file__)) == args.helper_sha256, "separately reviewed helper differs")
    source, checkpoint = args.source.resolve(strict=True), args.checkpoint.resolve(strict=True)
    scratch = scratch_path(args.scratch, checkpoint, source)
    output = args.output.resolve()
    require(
        not output.exists()
        and not any(
            output.is_relative_to(root)
            for root in (source, checkpoint, scratch, Path("/var/lib/swingset").resolve())
        ),
        "receipt must be new and outside input/state roots",
    )
    receipt = source / "extension-source.json"
    require(sha(receipt) == SOURCE_RECEIPT, "frozen runtime receipt differs")
    name = "journal/tools/runtime/rehearse_extension_migration.py"
    expected = json.loads(receipt.read_bytes())["files"][name]
    require(
        sha(source / name) == (expected["sha256"] if isinstance(expected, dict) else expected),
        "source verifier differs",
    )
    spec = importlib.util.spec_from_file_location("input_frozen_verifier", source / name)
    require(spec is not None and spec.loader is not None, "cannot import frozen verifier")
    assert spec is not None and spec.loader is not None
    util = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(util)
    util.verify_source(source, receipt, SOURCE_RECEIPT)
    sys.path[:0] = [str(source / "src"), str(source)]
    from swingset.clock import SystemClock
    from swingset.schedule.cycle import versions
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture

    def bound() -> None:
        for name, module in tuple(sys.modules.items()):
            if name == "swingset" or name.startswith("swingset."):
                file = getattr(module, "__file__", None)
                require(
                    file is not None and Path(file).resolve().is_relative_to(source / "src"),
                    "runtime import escaped frozen source",
                )

    def no_network(event: str, values: Any) -> None:
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
            raise RuntimeError("offline rehearsal forbids network and subprocesses")

    bound()
    sys.addaudithook(no_network)
    report: dict[str, Any] = dict(
        format="extension-input-rehearsal-v1",
        phase=args.phase,
        source=str(source),
        source_receipt_sha256=SOURCE_RECEIPT,
        helper_sha256=args.helper_sha256,
        checkpoint_sha256=CHECKPOINT_SHA,
        started_at=datetime.now(UTC).isoformat(),
        passed=False,
        network_requests=0,
        publication=False,
        repairs_activated=False,
        production_acceptance=False,
    )
    try:
        if args.phase == "prepare":
            marker = prepare(checkpoint, scratch, source, util)
            report["marker_sha256"] = sha(scratch / MARKER)
        else:
            require(args.marker_sha256 == sha(scratch / MARKER), "reviewed scratch marker differs")
            marker = json.loads((scratch / MARKER).read_bytes())
            require(
                marker["source"] == str(source)
                and marker["scratch"] == str(scratch)
                and marker["checkpoint"] == str(checkpoint)
                and marker["helper_sha256"] == args.helper_sha256
                and marker["source_receipt_sha256"] == SOURCE_RECEIPT
                and marker["checkpoint_sha256"] == CHECKPOINT_SHA,
                "scratch marker scope differs",
            )
            require(
                not (scratch / "state.sqlite").is_symlink()
                and (scratch / "state.sqlite").stat().st_nlink == 1,
                "scratch database is shared",
            )
            require(
                args.config is not None and args.overrides is not None,
                "exact external inputs required",
            )
            files = external_inputs(source, args.config, args.overrides)
            verify_files(scratch, marker)
            with open_database(scratch, read_only=True) as inspected:
                require(inspected.schema_version == 28, "scratch phase cannot authorize migration")
            with open_database(scratch) as db:
                require(
                    protected(
                        db.connection, marker["admission_highwater"], marker["policy_highwater"]
                    )
                    == marker["protected"],
                    "protected state changed",
                )
                bundle = capture(args.config, args.overrides, scratch, versions())
                clock = SystemClock()
                if args.phase == "accept":
                    require(
                        not (scratch / "extension-input-acceptance.json").exists(),
                        "scratch inputs already accepted",
                    )
                    report["accepted_inputs"] = sorted(accept(db, bundle, clock))
                    write_new(
                        scratch / "extension-input-acceptance.json",
                        dict(
                            bundle_digest=bundle.digest,
                            external_files=files,
                            marker_sha256=args.marker_sha256,
                        ),
                    )
                else:
                    accepted = json.loads(
                        (scratch / "extension-input-acceptance.json").read_bytes()
                    )
                    require(
                        accepted
                        == dict(
                            bundle_digest=bundle.digest,
                            external_files=files,
                            marker_sha256=args.marker_sha256,
                        ),
                        "accepted scratch bundle differs",
                    )
                    require(
                        db.connection.execute(
                            "SELECT value FROM meta WHERE key='input_bundle_hash'"
                        ).fetchone()[0]
                        == bundle.digest,
                        "runtime input authority changed",
                    )

                    def held() -> None:
                        require(
                            sha(scratch / "operator-hold")
                            == marker["retained_files"]["operator-hold"]["sha256"]
                            and not (scratch / "RESTORE_PENDING").exists(),
                            "scratch hold or restore interlock changed",
                        )

                    report["turn"] = drain(
                        db,
                        bundle,
                        clock,
                        max_seconds=args.max_seconds,
                        max_units=args.max_units,
                        selection_window=selection_window,
                        verify_turn=held,
                    )
                require(
                    protected(
                        db.connection, marker["admission_highwater"], marker["policy_highwater"]
                    )
                    == marker["protected"],
                    "rehearsal changed paid requests, controls or original admissions",
                )
                require(
                    db.connection.execute("PRAGMA foreign_key_check").fetchone() is None,
                    "scratch foreign-key integrity failed",
                )
                report["input_bundle_hash"] = bundle.digest
                report["judge_continuity"] = judge_continuity(db.connection, marker["named_judges"])
                require(
                    not report["judge_continuity"]["requires_review"],
                    "named judge identity changed; explicit evidence review required",
                )
            require(
                external_inputs(source, args.config, args.overrides) == files,
                "external inputs changed during phase",
            )
        verify_files(scratch, marker)
        require(
            sha(checkpoint / "state.sqlite") == marker["origin_database_sha256"],
            "origin database changed",
        )
        util.verify_source(source, receipt, SOURCE_RECEIPT)
        bound()
        report["passed"] = True
    except BaseException as error:
        report.update(
            error_type=type(error).__name__,
            error=str(error),
            remaining_work="requires_inspection_before_resume",
        )
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        write_new(output, report)


if __name__ == "__main__":
    main()
