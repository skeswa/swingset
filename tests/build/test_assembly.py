"""The streamed assembly publishes the `whole` assembly's rows (plan phase 3, WP8)."""

import importlib.util
import json
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from build.test_partitions import remint, settled
from build.test_release_paths import link_baseline, on, policy
from test_h15_acceptance import source_fixture as source_fixture
from test_h16_acceptance import release_state as release_state

from swingset.build import assembly, service
from swingset.build.assembly import Meter, close_keys
from swingset.state.inputs import accept
from swingset.state.work import WorkUnit

MODULE_PATH = Path("journal/tools/releases/compare_candidates.py")
SPEC = importlib.util.spec_from_file_location("compare_candidates", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


def rows(result, table):
    return [
        row
        for path in sorted((result.path / "data" / table).glob("*.parquet"))
        for row in pq.read_table(path).to_pylist()
    ]


def both(f, bundle, clock, run):
    """`whole` and `partitioned` candidates of one state at one instant."""
    whole = service.build_release(f.db, on(bundle, "whole"), clock, run)
    streamed = service.build_release(f.db, on(bundle, "partitioned"), clock, run)
    assert not whole.reused and not streamed.reused
    return whole, streamed


def assert_same_rows(whole, streamed):
    report = COMPARE.compare(whole.path, streamed.path)
    assert report["tables_only_in_base"] == report["tables_only_in_new"] == []
    assert report["differing_tables"] == [], {
        table: {
            key: report["tables"][table][key]
            for key in ("rows", "added_keys", "removed_keys", "changed_keys", "changed_columns")
            if key in report["tables"][table]
        }
        for table in report["differing_tables"]
    }
    left = json.loads((whole.path / "_meta/manifest.json").read_bytes())
    right = json.loads((streamed.path / "_meta/manifest.json").read_bytes())
    assert left["row_counts"] == right["row_counts"]
    for key in ("source_snapshot_counts", "calendar_horizon", "latest_event_covered"):
        assert left[key] == right[key], key
    # The policies differ only in the path and its trigger.
    for value in (left["release_policy"], right["release_policy"]):
        value.pop("path")
        value.pop("build_trigger")
    assert left["release_policy"] == right["release_policy"]
    assert (whole.path / "README.md").read_bytes() == (streamed.path / "README.md").read_bytes()
    assert right["release_policy"]["mode"] == "partitioned"


def with_suppressions(f, body: bytes):
    files = {**f.bundle.files, "overrides/suppressions.csv": body}
    bundle = replace(f.bundle, digest=sha256(body + b"|suppressions").hexdigest(), files=files)
    accept(f.db, bundle, f.clock)
    return bundle


def linked(f, tmp_path):
    """The source fixture with a baseline, and Alice's entry joined to dancer 999.

    The join is written into the link's generation as a confirmed manual
    link with a candidate row, so the identity policy decides a proposed
    join (and, lacking a positive decision, withholds it).
    """
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    alice = "event-a/novice-jj/L-name-alice-example"

    def join(table, key, payload):
        if table == "entries" and payload["entry_id"] == alice:
            return {**payload, "wsdc_id": 999, "link_status": "confirmed", "link_confidence": 1.0}
        if table == "identity_links" and payload["subject_id"] == alice:
            return {**payload, "wsdc_id": 999, "status": "confirmed", "method": "manual"}
        if table == "placements":
            return {**payload, "leader_wsdc_id": 999, "registry_points_leader": 1}
        return payload

    candidate = {
        "subject_kind": "entry",
        "subject_id": alice,
        "wsdc_id": 999,
        "score": 0.9,
        "rank": 1,
        "chosen": 1,
        "registry_confirms": 1,
    }
    remint(
        f,
        WorkUnit("link", "event", "event-a"),
        join,
        extra=(("link_candidates", json.dumps(["entry", alice, 999]), candidate),),
    )
    f.corpus.clock.sleep(60)
    return alice


def test_streamed_equals_whole_with_a_baseline_with_joins_and_an_identity_decision(
    release_state,
):
    f = release_state
    whole, streamed = both(f, f.bundle, f.clock, f.run)
    assert_same_rows(whole, streamed)
    value = policy(streamed)
    # The baseline joins an entry to dancer 100; the journal holds a positive
    # decision about it. The join is withdrawn (dancer 100 has no generation).
    assert value["baseline_commit"] == "published-fixture"
    assert value["token"]["journal_generation"] >= 1
    assert {row["reason"] for row in rows(streamed, "changelog")} >= {"link_downgraded"}
    assert rows(streamed, "callback_marks")


def test_streamed_equals_whole_when_the_identity_policy_decides_a_join(source_fixture, tmp_path):
    f = source_fixture
    alice = linked(f, tmp_path)
    whole, streamed = both(f, f.bundle, f.corpus.clock, f.corpus.run)
    assert_same_rows(whole, streamed)
    counts = policy(streamed)["default_join_counts"]
    assert sum(counts.values()) == 1 and "accepted" not in counts
    entry = next(row for row in rows(streamed, "entries") if row["entry_id"] == alice)
    assert entry["wsdc_id"] is None and entry["link_status"] == "unmatched"
    (candidate,) = rows(streamed, "link_candidates")
    assert candidate["chosen"] is False


@pytest.mark.parametrize(
    "selectors",
    [
        b"wsdc_id,name_norm,reason,date\n,orian breaux,Private note,2026-09-13\n",
        b"wsdc_id,name_norm,reason,date\n100,,Private note,2026-09-13\n",
    ],
)
def test_streamed_equals_whole_with_suppressions(release_state, selectors):
    f = release_state
    bundle = with_suppressions(f, selectors)
    whole, streamed = both(f, bundle, f.clock, f.run)
    assert_same_rows(whole, streamed)
    entries = rows(streamed, "entries")
    assert any(row["link_status"] == "suppressed" for row in entries) or selectors.startswith(
        b"wsdc_id,name_norm,reason,date\n100"
    )
    assert b"Private note" not in (streamed.path / "_meta/manifest.json").read_bytes()


@pytest.mark.parametrize("snapshot", ["registry-offline", "round-one"])
def test_streamed_equals_whole_with_a_revoked_dancer_or_event(
    source_fixture, tmp_path, monkeypatch, snapshot
):
    """Revocations the assembly closes: a dancer's snapshot, or an event's sheet.

    Stage 1 withholds rows it sees revoked; a revocation it does not see
    (here, patched into the assembly's check) is closed by the assembly,
    per partition in the streamed path.
    """
    from swingset.build import support_closure

    f = source_fixture
    alice = linked(f, tmp_path)
    original = support_closure.interpretation_support

    def support(conn, value, *, parser_version=None):
        if value == snapshot:
            return {"state": "revoked", "usable": False}
        return original(conn, value, parser_version=parser_version)

    monkeypatch.setattr(support_closure, "interpretation_support", support)
    whole, streamed = both(f, f.bundle, f.corpus.clock, f.corpus.run)
    assert_same_rows(whole, streamed)
    withdrawals = policy(streamed)["support_withdrawals"]
    if snapshot == "registry-offline":
        assert withdrawals["reasons"]["dancer_support_revoked"] == 1
        removed = withdrawals["removed_rows"]
        assert removed["dancers"] == removed["link_candidates"] == 1
        assert 999 not in {row["wsdc_id"] for row in rows(streamed, "dancers")}
        entry = next(row for row in rows(streamed, "entries") if row["entry_id"] == alice)
        assert entry["wsdc_id"] is None
    else:
        assert withdrawals["reasons"]["event_scope_omitted"] == 1
        assert "event-a" not in {row["event_id"] for row in rows(streamed, "events")}
        assert not rows(streamed, "entries")


def test_streamed_equals_whole_with_a_withheld_event(source_fixture, tmp_path):
    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))

    def duplicate_place(table, key, payload):
        return {**payload, "place": 2} if table == "placements" else payload

    remint(f, WorkUnit("project", "event", "event-a"), duplicate_place)
    clock = f.corpus.clock
    clock.sleep(60)
    whole, streamed = both(f, f.bundle, clock, f.corpus.run)
    assert_same_rows(whole, streamed)
    assert policy(streamed)["withheld"]["by_reason"] == {"invalid_placements": 1}
    coverage = [
        row
        for row in rows(streamed, "coverage")
        if row["scope_kind"] == "event" and row["scope_id"] == "event-a"
    ]
    assert coverage and all(row["scope_status"] == "withheld" for row in coverage)


