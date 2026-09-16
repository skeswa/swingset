"""Research readers must not create sidecars in immutable checkpoint directories."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


def load_research(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "journal/tools/runtime" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checkpoint(path):
    path.mkdir(parents=True)
    conn = sqlite3.connect(path / "state.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE entries(event_id TEXT)")
    conn.commit()
    conn.close()
    (path / "checkpoint.json").write_text("{}")
    for name in ("blobs", "extracts"):
        (path / name).mkdir()
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


def test_benchmark_clone_uses_immutable_source_and_closes_connections(tmp_path):
    source = tmp_path / "checkpoint"
    before = checkpoint(source)
    receipt = load_research("benchmark_requirements").clone_checkpoint(source, tmp_path / "clone")
    assert receipt["immutable_source_connection"]
    assert before == {item.name: item.read_bytes() for item in source.iterdir() if item.is_file()}
    assert not (source / "state.sqlite-wal").exists()
    assert not (source / "state.sqlite-shm").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_reference_benchmark_checkpoint_reads_create_no_sidecars(tmp_path, monkeypatch, explicit):
    source = tmp_path / ("explicit" if explicit else "checkpoints") / "frozen"
    before = checkpoint(source)
    args = ["benchmark", "--state", str(source), "--output", str(tmp_path / "report.json")]
    if explicit:
        args.append("--immutable-checkpoint")
    monkeypatch.setattr(sys, "argv", args)
    load_research("benchmark_identity_references").main()
    assert before == {item.name: item.read_bytes() for item in source.iterdir() if item.is_file()}
