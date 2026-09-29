"""Change certificates must be as conservative as full dependency comparison."""

import hashlib
import json

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.state import derivations
from swingset.state.derivation_query import memo, query
from swingset.state.derivation_signatures import certificate
from swingset.state.recipes import recipe_inputs
from swingset.state.work import WorkUnit


def materialize(fixture):
    unit = WorkUnit("project", "event", "event-a")
    with fixture.db.transaction():
        selection = derivations.capture(fixture.conn, unit, now=fixture.corpus.clock.now())
        derivations.complete(
            fixture.conn,
            selection,
            rows=(),
            now=fixture.corpus.clock.now(),
            run_id=fixture.corpus.run,
        )
    assert derivations.current(fixture.conn, unit)
    return unit, selection


@pytest.mark.parametrize("mutation", ["update", "delete", "snapshot", "selection", "watch_source"])
def test_source_mutation_cannot_hide_behind_proven_materialization_signature(
    source_fixture, mutation
):
    f = source_fixture
    unit, selection = materialize(f)
    original = f.conn.execute(
        "SELECT observation_id,watch_id,snapshot_id FROM observations LIMIT 1"
    ).fetchone()
    with f.db.transaction():
        if mutation == "update":
            f.conn.execute(
                "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Different Name') WHERE observation_id=?",
                (original[0],),
            )
        elif mutation == "delete":
            f.conn.execute("DELETE FROM observations WHERE observation_id=?", (original[0],))
        elif mutation == "snapshot":
            f.conn.execute("UPDATE snapshots SET via='wayback' WHERE snapshot_id=?", (original[2],))
        elif mutation == "selection":
            f.conn.execute(
                "UPDATE source_units SET accepted_generation_id=NULL WHERE watch_id=?",
                (original[1],),
            )
        else:
            f.conn.execute(
                "UPDATE watches SET source='changed-source' WHERE watch_id=?", (original[1],)
            )
    assert not derivations.current(f.conn, unit)
    assert derivations.desired(f.conn, unit).fingerprint != selection.fingerprint


def test_false_mutable_cache_claim_has_no_immutable_completion_proof(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    f.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
    )
    generation = derivations.selected_generation(f.conn, unit)
    signature = certificate(f.conn, unit, recipe_inputs(f.conn, "project", unit.unit_kind), {})
    proof = derivations.canonical({"generation_id": generation, "certificate": signature})
    f.conn.execute(
        "UPDATE derivation_scopes SET desired_fingerprint=?,materialized_signature=? WHERE stage=? AND unit_kind=? AND unit_id=?",
        (
            derivations.desired(f.conn, unit).fingerprint,
            hashlib.sha256(proof.encode()).hexdigest(),
            unit.stage,
            unit.unit_kind,
            unit.unit_id,
        ),
    )
    assert not derivations.current(f.conn, unit)


def test_source_tokens_rollback_with_input_and_query_memo_does_not_keep_dirty_values(
    source_fixture,
):
    f = source_fixture
    unit, before = materialize(f)
    versions = [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_input_versions ORDER BY namespace,input_key"
        )
    ]
    with query(f.conn):
        assert (
            memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
            == before.fingerprint
        )
        with pytest.raises(RuntimeError, match="rollback"):
            with f.db.transaction():
                f.conn.execute(
                    "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
                )
                assert not derivations.current(f.conn, unit)
                assert (
                    memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
                    != before.fingerprint
                )
                raise RuntimeError("rollback")
        assert derivations.current(f.conn, unit)
        assert (
            memo(f.conn, "payload", lambda: derivations.desired(f.conn, unit).fingerprint)
            == before.fingerprint
        )
    assert [
        tuple(row)
        for row in f.conn.execute(
            "SELECT * FROM derivation_input_versions ORDER BY namespace,input_key"
        )
    ] == versions


def test_continuity_is_retained_and_selection_mutation_is_rejected(source_fixture):
    f = source_fixture
    unit = WorkUnit("project", "calendar", "legacy")
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())
        support_id = selection.continuity["dependency_set_id"]
        support = json.loads(
            f.conn.execute(
                "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                (support_id,),
            ).fetchone()[0]
        )
        assert any(
            row["table"] == "events" and row["row"]["event_id"] == "event-a" for row in support
        )
        selection.continuity["dependency_set_id"] = "tampered"
        with pytest.raises(derivations.SupersededWorkError, match="modified"):
            derivations.complete(
                f.conn, selection, rows=(), now=f.corpus.clock.now(), run_id=f.corpus.run
            )
    assert derivations.selected_generation(f.conn, unit) is None


