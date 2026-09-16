"""Old evidence references remain usable without trusting changed or escaped files."""

import hashlib
import json

import pytest

from swingset.history.catalog import retained_evidence_path


@pytest.mark.parametrize("escape", ["../outside.json", "/tmp/outside.json"])
def test_evidence_reference_cannot_escape_repository(tmp_path, escape):
    with pytest.raises(ValueError, match="escapes the repository"):
        retained_evidence_path(tmp_path, escape)


@pytest.mark.parametrize("change", ["bytes", "target", "symlink"])
def test_relocated_evidence_checks_hash_and_destination(tmp_path, change):
    evidence = tmp_path / "journal/evidence"
    evidence.mkdir(parents=True)
    target = evidence / "capture.json"
    target.write_bytes(b"[]")
    entry = {"path": "journal/evidence/capture.json", "sha256": hashlib.sha256(b"[]").hexdigest()}
    (evidence / "paths.json").write_text(
        json.dumps({"version": 1, "files": {"research/verification/capture.json": entry}})
    )
    assert retained_evidence_path(tmp_path, "research/verification/capture.json") == target
    if change == "bytes":
        target.write_bytes(b"[1]")
        expected = "retained hash"
    else:
        outside = tmp_path / "outside.json"
        outside.write_bytes(b"[]")
        if change == "symlink":
            target.unlink()
            target.symlink_to(outside)
        else:
            entry["path"] = "outside.json"
            (evidence / "paths.json").write_text(
                json.dumps({"version": 1, "files": {"research/verification/capture.json": entry}})
            )
        expected = "escapes journal evidence"
    with pytest.raises(ValueError, match=expected):
        retained_evidence_path(tmp_path, "research/verification/capture.json")
