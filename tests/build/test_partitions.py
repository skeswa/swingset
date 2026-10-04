"""Partition artifacts: stage 1 of a partitioned release (plan phase 3, WP6)."""

import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from test_h15_acceptance import add_dancer, drain_project, link_all
from test_h15_acceptance import source_fixture as source_fixture
from test_h16_acceptance import release_state as release_state
from test_project_event import EVENT
from test_release_closure_rows import baseline_events

from swingset.build import artifact_store, partitions
from swingset.build.closure import select as select_closure
from swingset.build.closure_rows import reconstruct
from swingset.build.input import _convert
from swingset.build.partitions import _TABLES, INTERNAL_SCHEMAS
from swingset.build.schema import SCHEMAS
from swingset.build.support_closure import _EventScopes
from swingset.state import collector, derivations, release_store
from swingset.state.release_store import ArtifactRow, PartitionRow
from swingset.state.retention import artifact_directory, partition_root
from swingset.state.work import WorkUnit


def settled(f, tmp_path, tables=()):
    """The offline source fixture with a dancer, projected and linked, and a baseline.

    The baseline publishes the live events (and ``tables``), so their legacy
    rows stay, as `reconstruct` keeps them.
    """
    add_dancer(f)
    drain_project(f)
    link_all(f)
    path = baseline_events(f, tmp_path / "baseline")
    if tables:
        import pyarrow.parquet as pq

        from swingset.build.files import sha256_file
        from swingset.build.input import _read_table

        manifest = json.loads((path / "_meta/manifest.json").read_text())
        for table in tables:
            directory = path / "data" / table
            directory.mkdir(parents=True)
            pq.write_table(
                pa.Table.from_pylist(_read_table(f.conn, table), schema=SCHEMAS[table]),
                directory / "part.parquet",
            )
            name = f"data/{table}/part.parquet"
            manifest["files"][name] = sha256_file(directory / "part.parquet")
        (path / "_meta/manifest.json").write_text(json.dumps(manifest))
    return path


def build(f, baseline, clock=None, run=None, **kwargs):
    clock = clock or f.corpus.clock
    return partitions.build(
        f.db,
        history_start=f.bundle.config.history_start,
        baseline=baseline,
        run_id=run or f.corpus.run,
        now=clock.now(),
        **kwargs,
    )


def rows(conn):
    return {row.partition: row for row in release_store.all_rows(conn, PartitionRow)}


def public(table, row):
    schema = SCHEMAS.get(table) or INTERNAL_SCHEMAS[table]
    value = dict(row)
    if table == "coverage":
        value.update(scope_kind="year", scope_id=str(value["year"]))
    return {field.name: _convert(value.get(field.name), field.type) for field in schema}


def arrow(table, values):
    schema = SCHEMAS.get(table) or INTERNAL_SCHEMAS[table]
    return pa.Table.from_pylist(values, schema=schema).to_pylist()


