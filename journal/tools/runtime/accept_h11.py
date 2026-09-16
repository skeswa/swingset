"""Guarded H11 migration and shadow-inventory acceptance; never deploy or fetch.

Without --execute only read-only preflight runs. The gate receipt records the
operator's already-completed remote verification; this driver cannot establish
remote truth offline. See h11-deployment-preparation.md for its exact schema.
"""

import argparse
import fcntl
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from swingset.admission.support import selection_digest
from swingset.cli import doctor
from swingset.publish.service import pending_candidates
from swingset.state import db as db_module
from swingset.state.db import open_database
from swingset.state.identity_journal import token
from swingset.state.requirement_report import human_report
from swingset.state.requirements import capture_cohort, scan

UNITS = tuple(
    f"swingset-{name}.{kind}"
    for name in ("cycle", "backup", "summary")
    for kind in ("service", "timer")
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def table_digest(conn: sqlite3.Connection, table: str) -> dict:
    # Internal constant table names only. Sorting by every column also handles
    # composite keys and NULL values without relying on physical row order.
    columns = len(conn.execute(f"PRAGMA table_info({table})").fetchall())
    if not columns:
        raise ValueError(f"required table missing: {table}")
    ordering = ",".join(str(index + 1) for index in range(columns))
    return query_digest(conn, f"SELECT * FROM {table} ORDER BY {ordering}")


def query_digest(conn: sqlite3.Connection, query: str) -> dict:
    count, result = 0, hashlib.sha256()
    for row in conn.execute(query):
        result.update(json.dumps(tuple(row), separators=(",", ":")).encode() + b"\n")
        count += 1
    return {"count": count, "sha256": result.hexdigest()}


def controls(conn: sqlite3.Connection) -> dict:
    result = {
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "tables": {
            table: table_digest(conn, table)
            for table in (
                "operator_pauses",
                "hosts",
                "host_budget",
                "pending_work",
                "accepted_inputs",
                "watches",
                "snapshots",
                "identity_decisions",
                "requirement_cohort_members",
                "admission_policies",
                "admission_reviews",
                "source_units",
                "admission_decisions",
                "identity_journal_acceptances",
                "identity_source_refs",
                "identity_reference_bindings",
                "identity_reference_migrations",
                "identity_link_resolutions",
            )
        },
        "cohorts": [
            dict(row)
            for row in conn.execute("SELECT * FROM requirement_cohorts ORDER BY cohort_id")
        ],
    }

    result["tables"].update(
        {
            "source_generation_selection": query_digest(
                conn,
                "SELECT generation_id,unit_key,page_kind,contract_version,input_fingerprint,"
                "previous_generation_id,work_token,created_at,run_id,state,removal_authority "
                "FROM source_generations ORDER BY generation_id",
            ),
            "accepted_input_and_journal_meta": query_digest(
                conn,
                "SELECT key,value FROM meta WHERE key IN ('input_bundle_hash','correction_detected_at') "
                "OR key LIKE 'identity_%' ORDER BY key",
            ),
            "admission_selection_digest": {"sha256": selection_digest(conn)},
            "identity_journal_token": asdict(token(conn)),
        }
    )
    return result


def validate_output_paths(output: Path, state: Path, source: Path, gate_path: Path) -> None:
    """Receipts must never enter retained evidence, publication, or source trees."""
    protected = [
        source.resolve(),
        Path(__file__).resolve().parents[3],
        (state / "checkpoints").resolve(),
        (state / "candidates").resolve(),
        (state / "baseline").resolve(),
    ]
    if gate_path.is_file():
        gate = json.loads(gate_path.read_bytes())
        if gate.get("checkpoint"):
            protected.append(Path(gate["checkpoint"]).resolve())
    assembly_path = source / "h11-source.json"
    if assembly_path.is_file():
        assembly = json.loads(assembly_path.read_bytes())
        protected.extend(
            Path(assembly[key]).resolve() for key in ("base", "reviewed") if assembly.get(key)
        )
    for path in (output, output.with_suffix(".doctor.json"), output.with_suffix(".doctor.txt")):
        resolved = path.resolve()
        if path.exists() or path.is_symlink():
            raise ValueError("all receipt output paths must be new")
        if (
            any(resolved.is_relative_to(root) for root in protected)
            or any(part in {"checkpoints", "candidates", "baseline"} for part in resolved.parts)
            or any(
                (parent / "checkpoint.json").is_file() or (parent / "BUILT").is_file()
                for parent in resolved.parents
            )
        ):
            raise ValueError(
                "receipt output cannot enter a checkpoint, candidate, baseline, or source tree"
            )


def system_hold(state: Path) -> dict:
    if not (state / "operator-hold").is_file():
        raise ValueError("persistent operator-hold marker is required")
    result = {}
    for unit in UNITS:
        command = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,ConditionResult",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        fields = dict(line.split("=", 1) for line in command.stdout.splitlines() if "=" in line)
        if fields.get("LoadState") != "loaded" or fields.get("ActiveState") not in {
            "inactive",
            "failed",
        }:
            raise ValueError(f"unit must be installed and stopped: {unit}: {fields}")
        if unit.endswith(".service"):
            unit_text = subprocess.run(
                ["systemctl", "cat", unit, "--no-pager"], capture_output=True, text=True, check=True
            ).stdout
            conditions = [
                line.strip()
                for line in unit_text.splitlines()
                if line.strip().startswith("Condition")
            ]
            expected = f"ConditionPathExists=!{state / 'operator-hold'}"
            if (
                not conditions
                or any(line != expected for line in conditions)
                or fields.get("ConditionResult") != "no"
            ):
                raise ValueError(
                    f"service must have the negative persistent hold condition, without reset/override, and ConditionResult=no: {unit}"
                )
            fields["verified_conditions"] = conditions
            fields["unit_text_sha256"] = hashlib.sha256(unit_text.encode()).hexdigest()
        result[unit] = fields
    return result


def verify_files(root: Path, expected: dict) -> None:
    for relative, expected_hash in expected.items():
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(c not in "0123456789abcdef" for c in expected_hash)
        ):
            raise ValueError("source and evidence manifests require SHA256 strings")
        path = root / relative
        if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("manifest path escapes its root")
        if not path.is_file() or digest(path) != expected_hash:
            raise ValueError(f"file differs from reviewed evidence: {path}")


