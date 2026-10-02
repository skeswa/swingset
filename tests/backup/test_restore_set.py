"""Installing a segmented set (architecture phase 4, WP6): every digest is
checked before anything is placed, and `restore --backup-set` activates only
against the public hub, as a checkpoint restore does."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from test_segments import backup, build_state, local

from swingset.backup.checkpoint import CheckpointError
from swingset.backup.restore_set import SetError, install, restore_from_set
from swingset.backup.segments import read_manifest
from swingset.build.files import canonical_json
from swingset.clock import FakeClock
from swingset.publish.service import RemoteCommit
from swingset.state.rwlock import is_lock_file


def _placed(target: Path) -> list[str]:
    return sorted(path.name for path in target.iterdir() if not is_lock_file(path.name))


def _set(tmp_path: Path) -> tuple[Path, dict[str, Any], Path]:
    state = tmp_path / "state"
    build_state(state)
    result = backup(state)
    return state, result, state / "checkpoints" / "sets" / result["set_id"]


def _rewrite_manifest(directory: Path, change: Any) -> None:
    manifest = read_manifest(directory)
    change(manifest)
    (directory / "backup.json").write_bytes(canonical_json(manifest))


def test_a_corrupt_part_fails_before_anything_is_placed(tmp_path: Path) -> None:
    state, _, directory = _set(tmp_path)
    part = directory / "db" / "part-0000.zst"
    body = bytearray(part.read_bytes())
    body[len(body) // 2] ^= 0xFF
    part.write_bytes(bytes(body))
    target = tmp_path / "restored"
    with pytest.raises(SetError, match="does not match its digest"):
        install(local(state), target)
    assert _placed(target) == []


def test_a_missing_segment_fails_before_anything_is_placed(tmp_path: Path) -> None:
    state, result, _ = _set(tmp_path)
    (state / "checkpoints" / "segments" / f"{result['new_segments'][0]}.tar").unlink()
    target = tmp_path / "restored"
    with pytest.raises(SetError, match="missing"):
        install(local(state), target)
    assert _placed(target) == []


def test_a_truncated_segment_fails_before_anything_is_placed(tmp_path: Path) -> None:
    state, result, _ = _set(tmp_path)
    segment = state / "checkpoints" / "segments" / f"{result['new_segments'][0]}.tar"
    segment.write_bytes(segment.read_bytes()[:-4096])
    target = tmp_path / "restored"
    with pytest.raises(SetError, match="does not match its digest"):
        install(local(state), target)
    assert _placed(target) == []


def test_a_wrong_item_digest_fails_before_anything_is_placed(tmp_path: Path) -> None:
    state, _, directory = _set(tmp_path)
    index = directory / "index.jsonl.zst"
    with pa.input_stream(str(index), compression="zstd") as stream:
        lines = stream.read().decode().splitlines()
    # The last line is a file extracted last: everything before it passed.
    entry = json.loads(lines[-1])
    entry[2] = "0" * 64
    lines[-1] = json.dumps(entry, separators=(",", ":"))
    with pa.output_stream(str(index), compression="zstd") as output:
        output.write(("\n".join(lines) + "\n").encode())
    body = index.read_bytes()

    def change(manifest: dict[str, Any]) -> None:
        manifest["index"]["sha256"] = hashlib.sha256(body).hexdigest()
        manifest["index"]["bytes"] = len(body)

    _rewrite_manifest(directory, change)
    target = tmp_path / "restored"
    with pytest.raises(SetError, match="does not match its digest"):
        install(local(state), target)
    assert _placed(target) == []


def test_a_database_that_does_not_match_its_raw_digest_fails(tmp_path: Path) -> None:
    state, _, directory = _set(tmp_path)
    _rewrite_manifest(directory, lambda manifest: manifest["database"].update(raw_sha256="0" * 64))
    target = tmp_path / "restored"
    with pytest.raises(SetError, match="joined database"):
        install(local(state), target)
    assert _placed(target) == []


def test_install_refuses_a_target_with_state(tmp_path: Path) -> None:
    state, _, _ = _set(tmp_path)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "state.sqlite").write_bytes(b"live")
    with pytest.raises(SetError, match="not empty"):
        install(local(state), target)
    assert (target / "state.sqlite").read_bytes() == b"live"


def test_install_quick_checks_and_names_the_latest_set(tmp_path: Path) -> None:
    state, result, _ = _set(tmp_path)
    summary = install(local(state), tmp_path / "restored", integrity="quick")
    assert summary["set_id"] == result["set_id"]
    assert summary["integrity"] == "quick"
    assert summary["classes"]["body"] == 1


class Hub:
    def __init__(self, head: str | None) -> None:
        self.current = head

    def head(self) -> str | None:
        return self.current

    def inspect(self, commit: str) -> RemoteCommit:
        raise AssertionError("a published baseline needs no inspection")


def test_restore_from_set_activates_against_the_public_head(tmp_path: Path) -> None:
    state, result, _ = _set(tmp_path)
    target = tmp_path / "restored"
    summary = restore_from_set(local(state), None, target, Hub("abc"), FakeClock(), lock_timeout=1)
    assert summary["set_id"] == result["set_id"]
    assert not (target / "RESTORE_PENDING").exists()
    copy = sqlite3.connect(target / "state.sqlite")
    assert copy.execute("SELECT host FROM hosts").fetchall() == [("h.example",)]
    copy.close()


def test_restore_from_set_keeps_the_marker_when_the_hub_differs(tmp_path: Path) -> None:
    state, result, _ = _set(tmp_path)
    target = tmp_path / "restored"
    with pytest.raises(CheckpointError, match="public head differs"):
        restore_from_set(
            local(state), result["set_id"], target, Hub("other"), FakeClock(), lock_timeout=1
        )
    assert (target / "RESTORE_PENDING").is_file()
    # A retry replaces the unverified install.
    restore_from_set(
        local(state), result["set_id"], target, Hub("abc"), FakeClock(), lock_timeout=1
    )
    assert not (target / "RESTORE_PENDING").exists()


def test_the_cli_writes_a_local_set_and_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset import cli

    state = tmp_path / "state"
    build_state(state)
    assert cli.main(["backup", "--segmented", "--local", "--state", str(state)]) == 0
    sets = state / "checkpoints" / "sets"
    (set_id,) = [path.name for path in sets.iterdir()]
    assert (sets / set_id / "backup.json").is_file()
    assert (
        cli.main(
            ["backup", "--segmented", "--local", "--destination", str(tmp_path / "d")]
            + ["--state", str(state)]
        )
        == 1
    )
    monkeypatch.setattr(cli, "hub", lambda: Hub("abc"))
    target = tmp_path / "restored"
    argv = ["restore", "--backup-set", "latest", "--from", str(state / "checkpoints")]
    assert cli.main([*argv, "--state", str(target), "--writer-stopped"]) == 0
    assert not (target / "RESTORE_PENDING").exists()
    assert (target / "baseline").is_symlink()
