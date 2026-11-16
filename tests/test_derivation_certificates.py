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


def test_inventory_certificate_keeps_exact_basis_with_one_token_snapshot(source_fixture):
    f = source_fixture
    unit = WorkUnit("project", "inventory", "all")
    recipe = {"code": "known"}
    context = {"label": "雪"}
    versions = {
        (row[0], row[1]): int(row[2])
        for row in f.conn.execute(
            "SELECT namespace,input_key,version FROM derivation_input_versions"
        )
    }
    pairs = [
        (name, "all")
        for name in (
            "calendar_raw",
            "inventory_registry",
            "inventory_sources",
            "snapshot_basis",
            "watch_sources",
            "source_selections",
        )
    ]
    for kind in ("calendar", "source_index", "dancer"):
        key = json.dumps(["project", kind], separators=(",", ":"))
        pairs.extend((("selected", key), ("replayed", key)))
    basis = [
        "certificate-v1",
        ("project", "inventory", "all"),
        recipe,
        context,
        *[(namespace, key, versions.get((namespace, key), 0)) for namespace, key in pairs],
    ]
    expected = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    reads = []
    with f.conn.observing(
        lambda sql, _params: (
            reads.append(sql) if "FROM derivation_input_versions" in str(sql) else None
        )
    ):
        assert certificate(f.conn, unit, recipe, context) == expected
    assert len(reads) == 1


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


def test_currentness_reads_staging_presence_with_pointer_and_honors_visibility(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    statements = []
    with f.conn.observing(lambda sql, _params: statements.append(str(sql))):
        assert derivations.current(f.conn, unit)
    assert sum("AS building_available FROM derivation_scopes" in sql for sql in statements) == 1
    assert not any(
        sql.startswith("SELECT 1 AS _value_0 FROM pg_catalog.pg_class") for sql in statements
    )

    with pytest.raises(RuntimeError, match="rollback shadow"):
        with f.db.transaction():
            f.conn.execute("CREATE TEMP VIEW derivation_building AS SELECT 1 AS unrelated")
            f.conn.execute(
                "SELECT set_config('search_path', 'pg_temp,' || current_setting('search_path'), true)"
            )
            statements.clear()
            with f.conn.observing(lambda sql, _params: statements.append(str(sql))):
                assert derivations.current(f.conn, unit)
            assert not any("FROM derivation_building WHERE" in sql for sql in statements)
            raise RuntimeError("rollback shadow")
    assert derivations.current(f.conn, unit)


def test_bulk_selected_generations_preserve_groups_missing_units_and_boundary(source_fixture):
    from types import SimpleNamespace

    f = source_fixture
    unit, _ = materialize(f)
    missing = [WorkUnit("project", "event", f"missing-{index}") for index in range(1024)]
    for mode in ("ordinary", "selected", "effective", "null", "foreign"):
        active = None
        if mode != "ordinary":
            active = SimpleNamespace(
                connection=object() if mode == "foreign" else f.conn,
                selected={unit: SimpleNamespace(generation_id="group-selected")},
                effective={unit: None if mode == "null" else "group-effective"}
                if mode in {"null", "effective"}
                else {},
            )
        token = derivations._group.set(active)
        try:
            expected = derivations.selected_generation(f.conn, unit)
            reads = []
            with f.conn.observing(lambda sql, _params, reads=reads: reads.append(str(sql))):
                found = derivations.selected_generations(f.conn, (unit, *missing, unit))
            assert found == {unit: expected, **dict.fromkeys(missing)}
            assert len(reads) == (2 if mode in {"ordinary", "foreign"} else 1)
        finally:
            derivations._group.reset(token)


@pytest.mark.parametrize("mode", ["ordinary", "selected", "effective", "null", "foreign"])
def test_inventory_dependencies_batch_pointers_without_changing_manifests(
    source_fixture, monkeypatch, mode
):
    from types import SimpleNamespace

    from swingset.state import derivation_dependencies

    f = source_fixture
    present, _ = materialize(f)
    missing = tuple(WorkUnit("project", "event", f"missing-雪-{i}") for i in range(1024))
    required = (present, *missing, present)
    monkeypatch.setattr(derivation_dependencies, "prerequisites", lambda conn, unit: required)
    inventory = WorkUnit("project", "inventory", "all")
    active = None
    if mode != "ordinary":
        active = SimpleNamespace(
            connection=object() if mode == "foreign" else f.conn,
            selected={present: SimpleNamespace(generation_id="selected-generation")},
            effective={present: None if mode == "null" else "effective-generation"}
            if mode in {"null", "effective"}
            else {},
        )
    token = derivations._group.set(active)
    try:

        def individually(conn, units):
            return {unit: derivations.selected_generation(conn, unit) for unit in units}

        reference = derivation_dependencies.dependencies(f.conn, inventory, individually)
        statements = []
        with f.conn.observing(lambda sql, _params: statements.append(str(sql))):
            actual = derivation_dependencies.dependencies(
                f.conn, inventory, derivations.selected_generations
            )
        assert actual == reference  # Includes complete canonical members and group digests.
        pointer_reads = [
            statement
            for statement in statements
            if "JOIN unnest(" in statement
            or statement.startswith("SELECT materialized_generation_id FROM derivation_scopes")
        ]
        assert len(pointer_reads) == (2 if mode in {"ordinary", "foreign"} else 1)
        groups = actual[1]
        members = next(group for group in groups.values() if len(group) == len(required))
        assert (
            sum(
                member["key"] == [present.stage, present.unit_kind, present.unit_id]
                for member in members
            )
            == 2
        )
    finally:
        derivations._group.reset(token)


def test_inventory_batched_dependencies_still_recheck_writes_and_rollback(
    source_fixture, monkeypatch
):
    from swingset.state import derivation_dependencies
    from swingset.state.attempts import SupersededWorkError

    f = source_fixture
    present, _ = materialize(f)
    monkeypatch.setattr(derivation_dependencies, "prerequisites", lambda conn, unit: (present,))
    inventory = WorkUnit("project", "inventory", "all")
    before = derivations.desired(f.conn, inventory)
    with pytest.raises(SupersededWorkError):
        with f.db.transaction():
            captured = derivations.capture(f.conn, inventory, now=f.corpus.clock.now())
            f.conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=NULL "
                "WHERE stage=%s AND unit_kind=%s AND unit_id=%s",
                (present.stage, present.unit_kind, present.unit_id),
            )
            assert derivations.desired(f.conn, inventory).fingerprint != before.fingerprint
            derivations.complete(
                f.conn, captured, rows=(), now=f.corpus.clock.now(), run_id=f.corpus.run
            )
    assert derivations.desired(f.conn, inventory).fingerprint == before.fingerprint


