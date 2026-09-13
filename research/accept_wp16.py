"""Reviewed WP16 schema14→15 migration only; preflight is read-only by default."""

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from research import accept_h11 as prior
from swingset import cli
from swingset.publish.service import pending_candidates
from swingset.state import db as db_module
from swingset.state.publication_fence import install_publication_fences
from swingset.state.requirement_report import human_report

NEW_TABLES = {"history_origin_intents", "history_origin_requests", "history_origin_operator_refs"}
SOURCE_RECEIPT = "wp16-source.json"


def integrity(conn: sqlite3.Connection) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA quick_check")] != [("ok",)] or conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchone():
        raise ValueError("database integrity failed")


def schema_objects(conn: sqlite3.Connection) -> dict[str, list[str | None]]:
    return {
        row[1]: [row[0], row[1], row[2], row[3]]
        for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    }


def protected_state(conn: sqlite3.Connection) -> dict[str, Any]:
    columns, tables = {}, {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        if not name.replace("_", "").isalnum():
            raise ValueError("unexpected schema identifier")
        columns[name] = [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')]
        order = ",".join(str(i + 1) for i in range(len(columns[name])))
        where = " WHERE key!='schema_version'" if name == "meta" else ""
        tables[name] = prior.query_digest(conn, f'SELECT * FROM "{name}"{where} ORDER BY {order}')
    return {
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "schema_meta": conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[
            0
        ],
        "columns": columns,
        "tables": tables,
        "schema_objects": schema_objects(conn),
        "sequences": prior.query_digest(conn, "SELECT name,seq FROM sqlite_sequence ORDER BY name"),
        "backup_neutral_meta": prior.query_digest(
            conn,
            "SELECT key,value FROM meta WHERE key!='schema_version' AND key NOT LIKE 'last_backup%' ORDER BY key",
        ),
    }


def expected_additions(source: Path) -> dict[str, list[str | None]]:
    # SQLite can declare FKs before referenced tables exist. This tiny specimen
    # derives exact new DDL and automatic indexes without copying production.
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
        conn.executescript(
            (source / "src/swingset/state/migrations/0015_origin_backfill.sql").read_text()
        )
        install_publication_fences(conn)
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if names != NEW_TABLES | {"meta"}:
            raise ValueError("migration15 contains unexpected table declarations")
        return {name: row for name, row in schema_objects(conn).items() if row[2] in NEW_TABLES}


def verify_migration(
    conn: sqlite3.Connection, before: dict[str, Any], source: Path
) -> dict[str, Any]:
    after = protected_state(conn)
    if after["schema"] != 15 or after["schema_meta"] != "15":
        raise ValueError("only schema14→15 migration is authorized")
    if set(after["tables"]) != set(before["tables"]) | NEW_TABLES:
        raise ValueError("migration changed table closure")
    if any(
        after["columns"][name] != columns or after["tables"][name] != before["tables"][name]
        for name, columns in before["columns"].items()
    ):
        raise ValueError("migration changed original columns or retained rows")
    if (
        after["sequences"] != before["sequences"]
        or after["backup_neutral_meta"] != before["backup_neutral_meta"]
    ):
        raise ValueError("migration changed retained authority")
    if any(after["tables"][name]["count"] for name in NEW_TABLES):
        raise ValueError("migration fabricated origin intent, cadence or operator evidence")
    if after["schema_objects"] != before["schema_objects"] | expected_additions(source):
        raise ValueError("migration changed prior schema objects or added unexpected objects")
    integrity(conn)
    return after


def verify_runtime(source: Path, gate: dict[str, Any], expected_receipt: str) -> None:
    receipt = source / SOURCE_RECEIPT
    if (
        gate.get("source_receipt_sha256") != expected_receipt
        or prior.digest(receipt) != expected_receipt
    ):
        raise ValueError("frozen WP16 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "wp16-reviewed-source-v1"
        or assembly.get("schema") != 15
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("reviewed schema15 source without activation required")
    prior.verify_files(source, assembly["files"])
    actual = {
        p.relative_to(source).as_posix()
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p != receipt
    }
    if actual != set(assembly["files"]):
        raise ValueError("frozen source closure differs")
    if (
        db_module.SCHEMA_VERSION != 15
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
        or Path(prior.__file__).resolve() != (source / "research/accept_h11.py").resolve()
    ):
        raise ValueError("runtime and helper must come from exact frozen schema15 source")


def evidence(gate_path: Path, gate: dict[str, Any], key: str) -> dict[str, Any]:
    name = gate[key]
    if name not in gate["evidence_files"]:
        raise ValueError("every prerequisite receipt must be separately hashed")
    return cast(dict[str, Any], json.loads((gate_path.parent / name).read_bytes()))


def baseline(state: Path, gate_path: Path, gate: dict[str, Any]) -> dict[str, Any]:
    target = (state / "baseline").resolve(strict=True)
    published = json.loads((target / "PUBLISHED").read_bytes())
    manifest_path = target / "_meta/manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    remote = evidence(gate_path, gate, "public_verification_receipt")
    expected = {"commit": gate["public_commit"], "manifest_sha256": gate["public_manifest_sha256"]}
    if (
        published.get("commit") != expected["commit"]
        or prior.digest(manifest_path) != expected["manifest_sha256"]
        or remote.get("verified") is not True
        or not remote.get("verified_at")
        or any(remote.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("current public baseline differs from verified post-H16 release")
    policy = manifest.get("release_policy", {})
    closure = policy.get("closure") or {}
    if (
        policy.get("mode") != "closure"
        or not published.get("verified_at")
        or not closure.get("digest")
        or not closure.get("cutoff")
        or published.get("closure_digest") != closure["digest"]
        or published.get("evidence_cutoff") != closure["cutoff"]
        or published.get("candidate_id") != target.name
    ):
        raise ValueError("a completed H16 closure publication receipt is required")
    prior.verify_files(target, manifest["files"])
    return expected | {
        "candidate_id": target.name,
        "publication_receipt_sha256": prior.digest(target / "PUBLISHED"),
    }


def verify_backup(
    state: Path, gate_path: Path, gate: dict[str, Any], public: dict[str, Any]
) -> dict[str, Any]:
    checkpoint = Path(gate["checkpoint"]).resolve(strict=True)
    manifest_path = checkpoint / "checkpoint.json"
    if prior.digest(manifest_path) != gate["checkpoint_manifest_sha256"]:
        raise ValueError("checkpoint manifest identity differs")
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest.get("schema_version") != 14
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != public["candidate_id"]
    ):
        raise ValueError("checkpoint must retain settled schema14 and current public baseline")
    actual = {
        p.relative_to(checkpoint).as_posix()
        for p in checkpoint.rglob("*")
        if p.is_file() and p != manifest_path
    }
    if actual != set(manifest["files"]):
        raise ValueError("checkpoint closure differs, including nested manifests and sidecars")
    prior.verify_checkpoint_files(checkpoint, manifest["files"])
    retained_baseline = checkpoint / "candidates" / public["candidate_id"]
    if (
        prior.digest(retained_baseline / "_meta/manifest.json") != public["manifest_sha256"]
        or prior.digest(retained_baseline / "PUBLISHED") != public["publication_receipt_sha256"]
    ):
        raise ValueError("checkpoint baseline files differ from actual verified public release")
    retained_publication = json.loads((retained_baseline / "PUBLISHED").read_bytes())
    if retained_publication.get("commit") != public["commit"]:
        raise ValueError("checkpoint publication receipt belongs to another commit")
    backup = evidence(gate_path, gate, "private_backup_receipt")
    remote = evidence(gate_path, gate, "private_verification_receipt")
    if (
        backup.get("commit") != gate["private_backup_commit"]
        or backup.get("manifest_hash") != gate["checkpoint_manifest_sha256"]
        or Path(backup.get("checkpoint", "")).resolve() != checkpoint
        or backup.get("files") != len(manifest["files"])
        or backup.get("schema_version") != 14
        or backup.get("baseline_commit") != public["commit"]
        or backup.get("source_receipt_sha256") != gate["predecessor_source_receipt_sha256"]
        or not backup.get("finished_at")
    ):
        raise ValueError("backup receipt does not bind the actual schema14 predecessor")
    if (
        remote.get("verified") is not True
        or remote.get("private") is not True
        or remote.get("commit") != backup["commit"]
        or remote.get("manifest_sha256") != backup["manifest_hash"]
        or not remote.get("verified_at")
    ):
        raise ValueError("private backup lacks matching completed remote verification")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as conn:
        integrity(conn)
        saved = protected_state(conn)
    if saved["schema"] != 14 or saved["schema_meta"] != "14":
        raise ValueError("checkpoint database is not schema14")
    return saved


def prerequisites(
    state: Path, source: Path, gate_path: Path, expected_receipt: str
) -> dict[str, Any]:
    if (
        "checkpoints" in state.resolve().parts
        or (state / "checkpoint.json").exists()
        or (state / "RESTORE_PENDING").exists()
    ):
        raise ValueError("migration target must be live settled state, never a checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "wp16-operational-gate-v1"
        or gate.get("h16_release_verified") is not True
        or not gate.get("evidence_files")
        or not gate.get("predecessor_source_receipt_sha256")
        or not gate.get("verified_at")
    ):
        raise ValueError("actual H16 release and verified predecessor gate required")
    if gate.get("driver_sha256") != prior.digest(Path(__file__)):
        raise ValueError("operations driver differs from reviewed gate")
    prior.verify_files(gate_path.parent, gate["evidence_files"])
    verify_runtime(source, gate, expected_receipt)
    hold = prior.system_hold(state)
    if pending_candidates(state):
        raise ValueError("publication intent is active")
    public = baseline(state, gate_path, gate)
    saved = verify_backup(state, gate_path, gate, public)
    return {
        "gate": gate,
        "gate_sha256": prior.digest(gate_path),
        "baseline": public,
        "hold": hold,
        "checkpoint": saved,
    }


def check_preimage(before: dict[str, Any], saved: dict[str, Any]) -> None:
    if (
        before["schema"] != 14
        or before["schema_meta"] != "14"
        or NEW_TABLES & before["tables"].keys()
    ):
        raise ValueError("preflight requires unmigrated schema14")
    changed = {
        name for name, value in before["tables"].items() if saved["tables"].get(name) != value
    }
    if changed - {"meta"} or any(
        before[key] != saved[key]
        for key in ("columns", "schema_objects", "sequences", "backup_neutral_meta")
    ):
        raise ValueError("live state diverged from latest verified schema14 checkpoint")


def check_idle(conn: sqlite3.Connection) -> None:
    if (
        conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled'").fetchone()
        or conn.execute("SELECT 1 FROM work_attempts WHERE outcome='running'").fetchone()
    ):
        raise ValueError("existing work requires coordinator reconciliation")


def preflight(state: Path, source: Path, gate_path: Path, expected_receipt: str) -> dict[str, Any]:
    receipt = prerequisites(state, source, gate_path, expected_receipt)
    with db_module.open_database(state, lock=False, read_only=True) as db:
        with db.transaction(immediate=False):
            integrity(db.connection)
            before = protected_state(db.connection)
            check_idle(db.connection)
    check_preimage(before, receipt.pop("checkpoint"))
    return receipt | {"before": before}


def resume_preflight(
    state: Path,
    source: Path,
    gate_path: Path,
    expected_receipt: str,
    intent_path: Path,
    intent_sha256: str,
) -> dict[str, Any]:
    if (
        intent_path.is_symlink()
        or any(parent.is_symlink() for parent in intent_path.parents)
        or prior.digest(intent_path) != intent_sha256
    ):
        raise ValueError("resume requires exact immutable pre-migration intent")
    intent = json.loads(intent_path.read_bytes())
    receipt = prerequisites(state, source, gate_path, expected_receipt)
    expected = {
        "format": "wp16-migration-intent-v1",
        "state": str(state.resolve()),
        "source": str(source.resolve()),
        "source_receipt_sha256": expected_receipt,
        "gate_sha256": receipt["gate_sha256"],
        "driver_sha256": prior.digest(Path(__file__)),
        "migration_authorized": True,
        "baseline": receipt["baseline"],
    }
    if any(intent.get(key) != value for key, value in expected.items()):
        raise ValueError("resume intent differs from reviewed source, gate or baseline")
    check_preimage(intent["before"], receipt.pop("checkpoint"))
    with db_module.open_database(state, lock=False, read_only=True) as db:
        with db.transaction(immediate=False):
            check_idle(db.connection)
            verify_migration(db.connection, intent["before"], source)
    return receipt | {
        "before": intent["before"],
        "resume_intent": str(intent_path.resolve()),
        "resume_intent_sha256": intent_sha256,
    }


def validate_output(output: Path, state: Path, source: Path, gate: Path) -> None:
    paths = (
        output,
        output.with_suffix(".sizes.json"),
        output.with_suffix(".intent.json"),
        output.with_suffix(".doctor.json"),
        output.with_suffix(".doctor.txt"),
        output.with_suffix(output.suffix + ".tmp"),
    )
    if len({p.resolve() for p in paths}) != len(paths):
        raise ValueError("receipt paths must be distinct")
    assembly = json.loads((source / SOURCE_RECEIPT).read_bytes())
    for path in paths:
        prior.validate_output_paths(path, state, source, gate)
        if path.resolve() == gate.resolve() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("receipt paths may not alias inputs or use symlink ancestors")
        if path.resolve().is_relative_to(state.resolve()) and not path.resolve().is_relative_to(
            (state / "operations").resolve()
        ):
            raise ValueError("in-state receipts must remain in private operations")
        protected = [(state / name).resolve() for name in ("blobs", "extracts", "inputs")]
        protected += [
            Path(assembly[key]).resolve() for key in ("base", "reviewed") if assembly.get(key)
        ]
        if any(path.resolve().is_relative_to(root) for root in protected):
            raise ValueError("receipt output cannot enter retained evidence or source roots")


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, value: object) -> None:
    data = value if isinstance(value, str) else json.dumps(value, sort_keys=True, indent=2) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def doctor_check(state: Path, source: Path, output: Path) -> dict[str, Any]:
    instant = datetime.now(UTC)
    with patch.object(cli, "SystemClock", lambda: SimpleNamespace(now=lambda: instant)):
        report = cli.doctor(argparse.Namespace(state=state, config=source / "config"))
    human = human_report(report["requirements"])
    if not prior.human_matches(report["requirements"], human):
        raise ValueError("doctor human and JSON requirements differ")
    code = (
        "import argparse,hashlib,json,sys; from pathlib import Path; from datetime import datetime; "
        "from types import SimpleNamespace; from swingset import cli; from swingset.state import db; "
        "assert db.SCHEMA_VERSION==15 and Path(db.__file__).resolve()==Path(sys.argv[4]).resolve(); "
        "cli.SystemClock=lambda:SimpleNamespace(now=lambda:datetime.fromisoformat(sys.argv[3])); "
        "report=cli.doctor(argparse.Namespace(state=Path(sys.argv[1]),config=Path(sys.argv[2]))); "
        "print(json.dumps({'sha256':hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'schema':report['schema_version']}))"
    )
    restarted = json.loads(
        subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(state),
                str(source / "config"),
                instant.isoformat(),
                str(source / "src/swingset/state/db.py"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        ).stdout
    )
    digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if restarted != {"sha256": digest, "schema": 15} or report["schema_version"] != 15:
        raise ValueError("fresh-process doctor differs")
    write_new(output.with_suffix(".doctor.json"), report)
    write_new(output.with_suffix(".doctor.txt"), human + "\n")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "report_sha256": digest,
        "json_bytes": output.with_suffix(".doctor.json").stat().st_size,
        "human_bytes": output.with_suffix(".doctor.txt").stat().st_size,
    }


def run(
    state: Path,
    source: Path,
    gate: Path,
    output: Path,
    *,
    expected_receipt: str,
    execute: bool = False,
    resume: Path | None = None,
    resume_sha256: str | None = None,
) -> dict[str, Any]:
    if (resume is None) != (resume_sha256 is None) or (resume is not None and not execute):
        raise ValueError("resume requires explicit execution and immutable intent hash")
    validate_output(output, state, source, gate)
    output.parent.mkdir(parents=True, exist_ok=True)
    assembly = json.loads((source / SOURCE_RECEIPT).read_bytes())
    write_new(
        output.with_suffix(".sizes.json"),
        {
            "database_bytes": (state / "state.sqlite").stat().st_size,
            "source_files": len(assembly["files"]),
            "source_bytes": sum((source / name).stat().st_size for name in assembly["files"]),
            "doctor_output_policy": "one fixed-time full report and fresh-process digest; private operations output only",
        },
    )
    with ExitStack() as guards:
        if execute:
            lock = guards.enter_context((state / "state.lock").open("r+b"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = (
            resume_preflight(state, source, gate, expected_receipt, resume, resume_sha256)
            if resume is not None and resume_sha256 is not None
            else preflight(state, source, gate, expected_receipt)
        )
        receipt.update(
            executed=False,
            passed=False,
            preflight_passed=True,
            network_requests=0,
            published=False,
            input_acceptance_executed=False,
            service_executed=False,
            acquisition_enabled=False,
            activation_executed=False,
            migration_executed=False,
            read_only_completion=resume is not None,
        )
        write_new(output, receipt)
        if not execute:
            return receipt
        if resume is None:
            intent_path = output.with_suffix(".intent.json")
            intent = {
                "format": "wp16-migration-intent-v1",
                "state": str(state.resolve()),
                "source": str(source.resolve()),
                "source_receipt_sha256": expected_receipt,
                "gate_sha256": receipt["gate_sha256"],
                "driver_sha256": prior.digest(Path(__file__)),
                "migration_authorized": True,
                "before": receipt["before"],
                "baseline": receipt["baseline"],
                "created_at": datetime.now(UTC).isoformat(),
            }
            write_new(intent_path, intent)
            receipt.update(
                migration_intent=str(intent_path.resolve()),
                migration_intent_sha256=prior.digest(intent_path),
            )
        started = monotonic()
        try:
            with db_module.open_database(state, lock=False, read_only=resume is not None) as db:
                receipt["executed"] = True
                receipt["migration_executed"] = resume is None
                with db.transaction(immediate=False):
                    after = verify_migration(db.connection, receipt["before"], source)
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                if verify_migration(db.connection, receipt["before"], source) != after:
                    raise ValueError("read-only reporting changed persisted state")
            if baseline(state, gate, receipt["gate"]) != receipt["baseline"] or pending_candidates(
                state
            ):
                raise ValueError("published baseline or publication intent changed")
            receipt.update(
                after=after,
                final_hold=prior.system_hold(state),
                passed=True,
                new_tables_empty=sorted(NEW_TABLES),
            )
        except BaseException as error:
            receipt["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            receipt["elapsed_seconds"] = monotonic() - started
            temporary = output.with_suffix(output.suffix + ".tmp")
            write_new(temporary, receipt)
            os.replace(temporary, output)
            sync_directory(output.parent)
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "source", "gate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume-intent", type=Path)
    parser.add_argument("--resume-intent-sha256")
    parser.add_argument("--max-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 1800:
        raise ValueError("execution bound must be 1–1800 seconds")

    def timed_out(*_: object) -> None:
        raise TimeoutError("bounded WP16 acceptance exceeded its execution window")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.max_seconds)
    try:
        run(
            args.state,
            args.source,
            args.gate,
            args.output,
            expected_receipt=args.source_receipt_sha256,
            execute=args.execute,
            resume=args.resume_intent,
            resume_sha256=args.resume_intent_sha256,
        )
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
