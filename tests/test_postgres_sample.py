"""Sampling writes a complete scratch closure while leaving its source exact."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from test_postgres_import import source_database  # noqa: F401

from swingset.state import postgres_import as importer
from swingset.state import postgres_sample


def seed_unit(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES('sample-run','2026-11-04T00:00:00Z',0)"
        )
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES('sample-watch','eepro','round','GET','https://example.test/','eepro.round','live')"
        )
        conn.execute(
            "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES('sample-unit','sample-watch','eepro.round')"
        )
        conn.execute(
            "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES('sample-generation','sample-unit','eepro.round','1','input','[]','{}','{}','{}','2026-11-04T00:00:00Z','sample-run','accepted','none')"
        )
        conn.execute(
            "UPDATE source_units SET accepted_generation_id='sample-generation' WHERE unit_key='sample-unit'"
        )


def test_sample_is_read_only_complete_and_importable(source_database, tmp_path):  # noqa: F811
    seed_unit(source_database)
    before = hashlib.sha256(source_database.read_bytes()).hexdigest()
    target = tmp_path / "sample"
    result = postgres_sample.sample(source_database, target, unit_limit=1)
    assert result["selected_units"] == 1
    assert result["rows"]["source_generations"] == 1
    assert result["source_read_only"]
    assert hashlib.sha256(source_database.read_bytes()).hexdigest() == before
    assert importer.preflight(target)["complete"]


def test_sample_budget_refuses_and_removes_partial_destination(source_database, tmp_path):  # noqa: F811
    seed_unit(source_database)
    target = tmp_path / "too-large"
    with pytest.raises(ValueError, match="budget"):
        postgres_sample.sample(source_database, target, max_database_bytes=100)
    assert not target.exists()
    assert not list(tmp_path.glob(".too-large.sampling-*"))


def test_explicit_event_roots_keep_current_caches_and_exclude_unselected_event(
    source_database,  # noqa: F811
    tmp_path,  # noqa: F811
):  # noqa: F811
    seed_unit(source_database)
    with sqlite3.connect(source_database) as conn:
        conn.execute("INSERT INTO series(series_id,name) VALUES('sample-series','Sample')")
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES('sample-snapshot','sample-watch','GET','https://example.test/','2026-11-04T00:00:00Z',200,0,1,'sample-run','Ok')"
        )
        for event in ("selected-event", "unselected-event"):
            conn.execute(
                "INSERT INTO events(event_id,series_id,name,year,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES(?,'sample-series',?,2026,'unknown','[]','eepro','sample-snapshot','1','2026-11-04T00:00:00Z','2026-11-04T00:00:00Z','sample-run')",
                (event, event),
            )
            conn.execute(
                "INSERT INTO canonical_scope_rows(scope_kind,scope_id,table_name,record_key) VALUES('event',?,'events',json_array(?))",
                (event, event),
            )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES('project','event','selected-event','2026-11-04T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES('sample-observation','sample-watch','sample-snapshot','event','event','selected-event',0,'1','1','{}')"
        )
    before = hashlib.sha256(source_database.read_bytes()).hexdigest()
    target = tmp_path / "event-sample"
    result = postgres_sample.sample(
        source_database, target, unit_limit=1, event_ids=("selected-event",)
    )
    assert result["event_ids"] == ["selected-event"]
    assert result["rows"]["observations"] == 1
    assert result["rows"]["canonical_scope_rows"] == 1
    with sqlite3.connect(target / "state.sqlite") as conn:
        assert conn.execute("SELECT event_id FROM events").fetchall() == [("selected-event",)]
        assert conn.execute("SELECT unit_id FROM derivation_scopes").fetchall() == [
            ("selected-event",)
        ]
    assert importer.preflight(target)["complete"]
    coherent = tmp_path / "canonical-cohort"
    bounded = postgres_sample.sample(
        source_database,
        coherent,
        unit_limit=0,
        event_ids=("selected-event",),
        include_derivation_history=False,
    )
    assert bounded["selected_units"] == 0
    assert bounded["rows"]["events"] == 1 and bounded["rows"]["observations"] == 1
    assert bounded["rows"]["derivation_scopes"] == 0
    assert importer.preflight(coherent)["complete"]
    assert hashlib.sha256(source_database.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="absent"):
        postgres_sample.sample(
            source_database, tmp_path / "missing-event", event_ids=("absent-event",)
        )


def test_sample_copies_verified_legacy_code_scope_bundle(source_database, tmp_path):  # noqa: F811
    from test_legacy_recipes import SCOPE, bundle

    from swingset.fetch.archive import canonical, digest
    from swingset.state.legacy_recipes import verify_bundle

    seed_unit(source_database)
    identity, group, files = bundle(source_database.parent)
    recipe = canonical(
        {
            "accepted_inputs": [
                ["pipeline", "recipe/env", "b" * 64],
                ["pipeline", SCOPE, digest(canonical(group))],
            ]
        }
    ).decode()
    with sqlite3.connect(source_database) as conn:
        # The synthetic source evidence remains immutable once recorded.
        conn.execute(
            "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,state,removal_authority) VALUES('legacy-code-generation','sample-unit','eepro.round','1','legacy-input','[]',?,'{}','{}','2026-11-04T00:00:00Z','sample-run','accepted','none')",
            (recipe,),
        )
        conn.execute(
            "UPDATE source_units SET accepted_generation_id='legacy-code-generation' WHERE unit_key='sample-unit'"
        )
    target = tmp_path / "with-code-witness"
    result = postgres_sample.sample(source_database, target, unit_limit=1)
    assert result["required_files"]["legacy_code_scopes"] == 1
    assert result["required_files"]["input_bundles"] == 1
    copied = {
        path.relative_to(target).as_posix(): path
        for path in (target / "inputs").rglob("*")
        if path.is_file()
    }
    assert verify_bundle(copied, identity)
    assert importer.preflight(target)["complete"]
    files["inputs/" + identity + "/runtime/swingset/example.py"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="legacy code scopes"):
        postgres_sample.sample(source_database, tmp_path / "corrupt-code-witness", unit_limit=1)