def test_input_versions_cannot_be_rewound_or_renamed(source_fixture):
    import sqlite3

    f = source_fixture
    materialize(f)
    row = f.conn.execute(
        "SELECT namespace,input_key,version FROM derivation_input_versions LIMIT 1"
    ).fetchone()
    assert row
    for assignment, params in (
        ("version=?", (row[2] - 1,)),
        ("input_key=?", ("renamed",)),
        ("namespace=?", ("renamed",)),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="must advance"):
            f.conn.execute(
                "UPDATE derivation_input_versions SET "
                + assignment
                + " WHERE namespace=? AND input_key=?",
                (*params, row[0], row[1]),
            )
    f.conn.execute(
        "UPDATE derivation_input_versions SET version=version+1 WHERE namespace=? AND input_key=?",
        (row[0], row[1]),
    )


def test_row_iteration_source_change_cannot_be_certified(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    before = derivations.selected_generation(f.conn, unit)
    with f.db.transaction():
        selection = derivations.capture(f.conn, unit, now=f.corpus.clock.now())

    def changing_rows():
        f.conn.execute(
            "UPDATE observations SET payload_json=replace(payload_json,'Alice Example','Changed')"
        )
        yield from ()

    with pytest.raises(derivations.SupersededWorkError):
        with f.db.transaction():
            derivations.complete(
                f.conn,
                selection,
                rows=changing_rows(),
                now=f.corpus.clock.now(),
                run_id=f.corpus.run,
            )
    assert derivations.selected_generation(f.conn, unit) == before
    assert derivations.current(f.conn, unit)


def settled(fixture):
    """Every project scope and link event-a materialized, with a calendar and a dancer."""
    from test_h15_acceptance import drain_project

    from swingset.link.service import link_event

    f = fixture
    with f.db.transaction():
        for kind, identifier in (("calendar", "calendar-x"), ("dancer", "1")):
            f.conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,?,?)",
                (kind, identifier, f.corpus.clock.now().isoformat()),
            )
    drain_project(f)
    link_event(f.db, "event-a", f.bundle, f.corpus.clock, f.corpus.run)
    units = [
        WorkUnit(*row)
        for row in f.conn.execute(
            "SELECT stage,unit_kind,unit_id FROM derivation_scopes "
            "WHERE materialized_generation_id IS NOT NULL ORDER BY 1,2,3"
        )
    ]
    assert {unit.unit_kind for unit in units if unit.stage == "project"} == {
        "calendar",
        "dancer",
        "inventory",
        "mapping",
        "resolution",
        "event",
        "source_event",
        "year",
    }
    assert WorkUnit("link", "event", "event-a") in units
    assert all(derivations.current(f.conn, unit) for unit in units)
    return units


def proofs(conn, units):
    """Each unit's certificate and desired fingerprint, both recomputed."""
    return {
        unit: (
            certificate(conn, unit, recipe_inputs(conn, unit.stage, unit.unit_kind), {}),
            derivations.desired(conn, unit).fingerprint,
        )
        for unit in units
    }