def ordered(values):
    return sorted(values, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def reconstructed(conn, closure, directory, baseline):
    """Reconstruct's rows, by the partition each would fall in (the parity helper)."""
    with reconstruct(conn, closure, directory=directory, baseline=baseline) as spool:
        tables = {
            name: [public(name, row) for row in spool.iter_table(name)]
            for name in spool.table_names()
            if name in SCHEMAS or name in INTERNAL_SCHEMAS
        }
    scopes = _EventScopes(tables)
    subjects = {("entry", row["entry_id"]): row["event_id"] for row in tables.get("entries", [])}
    subjects |= {("judge", row["judge_id"]): row["event_id"] for row in tables.get("judges", [])}
    result: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for table, values in tables.items():
        for row in values:
            if table in {"dancers", "registry_placements"}:
                name = f"dancers/{row['wsdc_id'] // partitions.DANCER_BATCH}"
            elif table == "coverage":
                name = f"year/{row['year']}"
            elif table in {"identity_links", "link_candidates"}:
                name = f"event/{subjects[(row['subject_kind'], row['subject_id'])]}"
            elif table == "source_event_map":
                name = f"event/{row['event_id']}"
            else:
                (event,) = scopes.events(row)
                name = f"event/{event}"
            result[name][table].append(row)
    return result


def assert_parity(conn, state_dir, closure, directory, baseline):
    expected = reconstructed(conn, closure, directory, baseline)
    built = {name: row for name, row in rows(conn).items() if row.state == "built"}
    assert set(expected) <= set(built), set(expected) - set(built)
    for name, row in built.items():
        artifact = partitions.rows_for(state_dir, row.artifact_digest)
        kind = name.split("/")[0]
        for table in _TABLES[kind]:
            want = ordered(arrow(table, expected.get(name, {}).get(table, [])))
            assert ordered(artifact.get(table, [])) == want, (name, table)
    return built


def test_an_event_artifact_equals_the_rows_reconstruct_yields(release_state, tmp_path):
    f = release_state
    report = build(f, f.baseline, clock=f.clock, run=f.run)
    assert report["rebuilt"] == report["partitions"] and report.get("withheld", 0) == 0
    closure = select_closure(f.conn, cutoff=f.clock.now(), baseline=f.baseline)
    built = assert_parity(f.conn, f.db.state_dir, closure, tmp_path / "spool", f.baseline)
    event = partitions.rows_for(f.db.state_dir, built[f"event/{EVENT}"].artifact_digest)
    # The legacy rows the baseline matches stay, with links and the source map.
    assert event["callback_marks"] and event["identity_links"] and event["source_event_map"]
    sidecar = artifact_store.sidecar(f.db.state_dir, built[f"event/{EVENT}"].artifact_digest)
    assert sidecar["counts"]["legacy_unassessed_retained"] > 0
    assert sidecar["inputs"]["baseline"] is not None


def test_dancer_and_year_artifacts_equal_reconstruct(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path, tables=("dancers", "registry_placements"))
    report = build(f, baseline)
    assert report.get("withheld", 0) == 0
    f.corpus.clock.sleep(60)
    closure = select_closure(f.conn, cutoff=f.corpus.clock.now(), baseline=baseline)
    built = assert_parity(f.conn, f.db.state_dir, closure, tmp_path / "spool", baseline)
    assert {"dancers/1", "year/2026", "event/event-a"} <= set(built)
    placement = partitions.rows_for(f.db.state_dir, built["dancers/1"].artifact_digest)[
        "registry_placements"
    ]
    # The year owns the association; the dancer's own rows do not name it.
    assert [row["event_id"] for row in placement] == ["2026-01-registry-joined-weekend"]


def test_an_unsupported_dancer_leaves_its_batch_and_is_listed(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    row = rows(f.conn)["dancers/1"]
    assert row.state == "built"
    sidecar = artifact_store.sidecar(f.db.state_dir, row.artifact_digest)
    # Legacy and not in the baseline: reconstruct omits it too.
    assert sidecar["withheld_subjects"] == {"dancers": [[999, "legacy_owned_values_not_published"]]}
    assert partitions.rows_for(f.db.state_dir, row.artifact_digest) == {}


def remint(
    f,
    unit,
    change=lambda table, key, payload: payload,
    extra=(),
    *,
    relink=True,
    dependency_set=None,
):
    """A new generation of one scope with changed output, and nothing else.

    It is written as a receipt directly: `derivations.complete` refuses a new
    output for unchanged inputs, and here only the output is meant to change.
    An event's link read the old generation, so it runs again (it cuts off:
    live rows are unchanged) unless ``relink`` is false, which leaves the
    partition's generations inconsistent, as a build mid-drain meets them.
    """
    import hashlib

    from swingset.build.closure_manifest import canonical

    conn = f.conn
    current = derivations.selected_generation(conn, unit)
    output = []
    for table, key, raw in conn.execute(
        "SELECT table_name,record_key,payload_json FROM derivation_rows WHERE generation_id=? ORDER BY ordinal",
        (current,),
    ):
        payload = change(table, key, json.loads(raw))
        if payload is not None:
            output.append((table, key, payload))
    output.extend(extra)
    f.corpus.clock.sleep(1)
    now = f.corpus.clock.now().isoformat()
    label = conn.execute(
        "SELECT input_fingerprint,recipe_json,dependency_set_id FROM derivation_generations WHERE generation_id=?",
        (current,),
    ).fetchone()
    digest = hashlib.sha256()
    created = "dg_" + hashlib.sha256(canonical([current, output]).encode()).hexdigest()
    with f.db.transaction():
        for ordinal, (table, key, payload) in enumerate(output):
            stored = canonical(payload)
            sha = hashlib.sha256(stored.encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (sha, stored))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (created, ordinal, table, key, sha),
            )
            digest.update(canonical((table, key, stored)).encode() + b"\n")
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                created,
                unit.stage,
                unit.unit_kind,
                unit.unit_id,
                label[0],
                label[1],
                dependency_set or label[2],
                current,
                digest.hexdigest(),
                len(output),
                now,
                f.corpus.run,
            ),
        )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=? "
            "WHERE stage=? AND unit_kind=? AND unit_id=?",
            (created, unit.stage, unit.unit_kind, unit.unit_id),
        )
    if relink and (unit.stage, unit.unit_kind) == ("project", "event"):
        link_all(f)
    return created


def test_a_corrupt_event_withholds_only_itself(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path)
    first = build(f, baseline)
    before = rows(f.conn)
    assert all(row.state == "built" for row in before.values())

    def duplicate_place(table, key, payload):
        return {**payload, "place": 2} if table == "placements" else payload

    remint(f, WorkUnit("project", "event", "event-a"), duplicate_place)
    second = build(f, baseline)
    after = rows(f.conn)
    assert after["event/event-a"].state == "withheld"
    assert after["event/event-a"].reason == "invalid_placements"
    assert "not contiguous" in json.loads(after["event/event-a"].detail_json)["withheld"]["message"]
    assert after["event/event-a"].artifact_digest is None
    for name, row in after.items():
        if name != "event/event-a":
            assert row == before[name]
    assert second["rebuilt"] == 1 and second["reused"] == first["partitions"] - 1
    assert second["withheld:invalid_placements"] == 1


