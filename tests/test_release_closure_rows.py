"""Reconstruction uses immutable output and exact published legacy values."""

import json
import sqlite3
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_h15_acceptance import drain_project
from test_h15_acceptance import source_fixture as source_fixture

from swingset.build.closure import hydrate, public_manifest, retain, select, validate
from swingset.build.closure_manifest import ClosureError, canonical, digest
from swingset.build.closure_rows import _legacy_matches, _put, reconstruct
from swingset.build.closure_support import observation_payload_loader, selected_observations
from swingset.build.files import sha256_file
from swingset.build.input import _read_table
from swingset.build.schema import SCHEMAS


def baseline_events(fixture, path: Path):
    path.mkdir()
    (path / "_meta").mkdir()
    directory = path / "data/events"
    directory.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(_read_table(fixture.conn, "events"), schema=SCHEMAS["events"]),
        directory / "part.parquet",
    )
    manifest = {"files": {"data/events/part.parquet": sha256_file(directory / "part.parquet")}}
    (path / "_meta/manifest.json").write_text(json.dumps(manifest))
    (path / "PUBLISHED").write_text(json.dumps({"commit": "published-before-cutoff"}))
    return path


def test_reconstruct_uses_pinned_rows_after_live_canonical_and_source_replacement(
    source_fixture, tmp_path
):
    f = source_fixture
    drain_project(f)
    baseline = baseline_events(f, tmp_path / "baseline")
    closure = select(f.conn, cutoff=f.corpus.clock.now(), baseline=baseline)
    with reconstruct(f.conn, closure, directory=tmp_path / "first", baseline=baseline) as rows:
        before = {name: list(rows.iter_table(name)) for name in rows.table_names()}
    assert before["entries"]
    assert all(row["wsdc_id"] is None for row in before["entries"])
    f.conn.execute("UPDATE entries SET name_raw='Arrived after cutoff'")
    f.conn.execute("DELETE FROM observations")
    validate(f.conn, closure)
    with reconstruct(f.conn, closure, directory=tmp_path / "second", baseline=baseline) as rows:
        after = {name: list(rows.iter_table(name)) for name in rows.table_names()}
    assert after == before
    assert all(row["name_raw"] != "Arrived after cutoff" for row in after["entries"])


def test_legacy_owned_values_require_exact_published_artifact(source_fixture, tmp_path):
    f = source_fixture
    drain_project(f)
    baseline = baseline_events(f, tmp_path / "baseline")
    closure = select(f.conn, cutoff=f.corpus.clock.now(), baseline=baseline)
    with reconstruct(f.conn, closure, directory=tmp_path / "good", baseline=baseline) as rows:
        assert rows.counts["legacy_unassessed_retained"] > 0
    (baseline / "data/events/part.parquet").write_bytes(b"tampered")
    with pytest.raises(ClosureError, match="baseline_owned_values_artifact_changed"):
        with reconstruct(f.conn, closure, directory=tmp_path / "bad", baseline=baseline):
            pass


def test_unpublished_legacy_event_is_omitted_with_structural_dependents(source_fixture, tmp_path):
    f = source_fixture
    drain_project(f)
    closure = select(f.conn, cutoff=f.corpus.clock.now())
    with reconstruct(f.conn, closure, directory=tmp_path) as rows:
        assert list(rows.iter_table("entries")) == []
        assert list(rows.iter_table("contests")) == []
        assert any(item["unit_id"] == "event-a" for item in rows.omissions)


def test_public_closure_commits_to_private_proof_without_disclosing_recipes(source_fixture):
    f = source_fixture
    drain_project(f)
    private = select(f.conn, cutoff=f.corpus.clock.now()).manifest()
    public = public_manifest(private)
    assert set(public) == {
        "format",
        "private_digest",
        "cutoff",
        "selected_generations",
        "support_token",
        "inventory_digest",
        "baseline",
        "digest",
    }
    assert public["selected_generations"]
    assert "recipe" not in json.dumps(public)
    assert "event-a" not in json.dumps(public)
    with pytest.raises(ClosureError, match="private_closure_proof_unavailable"):
        hydrate(f.conn, public)
    with f.db.transaction():
        retain(f.conn, private)
    assert hydrate(f.conn, public) == private
    validate(f.conn, public)
    forged = {**public, "selected_generations": []}
    forged["digest"] = digest({key: value for key, value in forged.items() if key != "digest"})
    with pytest.raises(ClosureError, match="public_closure_commitment_mismatch"):
        hydrate(f.conn, forged)


