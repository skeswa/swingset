"""Build a source-bound, exact-scope DCN origin fixture packet without I/O."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

runner = importlib.import_module("journal.tools.admission.dcn_origin_fixture_runner")

SOURCE = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
SOURCE_RECEIPT = "extension-source.json"
SOURCE_RECEIPT_SHA256 = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
PROPOSAL = (
    ROOT / "journal/evidence/admission/dcn-origin-score-pdf-proposal-2026-09-17/proposal.json"
)
PROPOSAL_SHA256 = "81ebe0dd06c1a748431cb8481f6b4ccfde303d4b5c8e09c1995751218c980bdc"
AUTHORITY = ROOT / "journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md"
SCOPE_DECISION = (
    ROOT / "journal/decisions/0088-scope-origin-score-pdf-controls-after-empty-archive-lookups.md"
)
POLICY_DECISION = ROOT / "journal/decisions/0090-bind-an-explicit-quarantine-source-policy.md"
RUNNER_NAME = "dcn_origin_fixture_runner.py"


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_source(source: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    if source != SOURCE:
        raise ValueError("the operation is bound to the deployed source 003 path")
    receipt_path = source / SOURCE_RECEIPT
    if digest(receipt_path.read_bytes()) != SOURCE_RECEIPT_SHA256:
        raise ValueError("source 003 receipt differs")
    receipt = json.loads(receipt_path.read_bytes())
    inventory = receipt.get("files")
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("source 003 inventory is absent")
    files = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    files.discard(SOURCE_RECEIPT)
    files = {name for name in files if "__pycache__" not in Path(name).parts}
    if files != set(inventory):
        raise ValueError("source 003 inventory is incomplete or has extra files")
    for name, value in inventory.items():
        relative = Path(name)
        path = source / relative
        expected = value["sha256"] if isinstance(value, dict) else value
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path.is_symlink()
            or digest(path.read_bytes()) != expected
        ):
            raise ValueError("source 003 file differs: " + name)
    if not (source / "src/swingset/state/migrations/0028_request_spacing.sql").is_file():
        raise ValueError("source 003 has no schema-28 request-spacing migration")
    return receipt


def _retained_files(proposal: dict[str, Any]) -> dict[str, bytes]:
    locator = proposal["locator_evidence"]
    archive = proposal["archive_first_evidence"]
    retained: dict[str, bytes] = {}
    for packet_name, relative, expected in (
        ("evidence/riga-results.html", locator["html_path"], locator["html_sha256"]),
        ("evidence/locator-context.json", locator["context_path"], locator["context_sha256"]),
        ("evidence/archive-receipt.json", archive["receipt_path"], archive["receipt_sha256"]),
        (
            "evidence/archive-independent-review.json",
            archive["independent_review_path"],
            archive["independent_review_sha256"],
        ),
    ):
        body = (ROOT / relative).read_bytes()
        if digest(body) != expected:
            raise ValueError("retained scope evidence differs: " + relative)
        retained[packet_name] = body
    if not json.loads(retained["evidence/archive-independent-review.json"])["passed"]:
        raise ValueError("exact Archive metadata review has not passed")
    html = retained["evidence/riga-results.html"]
    for target in proposal["targets"]:
        if target["url"] not in {
            "https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf",
            "https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf",
        }:
            raise ValueError("proposal contains an unapproved URL")
        if target["printed_href"].encode() not in html:
            raise ValueError("source HTML does not print the exact PDF locator")
    return retained


def build(source: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValueError("packet destination must be new")
    receipt = verify_source(source)
    if digest(PROPOSAL.read_bytes()) != PROPOSAL_SHA256:
        raise ValueError("exact origin proposal differs")
    proposal = json.loads(PROPOSAL.read_bytes())
    for path, expected in (
        (AUTHORITY, "84ad7a85d2a260b1ffab9ad07bbfe52ce04dbc2b2f0bee687ba51c05c566f498"),
        (SCOPE_DECISION, "f7848707c86c5a4482946312ae8869d9e1291216e42cce15e191360f14f4bdf6"),
        (POLICY_DECISION, "b2955d83d9ef5d4e1df506c2721259267c02f8c1a883bfccdac172210a47ddd8"),
    ):
        if digest(path.read_bytes()) != expected:
            raise ValueError("authority or policy decision changed: " + path.name)
    manifest = {
        "format": "dcn-origin-fixture-manifest-v1",
        "scope_proposal_sha256": PROPOSAL_SHA256,
        "event_id": "dcn:1546230",
        "contest_id": "2196606",
        "source": "dcn",
        "targets": proposal["targets"],
        "robots": proposal["robots"],
        "limits": proposal["limits"],
        "source_policy": {
            "ordinary_missing_entry_remains_missing": True,
            "fixture_operation_source_enabled": True,
            "ordinary_explicit_disabled_is_interlock": True,
            "policy_decision": "journal/decisions/0090-bind-an-explicit-quarantine-source-policy.md",
        },
        "reference_runtime": {
            "source": str(SOURCE),
            "source_receipt": SOURCE_RECEIPT,
            "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
            "schema": 28,
            "published_commit": runner.PUBLISHED_BASELINE,
            "acknowledged_candidate": runner.ACKNOWLEDGED_CANDIDATE,
        },
        "ordinary_config_sha256": digest((source.resolve() / "config/sources.toml").read_bytes()),
    }
    files = {
        RUNNER_NAME: Path(runner.__file__).read_bytes(),
        "scope-proposal.json": PROPOSAL.read_bytes(),
        "manifest.json": canonical(manifest) + b"\n",
        "authority/D-0087.md": AUTHORITY.read_bytes(),
        "authority/D-0088.md": SCOPE_DECISION.read_bytes(),
        "authority/D-0090.md": POLICY_DECISION.read_bytes(),
        "source/extension-source.json": (source.resolve() / SOURCE_RECEIPT).read_bytes(),
        **_retained_files(proposal),
    }
    closure = canonical(
        {
            "format": "dcn-origin-fixture-closure-v1",
            "files": {name: digest(body) for name, body in sorted(files.items())},
        }
    )
    output.mkdir(parents=True)
    for name, body in {**files, "closure.json": closure}.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    report = {
        "format": "dcn-origin-fixture-build-v1",
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "runner_sha256": digest(files[RUNNER_NAME]),
        "closure_sha256": digest(closure),
        "source": str(source.resolve()),
        "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
        "source_file_count": len(receipt["files"]),
        "scope_proposal_sha256": PROPOSAL_SHA256,
        "network_requests": 0,
        "executed": False,
        "files": {name: digest(body) for name, body in sorted(files.items())},
    }
    (output / "build-receipt.json").write_bytes(canonical(report) + b"\n")
    verify_packet(output)
    return report


def verify_packet(packet: Path, *, expected_closure_sha256: str | None = None) -> dict[str, Any]:
    if packet.is_symlink():
        raise ValueError("packet root cannot be a symlink")
    packet = packet.resolve(strict=True)
    closure_path = packet / "closure.json"
    closure_body = closure_path.read_bytes()
    closure = json.loads(closure_body)
    if closure.get("format") != "dcn-origin-fixture-closure-v1":
        raise ValueError("unknown fixture closure format")
    if expected_closure_sha256 is not None and digest(closure_body) != expected_closure_sha256:
        raise ValueError("reviewed closure digest differs")
    actual_paths = {
        path.relative_to(packet).as_posix() for path in packet.rglob("*") if path.is_file()
    }
    expected_paths = set(closure["files"]) | {"closure.json", "build-receipt.json"}
    if actual_paths != expected_paths or any(path.is_symlink() for path in packet.rglob("*")):
        raise ValueError("packet has an undeclared, missing or linked file")
    for name, expected in closure["files"].items():
        if digest((packet / name).read_bytes()) != expected:
            raise ValueError("packet file differs: " + name)
    report = json.loads((packet / "build-receipt.json").read_bytes())
    if (
        report.get("closure_sha256") != digest(closure_body)
        or report.get("files") != closure["files"]
    ):
        raise ValueError("packet build receipt differs")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))
