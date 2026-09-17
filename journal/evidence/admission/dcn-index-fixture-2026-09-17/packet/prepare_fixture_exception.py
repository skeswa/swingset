"""Prepare the approved fixture runner's exact gate; never acquire a resource.

Run this stdlib-only helper after staging the byte-exact reviewed wrapper and its
sibling helper-closure. The coordinator supplies the recorded owner decision.
Only new files beside the wrapper are written. Existing state is read under its
writer lock; actual execution rechecks controls and charges the shared budget.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SOURCE = Path("/nix/store/z689qy41inndill3d92ym8im852x3649-source")
SOURCE_RECEIPT_SHA = "0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb"
DRIVER = "dcn-index-fixture-h13-001.py"
DRIVER_SHA = "b068926b0491182338f51b3e7091a3610aac7287d399194c60cc143f76cbe2b1"
HELPER_SHA = "f07f9c55dc7c7f7313f23b9a31edfca93a4c6e4a41fec53efda8fb0c5843d72a"
MANIFEST_SHA = "8495f724833e077cde947c97360f38c24dee7265ade89549e6ae9e553f61e18e"
BASELINE_COMMIT = "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
BASELINE_CANDIDATE = "cand_8f31cad7226643ae"
OUTPUTS = ("authorization.json", "execution-gate.json", "preparation-receipt.json", "execute.sh")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_files(root: Path, inventory: dict[str, Any]) -> None:
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("empty source inventory")
    for name, expected in inventory.items():
        relative = Path(name)
        path = root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.resolve().is_relative_to(root)
        ):
            raise ValueError("inventory path escapes its root")
        if sha(path) != (expected["sha256"] if isinstance(expected, dict) else expected):
            raise ValueError("reviewed bytes changed: " + name)


def verify_packet(packet: Path) -> Path:
    if sha(packet / DRIVER) != DRIVER_SHA:
        raise ValueError("reviewed driver changed")
    helpers = packet / "helper-closure"
    children = list(helpers.rglob("*"))
    if helpers.is_symlink() or any(path.is_symlink() for path in children):
        raise ValueError("helper closure contains symlinks")
    if sha(helpers / "closure.json") != HELPER_SHA:
        raise ValueError("reviewed helper manifest changed")
    receipt = json.loads((helpers / "closure.json").read_bytes())
    if receipt["format"] != "fixture-helper-closure-v1":
        raise ValueError("unknown helper format")
    if {p.relative_to(helpers).as_posix() for p in children if p.is_file()} != set(
        receipt["files"]
    ) | {"closure.json"}:
        raise ValueError("undeclared or missing helper file")
    verify_files(helpers, receipt["files"])
    proposal = json.loads((helpers / "proposal.json").read_bytes())
    if hashlib.sha256(canonical(proposal)).hexdigest() != MANIFEST_SHA:
        raise ValueError("exact authorized proposal differs")
    return helpers


def verify_runtime(source: Path) -> int:
    receipt = source / "h16-source.json"
    if sha(receipt) != SOURCE_RECEIPT_SHA:
        raise ValueError("frozen schema14 source receipt changed")
    files = json.loads(receipt.read_bytes())["files"]
    verify_files(source, files)
    return len(files)


def inspect_state(state: Path, *, now: datetime) -> dict[str, Any]:
    """Read only; caller holds the existing state writer lock."""
    if (state / "RESTORE_PENDING").exists() or not (state / "operator-hold").is_file():
        raise ValueError("restore must be clear and the scheduled-service hold must remain")
    baseline = (state / "baseline").resolve(strict=True)
    published = json.loads((baseline / "PUBLISHED").read_bytes())
    if published.get("commit") != BASELINE_COMMIT or baseline.name != BASELINE_CANDIDATE:
        raise ValueError("acknowledged H16 publication baseline changed")
    with sqlite3.connect((state / "state.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        conn.execute("BEGIN")
        schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if schema is None or int(schema[0]) != 14:
            raise ValueError("exact schema14 required; this helper never migrates")
        day = now.astimezone(UTC).date().isoformat()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host='web.archive.org' AND day=?", (day,)
        ).fetchone()
        requests, received = tuple(budget) if budget else (0, 0)
        if requests >= 200:
            raise ValueError("shared archive daily budget exhausted")
        pauses = [dict(row) for row in conn.execute("SELECT * FROM operator_pauses")]
        host = conn.execute("SELECT * FROM hosts WHERE host='web.archive.org'").fetchone()
        pending = conn.execute("SELECT count(*) FROM pending_work").fetchone()[0]
        return dict(
            schema=14,
            baseline_path=str(baseline),
            published_sha256=sha(baseline / "PUBLISHED"),
            manifest_sha256=sha(baseline / "_meta/manifest.json"),
            archive_day=day,
            archive_requests=requests,
            archive_bytes=received,
            archive_requests_remaining_under_200=max(0, 200 - requests),
            host=dict(host) if host else None,
            operator_pauses=pauses,
            pending_work=pending,
            quota_basis="read-only preparation; actual gate rechecks and debits shared budget",
        )


def prepare(
    *,
    packet: Path,
    state: Path,
    quarantine: Path,
    approved_by: str,
    approved_at: datetime,
    decision: str,
    publication: str,
    now: datetime,
) -> dict[str, Any]:
    packet, state, quarantine = (
        packet.resolve(strict=True),
        state.resolve(strict=True),
        quarantine.resolve(),
    )
    if approved_at.tzinfo is None or now.tzinfo is None or approved_at > now:
        raise ValueError("recorded approval must be timezone-aware and not in the future")
    if any(not value.strip() for value in (approved_by, decision, publication)):
        raise ValueError("actual owner decision and publication references required")
    if quarantine == state or quarantine.is_relative_to(state) or state.is_relative_to(quarantine):
        raise ValueError("quarantine must be outside and separate from production state")
    if quarantine.exists() or any((packet / name).exists() for name in OUTPUTS):
        raise ValueError(
            "single-use preparation or quarantine already exists; do not retry by changing paths"
        )
    helpers = verify_packet(packet)
    source_files = verify_runtime(SOURCE)
    with (state / "state.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed = inspect_state(state, now=now)
        authorization = dict(
            approval="approved_exact_dcn_index_fixture_only",
            manifest_canonical_sha256=MANIFEST_SHA,
            state=str(state),
            quarantine=str(quarantine),
            published_stages=["V2", "V4"],
            approved_by=approved_by,
            owner_decision_reference=decision,
            publication_receipt_reference=publication,
            approved_at=approved_at.isoformat(),
            approval_time_basis="coordinator-observed owner response; not an inferred click timestamp",
            execution_window=dict(
                starts_at=now.isoformat(), expires_at=(now + timedelta(hours=24)).isoformat()
            ),
        )
        gate = dict(
            format="fixture-h13-coordinator-v2",
            driver_sha256=DRIVER_SHA,
            helper_root=str(helpers),
            helper_manifest_sha256=HELPER_SHA,
            source=str(SOURCE),
            source_receipt="h16-source.json",
            source_receipt_sha256=SOURCE_RECEIPT_SHA,
            schema=14,
            state=str(state),
            **{
                key: observed[key]
                for key in ("baseline_path", "published_sha256", "manifest_sha256")
            },
        )
        for name, value in (("authorization.json", authorization), ("execution-gate.json", gate)):
            with (packet / name).open("xb") as stream:
                stream.write(canonical(value) + b"\n")
        gate_sha = sha(packet / "execution-gate.json")
        command = [
            "/run/current-system/sw/bin/env",
            "PYTHONDONTWRITEBYTECODE=1",
            f"PYTHONPATH={SOURCE / 'src'}:{SOURCE}",
            "/var/lib/swingset/venv/bin/python",
            str(packet / DRIVER),
            "--repo",
            str(SOURCE),
            "--manifest",
            str(helpers / "proposal.json"),
            "--state",
            str(state),
            "--quarantine",
            str(quarantine),
            "--authorization",
            str(packet / "authorization.json"),
            "--execution-gate",
            str(packet / "execution-gate.json"),
            "--execution-gate-sha256",
            gate_sha,
            "--execute",
        ]
        receipt = dict(
            format="fixture-preparation-v1",
            prepared_at=now.isoformat(),
            source_files_verified=source_files,
            gate_sha256=gate_sha,
            authorization_sha256=sha(packet / "authorization.json"),
            state_observation=observed,
            command=command,
            executed=False,
            network_requests=0,
            isolation="Reviewed wrapper has H13/host gates and no production parse queue; it does not call ordinary scheduler backpressure.",
        )
        with (packet / "preparation-receipt.json").open("xb") as stream:
            stream.write(canonical(receipt) + b"\n")
        with (packet / "execute.sh").open("x") as stream:
            stream.write(
                "#!/bin/sh\nset -eu\n# Coordinator only: run once after all other worker operations stop.\nexec "
                + shlex.join(command)
                + "\n"
            )
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("packet", "state", "quarantine"):
        parser.add_argument("--" + key, required=True, type=Path)
    for key in ("approved-by", "approved-at", "decision", "publication"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = prepare(
        packet=args.packet,
        state=args.state,
        quarantine=args.quarantine,
        approved_by=args.approved_by,
        approved_at=datetime.fromisoformat(args.approved_at),
        decision=args.decision,
        publication=args.publication,
        now=datetime.now(UTC),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