def test_legacy_numeric_comparison_uses_exact_public_float_representation():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE baseline(table_name TEXT,record_key TEXT,payload TEXT)")
        field = SCHEMAS["callback_marks"].field("mark_value")
        public_value = pa.scalar(4.2, type=field.type).as_py()
        assert public_value != 4.2
        connection.execute(
            "INSERT INTO baseline VALUES (?,?,?)",
            ("callback_marks", "key", json.dumps({"mark_value": public_value})),
        )
        assert _legacy_matches(connection, "callback_marks", "key", {"mark_value": 4.2})
        # A distinct representable float remains a changed source-owned value.
        assert not _legacy_matches(connection, "callback_marks", "key", {"mark_value": 4.200001})
    finally:
        connection.close()


def test_selected_payload_loader_is_lazy_and_uses_retained_accepted_evidence(source_fixture):
    f = source_fixture
    drain_project(f)
    manifest = select(f.conn, cutoff=f.corpus.clock.now()).manifest()
    expected = list(selected_observations(f.conn, manifest))
    rounds = [item for item in expected if item["observation_kind"] == "round_sheet"]
    assert rounds
    statements = []
    f.conn.set_trace_callback(statements.append)
    try:
        loader = observation_payload_loader(f.conn, manifest)
        assert statements == []
        assert list(loader("not-selected")) == []
        assert statements == []
        # A later source selection can retire mutable observations; accepted
        # source-generation payloads continue to identify the pinned source cells.
        f.conn.execute("DELETE FROM observations")
        for snapshot in {item["snapshot_id"] for item in rounds}:
            assert list(loader(snapshot)) == [
                item["payload_json"] for item in rounds if item["snapshot_id"] == snapshot
            ]
    finally:
        f.conn.set_trace_callback(None)


def test_selection_decodes_shared_immutable_dependency_sets_once(source_fixture, monkeypatch):
    from swingset.build import closure_manifest

    f = source_fixture
    drain_project(f)
    reads = Counter()
    original = closure_manifest.members

    def counted(conn, identifier):
        reads[identifier] += 1
        yield from original(conn, identifier)

    monkeypatch.setattr(closure_manifest, "members", counted)
    selected = select(f.conn, cutoff=f.corpus.clock.now())
    assert len(selected.selected) > 3
    assert reads and max(reads.values()) == 1


def test_dependency_edge_must_name_the_actual_generation_scope(source_fixture):
    from swingset.build.closure import _Selection

    f = source_fixture
    drain_project(f)
    rows = list(f.conn.execute("SELECT * FROM derivation_generations WHERE stage='project'"))
    history = next(row for row in rows if row["unit_kind"] == "history")
    base = next(row for row in rows if row["unit_kind"] == "inventory")
    malformed = [
        {
            "kind": "derivation",
            "key": ["project", "dancer", "wrong-scope"],
            "generation_id": base["generation_id"],
        }
    ]
    dependency = digest(malformed)
    f.conn.execute(
        "INSERT INTO derivation_dependency_sets VALUES (?,?)", (dependency, canonical(malformed))
    )
    copied = dict(history)
    copied.update(generation_id="malformed-scope-edge", dependency_set_id=dependency)
    columns = list(copied)
    f.conn.execute(
        f"INSERT INTO derivation_generations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(copied.values()),
    )
    with pytest.raises(ClosureError, match="selected_dependency_scope_mismatch"):
        _Selection(f.conn, f.corpus.clock.now().isoformat()).add("malformed-scope-edge")


def test_partial_ownership_overlay_requires_a_retained_structural_row():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE rows(table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(table_name,record_key))"
        )
        patch = {"entry_id": "entry", "wsdc_id": 123}
        assert not _put(conn, "entries", '["entry"]', patch, patch=True)
        assert conn.execute("SELECT count(*) FROM rows").fetchone()[0] == 0
        base = {
            "entry_id": "entry",
            "contest_id": "contest",
            "event_id": "event",
            "name_raw": "Retained source person",
        }
        assert _put(conn, "entries", '["entry"]', base)
        assert _put(conn, "entries", '["entry"]', patch, patch=True)
        selected = json.loads(conn.execute("SELECT payload FROM rows").fetchone()[0])
        assert all(selected[key] == value for key, value in base.items())
        assert selected["wsdc_id"] == 123
    finally:
        conn.close()