@pytest.mark.parametrize(
    ("kind", "stale"),
    [
        (
            "calendar",
            {
                ("project", "inventory"),
                ("project", "mapping"),
                ("project", "resolution"),
                ("project", "year"),
            },
        ),
        # The year has no dancer prerequisite: a dancer's reconcile reaches it
        # through the epoch alone (F23). A link reads no per-kind epoch (F20).
        ("dancer", {("project", "inventory"), ("project", "year")}),
        # The fixture has no source_index scope: an empty cohort's epoch still
        # moves certificate and fingerprint together.
        ("source_index", {("project", "inventory"), ("project", "mapping")}),
        (
            "inventory",
            {("project", "mapping"), ("project", "resolution"), ("project", "year")},
        ),
        # A resolution reads its proposals as content, not the mapping.
        ("mapping", set()),
        (
            "resolution",
            {("project", "event"), ("project", "source_event"), ("project", "year")},
        ),
        ("event", {("project", "source_event"), ("project", "year")}),
        # A source event's own output is its events'; nothing reads its epoch.
        ("source_event", set()),
        ("year", set()),
    ],
)
def test_a_replay_epoch_reaches_exactly_the_kinds_that_depend_on_it(source_fixture, kind, stale):
    f = source_fixture
    units = settled(f)
    before = proofs(f.conn, units)
    with f.db.transaction():
        assert derivations.bump_replay(f.conn, "project", kind) == 1
        assert derivations.bump_replay(f.conn, "project", kind) == 2
    after = proofs(f.conn, units)
    for unit in units:
        moved = (unit.stage, unit.unit_kind) in stale
        # Stale by certificate and by fingerprint, never by one alone.
        assert (after[unit][0] != before[unit][0]) is moved, unit
        assert (after[unit][1] != before[unit][1]) is moved, unit
        assert derivations.current(f.conn, unit) is not moved, unit
    replays = [
        item
        for item in derivations.desired(f.conn, WorkUnit("project", "year", "2026")).dependencies
        if item["kind"] == "replay"
    ]
    assert {tuple(item["key"]): item["version"] for item in replays} == {
        ("project", name): 2 if name == kind else 0
        for name in ("calendar", "dancer", "inventory", "resolution", "event")
    }


def test_an_event_units_own_replay_reaches_its_link_only(source_fixture):
    """An event cutoff that rewrote live rows moves its kind's epoch and its
    own version; only its link reads the own version (plan F20)."""
    f = source_fixture
    units = settled(f)
    before = proofs(f.conn, units)
    f.conn.execute("DELETE FROM pending_work")
    with f.db.transaction():
        derivations.bump_replay(f.conn, "project", "event", unit_id="event-a")
    after = proofs(f.conn, units)
    stale = {("project", "source_event"), ("project", "year"), ("link", "event")}
    for unit in units:
        moved = (unit.stage, unit.unit_kind) in stale
        assert (after[unit][0] != before[unit][0]) is moved, unit
        assert (after[unit][1] != before[unit][1]) is moved, unit
    assert ("link", "event", "event-a") in {
        tuple(row) for row in f.conn.execute("SELECT stage,unit_kind,unit_id FROM pending_work")
    }


def test_a_replay_epoch_is_never_a_selected_group_member(source_fixture):
    f = source_fixture
    settled(f)
    unit = WorkUnit("project", "year", "2026")
    before = derivations.desired(f.conn, unit)
    with f.db.transaction():
        derivations.bump_replay(f.conn, "project", "calendar")
    after = derivations.desired(f.conn, unit)
    assert after.fingerprint != before.fingerprint
    groups = {
        item["key"]: item["fingerprint"]
        for item in after.dependencies
        if item["kind"] == "dependency_set"
    }
    assert groups == {
        item["key"]: item["fingerprint"]
        for item in before.dependencies
        if item["kind"] == "dependency_set"
    }
    assert not any(
        member.get("kind") == "replay"
        for members in after.dependency_sets.values()
        for member in members
    )


def test_bump_replay_requires_the_output_transaction(source_fixture):
    with pytest.raises(ValueError, match="output transaction"):
        derivations.bump_replay(source_fixture.conn, "project", "calendar")


@pytest.mark.parametrize("change", ["none", "replay", "raw", "new"])
def test_bulk_dancer_readiness_equals_current(source_fixture, change):
    """A raw token alone misses the certificate and takes the full comparison."""
    from swingset.state.derivation_dependencies import scopes
    from swingset.state.derivation_readiness import dancers_current

    f = source_fixture
    settled(f)
    with f.db.transaction():
        if change == "replay":
            for kind in ("calendar", "dancer", "year"):
                derivations.bump_replay(f.conn, "project", kind)
        elif change == "raw":
            f.conn.execute(
                "INSERT INTO derivation_input_versions VALUES ('raw','[\"dancer\",\"1\"]',1) "
                "ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1"
            )
        elif change == "new":
            f.conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES ('project','dancer','2',?)",
                (f.corpus.clock.now().isoformat(),),
            )
    with f.db.transaction():
        cohort = tuple(unit for unit in scopes(f.conn, "project") if unit.unit_kind == "dancer")
        assert WorkUnit("project", "dancer", "1") in cohort
        expected = all(derivations.current(f.conn, unit) for unit in cohort)
        assert expected is (change != "new")
        assert dancers_current(f.conn, cohort, current=derivations.current) is expected