@pytest.mark.parametrize(
    "mutation", ["UPDATE  SET", "DELETE", "snapshot", "selection", "watch_source"]
)
def test_source_mutation_cannot_hide_behind_proven_materialization_signature(
    source_fixture, mutation
):
    f = source_fixture
    unit, selection = materialize(f)
    original = f.conn.execute(
        "SELECT observation_id, watch_id, snapshot_id FROM observations LIMIT 1"
    ).fetchone()
    with f.db.transaction():
        if mutation == "UPDATE  SET":
            f.conn.execute(
                (
                    "UPDATE observations SET payload_json = REPLACE(payload_json, 'Alice Example', 'Different "
                    "Name') WHERE observation_id = %s"
                ),
                (original[0],),
            )
        elif mutation == "DELETE":
            f.conn.execute("DELETE FROM observations WHERE observation_id = %s", (original[0],))
        elif mutation == "snapshot":
            f.conn.execute(
                "UPDATE snapshots SET via = 'wayback' WHERE snapshot_id = %s", (original[2],)
            )
        elif mutation == "selection":
            f.conn.execute(
                "UPDATE source_units SET accepted_generation_id = NULL WHERE watch_id = %s",
                (original[1],),
            )
        else:
            f.conn.execute(
                "UPDATE watches SET source = 'changed-source' WHERE watch_id = %s", (original[1],)
            )
    assert not derivations.current(f.conn, unit)
    assert derivations.desired(f.conn, unit).fingerprint != selection.fingerprint


