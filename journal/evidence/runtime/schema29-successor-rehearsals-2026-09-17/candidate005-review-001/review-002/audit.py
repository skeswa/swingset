"""Independent candidate005 review of sealed schema29 successor packet003."""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
SOURCE = Path("/private/tmp/swingset-extension-freeze-20260917-005")
PACKET = ROOT / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-003"
EXPECTED = "6748af985bbe7b6a74c30c094c72cc117fd6f259a661fb5aeb052e51e309d230"
HELPER = "journal/tools/runtime/prepare_schema29_rehearsals.py"
VM_PYTHON = "/nix/store/s5rij9y3vb4gxbh0iqz3k0ndmsac7h02-python3-3.12.13/bin/python3.12"
TESTS = [
    "tests/test_schema29_rehearsal_packet.py",
    "tests/test_extension_input_rehearsal.py",
    "tests/test_extension_restore_rehearsal.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pins() -> dict[str, str]:
    names = [
        HELPER,
        *TESTS,
        *(
            "journal/tools/runtime/rehearse_extension_" + kind + ".py"
            for kind in ("migration", "restore", "inputs")
        ),
    ]
    return {name: sha(ROOT / name) for name in names}


def inventory(path: Path) -> dict[str, str]:
    assert not any(p.is_symlink() for p in path.rglob("*"))
    return {
        p.relative_to(path).as_posix(): sha(p)
        for p in path.rglob("*")
        if p.is_file()
    }


env = dict(
    os.environ,
    PYTHONDONTWRITEBYTECODE="1",
    PYTHONPATH=str(SOURCE / "src") + ":.",
    MYPYPATH=str(SOURCE / "src"),
)
packet_before = inventory(PACKET)
report = dict(
    started_at=datetime.now(UTC).isoformat(),
    source=str(SOURCE),
    source_receipt_sha256=sha(SOURCE / "extension-source.json"),
    before=pins(),
    packet=str(PACKET),
    packet_sha256=sha(PACKET / "packet.json"),
    expected_packet_sha256=EXPECTED,
    passed=False,
    production_operations=0,
    network_requests=0,
)
assert report["packet_sha256"] == EXPECTED
packet = json.loads((PACKET / "packet.json").read_bytes())
assert packet["source"] == "/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source"
assert packet["source_receipt_sha256"] == report["source_receipt_sha256"]
assert packet["predecessor_schema"] == 28 and packet["target_schema"] == 29
assert packet["predecessor_tables"] == 116
checks = {}
commands = {
    "pytest": [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "pytest",
        "-c",
        "/dev/null",
        "-p",
        "no:cacheprovider",
        "-q",
        *TESTS,
    ],
    "ruff": [str(ROOT / ".venv/bin/ruff"), "check", HELPER, TESTS[0]],
    "mypy": [str(ROOT / ".venv/bin/mypy"), HELPER],
}
for name, command in commands.items():
    result = subprocess.run(
        command,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
    )
    (OUT / (name + ".log")).write_text(result.stdout)
    checks[name] = dict(
        command=command,
        exit_code=result.returncode,
        log_sha256=sha(OUT / (name + ".log")),
    )
    assert result.returncode == 0, result.stdout
rebuilt = OUT / "rebuilt-packet"
assert not rebuilt.exists()
command = [
    "orb",
    "-m",
    "swingset",
    "-u",
    "root",
    VM_PYTHON,
    "/Users/skeswa/repos/skeswa/swingset/" + HELPER,
    "build",
    "--source",
    str(SOURCE),
    "--source-receipt-sha256",
    packet["source_receipt_sha256"],
    "--runtime-source",
    packet["source"],
    "--bases",
    "/Users/skeswa/repos/skeswa/swingset/journal/tools/runtime",
    "--output",
    "/Users/skeswa/repos/skeswa/swingset/" + str(rebuilt.relative_to(ROOT)),
]
result = subprocess.run(
    command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180
)
(OUT / "rebuild.log").write_text(result.stdout)
assert result.returncode == 0, result.stdout
assert inventory(rebuilt) == packet_before
checks["deterministic_rebuild"] = dict(
    command=command,
    exit_code=0,
    complete_file_inventory=packet_before,
    passed=True,
)
report.update(
    checks=checks,
    after=pins(),
    findings=[
        "Packet003 pins the exact candidate005 store source and receipt, schema28 checkpoint identity, private archive acknowledgment, and 116 predecessor tables.",
        "The builder derives restore and migration helpers with schema28 predecessor checks and only the documented singleton pressure epoch increment for restore.",
        "Review commands are offline validation and deterministic packet rebuild only; no production operation or network request was made.",
    ],
    limitations=[
        "This review does not execute actual-checkpoint migration, restore, scratch input replay, deployment, or publication.",
        "Candidate005 full validation remains the separately retained 2536-test receipt.",
    ],
)
assert report["before"] == report["after"]
assert inventory(PACKET) == packet_before
report.update(passed=True, finished_at=datetime.now(UTC).isoformat())
(OUT / "checks.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"passed": True, "checks": checks}))