def verify_checkpoint_files(root: Path, expected: dict) -> None:
    """Checkpoint format 1 records physical file size and SHA256 separately."""
    for relative, record in expected.items():
        if (
            not isinstance(record, dict)
            or set(record) != {"size", "sha256"}
            or type(record["size"]) is not int
            or record["size"] < 0
        ):
            raise ValueError("checkpoint file records require integer size and SHA256")
        verify_files(root, {relative: record["sha256"]})
        if (root / relative).stat().st_size != record["size"]:
            raise ValueError(f"checkpoint file size differs: {relative}")


def preflight(state: Path, source: Path, gate_path: Path) -> dict:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    hold = system_hold(state)
    if pending_candidates(state):
        raise ValueError("publication intent is active")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h11-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
    ):
        raise ValueError("gate must record completed V4 and private backup verification")
    if (
        not gate.get("private_backup_commit")
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("verification commit, timestamp, and retained evidence are required")
    verify_files(gate_path.parent, gate["evidence_files"])
    published = json.loads((state / "baseline/PUBLISHED").read_bytes())
    if (
        published["commit"] != gate["v4_commit"]
        or digest(state / "baseline/_meta/manifest.json") != gate["v4_manifest_sha256"]
    ):
        raise ValueError("acknowledged baseline differs from verified V4")
    checkpoint = Path(gate["checkpoint"]).resolve()
    if digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private backup checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest["schema_version"] != 9
        or manifest.get("pending_candidate") is not None
        or manifest["baseline_candidate"] != (state / "baseline").resolve().name
    ):
        raise ValueError(
            "backup must contain this V4 baseline at schema 9 without an active intent"
        )
    actual = {
        path.relative_to(checkpoint).as_posix()
        for path in checkpoint.rglob("*")
        if path.is_file() and path != checkpoint / "checkpoint.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("checkpoint closure differs, including any WAL/SHM sidecars")
    verify_checkpoint_files(checkpoint, manifest["files"])
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        if (
            saved.execute("PRAGMA user_version").fetchone()[0] != 9
            or saved.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or saved.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise ValueError("verified checkpoint database failed local integrity checks")
    assembly = json.loads((source / "h11-source.json").read_bytes())
    if assembly.get("format") != "h11-selective-source-v1" or not assembly.get(
        "build_runtime_preserved"
    ):
        raise ValueError("selective H11 source receipt is required")
    verify_files(source, assembly["files"])
    actual_source = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.name != "h11-source.json"
    }
    if actual_source != set(assembly["files"]):
        raise ValueError("assembled source has unrecorded or missing files")
    if Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve():
        raise ValueError("run with PYTHONPATH pointing to the assembled H11 source")
    with open_database(state, lock=False, read_only=True) as db:
        before = controls(db.connection)
    if before["schema"] != 9:
        raise ValueError("migration acceptance requires an unmigrated schema 9 state")
    return {
        "gate": gate,
        "gate_sha256": digest(gate_path),
        "assembly_sha256": digest(source / "h11-source.json"),
        "before": before,
        "hold": hold,
    }


def human_matches(report: dict, text: str) -> bool:
    """Verify every JSON inventory field survives the actual human renderer."""
    recovered = {}
    rows = []
    for line in text.splitlines():
        if line.startswith("{"):
            rows.append(json.loads(line))
        elif ": " in line:
            key, value = line.split(": ", 1)
            if key in report and key != "requirements":
                recovered[key] = json.loads(value)
    recovered["requirements"] = rows
    return recovered == report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.max_pages < 1:
        raise ValueError("output must be new and page bound positive")
    validate_output_paths(args.output, args.state, args.source, args.gate)
    with ExitStack() as guards:
        if args.execute:
            if (
                "checkpoints" in args.state.resolve().parts
                or (args.state / "checkpoint.json").exists()
            ):
                raise ValueError("never migrate an immutable checkpoint")
            lock = guards.enter_context((args.state / "state.lock").open("a+b"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Hold the ordinary process lock across preflight, migration, scan and
        # restart reads. The migrating database therefore needs no second lock.
        receipt = preflight(args.state, args.source, args.gate)
        receipt.update({"executed": False, "network_requests": 0, "published": False})
        if not args.execute:
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            return
        args.output.parent.mkdir(parents=True, exist_ok=True)

        def save() -> None:
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

        save()
        started = monotonic()
        with open_database(args.state, lock=False) as db:
            # The process lock now excludes other pipeline writers. Preconditions
            # are rechecked before inventory writes, after the atomic migration.
            receipt["executed"] = True
            receipt["migrated"] = controls(db.connection)
            save()
            if receipt["migrated"]["schema"] != 10:
                raise ValueError("expected schema 10 after migration")
            if receipt["before"]["tables"] != receipt["migrated"]["tables"]:
                raise ValueError("protected controls or input rows changed during migration")
            prior_cohorts = receipt["before"]["cohorts"]
            migrated_cohorts = receipt["migrated"]["cohorts"]
            if prior_cohorts != [
                {key: value for key, value in row.items() if key != "first_open_transition_cutoff"}
                for row in migrated_cohorts
            ] or any(row["first_open_transition_cutoff"] is not None for row in migrated_cohorts):
                raise ValueError("migration changed legacy cohorts or fabricated a cutoff")
            system_hold(args.state)
            if pending_candidates(args.state):
                raise ValueError("publication intent appeared during migration")
            now = datetime.now(UTC)
            run = db.start_run(now)
            with db.transaction():
                db.connection.execute("UPDATE requirement_scan SET cursor='',started_at=NULL")
            checked = 0
            pages = []
            complete = False
            for _ in range(args.max_pages):
                began = monotonic()
                count = scan(db, datetime.now(UTC), run, limit=100)
                checked += count
                pages.append(monotonic() - began)
                if count < 100:
                    complete = True
                    break
            receipt["scan"] = {
                "complete": complete,
                "scopes": checked,
                "pages": len(pages),
                "page_limit": 100,
                "max_page_seconds": max(pages),
            }
            if not complete:
                save()
                raise RuntimeError(
                    "bounded scan incomplete; cursor retained, acceptance not complete"
                )
            receipt["after"] = controls(db.connection)
            if receipt["after"]["tables"] != receipt["before"]["tables"]:
                raise ValueError("shadow scan changed protected operational state")
            with db.transaction():
                cohort = "h11-acceptance-" + run
                capture_cohort(db.connection, cohort, datetime.now(UTC))
                db.connection.execute(
                    "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                    (
                        datetime.now(UTC).isoformat(),
                        json.dumps({"h11_shadow_acceptance": True}),
                        run,
                    ),
                )
            receipt["cohort"] = cohort
            receipt["foreign_key_violations"] = len(
                db.connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        # Use the real doctor entry point after closing the migrating writer, then
        # a new interpreter to prove persisted state is independently readable.
        doctor_args = argparse.Namespace(state=args.state, config=args.source / "config")
        report = doctor(doctor_args)
        human = human_report(report["requirements"])
        receipt["doctor_human_json_agree"] = human_matches(report["requirements"], human)
        with args.output.with_suffix(".doctor.json").open("w") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        args.output.with_suffix(".doctor.txt").write_text(human + "\n")
        code = "import argparse,json,sys; from pathlib import Path; from swingset.cli import doctor; r=doctor(argparse.Namespace(state=Path(sys.argv[1]),config=Path(sys.argv[2]))); print(json.dumps({'schema_version':r['schema_version'],'pending_work':r['pending_work'],'requirements':{k:r['requirements'][k] for k in ('cohorts','states')}}))"
        restarted = json.loads(
            subprocess.run(
                [sys.executable, "-c", code, str(args.state), str(args.source / "config")],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        report_cohorts = report["requirements"]["cohorts"]
        fresh = next(row for row in report_cohorts if row["cohort_id"] == cohort)
        receipt["restart_read"] = {
            "schema": restarted["schema_version"],
            "cohorts_persisted": restarted["requirements"]["cohorts"] == report_cohorts,
            "states_persisted": restarted["requirements"]["states"]
            == report["requirements"]["states"],
            "pending_work_persisted": restarted["pending_work"] == report["pending_work"],
        }
        receipt["fresh_cohort_outside"] = fresh["outside_cohort"]
        if "gate" in receipt:
            receipt["baseline_unchanged"] = (
                json.loads((args.state / "baseline/PUBLISHED").read_bytes())["commit"]
                == receipt["gate"]["v4_commit"]
                and digest(args.state / "baseline/_meta/manifest.json")
                == receipt["gate"]["v4_manifest_sha256"]
            )
        else:
            receipt["baseline_unchanged"] = True
        receipt["final_hold"] = system_hold(args.state)
        receipt["passed"] = all(
            (
                receipt["baseline_unchanged"],
                receipt["doctor_human_json_agree"],
                fresh["outside_cohort"] == 0,
                restarted["schema_version"] == 10,
                receipt["restart_read"]["cohorts_persisted"],
                receipt["restart_read"]["states_persisted"],
                receipt["restart_read"]["pending_work_persisted"],
                not receipt["foreign_key_violations"],
                not pending_candidates(args.state),
                report["requirements"]["execution_enabled"] is False,
            )
        )
        receipt["elapsed_seconds"] = monotonic() - started
        save()
        if not receipt["passed"]:
            raise RuntimeError("H11 acceptance checks failed; hold remains in place")


if __name__ == "__main__":
    main()
