"""Build the separately approved exact Riga HTML packet; no authorization or HTTP.

Derive from the retained, independently reviewed schema28 metadata runner. Keep
its accounting and transport controls, replace its exact plan, and seal all bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from journal.tools.admission.build_dcn_results_lookup import canonical, digest, sub, verified_source

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
SOURCE_SHA = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
BASE = ROOT / "journal/evidence/admission/dcn-results-lookup-2026-09-17/packet"
BASE_SHA = "645d5d364c2ac261ec847cc47c44818e6afe4ff165d12d054a106938a13cf97d"
PROPOSAL = ROOT / "journal/evidence/admission/dcn-results-body-proposal-2026-09-17/proposal.json"
PROPOSAL_SHA = "17dea52f53b41b04093f4582a94da0f58afbe808408304f9681e2f3750575483"
DRIVER_NAME = "dcn-results-body-h13-001.py"
APPROVAL = "approved_exact_riga_results_html_only"


def build(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    if destination.exists():
        raise ValueError("packet must be new")
    verified_source(source)
    if (
        digest((BASE / "build-receipt.json").read_bytes()) != BASE_SHA
        or digest(PROPOSAL.read_bytes()) != PROPOSAL_SHA
    ):
        raise ValueError("reviewed base or exact proposal differs")
    old = json.loads((BASE / "build-receipt.json").read_bytes())
    for name, expected in old["files"].items():
        path = BASE / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.resolve().is_relative_to(BASE.resolve())
            or digest(path.read_bytes()) != expected
        ):
            raise ValueError("retained base bytes differ: " + name)
    proposal = json.loads(PROPOSAL.read_bytes())
    evidence = proposal["locator_evidence"]
    evidence_body = (ROOT / evidence["path"]).read_bytes()
    if (
        digest(evidence_body) != evidence["sha256"]
        or digest((ROOT / evidence["independent_review"]).read_bytes())
        != evidence["independent_review_sha256"]
    ):
        raise ValueError("independently reviewed locator evidence differs")
    target = proposal["target"]
    row = [
        target["capture"],
        target["original_url"],
        target["cdx_digest"],
        target["cdx_mimetype"],
        str(target["cdx_length_metadata"]),
    ]
    if row not in json.loads(evidence_body):
        raise ValueError("exact capture not in reviewed metadata")
    manifest = dict(
        version=1,
        approval_reference="journal/decisions/0073-approve-exact-riga-results-html-fixture.md",
        proposal_sha256=PROPOSAL_SHA,
        limits=dict(
            archived_body_targets=1,
            total_http_requests_including_redirects_and_robots=2,
            max_archive_redirects_per_body=0,
            cdx_requests=0,
            max_elapsed_seconds=900,
            max_received_bytes_per_response=2097152,
            max_total_received_bytes=4194304,
            minimum_gap_seconds=10,
            max_in_flight=1,
            shared_archive_daily_request_budget=200,
            source_origin_requests=0,
            pdf_requests=0,
            automatic_retries=0,
            automatic_alternate_captures=0,
            automatic_child_requests=0,
        ),
        targets=[
            dict(
                id="dcn-riga-results-20190719204919",
                source="dcn",
                original_url=target["original_url"],
                replay_url=target["archive_url"],
                capture_timestamp=target["capture"],
                evidence=dict(path="evidence/cdx-page0.json", sha256=evidence["sha256"], row=row),
            )
        ],
        metadata_queries=[],
        quarantine="Separate single-use HTML quarantine; no production observations, watches, interpretation or activation.",
    )
    manifest_sha = digest(canonical(manifest))
    helpers = BASE / "helper-closure"
    exception = (helpers / "fixture_helpers/fixture_exception.py").read_text()
    exception = sub(exception, old["manifest_canonical_sha256"], manifest_sha)
    exception = sub(exception, "approved_exact_dcn_results_cdx_only", APPROVAL)
    exception = sub(exception, "from swingset.history.catalog import retained_evidence_path\n", "")
    exception = sub(
        exception,
        "def read_manifest(",
        """def retained_evidence_path(repository: Path, relative: str) -> Path:
    base = Path(__file__).resolve().parents[1]
    candidate = (base / relative).resolve(strict=True)
    if not candidate.is_relative_to(base):
        raise FixtureStopped("retained evidence escapes helper closure")
    return candidate