@pytest.mark.parametrize("path", ["whole", "partitioned"])
def test_a_check_that_fails_after_assembly_withholds_only_its_partition(
    source_fixture, tmp_path, monkeypatch, path
):
    """I1 at assembly: a suppression that orphans a placement (here, one that
    drops event-a's entries) fails the builder's check for event-a only. The
    partition is withheld with `assembly_check:<check>`; the release is built."""
    from swingset.build import suppression

    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    body = b"wsdc_id,name_norm,reason,date\n424242,,Private note,2026-09-13\n"
    files = {**f.bundle.files, "overrides/suppressions.csv": body}
    bundle = replace(f.bundle, digest=sha256(body + b"|suppressions").hexdigest(), files=files)
    accept(f.db, bundle, f.corpus.clock)
    original = suppression.SuppressionPolicy.transform

    def orphaning(self, rows):
        original(self, rows)
        entries = rows.get("entries")
        if entries:
            entries[:] = [row for row in entries if not str(row["entry_id"]).startswith("event-a/")]

    monkeypatch.setattr(suppression.SuppressionPolicy, "transform", orphaning)
    f.corpus.clock.sleep(60)
    result = service.build_release(f.db, on(bundle, path), f.corpus.clock, f.corpus.run)
    events = {row["event_id"] for row in rows(result, "events")}
    assert "event-a" not in events and "2026-01-registry-joined-weekend" in events
    assert not [row for row in rows(result, "placements") if row["event_id"] == "event-a"]
    partitions = policy(result)["partitions"]
    assert partitions["assembly_withheld"] == 1
    assert [key for key in partitions if key.startswith("assembly_check:")] == [
        "assembly_check:invalid_placements"
    ]
    coverage = [
        row
        for row in rows(result, "coverage")
        if row["scope_kind"] == "event" and row["scope_id"] == "event-a"
    ]
    assert coverage and all(row["scope_status"] == "withheld" for row in coverage)


