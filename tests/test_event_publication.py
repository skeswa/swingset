"""Only acknowledged baseline evidence supplies published event page counts."""

import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from swingset.build.files import sha256_file
from swingset.schedule import event_publication as publication


def candidate(root, name, pages, *, legacy=False, transports=("unknown",)):
    path = root / "candidates" / name
    table = path / "data" / "coverage" / "coverage.parquet"
    table.parent.mkdir(parents=True)
    rows = [
        dict(
            scope_kind="source_event",
            scope_id="eepro:test",
            source="eepro",
            via=via,
            enumeration_id="enum-" + name,
            represented_pages=pages,
            listed_pages=3,
            method="source-event-release-v1",
            evidence_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for via in transports
    ]
    if legacy:
        rows = [dict(source="eepro", year=2026, events=1)]
    pq.write_table(pa.Table.from_pylist(rows), table)
    manifest = path / "_meta" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {
                "files": {"data/coverage/coverage.parquet": sha256_file(table)},
                "release_policy": {
                    "closure": {"digest": "closure-" + name, "cutoff": "2026-01-01T00:00:00Z"}
                },
            }
        )
    )
    (path / "BUILT").write_text(json.dumps({"manifest_hash": sha256_file(manifest)}))
    return path


def promote(root, path):
    (path / "PUBLISHED").write_text(
        json.dumps(
            {
                "commit": "commit-" + path.name,
                "verified_at": "2026-01-02T00:00:00Z",
                "candidate_id": path.name,
                "closure_digest": "closure-" + path.name,
                "evidence_cutoff": "2026-01-01T00:00:00Z",
            }
        )
    )
    baseline = root / "baseline"
    baseline.unlink(missing_ok=True)
    baseline.symlink_to(path)


def view(root, **kwargs):
    return publication.report(root, source="eepro", source_ref="eepro:test", **kwargs)


def test_local_build_never_advances_acknowledged_event_progress(tmp_path):
    first = candidate(tmp_path, "first", 1)
    assert view(tmp_path)["published_pages"] is None
    promote(tmp_path, first)
    assert view(tmp_path)["published_pages"] == 1
    second = candidate(tmp_path, "second", 2)
    assert view(tmp_path)["commit"] == "commit-first"
    assert view(tmp_path)["rows"][0]["enumeration_id"] == "enum-first"
    promote(tmp_path, second)
    current = view(tmp_path)
    assert current["published_pages"] == 2 and current["commit"] == "commit-second"
    json.dumps(current)  # Arrow timestamps do not leak into JSON output.


def test_missing_or_corrupt_receipt_and_artifacts_never_report_zero(tmp_path):
    path = candidate(tmp_path, "first", 1)
    (tmp_path / "baseline").symlink_to(path)
    assert not view(tmp_path)["supported"]
    promote(tmp_path, path)
    (path / "data/coverage/coverage.parquet").write_bytes(b"corrupted")
    result = view(tmp_path)
    assert result["reason"] == "publication_receipt_or_files_unverified"
    assert result["published_pages"] is None and result["rows"] == []


def test_legacy_baseline_and_missing_event_remain_unknown(tmp_path):
    promote(tmp_path, candidate(tmp_path, "legacy", 0, legacy=True))
    assert view(tmp_path)["reason"] == "event_coverage_unavailable"
    promote(tmp_path, candidate(tmp_path, "new", 1))
    other = publication.report(tmp_path, source="eepro", source_ref="eepro:absent")
    assert other["reason"] == "source_event_not_reported" and other["published_pages"] is None


def test_receipt_for_another_candidate_is_not_an_acknowledgment(tmp_path):
    path = candidate(tmp_path, "first", 1)
    promote(tmp_path, path)
    receipt = json.loads((path / "PUBLISHED").read_text())
    receipt["candidate_id"] = "another-candidate"
    (path / "PUBLISHED").write_text(json.dumps(receipt))
    assert view(tmp_path)["reason"] == "publication_receipt_or_files_unverified"


def test_concurrent_promotion_does_not_mix_candidate_and_receipt(tmp_path, monkeypatch):
    first = candidate(tmp_path, "first", 1)
    second = candidate(tmp_path, "second", 2)
    promote(tmp_path, first)
    original = publication.verify_candidate_files

    def verify(path):
        original(path)
        promote(tmp_path, second)

    monkeypatch.setattr(publication, "verify_candidate_files", verify)
    result = view(tmp_path)
    assert result["commit"] == "commit-first" and result["published_pages"] == 1


@pytest.mark.parametrize("transports", [("origin", "archive"), ("origin",), ("archive",)])
def test_overlapping_transport_rows_are_not_summed(tmp_path, transports):
    promote(tmp_path, candidate(tmp_path, "mixed", 1, transports=transports))
    result = view(tmp_path)
    assert result["supported"] and len(result["rows"]) == len(transports)
    assert result["published_pages"] is None


@pytest.mark.parametrize("field", ["candidate_id", "closure_digest", "evidence_cutoff"])
def test_legacy_receipt_cannot_bind_modern_extension_claims(tmp_path, field):
    path = candidate(tmp_path, "first", 1)
    promote(tmp_path, path)
    receipt = json.loads((path / "PUBLISHED").read_text())
    del receipt[field]
    (path / "PUBLISHED").write_text(json.dumps(receipt))
    result = view(tmp_path)
    assert result["reason"] == "event_publication_receipt_unbound"
    assert result["published_pages"] is None and result["rows"] == []


@pytest.mark.parametrize("policy", [["invalid"], {"closure": ["invalid"]}])
def test_malformed_manifest_policy_is_unverified_not_a_diagnostic_crash(tmp_path, policy):
    path = candidate(tmp_path, "first", 1)
    promote(tmp_path, path)
    manifest = path / "_meta/manifest.json"
    value = json.loads(manifest.read_text())
    value["release_policy"] = policy
    manifest.write_text(json.dumps(value))
    (path / "BUILT").write_text(json.dumps({"manifest_hash": sha256_file(manifest)}))
    assert view(tmp_path)["reason"] == "publication_receipt_or_files_unverified"