def test_a_stale_link_over_a_reminted_event_keeps_the_last_consistent_artifact(
    source_fixture, tmp_path
):
    """Blocker 1: a re-minted event whose link has not re-run is not merged
    with the old link's identity rows; the partition keeps what it last built,
    and builds anew once the link is current again."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    before = rows(f.conn)["event/event-a"]
    link = WorkUnit("link", "event", "event-a")

    def rename(table, key, payload):
        return (
            {**payload, "name_raw": "Totally Different Person"} if table == "entries" else payload
        )

    remint(f, WorkUnit("project", "event", "event-a"), rename, relink=False)
    assert not derivations.current(f.conn, link)
    report = build(f, baseline, clock=f.corpus.clock)
    kept = rows(f.conn)["event/event-a"]
    since = f.corpus.clock.now().isoformat()
    # The same artifact and inputs; the row notes since when it is kept stale.
    assert replace(kept, detail_json=before.detail_json) == before
    assert json.loads(kept.detail_json)["kept_not_current_since"] == since
    assert report["kept_not_current"] == 1
    # A later build keeps the first time.
    f.corpus.clock.sleep(3600)
    assert build(f, baseline)["kept_not_current"] == 1
    assert json.loads(rows(f.conn)["event/event-a"].detail_json)["kept_not_current_since"] == since
    # Once the link is current the event is built from consistent generations.
    link_all(f)
    build(f, baseline)
    after = rows(f.conn)["event/event-a"]
    assert after.state == "built" and after.artifact_digest != before.artifact_digest
    assert "kept_not_current_since" not in json.loads(after.detail_json)
    entries = partitions.rows_for(f.db.state_dir, after.artifact_digest)["entries"]
    assert {row["name_raw"] for row in entries} == {"Totally Different Person"}
    # A partition that never built and whose units are not current is withheld.
    with f.db.transaction():
        f.conn.execute("DELETE FROM release_partitions WHERE partition='event/event-a'")
    remint(f, WorkUnit("project", "event", "event-a"), relink=False)
    build(f, baseline)
    withheld = rows(f.conn)["event/event-a"]
    assert withheld.state == "withheld" and withheld.reason == "units_not_current"
    assert json.loads(withheld.detail_json)["withheld"]["units"] == ["link/event/event-a"]


def test_a_listing_two_partitions_claim_stays_with_the_current_one(source_fixture, tmp_path):
    """A map move built mid-drain: the moved-from partition's kept artifact and
    the moved-to partition's new one hold the same listing. Only one keeps it:
    the partition whose units are current, else the event the live map names."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    claim = {
        "source": "eepro",
        "source_ref": "eepro:test",
        "event_id": "event-b",
        "match_method": "name_date",
        "match_confidence": 1.0,
    }
    remint(
        f,
        WorkUnit("project", "event", "event-b"),
        extra=(("source_event_map", '["eepro","eepro:test"]', claim),),
    )
    # event-a's artifact claims the listing; its link is now stale, so the
    # partition keeps that artifact while event-b's is built with the claim.
    remint(f, WorkUnit("project", "event", "event-a"), relink=False)
    report = build(f, baseline)
    after = rows(f.conn)
    assert after["event/event-b"].state == "built"
    loser = after["event/event-a"]
    assert (loser.state, loser.reason) == ("withheld", "listing_claimed_elsewhere")
    assert json.loads(loser.detail_json)["withheld"] == {
        "listing": ["eepro", "eepro:test"],
        "claimed_by": "event/event-b",
    }
    assert report["withheld:listing_claimed_elsewhere"] == 1
    # Both current: the live map names event-a, so event-b's claim loses.
    link_all(f)
    build(f, baseline)
    after = rows(f.conn)
    assert after["event/event-a"].state == "built"
    assert after["event/event-b"].reason == "listing_claimed_elsewhere"
    # The loser is contested again every build, not reused: once event-a no
    # longer claims the listing, event-b is built with it.
    build(f, baseline)
    assert rows(f.conn)["event/event-b"].reason == "listing_claimed_elsewhere"

    def drop(table, key, payload):
        return None if (table, key) == ("source_event_map", '["eepro","eepro:test"]') else payload

    # event-a's claim is its resolution's `source_event_map` row.
    remint(f, WorkUnit("project", "resolution", "event-a"), drop)
    build(f, baseline)
    after = rows(f.conn)
    assert (after["event/event-b"].state, after["event/event-b"].reason) == ("built", None)
    # event-a's event unit now reads a newer resolution, so it keeps its last
    # artifact, which still claims the listing, and the current claim wins.
    loser = after["event/event-a"]
    assert (loser.state, loser.reason) == ("withheld", "listing_claimed_elsewhere")
    assert json.loads(loser.detail_json)["withheld"]["claimed_by"] == "event/event-b"
    listings = partitions.rows_for(f.db.state_dir, after["event/event-b"].artifact_digest)
    assert ["eepro:test"] == [row["source_ref"] for row in listings["source_event_map"]]