def test_false_mutable_cache_claim_has_no_immutable_completion_proof(source_fixture):
    f = source_fixture
    unit, _ = materialize(f)
    f.conn.execute(
        "UPDATE observations SET payload_json = REPLACE(payload_json, 'Alice Example', 'Changed')"
    )
    generation = derivations.selected_generation(f.conn, unit)
    signature = certificate(f.conn, unit, recipe_inputs(f.conn, "project", unit.unit_kind), {})
    proof = derivations.canonical({"generation_id": generation, "certificate": signature})
    f.conn.execute(
        (
            "UPDATE derivation_scopes SET desired_fingerprint = %s, materialized_signature = %s WHERE "
            "stage = %s AND unit_kind = %s AND unit_id = %s"
        ),
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
            "SELECT derivation_input_versions.namespace, derivation_input_versions.input_key, "
            "derivation_input_versions.version FROM derivation_input_versions ORDER BY namespace NULLS "
            "FIRST, input_key NULLS FIRST"
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
                    "UPDATE observations SET payload_json = REPLACE(payload_json, 'Alice Example', 'Changed')"
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
            "SELECT derivation_input_versions.namespace, derivation_input_versions.input_key, "
            "derivation_input_versions.version FROM derivation_input_versions ORDER BY namespace NULLS "
            "FIRST, input_key NULLS FIRST"
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
                "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id = %s",
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
    from swingset.state import engine

    f = source_fixture
    materialize(f)
    row = f.conn.execute(
        "SELECT namespace, input_key, version FROM derivation_input_versions LIMIT 1"
    ).fetchone()
    assert row
    for assignment, params in (
        ("version=%s", (row[2] - 1,)),
        ("input_key=%s", ("renamed",)),
        ("namespace=%s", ("renamed",)),
    ):
        with pytest.raises(engine.IntegrityError, match="must advance"):
            f.conn.execute(
                " UPDATE derivation_input_versions SET "
                + assignment
                + " WHERE namespace=%s AND input_key=%s ",
                (*params, row[0], row[1]),
            )
    f.conn.execute(
        "UPDATE derivation_input_versions SET version = version + 1 WHERE namespace = %s AND input_key = %s",
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
            "UPDATE observations SET payload_json = REPLACE(payload_json, 'Alice Example', 'Changed')"
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
                "INSERT INTO derivation_scopes (stage, unit_kind, unit_id, registered_at) VALUES ('project', %s, %s, %s)",
                (kind, identifier, f.corpus.clock.now().isoformat()),
            )
    drain_project(f)
    link_event(f.db, "event-a", f.bundle, f.corpus.clock, f.corpus.run)
    units = [
        WorkUnit(*row)
        for row in f.conn.execute(
            "SELECT stage, unit_kind, unit_id FROM derivation_scopes WHERE NOT "
            "materialized_generation_id IS NULL ORDER BY 1 NULLS FIRST, 2 NULLS FIRST, 3 NULLS FIRST"
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
        tuple(row) for row in f.conn.execute("SELECT stage, unit_kind, unit_id FROM pending_work")
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
                "INSERT INTO derivation_input_versions (namespace, input_key, version) VALUES ('raw', '[\"dancer\",\"1\"]', 1) ON CONFLICT(namespace, input_key) DO UPDATE SET version = derivation_input_versions.version + 1"
            )
        elif change == "new":
            f.conn.execute(
                (
                    "INSERT INTO derivation_scopes (stage, unit_kind, unit_id, registered_at) VALUES ('project', "
                    "'dancer', '2', %s)"
                ),
                (f.corpus.clock.now().isoformat(),),
            )
    with f.db.transaction():
        cohort = tuple(unit for unit in scopes(f.conn, "project") if unit.unit_kind == "dancer")
        assert WorkUnit("project", "dancer", "1") in cohort
        expected = all(derivations.current(f.conn, unit) for unit in cohort)
        assert expected is (change != "new")
        assert dancers_current(f.conn, cohort, current=derivations.current) is expected
