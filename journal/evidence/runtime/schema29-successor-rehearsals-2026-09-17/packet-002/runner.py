"""Seal reviewed schema28→29 migration, restore and offline input rehearsals.

Building does not operate production or establish runtime acceptance. Execution
uses a separately reviewed packet digest and the exact frozen runtime inventory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

CHECKPOINT = "/var/lib/swingset/checkpoints/extension28-held-20260917-002"
CHECKPOINT_SHA = "4929859092cbd3e15122f3598b5da477a65498976cc744c52cf2b142d1cebea3"
ARCHIVE_COMMIT = "d71060d4f77b6073f797bd7232bf2aa3694ba685"
BASES = {
    "inputs": "ddb2761bc302d53436f0a257c853cb827d42f071234761fe27c28bf94a8ab861",
    "restore": "1e816a9af567344a6917e8fccd01362f6a2075ca480306bf48f2a0df96e93edf",
    "migration": "225badb71efd35e5ed03b287bcd66c1cf01d4f7dd9e07c81570ccdd456d20f44",
}
HISTORY = (
    "event_stage_operations", "event_progress_receipts", "event_accounting_receipts",
    "event_retirement_receipts", "source_event_retirement_receipts", "event_timing_history",
)
EXTRA_PROTECTED = (
    "host_request_spacing", "host_request_spacing_baselines", "scheduler_event_turns",
    "scheduler_event_requests", "scheduler_capacity_requests", "history_origin_requests",
)
ARCHIVE_PROOF = {
    "receipt.json": "40e303ce9e783d00422112c872c84421f3128ad193e910093299b25e638a4532",
    "checkpoint.py": "10bdfb40799a348ab948cca9934639b873676cf737e11a0532ba603960d099ba",
    "checkpoint-verified.json": "0b9678b9f4831a486498f5aaa91e54c2431d58c20311d9ae63ca94d0e4c8c850",
}

RESTORE_EXPECTED = '''def expected_restore(conn: sqlite3.Connection, util: Any) -> dict[str, Any]:
    expected = util.table_receipts(conn)
    rows = [list(row) for row in conn.execute("SELECT * FROM event_pressure_state ORDER BY singleton")]
    require(expected["event_pressure_state"]["columns"] == ["singleton", "epoch", "sequence"]
            and len(rows) == 1 and rows[0][0] == 1, "restore pressure fence shape differs")
    rows[0][1] += 1
    body = json.dumps(rows[0], separators=(",", ":"), ensure_ascii=True).encode()
    expected["event_pressure_state"] = dict(expected["event_pressure_state"],
        sha256=hashlib.sha256(len(body).to_bytes(8, "big") + body).hexdigest())
    return expected


'''


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "cannot load sealed helper")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def replace(body: str, old: str, new: str, count: int = 1) -> str:
    require(body.count(old) == count, f"reviewed helper seam changed: {old!r}")
    return body.replace(old, new)


def derive(kind: str, body: str, receipt: str) -> str:
    """Only explicit reviewed schema, identity and preservation seams change."""
    require(hashlib.sha256(body.encode()).hexdigest() == BASES[kind], "base helper differs")
    if kind == "inputs":
        for old, new in (
            ("60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6", receipt),
            ("22102b3cc07b07b49800c702396a748bbbe733e148bbca9b426bff370792223d", CHECKPOINT_SHA),
            ("da38556935ee18ee26a93e121ef461514ac2f423", ARCHIVE_COMMIT),
            ("maximum_schema_version=28", "maximum_schema_version=29"),
            ('manifest["schema_version"] == 14', 'manifest["schema_version"] == 28'),
            ("schema14 predecessor", "schema28 predecessor"),
            ("db.schema_version == 28", "db.schema_version == 29"),
            ("schema=28,", "schema=29,"),
            ("inspected.schema_version == 28", "inspected.schema_version == 29"),
        ):
            body = replace(body, old, new)
        body = replace(body, "class ReplayLimit(Exception):", (
            f"PROTECTED += {EXTRA_PROTECTED!r}\n"
            f"HISTORY = {HISTORY!r}\nHISTORY_LIMITS: dict[str, int] = {{}}\n\n\n"
            "class ReplayLimit(Exception):"
        ))
        body = replace(body, "    result[\"original_execution_admissions\"] = query_hash(", (
            "    for name in HISTORY:\n"
            "        if name not in HISTORY_LIMITS:\n"
            "            HISTORY_LIMITS[name] = conn.execute(\n"
            "                f'SELECT coalesce(max(rowid),0) FROM \"{name}\"'\n"
            "            ).fetchone()[0]\n"
            "        result['original_' + name] = query_hash(\n"
            "            conn, f'SELECT * FROM \"{name}\" WHERE rowid<=? ORDER BY rowid',\n"
            "            (HISTORY_LIMITS[name],),\n"
            "        )\n"
            "    result['acquired_operations'] = query_hash(\n"
            "        conn, \"SELECT * FROM event_stage_operations WHERE stage='acquired' ORDER BY rowid\"\n"
            "    )\n"
            "    result[\"original_execution_admissions\"] = query_hash("
        ))
        body = replace(body, '        marker["named_judges"] = named_judges(db.connection)',
                       '        marker["history_limits"] = dict(HISTORY_LIMITS)\n'
                       '        marker["named_judges"] = named_judges(db.connection)')
        body = replace(body, '            marker = json.loads((scratch / MARKER).read_bytes())',
                       '            marker = json.loads((scratch / MARKER).read_bytes())\n'
                       '            require(set(marker["history_limits"]) == set(HISTORY), "history scope differs")\n'
                       '            HISTORY_LIMITS.update(marker["history_limits"])')
    elif kind == "restore":
        body = replace(body, 'manifest["schema_version"] == 14', 'manifest["schema_version"] == 28')
        body = replace(body, "database.schema_version == 14", "database.schema_version == 28")
        body = replace(body, 'report["schema14_activated_under_hold"]', 'report["schema28_activated_under_hold"]')
        body = replace(body, "args.target_schema >= 27", "args.target_schema == 29")
        body = replace(body, "def main() -> None:", RESTORE_EXPECTED + "def main() -> None:")
        body = replace(body, '            report["before"] = util.table_receipts(conn)',
                       '            report["before"] = util.table_receipts(conn)\n'
                       '            report["expected_restored"] = expected_restore(conn, util)\n'
                       '            report["restore_change_contract"] = "event_pressure_state_singleton_epoch_plus_one_only"')
        body = replace(body, 'util.table_receipts(database.connection) == report["before"]',
                       'util.table_receipts(database.connection) == report["expected_restored"]')
        body = replace(body, 'util.compare(report["before"], after)',
                       'util.compare(report["expected_restored"], after)')
    else:
        body = replace(body,
                       '    if Path(__file__).resolve() != source / "journal/tools/runtime/rehearse_extension_migration.py":\n'
                       '        raise ValueError("rehearsal helper must be part of the frozen source")',
                       '    # The sealed runner checks this separately frozen helper closure.')
        body = replace(body, 'manifest["schema_version"] != 14', 'manifest["schema_version"] != 28')
        body = replace(body, "schema14 baseline", "schema28 baseline")
        body = replace(body, "old_schema=14,", "old_schema=28,")
        body = replace(body, '        report["passed"] = True',
                       '        if (sha(destination / "operator-hold") != manifest["files"]["operator-hold"]["sha256"]\n'
                       '                or (destination / "RESTORE_PENDING").exists()):\n'
                       '            raise ValueError("migration specimen hold changed")\n'
                       '        report["passed"] = True')
    body = replace(body, 'if __name__ == "__main__":\n    main()',
                   'if __name__ == "__main__":\n    raise SystemExit("use the sealed packet runner")')
    compile(body, f"{kind}.py", "exec")
    return body


def schema_literal(source: Path) -> int:
    tree = ast.parse((source / "src/swingset/state/db.py").read_text())
    values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION" for target in node.targets)
              and isinstance(node.value, ast.Constant)]
    require(values == [29], "frozen runtime is not exact schema29")
    return 29


def build(source: Path, expected: str, runtime_source: str, bases: Path, output: Path) -> dict[str, Any]:
    sys.dont_write_bytecode = True
    require(re.fullmatch(r"/nix/store/[a-z0-9]{32}-source", runtime_source) is not None,
            "runtime source must be an exact Nix source path")
    require(not output.exists(), "packet output must be new")
    originals = {kind: bases / f"rehearse_extension_{kind}.py" for kind in BASES}
    for kind, path in originals.items():
        require(sha(path) == BASES[kind], "reviewed base helper differs")
    verifier = load(originals["migration"], "rehearsal_build_verifier")
    verifier.verify_source(source, source / "extension-source.json", expected)
    schema_literal(source)
    evidence = Path(__file__).resolve().parents[2] / "evidence/runtime/schema28-checkpoint-2026-09-17/production-002"
    for name, digest in ARCHIVE_PROOF.items():
        require(sha(evidence / name) == digest, "backup evidence differs")
    output.mkdir(parents=True)
    for kind, path in originals.items():
        (output / f"base-{kind}.py").write_bytes(path.read_bytes())
        (output / f"{kind}.py").write_text(derive(kind, path.read_text(), expected))
    (output / "runner.py").write_bytes(Path(__file__).read_bytes())
    for name in ARCHIVE_PROOF:
        (output / ("backup-" + name)).write_bytes((evidence / name).read_bytes())
    archive_proof(output)
    packet = dict(format="schema28-to29-rehearsal-packet-v1", source=runtime_source,
                  source_receipt_sha256=expected, checkpoint=CHECKPOINT,
                  checkpoint_sha256=CHECKPOINT_SHA, archive_commit=ARCHIVE_COMMIT,
                  predecessor_schema=28, predecessor_tables=116, target_schema=29,
                  private_archive_authority="retained_verified_receipt_not_new_remote_request",
                  source_acceptance="not_established_by_packet_build",
                  files={p.name: sha(p) for p in sorted(output.iterdir())})
    (output / "packet.json").write_text(json.dumps(packet, sort_keys=True, indent=2) + "\n")
    return packet


def packet_at(root: Path, expected: str) -> dict[str, Any]:
    require(sha(root / "packet.json") == expected, "packet digest differs")
    packet: dict[str, Any] = json.loads((root / "packet.json").read_bytes())
    names = {"runner.py", *(f"{prefix}{kind}.py" for prefix in ("", "base-") for kind in BASES),
             *("backup-" + name for name in ARCHIVE_PROOF)}
    require(set(packet["files"]) == names, "packet closure differs")
    require({p.name for p in root.iterdir()} == names | {"packet.json"}, "packet has extra files")
    for name in names:
        require(not (root / name).is_symlink() and sha(root / name) == packet["files"][name],
                "packet helper differs")
    archive_proof(root)
    require(packet["predecessor_schema"] == 28 and packet["target_schema"] == 29
            and packet["predecessor_tables"] == 116
            and packet["checkpoint"] == CHECKPOINT and packet["checkpoint_sha256"] == CHECKPOINT_SHA
            and packet["archive_commit"] == ARCHIVE_COMMIT, "packet authority differs")
    return packet


def archive_proof(root: Path) -> None:
    for name, digest in ARCHIVE_PROOF.items():
        require(sha(root / ("backup-" + name)) == digest, "private archive evidence differs")
    receipt = json.loads((root / "backup-receipt.json").read_bytes())
    verified = json.loads((root / "backup-checkpoint-verified.json").read_bytes())
    require(receipt["passed"] is True and receipt["live_database_changes"] == 0
            and receipt["checkpoint"] == verified and verified["path"] == CHECKPOINT
            and verified["manifest_sha256"] == CHECKPOINT_SHA
            and receipt["private_archive_commit"] == ARCHIVE_COMMIT,
            "private archive acknowledgment scope differs")


def predecessor(checkpoint: Path, expected: str) -> None:
    require(sha(checkpoint / "checkpoint.json") == expected, "checkpoint identity differs")
    require(not checkpoint.is_symlink() and not (checkpoint / "state.sqlite").is_symlink(),
            "checkpoint path is linked")
    with closing(sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        require(conn.execute("PRAGMA user_version").fetchone()[0] == 28
                and conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "28",
                "checkpoint database schema markers differ")
        require(conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 116,
                "checkpoint predecessor table population differs")
        require(conn.execute("SELECT 1 FROM execution_admissions WHERE state<>'settled'").fetchone() is None,
                "checkpoint has unsettled admissions")


def arguments(kind: str, args: list[str], packet: dict[str, Any], root: Path) -> list[str]:
    fixed = {"source": packet["source"], "checkpoint": packet["checkpoint"]}
    if kind != "inputs":
        fixed.update({"checkpoint-sha256": packet["checkpoint_sha256"],
                      "source-receipt": str(Path(packet["source"]) / "extension-source.json"),
                      "source-receipt-sha256": packet["source_receipt_sha256"]})
    if kind != "migration":
        fixed["helper-sha256"] = packet["files"][kind + ".py"]
    if kind == "restore":
        fixed.update({"target-schema": "29", "archive-commit": packet["archive_commit"],
                      "public-repo": "skeswa/swingset"})
    require(not any(value.split("=", 1)[0] in {"--" + name for name in fixed} for value in args),
            "caller cannot override sealed authority")
    return [str(root / f"{kind}.py"), *args, *(item for key, value in fixed.items() for item in ("--" + key, value))]


def run(root: Path, expected: str, kind: str, args: list[str]) -> None:
    sys.dont_write_bytecode = True
    packet = packet_at(root, expected)
    require(Path(__file__).resolve() == root / "runner.py", "run the sealed packet runner")
    verifier = load(root / "base-migration.py", "rehearsal_runtime_verifier")
    source = Path(packet["source"])
    verifier.verify_source(source, source / "extension-source.json", packet["source_receipt_sha256"])
    schema_literal(source)
    predecessor(Path(packet["checkpoint"]), packet["checkpoint_sha256"])
    sys.path[:0] = [str(source / "src"), str(source)]
    from swingset.state.db import SCHEMA_VERSION
    require(SCHEMA_VERSION == 29, "imported runtime schema differs")
    for name, module in tuple(sys.modules.items()):
        if name == "swingset" or name.startswith("swingset."):
            file = getattr(module, "__file__", None)
            require(file is not None and Path(file).resolve().is_relative_to(source / "src"), "runtime import escaped frozen source")
    sys.argv = arguments(kind, args, packet, root)
    if kind == "inputs":
        phase_parser = argparse.ArgumentParser(add_help=False)
        phase_parser.add_argument("phase", choices=("prepare", "accept", "drain"))
        phase_parser.add_argument("--scratch", type=Path, required=True)
        phase, _ = phase_parser.parse_known_args(args)
        if phase.phase != "prepare":
            scratch_markers(phase.scratch)
    if kind == "migration":
        def offline(event: str, values: Any) -> None:
            if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
                raise RuntimeError("migration rehearsal forbids network and subprocesses")
        sys.addaudithook(offline)
    module = load(root / f"{kind}.py", "sealed_schema29_" + kind)
    module.main()
    packet_at(root, expected)


def scratch_markers(scratch: Path) -> None:
    with closing(sqlite3.connect((scratch.resolve() / "state.sqlite").as_uri() + "?mode=ro", uri=True)) as conn:
        require(conn.execute("PRAGMA user_version").fetchone()[0] == 29
                and conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "29",
                "scratch schema markers differ; input phase cannot migrate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    for name in ("source", "bases", "output"):
        builder.add_argument("--" + name, type=Path, required=True)
    builder.add_argument("--source-receipt-sha256", required=True)
    builder.add_argument("--runtime-source", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--packet-sha256", required=True)
    runner.add_argument("kind", choices=tuple(BASES))
    runner.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "build":
        build(args.source.resolve(), args.source_receipt_sha256, args.runtime_source, args.bases, args.output)
        print(json.dumps({"packet_sha256": sha(args.output / "packet.json"), "output": str(args.output)}))
    else:
        values = args.args[1:] if args.args[:1] == ["--"] else args.args
        run(Path(__file__).resolve().parent, args.packet_sha256, args.kind, values)


if __name__ == "__main__":
    main()