@pytest.mark.parametrize(
    "change",
    [
        ("placements", "place", None),
        ("placements", "place", "first"),
        ("rounds", "judge_count", "many"),
    ],
)
def test_one_malformed_row_withholds_only_its_partition(source_fixture, tmp_path, change):
    """I1: a row that makes stage 1 raise (a null or text place in the
    builder's check, text in an integer column at the Arrow conversion)
    withholds its partition with the error; the rest are reused."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    first = build(f, baseline)
    before = rows(f.conn)
    table_name, field, value = change

    def mutate(table, key, payload):
        return {**payload, field: value} if table == table_name else payload

    remint(f, WorkUnit("project", "event", "event-a"), mutate)
    report = build(f, baseline)
    after = rows(f.conn)
    row = after["event/event-a"]
    assert row.state == "withheld"
    detail = json.loads(row.detail_json)["withheld"]
    assert row.reason == "partition_error"
    assert detail["class"] and detail["message"]
    for name, other in after.items():
        if name != "event/event-a":
            assert other == before[name]
    assert report["rebuilt"] == 1 and report["reused"] == first["partitions"] - 1


@pytest.mark.parametrize("change", ["contest_without_id", "round_with_a_dict_contest"])
def test_a_generation_selection_cannot_read_withholds_only_its_partition(
    source_fixture, tmp_path, change
):
    """I1 at selection: a row that makes `events_of` raise (a contest with no
    `contest_id`, an unhashable `contest_id`) withholds its partition as
    `partition_error`; stage 1 and every other partition go on."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    first = build(f, baseline)
    before = rows(f.conn)

    def mutate(table, key, payload):
        if change == "contest_without_id" and table == "contests":
            return {name: value for name, value in payload.items() if name != "contest_id"}
        if change == "round_with_a_dict_contest" and table == "rounds":
            return {**payload, "contest_id": {"x": 1}}
        return payload

    remint(f, WorkUnit("project", "event", "event-a"), mutate)
    report = build(f, baseline)
    after = rows(f.conn)
    row = after["event/event-a"]
    assert (row.state, row.reason) == ("withheld", "partition_error")
    detail = json.loads(row.detail_json)["withheld"]
    assert detail["class"] in {"KeyError", "TypeError"} and detail["scope"] == (
        "project/event/event-a"
    )
    assert {name for name, other in after.items() if other.state == "withheld"} == {"event/event-a"}
    for name, other in after.items():
        if name != "event/event-a":
            assert other == before[name]
    assert report["partition_errors"] == 1 and report["reused"] == first["partitions"] - 1
    # Never remembered as read: the next build tries again, and withholds again.
    again = build(f, baseline)
    assert rows(f.conn)["event/event-a"].reason == "partition_error"
    assert again["partition_errors"] == 1


def test_a_change_of_support_alone_rebuilds_the_partition(source_fixture, tmp_path):
    """The partition's inputs name each snapshot's support standing: with its
    generations unchanged, losing the admission decision withholds the event,
    and restoring it builds the event again."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    before = rows(f.conn)["event/event-a"]
    assert before.state == "built"
    decisions = [
        dict(row)
        for row in f.conn.execute(
            "SELECT * FROM admission_decisions WHERE generation_id=?", (f.generation,)
        )
    ]
    assert decisions
    with f.db.transaction():
        f.conn.execute("DELETE FROM admission_decisions WHERE generation_id=?", (f.generation,))
    report = build(f, baseline)
    withheld = rows(f.conn)["event/event-a"]
    assert withheld.inputs_digest != before.inputs_digest
    assert (withheld.state, withheld.reason) == ("withheld", "source_support_unavailable")
    assert report["rebuilt"] >= 1
    with f.db.transaction():
        for decision in decisions:
            names = ",".join(decision)
            marks = ",".join("?" for _ in decision)
            f.conn.execute(
                f"INSERT INTO admission_decisions({names}) VALUES ({marks})",
                tuple(decision.values()),
            )
    build(f, baseline)
    after = rows(f.conn)["event/event-a"]
    assert (after.state, after.reason) == ("built", None)
    assert after.inputs_digest == before.inputs_digest
    assert after.artifact_digest == before.artifact_digest


def test_a_dancer_generation_joins_the_batch_of_the_ids_its_rows_carry(source_fixture, tmp_path):
    """A lookup whose scope id is not the WSDC id its rows carry lands in the
    rows' batch, where the year patches its placements' association."""
    f = source_fixture
    baseline = settled(f, tmp_path, tables=("dancers", "registry_placements"))
    unit = WorkUnit("project", "dancer", "999")
    generation = derivations.selected_generation(f.conn, unit)
    label = f.conn.execute(
        "SELECT * FROM derivation_generations WHERE generation_id=?", (generation,)
    ).fetchone()
    with f.db.transaction():
        f.conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at,"
            "materialized_generation_id) VALUES ('project','dancer','lookup-redirect',?,NULL)",
            (label["created_at"],),
        )
        f.conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "dg_redirect",
                "project",
                "dancer",
                "lookup-redirect",
                label["input_fingerprint"],
                label["recipe_json"],
                label["dependency_set_id"],
                None,
                label["output_digest"],
                label["row_count"],
                label["created_at"],
                label["run_id"],
            ),
        )
        f.conn.execute(
            "INSERT INTO derivation_row_refs SELECT 'dg_redirect',ordinal,table_name,record_key,"
            "payload_sha256 FROM derivation_row_refs WHERE generation_id=?",
            (generation,),
        )
        f.conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id='dg_redirect' "
            "WHERE unit_kind='dancer' AND unit_id='lookup-redirect'"
        )
        f.conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=NULL "
            "WHERE unit_kind='dancer' AND unit_id='999'"
        )
    report = build(f, baseline)
    built = rows(f.conn)
    assert "dancers/unknown" not in built
    placement = partitions.rows_for(f.db.state_dir, built["dancers/1"].artifact_digest)[
        "registry_placements"
    ]
    assert [row["event_id"] for row in placement] == ["2026-01-registry-joined-weekend"]
    assert not report.get("partial_overlay_without_structural_base")


def _observation_changed(f, generation, **changes):
    """A copy of a generation's dependency set whose round-one observation differs."""
    import hashlib

    from swingset.build.closure_manifest import canonical

    conn = f.conn
    set_id = conn.execute(
        "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
        (generation,),
    ).fetchone()[0]
    manifest = json.loads(
        conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (set_id,),
        ).fetchone()[0]
    )
    changed = []
    for item in manifest:
        if item.get("kind") == "dependency_set" and item.get("key") == "source_observations":
            members = json.loads(
                conn.execute(
                    "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                    (item["fingerprint"],),
                ).fetchone()[0]
            )
            members = [
                {**leaf, **changes} if leaf.get("snapshot_id") == "round-one" else leaf
                for leaf in members
            ]
            body = canonical(members)
            fingerprint = hashlib.sha256(body.encode()).hexdigest()
            conn.execute(
                "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES (?,?)",
                (fingerprint, body),
            )
            item = {**item, "fingerprint": fingerprint}
        changed.append(item)
    body = canonical(changed)
    identifier = hashlib.sha256(body.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES (?,?)",
        (identifier, body),
    )
    return identifier


