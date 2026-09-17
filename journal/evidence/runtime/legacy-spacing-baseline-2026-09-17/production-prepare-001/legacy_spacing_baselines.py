"""Prepare read-only legacy spacing evidence; apply only an exact reviewed packet.

The proofs cover the original Archive fixture run and its exact DCN follow-up. Other hosts
remain blocked. No request, budget refund, input acceptance or worker activation
occurs here. The coordinator executes this helper under its separately pinned
runtime and helper inventory.
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
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

HOST = "web.archive.org"
PRIOR_HOSTS_SHA = "6c0b8d7b34e1c4a068374c59d0f41d52459f0113883b9868a6750c2753123122"
FIXTURE_FILES = {
    "quarantine/receipt.json": "195c2ddcff02401f780c1591031125fe7d254d0a251179654dc448d55e1ae99a",
    "quarantine/manifest.json": "c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93",
    "execution-admissions.json": "10eb3a3989629e83108c0fdac2564b86ed8a7a8bb3db08a13431bb61753e6ef1",
    "packet/helper-closure/fixture_helpers/fixture_transport.py": "5e873a4ccc711ef3c668246862ac6a4a33be0a90dd998a4b896af09c2e55a97a",
    "packet/fixture-exception-h13-002.py": "81357b33c8862bdc4bdd2d84f2479a7913a9d783b00b435910cc2328bdcf69f7",
    "packet/preparation-receipt.json": "fe33f63f97e24eae80ec58a8a672e5e5891a47c2de58e1881d923ec7fa1d752b",
}
DCN_FILES = {
    "quarantine/receipt.json": "a86b2220f764c4208cd1cec08861bd64a1521109ce27fd9b2d6d30076da08321",
    "quarantine/manifest.json": "8495f724833e077cde947c97360f38c24dee7265ade89549e6ae9e553f61e18e",
    "execution-admissions.json": "ebe48d5dbcb0ac6117d6f2e29e2113146784c8e76c848043d7f5b2ff2f885d9f",
    "production-after.json": "13117ca6d41ecfdbb64b80131cb31267b6eab7f2ab74eb4e660e45f2b2f3020a",
    "packet/helper-closure/fixture_helpers/fixture_transport.py": "16a06e016500524952819aacedfedd0d23507f73cca080ecf6b3296c84ba46d6",
    "packet/dcn-index-fixture-h13-001.py": "b068926b0491182338f51b3e7091a3610aac7287d399194c60cc143f76cbe2b1",
    "packet/preparation-receipt.json": "d16d40c499b5d78cea3126540fd5bdb76594ffc290e8b85361371520a9338343",
}
PROTECTED = ("hosts", "host_budget", "operator_pauses", "control_state", "control_events", "execution_admissions")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def protected(conn: sqlite3.Connection) -> dict[str, Any]:
    result = {}
    for table in PROTECTED:
        digest, count = hashlib.sha256(), 0
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            body = encoded(list(row))
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(body)
            count += 1
        result[table] = dict(rows=count, sha256=digest.hexdigest())
    return result


def guards(state: Path) -> dict[str, Any]:
    require((state / "operator-hold").is_file(), "operator hold is absent")
    require(not (state / "RESTORE_PENDING").exists(), "restore verification remains pending")
    units = {}
    for kind in ("cycle", "backup", "summary"):
        for suffix in ("service", "timer"):
            unit = f"swingset-{kind}.{suffix}"
            value = subprocess.check_output(["systemctl", "show", unit, "--property=ActiveState", "--value"], text=True).strip()
            require(value == "inactive", f"ordinary unit is active: {unit}")
            units[unit] = value
    return dict(operator_hold_sha256=sha(state / "operator-hold"), units=units)


def settled(conn: sqlite3.Connection) -> None:
    require(conn.execute("SELECT 1 FROM execution_admissions WHERE state!='settled' LIMIT 1").fetchone() is None, "unsettled execution/control admissions remain")


def fixture_evidence(root: Path, prior_hosts: Path) -> dict[str, Any]:
    from swingset.config import parse_hosts
    from swingset.fetch.robots import _policy

    for name, digest in FIXTURE_FILES.items():
        require(sha(root / name) == digest, f"retained fixture evidence differs: {name}")
    require(sha(prior_hosts) == PRIOR_HOSTS_SHA, "original reviewed host configuration differs")
    receipt = json.loads((root / "quarantine/receipt.json").read_bytes())
    manifest = json.loads((root / "quarantine/manifest.json").read_bytes())
    preparation = json.loads((root / "packet/preparation-receipt.json").read_bytes())
    admissions = [row for row in json.loads((root / "execution-admissions.json").read_bytes()) if row["action_kind"] == "request"]
    requests = receipt["requests"]
    require(len(requests) == len(admissions) == 8 and receipt["received_bytes"] == 130059, "unexpected fixture request accounting")
    require(preparation["state_observation"]["archive_requests"] == preparation["state_observation"]["archive_bytes"] == 0, "fixture did not begin with its proven zero daily debit")
    bodies = {}
    for request in requests:
        digest = request["body_sha256"]
        body = root / "quarantine/bodies" / digest
        require(request["complete"] is True and sha(body) == digest and body.stat().st_size == request["body_bytes"], "fixture body is missing or changed")
        bodies[digest] = request["body_bytes"]
    robots = [row for row in requests if row["purpose"] == "robots"]
    require(len(robots) == 1 and robots[0]["url"] == f"https://{HOST}/robots.txt", "robots request identity differs")
    robot = robots[0]
    policy = _policy(robot["url"], robot["http_status"], (root / "quarantine/bodies" / robot["body_sha256"]).read_bytes())
    old_floor = parse_hosts(prior_hosts.read_bytes())[HOST].min_gap_seconds
    gap = max(5.0, 10.0, old_floor, policy.crawl_delay)
    require(policy.allowed and gap == 10 and manifest["limits"]["minimum_gap_seconds"] == 10, "fixture's original gap is not the reviewed ten seconds")
    return dict(
        kind="archive-fixture-2026-09-17-eight-requests", files=FIXTURE_FILES,
        prior_hosts_sha256=PRIOR_HOSTS_SHA, original_effective_gap_seconds=gap,
        robots=dict(body_sha256=robot["body_sha256"], http_status=robot["http_status"], issued_at=robot["issued_at"], crawl_delay=policy.crawl_delay),
        bodies=bodies, started_at=receipt["started_at"], finished_at=receipt["finished_at"],
        day=preparation["state_observation"]["archive_day"], requests=8, received_bytes=130059,
        admissions=sorted(admissions, key=lambda row: row["action_id"]),
    )


def dcn_evidence(root: Path, previous: dict[str, Any]) -> dict[str, Any]:
    """Extend only the exact first proof with the independently retained follow-up."""
    from swingset.fetch.robots import _policy

    require(previous["kind"] == "archive-fixture-2026-09-17-eight-requests", "DCN proof needs its exact predecessor")
    for name, digest in DCN_FILES.items():
        require(sha(root / name) == digest, f"retained DCN evidence differs: {name}")
    receipt = json.loads((root / "quarantine/receipt.json").read_bytes())
    manifest = json.loads((root / "quarantine/manifest.json").read_bytes())
    preparation = json.loads((root / "packet/preparation-receipt.json").read_bytes())["state_observation"]
    after = json.loads((root / "production-after.json").read_bytes())
    require((preparation["archive_day"], preparation["archive_requests"], preparation["archive_bytes"]) == (previous["day"], previous["requests"], previous["received_bytes"]), "DCN predecessor paid usage differs")
    require(datetime.fromisoformat(receipt["started_at"]) > datetime.fromisoformat(previous["finished_at"]), "DCN order differs")
    requests = receipt["requests"]
    require(len(requests) == 2 and receipt["received_bytes"] == 2804642 and manifest["limits"]["minimum_gap_seconds"] == 10, "DCN scope differs")
    bodies = {}
    for request in requests:
        digest = request["body_sha256"]
        body = root / "quarantine/bodies" / digest
        require(request["complete"] is True and request["request_day"] == previous["day"] and sha(body) == digest and body.stat().st_size == request["body_bytes"], "DCN retained response differs")
        require(request["spacing_basis"] == "closed_exchange_completion_floor" and request["next_dispatch_not_before_elapsed_seconds"] >= request["exchange_completed_elapsed_seconds"] + 10, "DCN original completion gap differs")
        bodies[digest] = request["body_bytes"]
    robot, body_request = requests
    require(robot["purpose"] == "robots" and robot["url"] == f"https://{HOST}/robots.txt", "DCN robots identity differs")
    require(body_request["transport_dispatched_elapsed_seconds"] >= robot["next_dispatch_not_before_elapsed_seconds"], "DCN actual dispatch preceded original gap")
    policy = _policy(robot["url"], robot["http_status"], (root / "quarantine/bodies" / robot["body_sha256"]).read_bytes())
    gap = max(previous["original_effective_gap_seconds"], 10.0, policy.crawl_delay)
    require(policy.allowed and gap == 10, "DCN original robots policy differs")
    admissions = sorted((row for row in json.loads((root / "execution-admissions.json").read_bytes()) if row["action_kind"] == "request"), key=lambda row: row["action_id"])
    predecessor = {row["action_id"]: row for row in previous["admissions"]}
    require(len(admissions) == 10 and {row["action_id"]: row for row in admissions if row["action_id"] in predecessor} == predecessor, "DCN admission predecessor differs")
    require(all(row["host"] == HOST and row["state"] == "settled" for row in admissions), "DCN admissions are not settled Archive requests")
    usage = [row for row in after["archive_budget"] if row["day"] == previous["day"]]
    require(usage == [dict(host=HOST, day=previous["day"], requests=10, bytes=2934701)], "DCN retained cumulative ledger differs")
    return dict(kind="archive-fixtures-2026-09-17-ten-requests", previous=previous, files=DCN_FILES,
                original_effective_gap_seconds=gap,
                robots=dict(body_sha256=robot["body_sha256"], http_status=robot["http_status"], issued_at=robot["issued_at"], crawl_delay=policy.crawl_delay),
                bodies=bodies, started_at=previous["started_at"], finished_at=receipt["finished_at"],
                day=previous["day"], requests=10, received_bytes=2934701, admissions=admissions)


def assess_archive(conn: sqlite3.Connection, evidence: dict[str, Any]) -> str | None:
    row = conn.execute("SELECT requests,bytes FROM host_budget WHERE host=? AND day=?", (HOST, evidence["day"])).fetchone()
    if not row or tuple(row) != (evidence["requests"], evidence["received_bytes"]):
        return "paid usage differs from the retained fixture run"
    if conn.execute("SELECT 1 FROM host_budget WHERE host=? AND day>? AND requests>0 LIMIT 1", (HOST, evidence["day"])).fetchone():
        return "later paid requests lack a reviewed original-gap proof"
    rows = conn.execute("SELECT * FROM execution_admissions WHERE host=? AND action_kind='request' AND (julianday(admitted_at)>=julianday(?) OR julianday(admitted_at) IS NULL) ORDER BY action_id LIMIT ?", (HOST, evidence["started_at"], len(evidence["admissions"]) + 1))
    names = [column[0] for column in rows.description]
    actual = [dict(zip(names, row, strict=True)) for row in rows]
    if actual != evidence["admissions"]:
        return "request admission history differs from the retained fixture run"
    return None


def prepare(conn: sqlite3.Connection, config: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    settled(conn)
    schema = int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    require(schema in {14, 28}, "proposal supports only schema14 or schema28")
    items = []
    for row in conn.execute("SELECT * FROM hosts WHERE EXISTS(SELECT 1 FROM host_budget b WHERE b.host=hosts.host AND b.requests>0) ORDER BY host"):
        host = row[0]
        policy = asdict(config.host(host))
        reason = assess_archive(conn, evidence) if host == HOST else "no reviewed last-request, original-policy and robots proof"
        items.append(dict(host=host, eligible=reason is None, reason=reason, effective_policy=policy,
                          gap_seconds=max(5.0, policy["min_gap_seconds"], evidence["original_effective_gap_seconds"]) if reason is None else None,
                          cached_robots=dict(body_sha256=row[5], fetched_at=row[6], status=row[7])))
    return dict(format="reviewed-legacy-spacing-proposal-v1", schema=schema, hosts=items, evidence=evidence, protected=protected(conn))


def apply(conn: sqlite3.Connection, config: Any, proposal: dict[str, Any], evidence: dict[str, Any], *, proposal_sha256: str, stopped_at: datetime, clock: Any) -> dict[str, str]:
    from swingset.fetch.politeness import Gate

    require(conn.in_transaction, "apply requires an owned write transaction")
    require(int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]) == 28, "apply requires exact schema28 without migration")
    actual = prepare(conn, config, evidence)
    for key in ("hosts", "evidence", "protected"):
        require(actual[key] == proposal[key], f"proposal became stale: {key}")
    chosen = [row for row in actual["hosts"] if row["eligible"]]
    require(bool(chosen), "no host has a reviewed original-gap proof")
    gate = Gate(conn, config, clock)
    results = {}
    reference = "sha256:" + proposal_sha256
    for row in chosen:
        prior = conn.execute("SELECT reservation_id,gap_seconds FROM host_request_spacing WHERE host=?", (row["host"],)).fetchone()
        if prior and prior[1] is not None:
            existing = conn.execute("SELECT evidence_ref,gap_seconds FROM host_request_spacing_baselines WHERE reservation_id=? AND host=?", (prior[0], row["host"])).fetchone()
            require(existing is not None and tuple(existing) == (reference, row["gap_seconds"]), "host already has different spacing authority")
            results[row["host"]] = prior[0]
            continue
        results[row["host"]] = gate.establish_spacing_baseline(row["host"], gap_seconds=row["gap_seconds"], stopped_at=stopped_at, evidence_ref=reference)
    require(protected(conn) == proposal["protected"], "baseline changed protected hosts, budgets or controls")
    return results


def authorizer(action: int, table: str | None, column: str | None, database: str | None, origin: str | None) -> int:
    allowed = {(sqlite3.SQLITE_INSERT, "host_request_spacing_baselines"), (sqlite3.SQLITE_INSERT, "host_request_spacing"), (sqlite3.SQLITE_UPDATE, "host_request_spacing"), (sqlite3.SQLITE_INSERT, "hosts")}
    if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
        return sqlite3.SQLITE_OK if origin is None and (action, table) in allowed else sqlite3.SQLITE_DENY
    if action in {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def runtime_bound(source: Path) -> None:
    for module_name, module in tuple(sys.modules.items()):
        if module_name == "swingset" or module_name.startswith("swingset."):
            path = getattr(module, "__file__", None)
            require(path is not None and Path(path).resolve().is_relative_to(source / "src"), "runtime import escaped frozen source")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    for name in ("state", "source", "source-receipt", "config", "fixture-bundle", "prior-hosts", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source-receipt-sha256", "helper-sha256", "hosts-sha256", "sources-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--proposal-sha256")
    parser.add_argument("--dcn-bundle", type=Path)
    parser.add_argument("--coordinator-reviewed", action="store_true")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.umask(0o077)
    require(sha(Path(__file__)) == args.helper_sha256, "helper differs")
    require(not args.output.exists(), "output must be new")
    source = args.source.resolve(strict=True)
    require(sha(args.source_receipt) == args.source_receipt_sha256, "source receipt differs")
    name = "journal/tools/runtime/rehearse_extension_migration.py"
    entry = json.loads(args.source_receipt.read_bytes())["files"][name]
    require(sha(source / name) == (entry["sha256"] if isinstance(entry, dict) else entry), "frozen source helper differs")
    spec = importlib.util.spec_from_file_location("spacing_frozen_source", source / name)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load frozen source verifier")
    util = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(util)
    util.verify_source(source, args.source_receipt, args.source_receipt_sha256)
    from swingset.clock import SystemClock
    from swingset.config import load_config
    from swingset.state.control_lock import control_lock
    from swingset.state.db import SCHEMA_VERSION, open_database

    require(SCHEMA_VERSION == 28, "helper requires the reviewed schema28 runtime")
    bindings = dict(source=str(source), source_receipt_sha256=args.source_receipt_sha256, helper_sha256=args.helper_sha256,
                    config=str(args.config.resolve()), hosts_sha256=args.hosts_sha256, sources_sha256=args.sources_sha256,
                    fixture_bundle=str(args.fixture_bundle.resolve()), prior_hosts=str(args.prior_hosts.resolve()), state=str(args.state.resolve()))
    bindings["dcn_bundle"] = str(args.dcn_bundle.resolve()) if args.dcn_bundle else None

    def inputs() -> tuple[Any, dict[str, Any]]:
        require(sha(args.config / "hosts.toml") == args.hosts_sha256 and sha(args.config / "sources.toml") == args.sources_sha256, "reviewed configuration changed")
        evidence = fixture_evidence(args.fixture_bundle, args.prior_hosts)
        if args.dcn_bundle:
            evidence = dcn_evidence(args.dcn_bundle, evidence)
        return load_config(args.config), evidence

    config, evidence = inputs()
    runtime_bound(source)
    packet = None
    if args.mode == "apply":
        require(args.coordinator_reviewed and args.proposal is not None and args.proposal_sha256 is not None, "apply needs a concrete coordinator-reviewed proposal pin")
        require(sha(args.proposal) == args.proposal_sha256, "proposal differs")
        packet = json.loads(args.proposal.read_bytes())
        require(packet["format"] == "reviewed-legacy-spacing-proposal-v1" and packet["schema"] in {14, 28}, "unknown proposal format or schema")
        require(packet["bindings"] == bindings, "proposal runtime/configuration binding differs")
    clock = SystemClock()
    with open_database(args.state, read_only=True) as held:
        with control_lock(args.state, timeout=0):
            before = guards(args.state)
            with held.transaction(immediate=False):
                proposal = prepare(held.connection, config, evidence)
            stopped = clock.now()
            if packet is None:
                result = dict(**proposal, bindings=bindings, guards=before, stopped_at=stopped.isoformat(), prepared_at=stopped.isoformat(), applied=False)
            else:
                require(held.schema_version == 28 and packet["guards"] == before, "apply schema or hold changed")
                conn = sqlite3.connect((args.state.resolve() / "state.sqlite").as_uri() + "?mode=rw", uri=True, isolation_level=None)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA busy_timeout=1000")
                    conn.set_authorizer(authorizer)
                    conn.execute("BEGIN IMMEDIATE")
                    identifiers = apply(conn, config, packet, evidence, proposal_sha256=args.proposal_sha256, stopped_at=datetime.fromisoformat(packet["stopped_at"]), clock=clock)
                    runtime_bound(source)
                    require(guards(args.state) == before, "hold or ordinary services changed during apply")
                    inputs()
                    util.verify_source(source, args.source_receipt, args.source_receipt_sha256)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                result = dict(format="legacy-spacing-application-v1", applied=True, proposal_sha256=args.proposal_sha256, bindings=bindings, baseline_ids=identifiers, observed_at=clock.now().isoformat(), holds_removed=False, budgets_changed=False, requests_issued=0)
            runtime_bound(source)
            require(guards(args.state) == before, "hold or ordinary services changed")
            inputs()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as stream:
                stream.write(json.dumps(result, indent=2).encode() + b"\n")
                stream.flush()
                os.fsync(stream.fileno())


if __name__ == "__main__":
    main()
