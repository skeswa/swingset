"""Build the exact two-URL score-PDF metadata packet authorized by D-0087.

Build is offline. Execution remains coordinator-owned after independent packet
review. The fixed request plan contains CDX metadata only, never PDF requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from journal.tools.admission.build_dcn_results_lookup import (
    SOURCE,
    SOURCE_SHA,
    canonical,
    sub,
    verified_source,
)

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "journal/evidence/admission/dcn-results-lookup-2026-09-17/packet"
BASE_SHA = "645d5d364c2ac261ec847cc47c44818e6afe4ff165d12d054a106938a13cf97d"
PROPOSAL = (
    ROOT / "journal/evidence/admission/dcn-score-pdf-lookup-proposal-2026-09-17/proposal.json"
)
PROPOSAL_SHA = "806f9450d2d487fcbb8fc09ec4966cb685eee16099f1dd80c16757ca2b2aa18e"
AUTHORITY = "journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md"
DRIVER_NAME = "dcn-score-pdf-lookup-h13-001.py"
APPROVAL = "approved_exact_riga_score_pdf_cdx_under_d0087"


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def build(source: Path, destination: Path) -> dict[str, object]:
    if destination.exists() or destination.is_symlink():
        raise ValueError("packet must be new")
    verified_source(source.resolve(strict=True))
    if (
        digest((BASE / "build-receipt.json").read_bytes()) != BASE_SHA
        or digest(PROPOSAL.read_bytes()) != PROPOSAL_SHA
    ):
        raise ValueError("retained base packet or exact proposal differs")
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
    evidence_files = {
        "evidence/riga-results.html": (evidence["path"], evidence["sha256"]),
        "evidence/acquisition-audit.json": (
            evidence["independent_review"],
            evidence["independent_review_sha256"],
        ),
        "evidence/acquisition-receipt.json": (
            evidence["acquisition_receipt"],
            evidence["acquisition_receipt_sha256"],
        ),
        "evidence/locator-context.json": (evidence["context_path"], evidence["context_sha256"]),
    }
    retained = {}
    for name, (relative, expected) in evidence_files.items():
        body = (ROOT / relative).read_bytes()
        if digest(body) != expected:
            raise ValueError("retained locator evidence differs: " + relative)
        retained[name] = body
    if not json.loads(retained["evidence/acquisition-audit.json"])["passed"]:
        raise ValueError("acquisition audit did not pass")
    queries = proposal["metadata_queries"]
    if len(queries) != 2 or [q["printed_href"] for q in queries] != [
        "/eventdirector/en/roundscores/3451330.pdf",
        "/eventdirector/en/roundscores/3451331.pdf",
    ]:
        raise ValueError("exact printed PDF locator scope differs")
    if any(
        q["printed_href"].encode() not in retained["evidence/riga-results.html"] for q in queries
    ):
        raise ValueError("HTML does not print exact PDF locator")
    authority = (ROOT / AUTHORITY).read_bytes()
    if b"Status: Accepted" not in authority or b"remaining v2 acquisition" not in authority:
        raise ValueError("standing authority record differs")
    manifest = dict(
        version=1,
        approval_reference=AUTHORITY,
        authority_sha256=digest(authority),
        proposal_sha256=PROPOSAL_SHA,
        limits=dict(
            archived_body_targets=0,
            total_http_requests_including_redirects_and_robots=5,
            max_archive_redirects_per_body=0,
            cdx_requests=4,
            max_elapsed_seconds=900,
            max_received_bytes_per_response=2097152,
            max_total_received_bytes=8388608,
            minimum_gap_seconds=10,
            max_in_flight=1,
            shared_archive_daily_request_budget=200,
            source_origin_requests=0,
            pdf_requests=0,
            automatic_retries=0,
            automatic_alternate_captures=0,
            automatic_child_requests=0,
        ),
        targets=[],
        metadata_queries=queries,
        quarantine="Separate single-use metadata quarantine; no PDF, body, production facts, watches or activation.",
    )
    manifest_sha = digest(canonical(manifest))
    helpers = BASE / "helper-closure/fixture_helpers"
    exception = (helpers / "fixture_exception.py").read_text()
    exception = sub(exception, old["manifest_canonical_sha256"], manifest_sha)
    exception = sub(exception, "approved_exact_dcn_results_cdx_only", APPROVAL)
    exception = sub(
        exception,
        '        "approval": APPROVAL,',
        f'        "approval": APPROVAL,\n        "owner_decision_reference": "{AUTHORITY}",',
    )
    transport = (helpers / "fixture_transport.py").read_text()
    transport = sub(transport, '        query = manifest["metadata_queries"][0]\n', "")
    transport = sub(
        transport,
        '[query["probe_url"], query["page0_url"], f"https://{HOST}/robots.txt"]',
        '[query[key] for query in manifest["metadata_queries"] for key in ("probe_url", "page0_url")]\n            + [f"https://{HOST}/robots.txt"]',
    )
    start = transport.index('                query = self.manifest["metadata_queries"][0]\n')
    end = transport.index(
        '                self.receipt["status"] = "captured_pending_independent_review"', start
    )
    transport = (
        transport[:start]
        + """                self.receipt["metadata_results"] = []
                for query in self.manifest["metadata_queries"]:
                    self.source = query["source"]
                    row = {"id": query["id"], "original_url": query["original_url"], "status": "pending"}
                    self.receipt["metadata_results"].append(row)
                    self._save()
                    probe = self._request(client, query["probe_url"], "cdx")
                    row["probe_sha256"] = digest(probe.content)
                    payload = json.loads(probe.content)
                    pages = payload.get("pages") if isinstance(payload, dict) else payload
                    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 0:
                        raise FixtureStopped("unknown CDX probe shape")
                    row["pages_reported"] = pages
                    row["further_pages_unexamined"] = pages
                    row["status"] = "probe_captured"
                    self._save()
                    if pages:
                        page = self._request(client, query["page0_url"], "cdx")
                        row["page0_sha256"] = digest(page.content)
                        row["further_pages_unexamined"] = max(0, pages - 1)
                    row["status"] = "captured_pending_independent_review"
                    self._save()
"""
        + transport[end:]
    )
    files = {
        "fixture_helpers/__init__.py": b"",
        "fixture_helpers/fixture_exception.py": exception.encode(),
        "fixture_helpers/fixture_transport.py": transport.encode(),
        "proposal.json": canonical(manifest),
        "source-proposal.json": PROPOSAL.read_bytes(),
        "authority.md": authority,
        **retained,
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
        "one separately authorized metadata-only lookup.",
        "two exact score-PDF metadata lookups under standing D-0087 authority.",
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
    verified_source(source.resolve(strict=True))
    report = dict(
        format="exact-dcn-score-pdf-metadata-build-v1",
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
        owner_approval_reference=AUTHORITY,
        authority_sha256=digest(authority),
    )
    (destination / "build-receipt.json").write_bytes(canonical(report) + b"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination), indent=2))
