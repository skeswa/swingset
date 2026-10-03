"""`[release] path`: partitioned, whole and closure builds (plan phase 3, WP7; D-0224)."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from build.test_partitions import settled
from test_h15_acceptance import add_dancer, drain_project, link_all
from test_h15_acceptance import source_fixture as source_fixture
from test_h16_acceptance import release_state as release_state

from swingset.build import artifact_store, service
from swingset.build.assembly import close_absent_dancers
from swingset.build.closure import select as select_closure
from swingset.config import ReleaseConfig
from swingset.publish.service import PublishError, publish
from swingset.state import derivations, release_store
from swingset.state.release_store import ManifestRow
from swingset.state.retention import artifact_directory
from swingset.state.work import WorkUnit

#: Public tables whose rows the paths must agree on. Coverage, snapshots,
#: the review queue and the changelog carry per-build columns and support
#: disclosure, which the paths gather differently.
FACTS = (
    "events",
    "contests",
    "rounds",
    "heats",
    "entries",
    "judges",
    "callbacks",
    "callback_marks",
    "final_marks",
    "final_scores",
    "placements",
    "dancers",
    "registry_placements",
    "identity_links",
    "link_candidates",
)


def on(bundle, path):
    """The same accepted bundle with another `[release] path`."""
    return replace(bundle, config=replace(bundle.config, release=ReleaseConfig(path)))


def approved(bundle):
    """The bundle with the decreases the fixture's partitioned candidate makes approved.

    Its baseline links an entry to dancer 100, whom the key check closes
    (D-0224); the publication gate needs these rows to publish (D-0225).
    """
    body = "measure,baseline_value,candidate_value,reason,approved_by,approved_at\n" + "".join(
        f"{name},1,0,absent dancer closed,test,2026-09-29\n"
        for name in ("dancers", "confirmed_links", "linked_entries")
    )
    return replace(bundle, files={**bundle.files, "overrides/release_decreases.csv": body.encode()})


def policy(result):
    return json.loads((result.path / "_meta/manifest.json").read_bytes())["release_policy"]


def rows(result, table):
    directory = result.path / "data" / table
    if not directory.is_dir():
        return []
    values = [
        row
        for path in sorted(directory.glob("*.parquet"))
        for row in pq.read_table(path).to_pylist()
    ]
    return sorted(values, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def ordered(values):
    return sorted(values, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def facts(result):
    return {table: rows(result, table) for table in FACTS}


def link_baseline(f, path: Path) -> None:
    """Acknowledge `settled`'s baseline: its receipt names the manifest's hash."""
    from swingset.build.files import sha256_file

    (path / "BUILT").write_text(
        json.dumps(
            {"manifest_hash": sha256_file(path / "_meta/manifest.json"), "content_hash": "settled"}
        )
    )
    (f.db.state_dir / "baseline").symlink_to(path)


def test_the_three_paths_build_from_one_fixture(release_state):
    f = release_state
    built = {}
    for path in ("closure", "whole", "partitioned"):
        built[path] = service.build_release(f.db, on(f.bundle, path), f.clock, f.run)
        assert not built[path].reused
        f.clock.sleep(60)
    assert policy(built["closure"])["mode"] == "closure"
    for path in ("whole", "partitioned"):
        value = policy(built[path])
        assert value["mode"] == "partitioned" and value["path"] == path
        # The 2.4 MB public closure is not carried; the manifest names it all.
        assert "closure" not in value and value["withheld"]["count"] == 0
    assert (
        policy(built["whole"])["manifest_digest"] == policy(built["partitioned"])["manifest_digest"]
    )
    # The streamed assembly publishes the rows the whole one does (WP8).
    assert facts(built["whole"]) == facts(built["partitioned"])
    whole, closure = facts(built["whole"]), facts(built["closure"])
    # The fixture links an entry to dancer 100, which no dancer generation
    # holds. The partitioned paths' key check closes that join; the closure
    # path publishes it. That is the only difference.
    assert policy(built["whole"])["partitions"]["identity_of_absent_dancer_closed"] > 0
    closed = {table: [dict(row) for row in values] for table, values in closure.items()}
    close_absent_dancers(closed, Counter())
    for table in FACTS:
        assert whole[table] == ordered(closed[table]), table
    assert whole["events"] and whole["callback_marks"] and closure["identity_links"]


def test_whole_equals_closure_rows_on_the_settled_source_fixture(source_fixture, tmp_path):
    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    clock = f.corpus.clock
    clock.sleep(60)
    closure = service.build_release(f.db, on(f.bundle, "closure"), clock, f.corpus.run)
    clock.sleep(60)
    whole = service.build_release(f.db, on(f.bundle, "whole"), clock, f.corpus.run)
    assert policy(closure)["mode"] == "closure" and policy(whole)["mode"] == "partitioned"
    left, right = facts(whole), facts(closure)
    for table in FACTS:
        assert left[table] == right[table], table
    assert left["dancers"] and left["placements"] and left["registry_placements"]


def test_two_attempts_write_the_same_manifest_and_the_build_names_it(release_state):
    from swingset.build import partitioned
    from swingset.state.retention import manifest_file

    f = release_state
    first = service.build_release(f.db, on(f.bundle, "partitioned"), f.clock, f.run)
    digest = policy(first)["manifest_digest"]
    body = manifest_file(f.db.state_dir, digest).read_bytes()
    f.clock.sleep(3600)
    again = partitioned.prepare(
        f.db,
        f.bundle.file_hashes,
        history_start=f.bundle.config.history_start,
        baseline=(f.db.state_dir / "baseline").resolve(),
        run_id="another-run",
        now=f.clock.now(),
    )
    assert again.digest == digest and again.report["rebuilt"] == 0
    assert manifest_file(f.db.state_dir, digest).read_bytes() == body
    value = json.loads(body)
    # No clock, run id or path: the evidence cutoff is a generation's time.
    assert f.run not in body.decode() and "another-run" not in body.decode()
    assert str(f.db.state_dir) not in body.decode()
    assert value["format"] == "release-manifest-v1" and value["evidence_cutoff"]
    assert value["built"] == sorted(value["built"]) and value["withheld"] == []
    assert value["correction_token"] == policy(first)["token"]
    row = release_store.get(f.conn, ManifestRow, digest)
    assert row is not None and row.candidate_id == first.candidate_id and row.bytes == len(body)
    # The build generation's context names the manifest.
    context = json.loads(
        f.conn.execute(
            "SELECT recipe_json FROM derivation_generations WHERE stage='build' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
    )["context"]
    assert context["release_manifest"] == digest and context["mode"] == "partitioned"
    # An unchanged manifest reuses the candidate within the health bucket.
    reused = service.build_release(f.db, on(f.bundle, "partitioned"), f.clock, f.run)
    assert reused.reused and reused.path == first.path


def test_the_closure_path_withholds_a_year_naming_a_superseded_dancer(source_fixture, tmp_path):
    """D-0205's F1 scenario, inverted (D-0224).

    A dancer renamed: the year keeps its generation, which names the
    inventory's kept generation, which names the old dancer. The closure
    path skips an anchor whose walk reaches a generation other than its
    scope's own anchor, so the year's events are withheld, never published
    stale. The partitioned path takes current pointers and publishes them.
    """
    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    year = WorkUnit("project", "year", "2026")
    old_year = derivations.selected_generation(f.conn, year)
    add_dancer(f, "Diane", last_name="Abele")
    drain_project(f)
    link_all(f)
    assert derivations.selected_generation(f.conn, year) == old_year
    clock = f.corpus.clock
    clock.sleep(60)
    pinned = select_closure(f.conn, cutoff=clock.now()).manifest()
    withheld = {
        (item["unit_kind"], item["unit_id"]): item["reason"]
        for item in pinned["inventory"]
        if item["status"] == "withheld" and item["stage"] == "project"
    }
    assert withheld[("year", "2026")] == "year_anchor_conflict"
    assert withheld[("event", "event-a")] == "year_anchor_conflict"
    selected = {(item["unit_kind"], item["unit_id"]): item for item in pinned["selected"]}
    assert selected[("dancer", "999")]["generation_id"] == derivations.selected_generation(
        f.conn, WorkUnit("project", "dancer", "999")
    )
    closure = service.build_release(f.db, on(f.bundle, "closure"), clock, f.corpus.run)
    assert not [row for row in rows(closure, "events") if row["year"] == 2026]
    clock.sleep(60)
    partitioned = service.build_release(f.db, on(f.bundle, "partitioned"), clock, f.corpus.run)
    assert {row["event_id"] for row in rows(partitioned, "events")} >= {"event-a"}
    names = {row["wsdc_id"]: row["first_name"] for row in rows(partitioned, "dancers")}
    assert names.get(999) in (None, "Diane")
    assert policy(partitioned)["withheld"]["count"] == 0


def test_the_release_summary_names_the_manifest_and_stage_one(release_state):
    from swingset.schedule.release_steps import build_and_publish

    f = release_state
    summary = {"stages": []}
    candidate = build_and_publish(
        f.db, on(f.bundle, "partitioned"), f.clock, f.run, summary, hub=None, dry_run=True
    )
    report = summary["partitions"]
    assert report["path"] == "partitioned" and report["withheld"] == 0
    assert report["manifest_digest"] == policy(candidate)["manifest_digest"]
    assert report["built"] == report["partitions"] == report["rebuilt"] > 0
    assert report["manifest_bytes"] > 0 and report["peak_rss_bytes"] > 0


def test_a_partitioned_candidate_publishes_through_the_hub(release_state):
    f = release_state
    result = service.build_release(
        f.db, on(approved(f.bundle), "partitioned"), f.clock, f.run, f.hub
    )
    published = publish(f.db.state_dir, result.path, f.hub)
    assert published.state == "published" and f.hub.calls == 1
    receipt = json.loads((result.path / "PUBLISHED").read_bytes())
    assert receipt["manifest_digest"] == policy(result)["manifest_digest"]
    assert receipt["evidence_cutoff"] == policy(result)["evidence_cutoff"]


def test_a_changed_artifact_file_refuses_publication(release_state):
    f = release_state
    result = service.build_release(
        f.db, on(approved(f.bundle), "partitioned"), f.clock, f.run, f.hub
    )
    from swingset.state.retention import manifest_file

    value = json.loads(
        manifest_file(f.db.state_dir, policy(result)["manifest_digest"]).read_bytes()
    )
    _, artifact = value["built"][0]
    sidecar = artifact_store.sidecar(f.db.state_dir, artifact)
    name = next(iter(sidecar["tables"].values()))["file"]
    path = artifact_directory(f.db.state_dir, artifact) / name
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(PublishError, match="release manifest or artifact changed"):
        publish(f.db.state_dir, result.path, f.hub)
    assert f.hub.calls == 0
