"""Offline independent review of the exact schema28→29 rehearsal packet."""
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
SOURCE = Path("/private/tmp/swingset-extension-freeze-20260917-004")
PACKET = Path("/tmp/swingset-schema29-rehearsals-20260917-dev003")
EXPECTED = "3dae03a682ed2d75ad9ac0d41b264335c062bc15dccd4503b1aa7b34c41f9ecf"
HELPER = "journal/tools/runtime/prepare_schema29_rehearsals.py"
TESTS = ["tests/test_schema29_rehearsal_packet.py", "tests/test_extension_input_rehearsal.py", "tests/test_extension_restore_rehearsal.py"]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def pins():
    names = [HELPER, *TESTS, *("journal/tools/runtime/rehearse_extension_" + kind + ".py" for kind in ("migration", "restore", "inputs"))]
    return {name: sha(ROOT / name) for name in names}

def inventory(path):
    assert not any(p.is_symlink() for p in path.rglob("*"))
    return {p.relative_to(path).as_posix(): sha(p) for p in path.rglob("*") if p.is_file()}

env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(SOURCE / "src") + ":.", MYPYPATH=str(SOURCE / "src"))
report = dict(started_at=datetime.now(UTC).isoformat(), source=str(SOURCE), source_receipt_sha256=sha(SOURCE / "extension-source.json"), before=pins(), packet=str(PACKET), packet_sha256=sha(PACKET / "packet.json"), passed=False, production_operations=0, network_requests=0)
assert report["packet_sha256"] == EXPECTED
packet_before = inventory(PACKET)
checks = {}
commands = {
    "pytest": [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-c", "/dev/null", "-p", "no:cacheprovider", "-q", *TESTS],
    "ruff": [str(ROOT / ".venv/bin/ruff"), "check", HELPER, TESTS[0]],
    "mypy": [str(ROOT / ".venv/bin/mypy"), HELPER],
}
for name, command in commands.items():
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240)
    (OUT / (name + ".log")).write_text(result.stdout)
    checks[name] = dict(command=command, exit_code=result.returncode, log_sha256=sha(OUT / (name + ".log")))
    assert result.returncode == 0, result.stdout
with tempfile.TemporaryDirectory(prefix="swingset-independent-schema29-") as temporary:
    rebuilt = Path(temporary) / "packet"
    packet = json.loads((PACKET / "packet.json").read_bytes())
    command = [str(ROOT / ".venv/bin/python"), HELPER, "build", "--source", str(SOURCE), "--source-receipt-sha256", packet["source_receipt_sha256"], "--runtime-source", packet["source"], "--bases", "journal/tools/runtime", "--output", str(rebuilt)]
    result = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    (OUT / "rebuild.log").write_text(result.stdout)
    assert result.returncode == 0, result.stdout
    assert inventory(rebuilt) == packet_before
    checks["deterministic_rebuild"] = dict(command=command, exit_code=0, complete_file_inventory=packet_before, passed=True)
report.update(checks=checks, after=pins(), findings_resolved=["Actual schema28 restore increments only the singleton observation epoch; the derived helper now verifies that exact expected delta before comparing the subsequent migration.", "Private archive authority is the sealed passed backup002 receipt and checkpoint helper, with exact head/manifest checks at that receipt date; this review performs no renewed private remote read."], limitations=["Candidate004 full suite acceptance is not established; coordinator reported legacy spacing fixture failures.", "No actual-checkpoint migration, operational restore, input acceptance, deployment or publication performed by this review."])
assert report["before"] == report["after"]
assert inventory(PACKET) == packet_before
report.update(passed=True, finished_at=datetime.now(UTC).isoformat())
(OUT / "checks.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"passed": True, "receipt": str(OUT / "checks.json"), "pytest": (OUT / "pytest.log").read_text().strip()}))