@pytest.mark.parametrize(
    "changes", [{"payload_sha256": "0" * 64}, {"extract_version": "999"}, {"seq": 7}]
)
def test_support_is_matched_per_observation(source_fixture, tmp_path, changes):
    """D-0022: an accepted generation of the snapshot and parser is not
    enough; it must contain the observation the rows came from (another
    extract version or payload is legacy, as in closure_support)."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    assert rows(f.conn)["event/event-a"].state == "built"
    event = WorkUnit("project", "event", "event-a")
    changed = _observation_changed(f, derivations.selected_generation(f.conn, event), **changes)
    remint(f, event, dependency_set=changed)
    build(f, baseline)
    row = rows(f.conn)["event/event-a"]
    # Legacy rows the baseline does not publish withhold the event.
    assert (row.state, row.reason) == ("withheld", "source_support_unavailable")
    assert json.loads(row.detail_json)["legacy"] is True


def test_support_reads_the_newest_equivalence_of_a_kept_generation(source_fixture, tmp_path):
    """A re-run with unchanged output keeps the generation and records its new
    read set as an equivalence. Support matches that set, not the one saved
    with the generation: after a relabel (contract 5's extract 4 to 5) the
    old set names observations no accepted generation holds any more. A
    withheld verdict built on the old set is not reused once the new set
    lands."""
    import hashlib

    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    first = rows(f.conn)["event/event-a"]
    assert first.state == "built"
    event = WorkUnit("project", "event", "event-a")
    current = derivations.selected_generation(f.conn, event)
    label = f.conn.execute(
        "SELECT recipe_json,dependency_set_id FROM derivation_generations WHERE generation_id=?",
        (current,),
    ).fetchone()
    # The generation was made when its observations carried another label.
    stale = _observation_changed(f, current, extract_version="stale-label")
    kept = remint(f, event, dependency_set=stale)
    build(f, baseline)
    row = rows(f.conn)["event/event-a"]
    assert (row.state, row.reason) == ("withheld", "source_support_unavailable")
    # Its unchanged-output re-run read the relabelled observations.
    f.corpus.clock.sleep(1)
    recipe = hashlib.sha256(label["recipe_json"].encode()).hexdigest()
    with f.db.transaction():
        f.conn.execute(
            "INSERT OR IGNORE INTO derivation_recipes VALUES (?,?)", (recipe, label["recipe_json"])
        )
        f.conn.execute(
            "INSERT INTO derivation_equivalences VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "project",
                "event",
                "event-a",
                "relabelled-inputs",
                kept,
                recipe,
                label["dependency_set_id"],
                f.corpus.clock.now().isoformat(),
                f.corpus.run,
            ),
        )
    report = build(f, baseline)
    row = rows(f.conn)["event/event-a"]
    assert (row.state, row.reason) == ("built", None)
    assert report["rebuilt"] >= 1
    # The same rows, standing on the same support, as before the relabel.
    assert json.loads(row.detail_json)["counts"] == json.loads(first.detail_json)["counts"]
    assert {
        table: len(values)
        for table, values in partitions.rows_for(f.db.state_dir, row.artifact_digest).items()
    } == {
        table: len(values)
        for table, values in partitions.rows_for(f.db.state_dir, first.artifact_digest).items()
    }


def test_a_revoked_snapshot_withholds_its_event(source_fixture, tmp_path):
    from swingset.admission.select import revoke_generation

    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    revoke_generation(
        f.db,
        f.generation,
        reason="Synthetic adjudication proved the sheet wrong",
        now=f.corpus.clock.now().isoformat(),
        run_id=f.corpus.run,
    )
    build(f, baseline)
    after = rows(f.conn)
    assert after["event/event-a"].state == "withheld"
    assert after["event/event-a"].reason == "source_revoked"
    assert {name for name, row in after.items() if row.state == "withheld"} == {"event/event-a"}


def _rewrite_baseline_events(baseline, change):
    """Rewrite the baseline's `events` file through ``change`` and re-hash it."""
    import pyarrow.parquet as pq

    from swingset.build.files import sha256_file

    path = baseline / "data/events/part.parquet"
    values = change(pq.read_table(path).to_pylist())
    pq.write_table(pa.Table.from_pylist(values, schema=SCHEMAS["events"]), path)
    manifest = json.loads((baseline / "_meta/manifest.json").read_text())
    manifest["files"]["data/events/part.parquet"] = sha256_file(path)
    (baseline / "_meta/manifest.json").write_text(json.dumps(manifest))


@pytest.mark.parametrize("published", [True, False])
def test_a_rejected_legacy_header_publishes_the_baseline_row(source_fixture, tmp_path, published):
    """D-0251: a legacy header that differs from the baseline no longer
    withholds its event. The baseline's own row stands in, with its source
    columns and provenance, and the event's accepted results publish. An
    event the baseline lacks stays withheld."""
    f = source_fixture
    baseline = settled(f, tmp_path)
    live = next(row for row in _read_events(f) if row["event_id"] == "event-a")

    def change(values):
        if not published:
            return [row for row in values if row["event_id"] != "event-a"]
        return [
            {**row, "city": "Published City", "website": "https://published.example"}
            if row["event_id"] == "event-a"
            else row
            for row in values
        ]

    _rewrite_baseline_events(baseline, change)
    build(f, baseline)
    row = rows(f.conn)["event/event-a"]
    if not published:
        assert (row.state, row.reason) == ("withheld", "source_support_unavailable")
        return
    assert (row.state, row.reason) == ("built", None)
    detail = json.loads(row.detail_json)
    assert detail["counts"]["legacy_owned_values_not_published"] >= 1
    assert detail["counts"]["baseline_header_republished"] == 1
    artifact = partitions.rows_for(f.db.state_dir, row.artifact_digest)
    (header,) = artifact["events"]
    # Every source column is the baseline's, including where it came from.
    assert (header["city"], header["website"]) == ("Published City", "https://published.example")
    assert (header["snapshot_id"], header["source"]) == (live["snapshot_id"], live["source"])
    assert artifact["placements"] and artifact["entries"]
    # Reconstruct (the closure path) makes the same choice.
    closure = select_closure(f.conn, cutoff=f.corpus.clock.now(), baseline=baseline)
    with reconstruct(f.conn, closure, directory=tmp_path / "spool", baseline=baseline) as spool:
        events = {item["event_id"]: item for item in spool.iter_table("events")}
        assert events["event-a"]["city"] == "Published City"
        assert spool.counts["baseline_header_republished"] == 1
        assert any(item["event_id"] == "event-a" for item in spool.iter_table("placements"))


def _read_events(f):
    from swingset.build.input import _read_table

    return _read_table(f.conn, "events")


def test_a_row_naming_two_events_joins_them(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    assert {"event/event-a", "event/event-b"} <= set(rows(f.conn))
    # A contest of event-a's generation that names event-b.
    contest = next(
        json.loads(raw)
        for (raw,) in f.conn.execute(
            "SELECT payload_json FROM derivation_rows WHERE generation_id=? AND table_name='contests'",
            (derivations.selected_generation(f.conn, WorkUnit("project", "event", "event-a")),),
        )
    )
    shared = {**contest, "contest_id": "event-b/shared", "event_id": "event-b"}
    remint(
        f,
        WorkUnit("project", "event", "event-a"),
        extra=(("contests", '["event-b/shared"]', shared),),
    )
    report = build(f, baseline)
    after = rows(f.conn)
    assert "event/event-a+event-b" in after
    assert "event/event-a" not in after and "event/event-b" not in after
    assert report["multi_event"] == 1 and report["stale_removed"] == 2
    joined = partitions.rows_for(f.db.state_dir, after["event/event-a+event-b"].artifact_digest)
    assert {row["contest_id"] for row in joined["contests"]} >= {"event-b/shared"}


def test_unchanged_inputs_rebuild_nothing_and_one_new_generation_rebuilds_one(
    source_fixture, tmp_path
):
    f = source_fixture
    baseline = settled(f, tmp_path)
    first = build(f, baseline)
    digests = {name: row.artifact_digest for name, row in rows(f.conn).items()}
    again = build(f, baseline)
    assert again["rebuilt"] == 0 and again["reused"] == first["partitions"]
    assert {name: row.artifact_digest for name, row in rows(f.conn).items()} == digests

    def rename(table, key, payload):
        return {**payload, "name_raw": "Renamed Entrant"} if table == "entries" else payload

    remint(f, WorkUnit("project", "event", "event-a"), rename)
    third = build(f, baseline)
    assert third["rebuilt"] == 1
    changed = {name for name, row in rows(f.conn).items() if row.artifact_digest != digests[name]}
    assert changed == {"event/event-a"}


CRASH = """
import os, sys
from datetime import date, datetime, timezone
from pathlib import Path
from swingset.build import artifact_store, partitions
from swingset.state.db import open_database

artifact_store._publish = lambda temporary, final: os._exit(9)
with open_database(Path(sys.argv[1]), lock=False) as database:
    partitions.build(
        database,
        history_start=date.fromisoformat(sys.argv[2]),
        baseline=Path(sys.argv[3]),
        run_id="crash",
        now=datetime.now(timezone.utc),
    )
"""


def test_a_crash_before_the_rename_leaves_no_row(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path)
    environment = {**os.environ, "PYTHONPATH": str(Path("src").resolve())}
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASH,
            str(f.db.state_dir),
            f.bundle.config.history_start.isoformat(),
            str(baseline),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert process.returncode == 9, process.stderr
    assert release_store.all_rows(f.conn, PartitionRow) == []
    assert release_store.all_rows(f.conn, ArtifactRow) == []
    root = partition_root(f.db.state_dir)
    leftovers = [path for path in root.iterdir() if path.name.startswith(".tmp-")]
    assert len(leftovers) == 1 and (leftovers[0] / "sidecar.json").is_file()
    assert not [path for path in root.glob("??/*")]
    # The collector removes the unfinished write once it is a day old.
    later = datetime.now(UTC) + timedelta(days=2)
    assert collector.collect_artifacts(f.db, now=later)["temporary"] == 1
    assert not leftovers[0].exists()
    report = build(f, baseline)
    assert report["built"] == report["partitions"]


def old(path, now):
    stamp = (now - timedelta(days=2)).timestamp()
    os.utime(path, (stamp, stamp))


def test_the_collector_keeps_named_artifacts_and_removes_old_orphans(source_fixture, tmp_path):
    f = source_fixture
    baseline = settled(f, tmp_path)
    build(f, baseline)
    now = f.corpus.clock.now()
    named = {row.artifact_digest for row in rows(f.conn).values()}
    state_dir = f.db.state_dir

    def orphan(label):
        table = pa.Table.from_pylist([{"label": label}])
        return artifact_store.write(state_dir, {"orphan": table}, {"partition": label})[0]

    rowless, stale, young, retained = (orphan(name) for name in ("a", "b", "c", "d"))
    with f.db.transaction() as conn:
        for digest, age in ((stale, 2), (young, 0), (retained, 2)):
            stamp = (now - timedelta(days=age)).isoformat()
            release_store.upsert(conn, ArtifactRow(digest, "event/gone", 1, stamp, stamp))
    for digest in (rowless, stale, retained):
        old(artifact_directory(state_dir, digest), now)
    for path in artifact_directory(state_dir, rowless).parent.parent.iterdir():
        if len(path.name) == 2:
            for child in path.iterdir():
                if child.name in named:
                    old(child, now)
    # A retained candidate's release manifest names one unnamed artifact.
    manifest = "f" * 64
    (partition_root(state_dir) / "manifests").mkdir(parents=True)
    (partition_root(state_dir) / "manifests" / f"{manifest}.json").write_text(
        json.dumps({"built": [["event/gone", retained]]})
    )
    candidate = state_dir / "candidates" / "cand_partitioned"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "_meta" / "manifest.json").write_text(
        json.dumps({"release_policy": {"mode": "partitioned", "manifest_digest": manifest}})
    )
    (candidate / "BUILT").write_text("{}")
    result = collector.collect_artifacts(f.db, now=now)
    assert result["removed"] == 2 and result["rows"] == 1
    assert not artifact_directory(state_dir, rowless).exists()
    assert not artifact_directory(state_dir, stale).exists()
    for digest in (young, retained, *named):
        assert artifact_store.exists(state_dir, digest)
    assert {row.artifact_digest for row in release_store.all_rows(f.conn, ArtifactRow)} >= {
        young,
        retained,
        *named,
    }
    # The next build still reuses every named artifact.
    assert build(f, baseline)["rebuilt"] == 0


def test_the_collector_removes_old_unretained_manifests_and_build_scratch(source_fixture, tmp_path):
    """I6: a release manifest and its row go a day after writing unless a
    retained candidate or a build pointer names it; so does killed-build
    scratch under state/tmp."""
    from swingset.state.release_store import ManifestRow
    from swingset.state.retention import manifest_file

    f = source_fixture
    state_dir = f.db.state_dir
    now = f.corpus.clock.now()
    digests = {
        name: letter * 64
        for name, letter in (
            ("old", "a"),
            ("young", "b"),
            ("retained", "c"),
            ("pointer", "d"),
            ("file", "e"),
        )
    }
    for name, digest in digests.items():
        path = manifest_file(state_dir, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"built": []}))
        if name != "young":
            old(path, now)
        if name != "file":
            stamp = now if name == "young" else now - timedelta(days=3)
            with f.db.transaction() as conn:
                release_store.upsert(conn, ManifestRow(digest, 20, stamp.isoformat()))
    for name, digest in (
        ("cand_retained", digests["retained"]),
        ("cand_pointer", digests["pointer"]),
    ):
        candidate = state_dir / "candidates" / name
        (candidate / "_meta").mkdir(parents=True)
        (candidate / "_meta" / "manifest.json").write_text(
            json.dumps({"release_policy": {"mode": "partitioned", "manifest_digest": digest}})
        )
    # A built candidate is among the newest three, so retained; the pointer's
    # candidate is not built, and only the build pointer names it.
    (state_dir / "candidates" / "cand_retained" / "BUILT").write_text("{}")
    with f.db.transaction() as conn:
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
            "VALUES ('build','release','all',?)",
            (now.isoformat(),),
        )
        conn.execute(
            "INSERT INTO derivation_payloads VALUES ('p',?)",
            (json.dumps({"path": str(state_dir / "candidates" / "cand_pointer")}),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES ('empty','[]')"
        )
        conn.execute(
            "INSERT INTO derivation_generations VALUES "
            "('g-build','build','release','all','f','{}','empty',NULL,'d',1,?,?)",
            (now.isoformat(), f.corpus.run),
        )
        conn.execute("INSERT INTO derivation_row_refs VALUES ('g-build',0,'artifact','[]','p')")
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id='g-build' WHERE stage='build'"
        )
    scratch = state_dir / "tmp"
    for name in ("partitions-a", "closure-b", "assembly-c", "partitions-young", "keep-me"):
        (scratch / name).mkdir(parents=True)
        (scratch / name / "x").write_bytes(b"12345")
        if name != "partitions-young":
            old(scratch / name / "x", now)
            old(scratch / name, now)
    result = collector.collect_artifacts(f.db, now=now)
    assert result["manifests"] == 2
    assert result["scratch"] == 3
    left = {row.manifest_digest for row in release_store.all_rows(f.conn, ManifestRow)}
    assert left == {digests["young"], digests["retained"], digests["pointer"]}
    for name in ("young", "retained", "pointer"):
        assert manifest_file(state_dir, digests[name]).exists()
    for name in ("old", "file"):
        assert not manifest_file(state_dir, digests[name]).exists()
    assert sorted(path.name for path in scratch.iterdir()) == ["keep-me", "partitions-young"]


