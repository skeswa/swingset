"""Resume real project/link workers on a marker-bound SQLite scratch copy only."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from time import monotonic
from typing import Any

from research.accept_h11 import digest, verify_files
from swingset.clock import Clock, SystemClock
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.schedule.cycle import versions
from swingset.schedule.derive import derive_one
from swingset.state import db as db_module
from swingset.state import derivations
from swingset.state.attempts import eligible, recover_interrupted
from swingset.state.controls import recover_admissions
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit
from swingset.state.write_deadline import WRITE_SECONDS

MARKER = "offline-derivation-scratch.json"
COHORTS = tuple(
    ("project", kind)
    for kind in (
        "calendar",
        "dancer",
        "source_index",
        "inventory",
        "map",
        "event",
        "source_event",
        "history",
    )
) + (("link", "event"),)


class ReplayLimit(Exception):
    """The remaining invocation time belongs to worker rollback and receipts."""


@contextmanager
def selection_window(conn: sqlite3.Connection, deadline: float) -> Iterator[None]:
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


def _scratch_path(path: Path) -> Path:
    path = path.resolve()
    if (
        path == Path("/var/lib/swingset")
        or path.is_relative_to("/var/lib/swingset")
        or "checkpoints" in path.parts
        or (path / "checkpoint.json").exists()
    ):
        raise ValueError("refusing production or checkpoint as scratch")
    return path


def verify_runtime(source: Path, receipt_sha256: str) -> None:
    receipt = source / "h16-source.json"
    if digest(receipt) != receipt_sha256:
        raise ValueError("frozen source receipt changed")
    manifest = json.loads(receipt.read_bytes())
    if (
        manifest.get("format") != "h16-reviewed-source-v1"
        or manifest.get("schema") != db_module.SCHEMA_VERSION
    ):
        raise ValueError("expected frozen H16 runtime and exact schema")
    verify_files(source, manifest["files"])
    actual = {
        p.relative_to(source).as_posix()
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p != receipt
    }
    if (
        actual != set(manifest["files"])
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
    ):
        raise ValueError("runtime is not the exact frozen source")


def prepare_scratch(
    checkpoint: Path, scratch: Path, source: Path, *, receipt_sha256: str
) -> dict[str, Any]:
    """Copy verified SQLite; deliberately retain no writable links to source artifacts."""
    scratch = _scratch_path(scratch)
    checkpoint = checkpoint.resolve(strict=True)
    verify_runtime(source, receipt_sha256)
    if scratch.exists():
        raise ValueError("scratch destination must be new")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    original = checkpoint / "state.sqlite"
    expected = manifest["files"]["state.sqlite"]
    if (
        manifest["schema_version"] != db_module.SCHEMA_VERSION
        or original.stat().st_size != expected["size"]
        or digest(original) != expected["sha256"]
    ):
        raise ValueError("checkpoint SQLite identity or schema differs")
    with closing(sqlite3.connect(original.as_uri() + "?mode=ro&immutable=1", uri=True)) as saved:
        if saved.execute("PRAGMA user_version").fetchone()[0] != db_module.SCHEMA_VERSION:
            raise ValueError("checkpoint database schema differs")
        scratch.mkdir(parents=True)
        with closing(sqlite3.connect(scratch / "state.sqlite")) as copied:
            saved.backup(copied)
    marker = {
        "format": "offline-derivation-scratch-v1",
        "scratch": str(scratch),
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": digest(checkpoint / "checkpoint.json"),
        "origin_database_sha256": expected["sha256"],
        "copied_database_sha256": digest(scratch / "state.sqlite"),
        "source": str(source.resolve()),
        "source_receipt_sha256": receipt_sha256,
        "schema": db_module.SCHEMA_VERSION,
        "purpose": "project_link_only",
        "raw_artifacts_copied": False,
    }
    durable_write(scratch / MARKER, canonical(marker))
    return marker


def validate_scratch(scratch: Path, source: Path, receipt_sha256: str) -> dict[str, Any]:
    scratch = _scratch_path(scratch)
    marker = json.loads((scratch / MARKER).read_bytes())
    if (
        marker.get("format") != "offline-derivation-scratch-v1"
        or marker.get("scratch") != str(scratch)
        or marker.get("source") != str(source.resolve())
        or marker.get("source_receipt_sha256") != receipt_sha256
        or marker.get("purpose") != "project_link_only"
        or marker.get("schema") != db_module.SCHEMA_VERSION
    ):
        raise ValueError("scratch marker does not bind this invocation")
    verify_runtime(source, receipt_sha256)
    original = Path(marker["checkpoint"]) / "state.sqlite"
    if (
        os.path.samefile(original, scratch / "state.sqlite")
        or (scratch / "state.sqlite").is_symlink()
    ):
        raise ValueError("scratch SQLite must be an independent copy")
    if digest(original.parent / "checkpoint.json") != marker["checkpoint_manifest_sha256"]:
        raise ValueError("origin checkpoint receipt changed")
    if any(
        (scratch / name).is_symlink()
        for name in ("inputs", "blobs", "extracts", "candidates", "baseline")
    ):
        raise ValueError("scratch may not contain linked evidence or publication roots")
    with db_module.open_database(scratch, lock=False, read_only=True) as db:
        if db.schema_version != db_module.SCHEMA_VERSION:
            raise ValueError("scratch migration is not authorized")
    return dict(marker)


def run(
    scratch: Path,
    source: Path,
    config: Path,
    overrides: Path,
    output: Path,
    *,
    receipt_sha256: str,
    max_seconds: float = 600,
    max_units: int | None = None,
    progress_every: int = 100,
    clock: Clock | None = None,
) -> dict[str, Any]:
    if (
        not 60 <= max_seconds <= 3600
        or max_units is not None
        and max_units < 1
        or not 1 <= progress_every <= 1000
    ):
        raise ValueError("invalid replay bounds")
    started = monotonic()
    scratch = _scratch_path(scratch)
    marker = validate_scratch(scratch, source, receipt_sha256)
    if output.is_symlink():
        raise ValueError("progress output may not be a symlink")
    output = output.resolve()
    if (
        output.is_relative_to("/var/lib/swingset")
        or output == scratch
        or output.is_relative_to(scratch)
        or output.is_relative_to(source.resolve())
        or output.is_relative_to(Path(marker["checkpoint"]))
    ):
        raise ValueError("progress must remain outside scratch, source, and checkpoint")
    clock = clock or SystemClock()
    counts: Counter[str] = Counter()
    receipt: dict[str, Any] = {
        "format": "offline-derivation-replay-v1",
        "scratch": str(scratch),
        "source_receipt_sha256": receipt_sha256,
        "marker_sha256": digest(scratch / MARKER),
        "max_seconds": max_seconds,
        "sweeps": 0,
        "attempted": 0,
        "completed": 0,
        "selection_seconds": 0.0,
        "worker_seconds": 0.0,
        "network_requests": 0,
        "parse_executed": False,
        "published": False,
        "fairness_claimed": False,
        "status": "starting",
    }

    def save() -> None:
        receipt.update(elapsed_seconds=monotonic() - started, outcomes=dict(counts))
        durable_write(output, canonical(receipt))

    output.parent.mkdir(parents=True, exist_ok=True)
    # Each invocation owns a new receipt; resume work from immutable DB proof.
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(descriptor)
    save()
    deadline = started + max_seconds
    with db_module.open_database(scratch) as db:
        if (
            db.connection.execute(
                "SELECT 1 FROM execution_admissions WHERE state!='settled' AND action_kind NOT IN ('project','link')"
            ).fetchone()
            or db.connection.execute(
                "SELECT 1 FROM work_attempts WHERE outcome='running' AND stage NOT IN ('project','link')"
            ).fetchone()
        ):
            raise ValueError(
                "scratch has a non-derivation action requiring explicit reconciliation"
            )
        with db.transaction() as conn:
            receipt["recovered_admissions"] = recover_admissions(conn, now=clock.now())
            receipt["interrupted_attempts"] = recover_interrupted(db, now=clock.now())
        bundle = capture(config, overrides, scratch, versions())
        accept(db, bundle, clock)
        run_id = db.start_run(clock.now())
        receipt.update(run_id=run_id, input_bundle_hash=bundle.digest, status="running")
        attempted: set[tuple[WorkUnit, str]] = set()
        try:
            while monotonic() < deadline - WRITE_SECONDS - 5:
                receipt["sweeps"] += 1
                progress = 0
                for stage, kind in COHORTS:
                    selected_at = monotonic()
                    with selection_window(db.connection, deadline):
                        units = [
                            unit
                            for unit in derivations.known_units(db.connection, stage)
                            if unit.unit_kind == kind
                        ]
                    receipt["selection_seconds"] += monotonic() - selected_at
                    for unit in units:
                        if (
                            monotonic() >= deadline - WRITE_SECONDS - 5
                            or max_units is not None
                            and receipt["attempted"] >= max_units
                        ):
                            receipt["status"] = "bounded_stop"
                            return receipt
                        selected_at = monotonic()
                        with selection_window(db.connection, deadline):
                            if derivations.current(db.connection, unit) or not derivations.ready(
                                db.connection, unit
                            ):
                                receipt["selection_seconds"] += monotonic() - selected_at
                                continue
                            selected = derivations.desired(db.connection, unit)
                            key = unit, selected.fingerprint
                            allowed = key not in attempted and eligible(
                                db.connection, unit, selected.fingerprint, clock.now()
                            )
                        receipt["selection_seconds"] += monotonic() - selected_at
                        if not allowed:
                            continue
                        attempted.add(key)
                        receipt["attempted"] += 1
                        worker_at = monotonic()
                        outcome = derive_one(db, Archive(scratch), unit, bundle, clock, run_id)
                        receipt["worker_seconds"] += monotonic() - worker_at
                        counts[outcome.reason or "output_committed"] += 1
                        if outcome.reason is None:
                            receipt["completed"] += 1
                            progress += 1
                        if receipt["attempted"] % progress_every == 0:
                            save()
                if not progress:
                    receipt["status"] = "no_runnable_progress"
                    break
            else:
                receipt["status"] = "bounded_stop"
            with selection_window(db.connection, deadline):
                receipt["unfinished_by_scope"] = dict(
                    Counter(
                        unit.stage + "/" + unit.unit_kind
                        for stage in ("project", "link")
                        for unit in derivations.pending_units(db.connection, stage)
                    )
                )
            if not receipt["unfinished_by_scope"]:
                receipt["status"] = "current"
            return receipt
        except ReplayLimit:
            receipt["status"] = "bounded_stop"
            return receipt
        except BaseException as error:
            receipt["status"] = "interrupted"
            receipt["error_type"] = type(error).__name__
            raise
        finally:
            receipt["generation_count"] = db.connection.execute(
                "SELECT count(*) FROM derivation_generations"
            ).fetchone()[0]
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE runs SET finished_at=? WHERE run_id=?",
                    (clock.now().isoformat(), run_id),
                )
            save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--prepare-from", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=float, default=600)
    parser.add_argument("--max-units", type=int)
    args = parser.parse_args()
    if args.prepare_from:
        prepare_scratch(
            args.prepare_from, args.scratch, args.source, receipt_sha256=args.source_receipt_sha256
        )
    elif args.config and args.overrides and args.output:
        run(
            args.scratch,
            args.source,
            args.config,
            args.overrides,
            args.output,
            receipt_sha256=args.source_receipt_sha256,
            max_seconds=args.max_seconds,
            max_units=args.max_units,
        )
    else:
        parser.error("replay requires --config, --overrides, and --output")


if __name__ == "__main__":
    main()
