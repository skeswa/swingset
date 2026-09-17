"""Fresh schema28 checkpoint packets must bind candidate005 and all evidence."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "journal/tools/runtime"
OLD_PACKET = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-003"
OLD_PACKET_SHA256 = "6748af985bbe7b6a74c30c094c72cc117fd6f259a661fb5aeb052e51e309d230"
SPEC = importlib.util.spec_from_file_location(
    "prepare_current_schema29_rehearsals",
    TOOLS / "prepare_current_schema29_rehearsals.py",
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    source = tmp_path / "candidate005"
    (source / "src/swingset/state").mkdir(parents=True)
    (source / "src/swingset/backup").mkdir(parents=True)
    (source / "src/swingset/state/db.py").write_text("SCHEMA_VERSION = 29\n")
    (source / "src/swingset/backup/checkpoint.py").write_text("SCHEMA_VERSION = 29\n")
    source_files = {
        "src/swingset/state/db.py": digest(source / "src/swingset/state/db.py"),
        "src/swingset/backup/checkpoint.py": digest(source / "src/swingset/backup/checkpoint.py"),
    }
    source_receipt = source / "extension-source.json"
    source_receipt.write_text(json.dumps({"files": source_files}, sort_keys=True))
    monkeypatch.setattr(builder, "SOURCE", source)
    monkeypatch.setattr(builder, "SOURCE_RECEIPT_SHA256", digest(source_receipt))

    checkpoint_root = tmp_path / "checkpoints"
    checkpoint_root.mkdir()
    monkeypatch.setattr(builder, "CHECKPOINT_ROOT", checkpoint_root)
    checkpoint = checkpoint_root / "extension28-held-test001"
    checkpoint.mkdir()
    db_path = checkpoint / "state.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version','28')")
        conn.execute("CREATE TABLE execution_admissions (state TEXT NOT NULL)")
        conn.execute("INSERT INTO execution_admissions VALUES ('settled')")
        conn.execute(
            "CREATE TABLE host_budget (host TEXT NOT NULL, day TEXT NOT NULL, "
            "requests INTEGER NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(host,day))"
        )
        conn.execute("INSERT INTO host_budget VALUES ('web.archive.org','2026-09-17',20,2952065)")
        conn.execute("INSERT INTO host_budget VALUES ('danceconvention.net','2026-09-17',4,132764)")
        for index in range(113):
            conn.execute(f'CREATE TABLE "retained_{index:03}" (id INTEGER PRIMARY KEY)')
        conn.execute("PRAGMA user_version=28")
    artifacts = {
        "operator-hold": b"held\n",
        "dcn-origin-event-days.json": b"origin accounting\n",
        "dcn-origin-robots-cache.json": b"robots provenance\n",
        f"candidates/{builder.CANDIDATE}/PUBLISHED": json.dumps(
            {
                "commit": builder.PUBLIC_BASELINE,
                "candidate_id": builder.CANDIDATE,
                "verified_at": "2026-09-17T20:00:00+00:00",
                "closure_digest": None,
                "evidence_cutoff": None,
            }
        ).encode(),
    }
    for name, content in artifacts.items():
        path = checkpoint / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(
        builder, "HOLD_SHA256", hashlib.sha256(artifacts["operator-hold"]).hexdigest()
    )
    files: dict[str, dict[str, Any]] = {
        "state.sqlite": {"size": db_path.stat().st_size, "sha256": digest(db_path)},
        **{
            name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in artifacts.items()
        },
    }
    manifest = {
        "format": 1,
        "schema_version": 28,
        "baseline_candidate": builder.CANDIDATE,
        "pending_candidate": None,
        "input_bundle_hash": builder.BUNDLE,
        "files": files,
    }
    manifest_path = checkpoint / "checkpoint.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    manifest_sha = digest(manifest_path)
    verified = checkpoint_root / "checkpoint-verified.json"
    summary = {
        "path": str(checkpoint),
        "manifest_sha256": manifest_sha,
        "files": len(files),
        "bytes": sum(int(item["size"]) for item in files.values()),
    }
    verified.write_text(json.dumps(summary, sort_keys=True))
    archive_commit = "a" * 40
    receipt = checkpoint_root / "receipt.json"
    receipt_value = {
        "format": "held-extension-schema28-checkpoint-v1",
        "passed": True,
        "source": builder.DEPLOYED_SOURCE,
        "system": builder.DEPLOYED_SYSTEM,
        "baseline": builder.PUBLIC_BASELINE,
        "units": {
            f"swingset-{kind}.{suffix}": "inactive"
            for kind in ("cycle", "backup", "summary")
            for suffix in ("service", "timer")
        },
        "live_database_changes": 0,
        "checkpoint": summary,
        "private_archive_commit": archive_commit,
    }
    receipt.write_text(json.dumps(receipt_value, sort_keys=True))
    helper = checkpoint_root / "checkpoint.py"
    helper.write_text("reviewed checkpoint helper\n")
    return {
        "source": source,
        "checkpoint": checkpoint,
        "checkpoint_sha": manifest_sha,
        "receipt": receipt,
        "verified": verified,
        "helper": helper,
        "bases": TOOLS,
        "output": tmp_path / "packet",
        "runtime_source": builder.RUNTIME_SOURCE,
        "source_receipt_sha": digest(source_receipt),
        "receipt_sha": digest(receipt),
        "verified_sha": digest(verified),
        "helper_sha": digest(helper),
        "manifest": manifest,
    }


def build_packet(inputs: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        builder.build(
            source=inputs["source"],
            source_receipt_sha256=inputs["source_receipt_sha"],
            runtime_source=inputs["runtime_source"],
            checkpoint=inputs["checkpoint"],
            checkpoint_sha256=inputs["checkpoint_sha"],
            capture_receipt=inputs["receipt"],
            capture_receipt_sha256=inputs["receipt_sha"],
            checkpoint_verified=inputs["verified"],
            checkpoint_verified_sha256=inputs["verified_sha"],
            checkpoint_helper=inputs["helper"],
            checkpoint_helper_sha256=inputs["helper_sha"],
            bases=inputs["bases"],
            output=inputs["output"],
        ),
    )


def refresh_checkpoint_pins(inputs: dict[str, Any]) -> None:
    checkpoint = inputs["checkpoint"]
    manifest = inputs["manifest"]
    for name in manifest["files"]:
        path = checkpoint / name
        content = path.read_bytes()
        manifest["files"][name] = {
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest_path = checkpoint / "checkpoint.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    inputs["checkpoint_sha"] = digest(manifest_path)
    summary = json.loads(inputs["verified"].read_bytes())
    summary["manifest_sha256"] = inputs["checkpoint_sha"]
    summary["files"] = len(manifest["files"])
    summary["bytes"] = sum(int(record["size"]) for record in manifest["files"].values())
    inputs["verified"].write_text(json.dumps(summary, sort_keys=True))
    inputs["verified_sha"] = digest(inputs["verified"])
    receipt = json.loads(inputs["receipt"].read_bytes())
    receipt["checkpoint"] = summary
    inputs["receipt"].write_text(json.dumps(receipt, sort_keys=True))
    inputs["receipt_sha"] = digest(inputs["receipt"])


def test_packet_binds_fresh_checkpoint_ack_and_top_level_sidecars(
    setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    before = digest(OLD_PACKET / "packet.json")
    packet = build_packet(setup)
    assert packet["checkpoint"] == str(setup["checkpoint"])
    assert packet["checkpoint_sha256"] == setup["checkpoint_sha"]
    assert packet["archive_commit"] == "a" * 40
    assert packet["predecessor_schema"] == 28
    assert packet["predecessor_tables"] == 116
    assert packet["paid_archive_usage"] == {
        "day": "2026-09-17",
        "requests": 20,
        "bytes": 2_952_065,
    }
    assert packet["paid_origin_usage"] == {
        "host": "danceconvention.net",
        "day": "2026-09-17",
        "requests": 4,
        "bytes": 132_764,
    }
    assert set(packet["top_level_sidecars"]) == {
        "operator-hold",
        "dcn-origin-event-days.json",
        "dcn-origin-robots-cache.json",
    }
    migration = (setup["output"] / "migration.py").read_text()
    assert 'report["top_level_sidecars"] = top_level_sidecars' in migration
    runner_path = setup["output"] / "runner.py"
    spec = importlib.util.spec_from_file_location("fresh_packet_runner", runner_path)
    assert spec is not None and spec.loader is not None
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    runner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runner
    spec.loader.exec_module(runner)
    packet_sha = digest(setup["output"] / "packet.json")
    assert (
        runner.packet_at(setup["output"], packet_sha)["top_level_sidecars"]
        == packet["top_level_sidecars"]
    )
    second_inputs = dict(setup)
    second_inputs["output"] = setup["output"].parent / "packet-rebuilt"
    build_packet(second_inputs)
    first_files = {path.name: digest(path) for path in setup["output"].iterdir()}
    second_files = {path.name: digest(path) for path in second_inputs["output"].iterdir()}
    assert first_files == second_files
    sidecars_path = setup["output"] / "checkpoint-sidecars.json"
    sidecars_original = sidecars_path.read_bytes()
    sidecars_path.write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="packet helper differs"):
        runner.packet_at(setup["output"], packet_sha)
    sidecars_path.write_bytes(sidecars_original)
    (setup["output"] / "unexpected").write_text("not in packet closure")
    with pytest.raises(ValueError, match="packet has extra files"):
        runner.packet_at(setup["output"], packet_sha)
    assert digest(OLD_PACKET / "packet.json") == before == OLD_PACKET_SHA256


@pytest.mark.parametrize("drift", ["schema", "table", "baseline", "pending"])
def test_checkpoint_predecessor_drift_is_rejected(setup: dict[str, Any], drift: str) -> None:
    manifest = setup["manifest"]
    db_path = setup["checkpoint"] / "state.sqlite"
    if drift in {"schema", "table"}:
        with sqlite3.connect(db_path) as conn:
            if drift == "schema":
                conn.execute("PRAGMA user_version=27")
            else:
                conn.execute("CREATE TABLE extra_predecessor (id INTEGER)")
        manifest["files"]["state.sqlite"] = {
            "size": db_path.stat().st_size,
            "sha256": digest(db_path),
        }
    elif drift == "baseline":
        manifest["baseline_candidate"] = "cand_wrong"
    else:
        manifest["pending_candidate"] = "cand_pending"
    manifest_path = setup["checkpoint"] / "checkpoint.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    setup["checkpoint_sha"] = digest(manifest_path)
    summary = json.loads(setup["verified"].read_bytes())
    summary["manifest_sha256"] = setup["checkpoint_sha"]
    summary["bytes"] = sum(record["size"] for record in manifest["files"].values())
    setup["verified"].write_text(json.dumps(summary, sort_keys=True))
    setup["verified_sha"] = digest(setup["verified"])
    receipt = json.loads(setup["receipt"].read_bytes())
    receipt["checkpoint"] = summary
    setup["receipt"].write_text(json.dumps(receipt, sort_keys=True))
    setup["receipt_sha"] = digest(setup["receipt"])
    with pytest.raises(ValueError, match="schema|population|predecessor"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_packet_refuses_arbitrary_source_and_unpinned_evidence(setup: dict[str, Any]) -> None:
    wrong_source = setup["source"].parent / "other-source"
    wrong_source.mkdir()
    with pytest.raises(ValueError, match="source is not frozen"):
        builder.build(
            **{
                "source": wrong_source,
                "source_receipt_sha256": setup["source_receipt_sha"],
                "runtime_source": setup["runtime_source"],
                "checkpoint": setup["checkpoint"],
                "checkpoint_sha256": setup["checkpoint_sha"],
                "capture_receipt": setup["receipt"],
                "capture_receipt_sha256": setup["receipt_sha"],
                "checkpoint_verified": setup["verified"],
                "checkpoint_verified_sha256": setup["verified_sha"],
                "checkpoint_helper": setup["helper"],
                "checkpoint_helper_sha256": setup["helper_sha"],
                "bases": setup["bases"],
                "output": setup["output"],
            }
        )
    with pytest.raises(ValueError, match="verified summary pin differs"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            "0" * 64,
            setup["helper"],
            setup["helper_sha"],
        )


def test_checkpoint_sidecar_bytes_must_match_pinned_manifest(setup: dict[str, Any]) -> None:
    (setup["checkpoint"] / "dcn-origin-event-days.json").write_bytes(b"altered origin days")
    with pytest.raises(ValueError, match="checkpoint artifact differs"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_stale_checkpoint_paid_archive_usage_is_rejected(setup: dict[str, Any]) -> None:
    with sqlite3.connect(setup["checkpoint"] / "state.sqlite") as conn:
        conn.execute("UPDATE host_budget SET requests=19 WHERE host='web.archive.org'")
    refresh_checkpoint_pins(setup)
    with pytest.raises(ValueError, match="current paid Archive usage"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_stale_checkpoint_paid_origin_usage_is_rejected(setup: dict[str, Any]) -> None:
    with sqlite3.connect(setup["checkpoint"] / "state.sqlite") as conn:
        conn.execute("UPDATE host_budget SET requests=3 WHERE host='danceconvention.net'")
    refresh_checkpoint_pins(setup)
    with pytest.raises(ValueError, match="paid DCN origin usage"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


@pytest.mark.parametrize("missing", sorted(builder.ORIGIN_SIDECARS))
def test_packet_requires_current_origin_sidecars(setup: dict[str, Any], missing: str) -> None:
    path = setup["checkpoint"] / missing
    path.unlink()
    setup["manifest"]["files"].pop(missing)
    manifest_path = setup["checkpoint"] / "checkpoint.json"
    manifest_path.write_text(json.dumps(setup["manifest"], sort_keys=True, separators=(",", ":")))
    setup["checkpoint_sha"] = digest(manifest_path)
    summary = json.loads(setup["verified"].read_bytes())
    summary["manifest_sha256"] = setup["checkpoint_sha"]
    summary["files"] = len(setup["manifest"]["files"])
    summary["bytes"] = sum(record["size"] for record in setup["manifest"]["files"].values())
    setup["verified"].write_text(json.dumps(summary, sort_keys=True))
    setup["verified_sha"] = digest(setup["verified"])
    receipt = json.loads(setup["receipt"].read_bytes())
    receipt["checkpoint"] = summary
    setup["receipt"].write_text(json.dumps(receipt, sort_keys=True))
    setup["receipt_sha"] = digest(setup["receipt"])
    with pytest.raises(ValueError, match="DCN origin sidecars"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_private_acknowledgment_and_published_receipt_shape_are_required(
    setup: dict[str, Any],
) -> None:
    receipt = json.loads(setup["receipt"].read_bytes())
    receipt["private_archive_commit"] = "bad"
    setup["receipt"].write_text(json.dumps(receipt, sort_keys=True))
    setup["receipt_sha"] = digest(setup["receipt"])
    with pytest.raises(ValueError, match="private archive acknowledgment"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_published_candidate_identity_shape_is_required(setup: dict[str, Any]) -> None:
    published_path = setup["checkpoint"] / "candidates" / builder.CANDIDATE / "PUBLISHED"
    published = json.loads(published_path.read_bytes())
    published["candidate_id"] = "cand_wrong"
    published_path.write_text(json.dumps(published, sort_keys=True))
    refresh_checkpoint_pins(setup)
    with pytest.raises(ValueError, match="public baseline differs"):
        builder.validate_checkpoint(
            setup["checkpoint"],
            setup["checkpoint_sha"],
            setup["receipt"],
            setup["receipt_sha"],
            setup["verified"],
            setup["verified_sha"],
            setup["helper"],
            setup["helper_sha"],
        )


def test_exact_base_helper_drift_is_rejected(setup: dict[str, Any], tmp_path: Path) -> None:
    base = (TOOLS / "rehearse_extension_migration.py").read_text() + "\n"
    with pytest.raises(ValueError, match="reviewed base helper differs"):
        builder.derive_helper(
            "migration", base, setup["source_receipt_sha"], setup["checkpoint_sha"], "a" * 40, {}
        )


def test_untrusted_base_is_rejected_before_module_execution(
    setup: dict[str, Any], tmp_path: Path
) -> None:
    bases = tmp_path / "bases"
    bases.mkdir()
    for kind in builder.BASES:
        source = TOOLS / f"rehearse_extension_{kind}.py"
        (bases / source.name).write_bytes(source.read_bytes())
    marker = tmp_path / "untrusted-base-executed"
    (bases / "rehearse_extension_migration.py").write_text(
        f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')\n"
    )
    inputs = dict(setup)
    inputs["bases"] = bases
    with pytest.raises(ValueError, match="reviewed migration base helper differs"):
        build_packet(inputs)
    assert not marker.exists()
