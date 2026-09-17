from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
PACKET = ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17/packet-001"
OPERATIONS = ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17/operations-001"
RUNNER = ROOT / "journal/tools/admission/dcn_origin_fixture_runner.py"
BUILDER = ROOT / "journal/tools/admission/build_dcn_origin_fixture.py"
TESTS = ROOT / "tests/test_dcn_origin_fixture_runner.py"
CLOSURE_SHA256 = "875f0ef40105a66ad3ef20ec87f88be10e4332b181292c6b76a9b0f705994893"
EXPECTED = {
    RUNNER: "df5f0ea1b4167da0bf63f0d2b75bf99e323b93f910d131dd86dd24a935bc6108",
    BUILDER: "ffda6bc940f534d1cc001f922ecdc785f9aaec04ff0e888048379938d7279c64",
    TESTS: "8584fbd7abbf0b47ee2545bbe2d563de3af5668f1ab55f81225a0d532f3af560",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from journal.tools.admission.dcn_origin_fixture_runner import verify_packet

    for path, expected in EXPECTED.items():
        actual = digest(path)
        if actual != expected:
            raise SystemExit(f"source pin changed for {path}: {actual}")

    manifest = verify_packet(PACKET, CLOSURE_SHA256)
    receipt = json.loads((PACKET / "build-receipt.json").read_bytes())
    closure = json.loads((PACKET / "closure.json").read_bytes())
    if digest(PACKET / "dcn_origin_fixture_runner.py") != EXPECTED[RUNNER]:
        raise SystemExit("packet runner differs from the reviewed source bytes")
    if (
        manifest["reference_runtime"]["source"]
        != "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
        or manifest["reference_runtime"]["schema"] != 28
        or manifest["reference_runtime"]["published_commit"]
        != "2a6c7dc744fb36eabb5163c0a527d787d3721f4f"
        or manifest["reference_runtime"]["acknowledged_candidate"]
        != "cand_8f31cad7226643ae"
        or receipt["source_file_count"] != 2221
        or receipt["network_requests"] != 0
        or receipt["executed"] is not False
    ):
        raise SystemExit("packet manifest or build receipt differs from the reviewed scope")

    gate_path = OPERATIONS / "gate.json"
    auth_path = OPERATIONS / "authorization.json"
    prestate_path = OPERATIONS / "pre-state.json"
    preflight_path = OPERATIONS / "preflight-002.json"
    gate = json.loads(gate_path.read_bytes())
    authorization = json.loads(auth_path.read_bytes())
    prestate = json.loads(prestate_path.read_bytes())
    preflight = json.loads(preflight_path.read_bytes())
    expected_units = [
        "swingset-cycle.service",
        "swingset-cycle.timer",
        "swingset-backup.service",
        "swingset-backup.timer",
        "swingset-summary.service",
        "swingset-summary.timer",
    ]
    if (
        gate["packet_closure_sha256"] != CLOSURE_SHA256
        or gate["authorization_sha256"] != digest(auth_path)
        or gate["source"] != manifest["reference_runtime"]["source"]
        or gate["schema_version"] != 28
        or gate["system"] != prestate["active_system"]
        or gate["system"] != prestate["persistent_system"]
        or gate["inactive_units"] != expected_units
        or any(prestate["units"].get(unit) != "inactive" for unit in expected_units)
        or prestate["read_only"] is not True
        or prestate["source_requests"] != 0
        or prestate["schema"] != 28
        or prestate["control_revision"] != gate["control_revision"]
        or prestate["unsettled_admissions"]
        or preflight["exit_code"] != 0
        or preflight["execution"] is not False
        or CLOSURE_SHA256 not in preflight["stdout"]
    ):
        raise SystemExit("operation gate, pre-state or dry-preflight evidence differs")
    if authorization["packet_closure_sha256"] != CLOSURE_SHA256:
        raise SystemExit("authorization packet closure differs")

    environment = {**__import__("os").environ, "PYTHONPATH": str(ROOT)}
    test = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-c", "/dev/null", "-p", "no:cacheprovider", "-q", str(TESTS)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    lint = subprocess.run(
        [str(ROOT / ".venv/bin/ruff"), "check", str(RUNNER), str(BUILDER), str(TESTS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if test.returncode or "37 passed" not in test.stdout or lint.returncode:
        raise SystemExit("focused validation changed or failed")

    review = {
        "format": "dcn-origin-runner-independent-review-v1",
        "reviewed_at": datetime.now(UTC).isoformat(),
        "verdict": "pass_for_coordinator_operation_after_preflight",
        "implementation_status": "implemented",
        "test_status": "tested",
        "deployment_status": "not_deployed",
        "publication_status": "not_published",
        "http_requests_by_reviewer": 0,
        "production_writes_by_reviewer": 0,
        "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in EXPECTED},
        "packet": {
            "path": str(PACKET.relative_to(ROOT)),
            "closure_sha256": digest(PACKET / "closure.json"),
            "runner_sha256": digest(PACKET / "dcn_origin_fixture_runner.py"),
            "build_receipt_sha256": digest(PACKET / "build-receipt.json"),
            "closure_member_count": len(closure["files"]),
            "source_file_count": receipt["source_file_count"],
            "network_requests": receipt["network_requests"],
            "executed": receipt["executed"],
            "local_closure_verification": "passed",
            "independent_vm_rebuild": "not_performed",
        },
        "operation_evidence": {
            "gate_sha256": digest(gate_path),
            "authorization_sha256": digest(auth_path),
            "pre_state_sha256": digest(prestate_path),
            "preflight_sha256": digest(preflight_path),
            "preflight_exit_code": preflight["exit_code"],
            "preflight_execution": preflight["execution"],
            "preflight_time": preflight["checked_at"],
        },
        "validation": {
            "pytest_command": "PYTHONPATH=. .venv/bin/python -m pytest -c /dev/null -p no:cacheprovider -q tests/test_dcn_origin_fixture_runner.py",
            "pytest_exit_code": test.returncode,
            "pytest_stdout": test.stdout.strip(),
            "ruff_command": ".venv/bin/ruff check journal/tools/admission/dcn_origin_fixture_runner.py journal/tools/admission/build_dcn_origin_fixture.py tests/test_dcn_origin_fixture_runner.py",
            "ruff_exit_code": lint.returncode,
            "ruff_stdout": lint.stdout.strip(),
        },
        "review_notes": [
            "The fixture remains quarantined; no watches or production interpretations are created.",
            "The exact VM CLI preflight passed with execution=false; no origin request has yet been issued.",
            "The reviewer verified the exact packet closure locally; an independent VM rebuild was not available from this reviewer context.",
            "The runtime guard checks the pinned active and persistent system, six inactive units, and hold marker at CLI preflight and before each request.",
            "Fresh source-year acceptance is not asserted by this origin fixture operation.",
        ],
    }
    output = Path(__file__).with_name("review.json")
    output.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n")
    print(output.relative_to(ROOT))
    print(json.dumps(review, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
