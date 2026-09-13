"""Build files and SQLite materialization remain consistent across interruptions."""

import json
import shutil
from pathlib import Path

import pytest

from swingset.build import generations, service
from swingset.build.builder import BuildError
from swingset.clock import FakeClock
from swingset.publish.safety import PublicationHeldError, _validate_candidate
from swingset.schedule.cycle import versions
from swingset.state import derivations
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit


@pytest.fixture
def empty_release(tmp_path):
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    with open_database(tmp_path) as db:
        clock = FakeClock()
        bundle = capture(Path("config"), overrides, tmp_path, versions())
        accept(db, bundle, clock)
        run = db.start_run(clock.now())
        yield db, bundle, clock, run


def test_interrupted_completion_retains_files_without_false_materialization(
    empty_release, monkeypatch
):
    db, bundle, clock, run = empty_release
    complete = derivations.complete

    def interrupt(*args, **kwargs):
        complete(*args, **kwargs)
        raise KeyboardInterrupt("after generation rows, before commit")

    monkeypatch.setattr(derivations, "complete", interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.build_release(db, bundle, clock, run)
    candidates = list((db.state_dir / "candidates").glob("*/BUILT"))
    assert len(candidates) == 1
    assert (
        db.connection.execute(
            "SELECT count(*) FROM derivation_generations WHERE stage='build'"
        ).fetchone()[0]
        == 0
    )
    built = json.loads(candidates[0].read_bytes())
    assert not generations.completed(
        db.connection, candidates[0].parent.name, built["manifest_hash"]
    )
    manifest = json.loads((candidates[0].parent / "_meta/manifest.json").read_bytes())
    with pytest.raises(PublicationHeldError, match="durable build completion"):
        _validate_candidate(db.connection, db.state_dir, candidates[0].parent, built, manifest)
    monkeypatch.setattr(derivations, "complete", complete)
    result = service.build_release(db, bundle, clock, run)
    assert result.reused and result.path == candidates[0].parent
    assert generations.completed(db.connection, result.candidate_id, result.manifest_hash)
    selection = generations.select(
        db,
        bundle_digest=bundle.digest,
        correction_only=False,
        closure=json.loads((result.path / "_meta/manifest.json").read_bytes())["release_policy"][
            "closure"
        ],
    )
    assert derivations.current(
        db.connection, WorkUnit("build", "release", "all"), context=selection.context
    )


def test_new_inputs_reject_completed_old_build_files(empty_release, monkeypatch):
    db, bundle, clock, run = empty_release
    build = service.build_candidate
    retained = []

    def changed_inputs(*args, **kwargs):
        result = build(*args, **kwargs)
        retained.append(result.path)
        assert bundle.overrides_dir is not None
        suppression = bundle.overrides_dir / "suppressions.csv"
        suppression.write_text(
            suppression.read_text() + "1,Material correction during build,2026-01-01\n"
        )
        replacement = capture(Path("config"), bundle.overrides_dir, db.state_dir, versions())
        accept(db, replacement, clock)
        return result

    monkeypatch.setattr(service, "build_candidate", changed_inputs)
    with pytest.raises(BuildError, match="input bundle changed"):
        service.build_release(db, bundle, clock, run)
    assert (retained[0] / "BUILT").is_file()
    assert (retained[0] / "REJECTED").is_file()
    assert (
        db.connection.execute(
            "SELECT count(*) FROM derivation_generations WHERE stage='build'"
        ).fetchone()[0]
        == 0
    )


def test_missing_candidate_body_is_not_current_even_with_intact_manifest(empty_release):
    db, bundle, clock, run = empty_release
    result = service.build_release(db, bundle, clock, run)
    selection = generations.select(
        db,
        bundle_digest=bundle.digest,
        correction_only=False,
        closure=json.loads((result.path / "_meta/manifest.json").read_bytes())["release_policy"][
            "closure"
        ],
    )
    unit = WorkUnit("build", "release", "all")
    assert derivations.current(db.connection, unit, context=selection.context)
    next((result.path / "data").rglob("*.parquet")).unlink()
    assert (result.path / "BUILT").is_file() and (result.path / "_meta/manifest.json").is_file()
    assert not derivations.current(db.connection, unit, context=selection.context)
    recovered = service.build_release(db, bundle, clock, run)
    assert recovered.path != result.path and not recovered.reused
    assert recovered.content_hash == result.content_hash
    assert (result.path / "REJECTED").is_file()
    assert derivations.current(db.connection, unit, context=selection.context)