@pytest.mark.parametrize("path", ["whole", "partitioned"])
def test_a_check_that_fails_after_identity_policy_withholds_only_its_partition(
    source_fixture, tmp_path, monkeypatch, path
):
    """I1 at assembly with no suppression, production's case: the builder's
    checks run on each partition after the identity policy. One that leaves
    event-a's placements orphaned withholds event-a only."""
    from swingset.build import identity_policy

    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    assert not f.bundle.csv("suppressions.csv")
    original = identity_policy.IdentityPolicy.apply

    def orphaning(self, tables, mutable, previous):
        original(self, tables, mutable, previous)
        entries = mutable.get("entries")
        if entries:
            entries[:] = [row for row in entries if not str(row["entry_id"]).startswith("event-a/")]

    monkeypatch.setattr(identity_policy.IdentityPolicy, "apply", orphaning)
    f.corpus.clock.sleep(60)
    report: dict = {}
    result = service.build_release(
        f.db, on(f.bundle, path), f.corpus.clock, f.corpus.run, report=report
    )
    # The build record names it: stage 1's row still says built.
    assert report["assembly_withheld"] == {"event/event-a": "assembly_check:invalid_placements"}
    events = {row["event_id"] for row in rows(result, "events")}
    assert "event-a" not in events and "2026-01-registry-joined-weekend" in events
    assert not [row for row in rows(result, "placements") if row["event_id"] == "event-a"]
    partitions = policy(result)["partitions"]
    assert partitions["assembly_withheld"] == 1
    assert partitions["assembly_check:invalid_placements"] == 1
    coverage = [
        row
        for row in rows(result, "coverage")
        if row["scope_kind"] == "event" and row["scope_id"] == "event-a"
    ]
    assert coverage and all(row["scope_status"] == "withheld" for row in coverage)


@pytest.mark.parametrize("damage", ["truncate", "delete"])
def test_a_damaged_artifact_is_rebuilt_by_the_next_build(source_fixture, tmp_path, damage):
    """I1, I8: stage 1 reuses an artifact only when every file matches its
    sidecar's sha256, so one damaged file costs one rebuild, not every
    later release."""
    from build.test_partitions import rows as partition_rows

    from swingset.build import artifact_store
    from swingset.state.retention import artifact_directory

    f = source_fixture
    link_baseline(f, settled(f, tmp_path, tables=("dancers", "registry_placements")))
    f.corpus.clock.sleep(60)
    bundle = on(f.bundle, "partitioned")
    service.build_release(f.db, bundle, f.corpus.clock, f.corpus.run)
    row = partition_rows(f.conn)["event/event-a"]
    directory = artifact_directory(f.db.state_dir, row.artifact_digest)
    victim = (
        directory
        / artifact_store.sidecar(f.db.state_dir, row.artifact_digest)["tables"]["entries"]["file"]
    )
    if damage == "truncate":
        victim.write_bytes(victim.read_bytes()[:100])
    else:
        victim.unlink()
    assert not artifact_store.intact(f.db.state_dir, row.artifact_digest)
    # Change another event so the next build is not a reuse of the candidate.
    remint(
        f,
        WorkUnit("project", "event", "event-b"),
        lambda table, key, payload: (
            {**payload, "name_raw": "Renamed"} if table == "entries" else payload
        ),
    )
    f.corpus.clock.sleep(3600 * 7)
    result = service.build_release(f.db, bundle, f.corpus.clock, f.corpus.run)
    after = partition_rows(f.conn)["event/event-a"]
    assert after.state == "built" and after.artifact_digest == row.artifact_digest
    assert artifact_store.intact(f.db.state_dir, after.artifact_digest)
    assert "event-a" in {row["event_id"] for row in rows(result, "events")}
    assert any(str(row["entry_id"]).startswith("event-a/") for row in rows(result, "entries"))