def read_manifest(""",
    )
    transport = (helpers / "fixture_helpers/fixture_transport.py").read_text()
    transport = sub(
        transport,
        "from datetime import datetime, timedelta",
        "from datetime import UTC, datetime, timedelta",
    )
    transport = sub(transport, '        query = manifest["metadata_queries"][0]\n', "")
    transport = sub(
        transport,
        '[query["probe_url"], query["page0_url"], f"https://{HOST}/robots.txt"]',
        '[f"https://{HOST}/robots.txt"]',
    )
    start = transport.index('                query = self.manifest["metadata_queries"][0]\n')
    end = transport.index(
        '                self.receipt["status"] = "captured_pending_independent_review"', start
    )
    transport = transport[:start] + transport[end:]
    transport = sub(
        transport,
        '                    self.receipt["targets"].append(',
        """                    if not response.headers.get("memento-datetime"):
                        raise FixtureStopped("response lacks independent Memento capture time")
                    observed_capture = captured_at(dict(response.headers), str(response.url))
                    expected_capture = datetime.strptime(target["capture_timestamp"], "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat()
                    if observed_capture != expected_capture:
                        raise FixtureStopped("response capture differs from exact approved capture")
                    self.receipt["targets"].append(""",
    )
    transport = sub(
        transport,
        '"captured_at": captured_at(dict(response.headers), str(response.url)),',
        '"captured_at": observed_capture,',
    )
    # Captured bytes stay in quarantine even on mismatch. No retry or alternative.
    files = {
        "fixture_helpers/__init__.py": b"",
        "fixture_helpers/fixture_exception.py": exception.encode(),
        "fixture_helpers/fixture_transport.py": transport.encode(),
        "proposal.json": canonical(manifest),
        "source-proposal.json": PROPOSAL.read_bytes(),
        "evidence/cdx-page0.json": evidence_body,
    }
    closure = canonical(
        dict(
            format="fixture-helper-closure-v1",
            files={name: digest(body) for name, body in sorted(files.items())},
        )
    )
    driver = (BASE / "dcn-results-lookup-h13-001.py").read_text()
    driver = sub(driver, old["helper_manifest_sha256"], digest(closure))
    driver = sub(
        driver,
        "H13 schema28 adapter for one separately authorized metadata-only lookup.",
        "H13 schema28 adapter for one separately authorized exact Riga HTML body.",
    )
    driver = sub(
        driver,
        "Exact CDX allowlist, no body requests or activation.",
        "Exact body allowlist, no CDX requests or activation.",
    )
    prepare = (BASE / "prepare_fixture_exception.py").read_text()
    for before, after in [
        (old["driver_sha256"], digest(driver.encode())),
        (old["helper_manifest_sha256"], digest(closure)),
        (old["manifest_canonical_sha256"], manifest_sha),
        ("dcn-results-lookup-h13-001.py", DRIVER_NAME),
        ("approved_exact_dcn_results_cdx_only", APPROVAL),
    ]:
        prepare = sub(prepare, before, after)
    outputs = {
        **{f"helper-closure/{name}": body for name, body in files.items()},
        "helper-closure/closure.json": closure,
        DRIVER_NAME: driver.encode(),
        "prepare_fixture_exception.py": prepare.encode(),
    }
    destination.mkdir(parents=True)
    for name, body in outputs.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    verified_source(source)
    report = dict(
        format="exact-dcn-results-body-build-v1",
        builder_sha256=digest(Path(__file__).read_bytes()),
        builder_dependency_sha256=digest(
            Path(__file__).with_name("build_dcn_results_lookup.py").read_bytes()
        ),
        runtime_source=SOURCE,
        runtime_receipt_sha256=SOURCE_SHA,
        base_packet_receipt_sha256=BASE_SHA,
        proposal_sha256=PROPOSAL_SHA,
        manifest_canonical_sha256=manifest_sha,
        driver_sha256=digest(driver.encode()),
        helper_manifest_sha256=digest(closure),
        files={name: digest(body) for name, body in sorted(outputs.items())},
        network_requests=0,
        executed=False,
        owner_approval_reference=manifest["approval_reference"],
    )
    (destination / "build-receipt.json").write_bytes(canonical(report) + b"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination), indent=2))