def test_the_command_builds_lists_and_shows(source_fixture, tmp_path, capsys):
    from swingset import cli

    f = source_fixture
    settled(f, tmp_path)
    report = build(f, None)
    assert report["partitions"] > 0
    names = [row["partition"] for row in partitions.listing(f.db)]
    shown = partitions.show(f.db, names[0])
    assert shown["partition"] == names[0]
    assert cli.main(["partitions", "list", "--state", str(f.db.state_dir)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["partition"] for row in listed] == names


def test_a_reused_partition_whose_artifact_is_gone_is_rebuilt(source_fixture, tmp_path):
    import shutil

    f = source_fixture
    baseline = settled(f, tmp_path)
    first = build(f, baseline)
    row = rows(f.conn)["event/event-a"]
    shutil.rmtree(artifact_directory(f.db.state_dir, row.artifact_digest))
    again = build(f, baseline)
    assert again["rebuilt"] == 1 and again["reused"] == first["partitions"] - 1
    assert artifact_store.exists(f.db.state_dir, rows(f.conn)["event/event-a"].artifact_digest)


def test_the_evidence_cutoff_counts_accepted_source_generations(source_fixture, tmp_path):
    """F24: the newest created_at among the generations and the accepted
    source generations the artifacts name, never the clock."""
    from swingset.build import partitioned
    from swingset.build.manifest import evidence_cutoff

    f = source_fixture
    baseline = settled(f, tmp_path)
    source = f.conn.execute(
        "SELECT created_at FROM source_generations WHERE generation_id=?", (f.generation,)
    ).fetchone()[0]
    assert (
        evidence_cutoff(f.conn, [], [f.generation])
        == datetime.fromisoformat(source.replace("Z", "+00:00")).isoformat()
    )
    release = partitioned.prepare(
        f.db,
        f.bundle.file_hashes,
        history_start=f.bundle.config.history_start,
        baseline=baseline,
        run_id=f.corpus.run,
        now=f.corpus.clock.now(),
    )
    named = sorted({g for item in release.support for g in item[3]})
    assert f.generation in named
    generations = {
        g for sidecar in release.sidecars.values() for g in sidecar.get("generations", ())
    }
    assert release.value["evidence_cutoff"] == evidence_cutoff(f.conn, generations, named)


def test_an_artifact_collected_mid_build_is_written_again_at_once(
    source_fixture, tmp_path, monkeypatch
):
    """A collector race costs one more stage 1, not a build interval."""
    from swingset.build import partitioned

    f = source_fixture
    baseline = settled(f, tmp_path)
    original = partitions.build
    calls = []

    def racing(*args, **kwargs):
        report = original(*args, **kwargs)
        calls.append(report)
        return {**report, "collected_while_building": 1} if len(calls) == 1 else report

    monkeypatch.setattr(partitions, "build", racing)
    release = partitioned.prepare(
        f.db,
        f.bundle.file_hashes,
        history_start=f.bundle.config.history_start,
        baseline=baseline,
        run_id=f.corpus.run,
        now=f.corpus.clock.now(),
    )
    assert len(calls) == 2 and release.report["collected_retried"] == 1
    assert release.built


def test_the_selection_reads_one_snapshot_while_a_generation_is_evicted(source_fixture, tmp_path):
    """A pointer read by `pointers` whose references another connection then
    moves out (a flip and an eviction by maintain beside the build) still
    gives its whole reference list: the selection reads one snapshot."""
    import sqlite3

    f = source_fixture
    settled(f, tmp_path)
    conn = f.db.connection
    before, _ = partitions.select(conn)
    shared = [
        generation
        for generation in partitions.pointers(conn)
        if generation.unit_kind in {"calendar", "inventory", "year"} and generation.row_count
    ]
    assert shared
    target = shared[0].generation_id
    real = partitions.pointers

    def then_evicted(conn_):
        found = real(conn_)
        other = sqlite3.connect(f.db.state_dir / "state.sqlite", isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute(
                "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) "
                "VALUES (1,'test','2026-10-01T00:00:00Z')"
            )
            other.execute("DELETE FROM derivation_row_refs WHERE generation_id=?", (target,))
            other.execute(
                "INSERT INTO derivation_evicted VALUES (?,'2026-10-01T00:00:00Z')", (target,)
            )
            other.execute("DELETE FROM storage_eviction_authority")
            other.execute("COMMIT")
        finally:
            other.close()
        return found

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(partitions, "pointers", then_evicted)
        after, _ = partitions.select(conn)
    assert [(p.name, len(p.shared)) for p in after] == [(p.name, len(p.shared)) for p in before]