def test_a_key_check_neutralizes_an_entry_whose_dancer_is_absent(release_state):
    """The fixture links an entry to dancer 100, which no dancer generation holds."""
    f = release_state
    streamed = service.build_release(f.db, on(f.bundle, "partitioned"), f.clock, f.run)
    value = policy(streamed)
    assert value["partitions"]["identity_of_absent_dancer_closed"] >= 2
    entry = next(row for row in rows(streamed, "entries") if row["entry_id"] == f.identifier)
    assert entry["wsdc_id"] is None and entry["link_status"] == "unmatched"
    assert not [row for row in rows(streamed, "identity_links") if row["wsdc_id"] == 100]
    assert not [
        row
        for row in rows(streamed, "placements")
        if 100 in (row["leader_wsdc_id"], row["follower_wsdc_id"])
    ]
    assert 100 not in {row["wsdc_id"] for row in rows(streamed, "dancers")}


def test_close_keys_counts_withheld_and_absent_dancers_apart():
    tables = {
        "entries": [
            {"entry_id": "e1", "wsdc_id": 1, "link_status": "confirmed", "link_confidence": 1.0},
            {"entry_id": "e2", "wsdc_id": 2, "link_status": "confirmed", "link_confidence": 1.0},
            {"entry_id": "e3", "wsdc_id": 3, "link_status": "confirmed", "link_confidence": 1.0},
        ],
        "judges": [],
        "identity_links": [{"link_id": "l1", "wsdc_id": 2}, {"link_id": "l3", "wsdc_id": 3}],
        "link_candidates": [],
        "placements": [
            {
                "placement_id": "p1",
                "leader_entry_id": "e3",
                "leader_wsdc_id": 3,
                "follower_entry_id": "e2",
                "follower_wsdc_id": 2,
                "registry_points_leader": 4,
                "registry_points_follower": 4,
                "registry_confirmed": True,
                "points_matches_expected": True,
            }
        ],
    }
    counts: Counter[str] = Counter()
    close_keys(tables, absent={1, 2}, withheld=lambda value: value == 1, counts=counts)
    assert counts == {
        "identity_of_withheld_dancer_closed": 1,
        "identity_of_absent_dancer_closed": 2,
    }
    assert [row["wsdc_id"] for row in tables["entries"]] == [None, None, 3]
    assert [row["link_id"] for row in tables["identity_links"]] == ["l3"]
    (placement,) = tables["placements"]
    assert placement["leader_wsdc_id"] == 3 and placement["follower_wsdc_id"] is None
    assert placement["registry_confirmed"] is False


def test_the_meter_keeps_the_largest_unit():
    meter = Meter()
    with meter.unit("partition"):
        meter.take(10)
        meter.take(5)
    meter.take(7)
    with meter.unit("partition"):
        meter.take(3)
    assert meter.peak == 15 and meter.units == {"partition": 15, "stream": 7}


def test_the_assembly_leaves_no_scratch(release_state):
    f = release_state
    service.build_release(f.db, on(f.bundle, "partitioned"), f.clock, f.run)
    scratch = f.db.state_dir / "tmp"
    assert not [path for path in scratch.glob("assembly-*")] if scratch.exists() else True
    assert assembly.MEMORY_LIMIT == "1GB"


def test_a_killed_builds_scratch_goes_once_a_day_old(tmp_path):
    import os
    import time

    old, new = tmp_path / "assembly-old", tmp_path / "assembly-new"
    for path in (old, new):
        (path / "spill").mkdir(parents=True)
    stamp = time.time() - assembly.STALE_SECONDS - 60
    os.utime(old, (stamp, stamp))
    assembly._remove_stale(tmp_path)
    assert not old.exists() and new.exists()
