"""Seed receipts and scratch replay cannot overwrite the source development data."""

import json
import uuid

import pytest

from swingset.dev import replay, seed, services
from swingset.state.environment import EnvironmentRefused


@pytest.fixture
def local_seed(tmp_path, monkeypatch):
    # Lifecycle tests isolate creation/copy/cleanup; the real cohort has its own case.
    called = []
    monkeypatch.setattr(seed, "populate", lambda state: called.append(state) or {"counts": {}})
    service = services.ensure_service()
    name = "case_" + uuid.uuid4().hex[:10]
    state = tmp_path / "state"
    receipt = seed.create(name, service=service, state=state)
    try:
        yield name, state, service, receipt, called
    finally:
        seed.drop(name, service=service, state=state)


def test_seed_reuse_checks_receipt_without_replaying_or_replacing_data(local_seed):
    name, state, service, receipt, called = local_seed
    assert seed.create(name, service=service, state=state, reuse=True) == receipt
    assert len(called) == 1
    with pytest.raises(ValueError, match="already exists"):
        seed.create(name, service=service, state=state)
    manifest = json.loads((state / "seed.json").read_text())
    manifest["fixture_checksum"] = "changed"
    services.write_private(state / "seed.json", manifest)
    with pytest.raises(ValueError, match="fixtures or transform versions changed"):
        seed.create(name, service=service, state=state, reuse=True)
    assert len(called) == 1


def test_replay_copy_is_independent_and_cleanup_runs_after_failure(local_seed):
    from swingset.state.db import open_database

    name, state, service, _receipt, _called = local_seed
    with service.connect(seed.database_name(name)) as source:
        source.execute("INSERT INTO ops.meta(key,value) VALUES ('replay-probe','original')")
    copied = None
    with pytest.raises(RuntimeError, match="interrupted"):
        with replay.clone(state, service) as scratch, open_database(scratch) as database:
            copied = database.connection.info.dbname
            database.connection.execute(
                "UPDATE ops.meta SET value='scratch' WHERE key='replay-probe'"
            )
            raise RuntimeError("interrupted")
    with service.connect(seed.database_name(name)) as source:
        assert (
            source.execute("SELECT value FROM ops.meta WHERE key='replay-probe'").fetchone()[0]
            == "original"
        )
    with service.connect() as admin:
        assert (
            admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (copied,)).fetchone()
            is None
        )


def test_production_row_refuses_replay_even_when_marker_says_dev(local_seed, monkeypatch):
    name, state, service, _receipt, _called = local_seed
    monkeypatch.delenv("SWINGSET_ENV", raising=False)
    with service.connect(seed.database_name(name)) as source:
        source.execute("UPDATE ops.environment_identity SET environment='production'")
    try:
        with pytest.raises(EnvironmentRefused):
            with replay.clone(state, service):
                pytest.fail("production data was cloned for development")
    finally:
        with service.connect(seed.database_name(name)) as source:
            source.execute("UPDATE ops.environment_identity SET environment='dev'")


def test_replay_file_limits_and_external_symlinks_are_refused(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    (state / "outside").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="relative file links"):
        replay._files_bounded(state)
    (state / "outside").unlink()
    monkeypatch.setattr(replay, "MAX_FILE_BYTES", 4)
    (state / "large").write_bytes(b"12345")
    with pytest.raises(ValueError, match="bounded slice"):
        replay._files_bounded(state)


def test_fixture_cohort_is_fixed_and_contains_captured_registry_data():
    corpus = seed.fixtures()
    assert len([name for name in corpus if name.endswith(".htm")]) == 33
    assert len(corpus) == 36
    assert seed.fixture_checksum() == seed.fixture_checksum()
    registry = json.loads(corpus["https://points.worldsdc.com/lookup2020/find"])
    assert registry["leader"]["dancer"]["wscid"] == 1


def test_seed_cache_tracks_transform_versions_but_not_source_edits(monkeypatch):
    import importlib

    versions_module = importlib.import_module("swingset.versions")
    values = {"projector": "1", "linker": "2", "parser/example": "3", "repository": "source:first"}
    monkeypatch.setattr(versions_module, "versions", lambda: values)
    first = seed.transform_checksum()
    values["repository"] = "source:edited"
    assert seed.transform_checksum() == first
    values["parser/example"] = "4"
    assert seed.transform_checksum() != first


@pytest.mark.parametrize("name", ["../production", "Production", "replay_collision", "", "a" * 41])
def test_seed_names_cannot_escape_the_dev_namespace(name):
    with pytest.raises(ValueError):
        seed.database_name(name)


