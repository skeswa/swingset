import json
from pathlib import Path

import pytest

import swingset.backup.huggingface as module
from swingset.backup.huggingface import HuggingFaceArchive


def test_download_rejects_manifest_path_traversal_before_artifact_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "remote-checkpoint.json"
    manifest.write_text(json.dumps({"files": {"../escape": {"sha256": "x", "size": 1}}}))

    def download(**_kwargs: object) -> str:
        return str(manifest)

    monkeypatch.setattr(module, "hf_hub_download", download)
    archive = HuggingFaceArchive("owner/archive", token="secret", api=object())  # type: ignore[arg-type]
    destination = tmp_path / "download"
    with pytest.raises(ValueError, match="unsafe checkpoint path"):
        archive.download("commit", destination)
    assert not (tmp_path / "escape").exists()
    assert set(path.name for path in destination.iterdir()) == {"checkpoint.json"}