def test_new_projector_code_replays_without_a_version_bump_or_seed_write(local_seed, monkeypatch):
    import importlib
    from dataclasses import replace

    from test_project_registry import project

    from swingset.clock import FakeClock
    from swingset.model.canonical import Dancer
    from swingset.project.writer import Projection
    from swingset.sources.base import ParseContext
    from swingset.sources.wsdc_registry import DancerPage
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture
    from swingset.state.work import WorkUnit
    from swingset.versions import versions

    _name, state, service, _receipt, _called = local_seed
    config, overrides = seed._config(state)
    with open_database(state) as database:
        bundle = capture(config, overrides, state, versions())
        accept(database, bundle, FakeClock(seed.NOW))
    page = DancerPage()
    body = seed.fixtures()["https://points.worldsdc.com/lookup2020/find"]
    payload = (
        page.parse(
            page.extract(body),
            ParseContext(
                "snap",
                "registry-1",
                "https://points.worldsdc.com/lookup2020/find",
                "wsdc_registry",
                page.kind,
                "wsdc:1",
                seed.NOW.isoformat(),
            ),
        )
        .observations[0]
        .payload
    )
    project(state, payload)
    processing = importlib.import_module("swingset.project.process")
    with open_database(state) as database:
        processing.process_unit(
            database, WorkUnit("project", "dancer", "1"), bundle, FakeClock(seed.NOW), "run"
        )
        original_version = database.connection.execute(
            "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/code/project/dancer'"
        ).fetchone()[0]
        assert (
            database.connection.execute(
                "SELECT first_name FROM dancers WHERE wsdc_id=1"
            ).fetchone()[0]
            == "Diane"
        )
    original = processing.project_dancer

    def candidate(*args, **kwargs):
        found = original(*args, **kwargs)
        rows = tuple(
            replace(row, first_name="Candidate", name_norm="candidate abele")
            if isinstance(row, Dancer)
            else row
            for row in found.rows
        )
        return Projection(rows, found.findings)

    monkeypatch.setattr(processing, "project_dancer", candidate)
    result = replay.run(state, unit="project/dancer/1", service=service)
    assert result["complete"]
    assert result["results"][0]["state"] == "changed"
    with open_database(state) as database:
        assert (
            database.connection.execute(
                "SELECT first_name FROM dancers WHERE wsdc_id=1"
            ).fetchone()[0]
            == "Diane"
        )
        assert (
            database.connection.execute(
                "SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/code/project/dancer'"
            ).fetchone()[0]
            == original_version
        )


@pytest.mark.extended
def test_real_offline_cohort_seed_settles_and_replays_on_a_copy(tmp_path):
    service = services.ensure_service()
    name = "cohort_" + uuid.uuid4().hex[:10]
    state = tmp_path / "cohort"
    try:
        receipt = seed.create(name, service=service, state=state)
        assert receipt["complete"]
        assert receipt["summary"]["counts"]["snapshots"] == 36
        assert receipt["summary"]["counts"]["dancers"] == 1
        assert receipt["summary"]["counts"]["pending_work"] == 0
        result = replay.run(state, event=seed.EVENT_ID, service=service)
        assert result["complete"]
        assert result["units"] <= replay.MAX_UNITS
        assert all(
            not unit["unit"].startswith(("project/year/", "project/source_event/"))
            for unit in result["results"]
        )
        assert all(unit["state"] == "equal" for unit in result["results"]), [
            unit for unit in result["results"] if unit["state"] != "equal"
        ]
    finally:
        if (state / "seed.json").is_file():
            seed.drop(name, service=service, state=state)


@pytest.mark.extended
@pytest.mark.parametrize("scenario", ["source-parse", "identity-correction", "release-gate"])
def test_named_scenarios_run_real_fixture_code_without_outside_effects(tmp_path, scenario):
    from swingset.state.db import open_database

    service = services.ensure_service()
    name = "scenario_" + uuid.uuid4().hex[:10]
    state = tmp_path / "scenario"
    try:
        receipt = seed.create(name, service=service, state=state, scenario=scenario)
        result = receipt["scenario_result"]
        assert receipt["complete"] and result["name"] == scenario
        with open_database(state) as database:
            assert database.environment == "dev"
            if scenario == "source-parse":
                assert result["unit"].startswith("parse/snapshot/")
            elif scenario == "identity-correction":
                assert (
                    database.connection.execute(
                        "SELECT COUNT(*) FROM identity_decisions"
                    ).fetchone()[0]
                    == 2
                )
                assert (
                    database.connection.execute(
                        "SELECT decision FROM identity_decisions WHERE decision_id='dev-review-2'"
                    ).fetchone()[0]
                    == "hold_unlinked"
                )
            else:
                assert result["verdict"]["state"] == "refused"
                assert "decrease:dancers 1->0" in result["verdict"]["reasons"]
        assert not (state / "baseline").exists()
        assert not list((state / "candidates").glob("*/PUBLISHING"))
    finally:
        if (state / "seed.json").is_file():
            seed.drop(name, service=service, state=state)
