"""Large closures retain exact proof while visiting their sets only linearly."""

from collections.abc import MutableSet
from copy import deepcopy

import pytest

from swingset.build import closure
from swingset.build.closure_manifest import ClosureError, canonical, digest
from swingset.clock import FakeClock
from swingset.state.db import open_database


class CountedSet(MutableSet):
    def __init__(self):
        self.values = set()
        self.enumerated = 0

    def __contains__(self, item):
        return item in self.values

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        for item in self.values:
            self.enumerated += 1
            yield item

    def add(self, item):
        self.values.add(item)

    def discard(self, item):
        self.values.discard(item)


@pytest.fixture
def graph_db(tmp_path):
    with open_database(tmp_path) as db:
        clock = FakeClock()
        run = db.start_run(clock.now())
        yield db, clock.now().isoformat(), run


def add_generation(fixture, identifier, dependencies, *, continuity=None, year=False, scope=None):
    """One project generation, its scope's pointer; an event unless `year` or `scope`."""
    db, at, run = fixture
    conn = db.connection
    dependency = digest(dependencies)
    recipe = {"continuity": {"dependency_set_id": continuity}} if continuity else {}
    kind, unit = scope or (("year", "2026") if year else ("event", identifier))
    with db.transaction():
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
            (dependency, canonical(dependencies)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,?,?)",
            (kind, unit, at),
        )
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                "project",
                kind,
                unit,
                digest(identifier),
                canonical(recipe),
                dependency,
                None,
                digest([]),
                0,
                at,
                run,
            ),
        )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=? WHERE stage='project' AND unit_kind=? AND unit_id=?",
            (identifier, kind, unit),
        )
    return dependency


def edge(identifier, kind="event", unit=None):
    return {
        "kind": "derivation",
        "key": ["project", kind, unit or identifier],
        "generation_id": identifier,
    }


def test_wide_closure_enumerates_set_inventory_once_and_loads_each_receipt_once(
    graph_db, monkeypatch
):
    db, at, _ = graph_db
    count = 256
    for index in range(count):
        add_generation(graph_db, f"event-{index}", [{"kind": "version", "value": index}])
    add_generation(graph_db, "year", [edge(f"event-{index}") for index in range(count)], year=True)
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    assert len(pinned["selected"]) == count + 1
    assert len(pinned["dependency_sets"]) == count + 1

    original = closure._Selection.__init__
    inventories = []

    def counted(self, conn, cutoff):
        original(self, conn, cutoff)
        self.traversed = CountedSet()
        inventories.append(self.traversed)

    monkeypatch.setattr(closure._Selection, "__init__", counted)
    reads = []
    db.connection.set_trace_callback(
        lambda sql: (
            reads.append(sql)
            if sql.startswith("SELECT * FROM derivation_generations WHERE generation_id=")
            else None
        )
    )
    try:
        closure.validate(db.connection, pinned)
    finally:
        db.connection.set_trace_callback(None)
    assert len(reads) == count + 1
    assert sum(item.enumerated for item in inventories) == count + 1


def test_continuity_does_not_mark_untraversed_dependency_members_visited(graph_db):
    db, at, _ = graph_db
    leaf = add_generation(graph_db, "leaf", [])
    shared = digest([edge("leaf")])
    with db.transaction():
        db.connection.execute(
            "INSERT INTO derivation_dependency_sets VALUES (?,?)",
            (shared, canonical([edge("leaf")])),
        )
    add_generation(graph_db, "prior-support", [], continuity=shared)
    add_generation(graph_db, "actual-owner", [edge("leaf")])
    add_generation(graph_db, "year", [edge("prior-support"), edge("actual-owner")], year=True)
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    assert {item["generation_id"] for item in pinned["selected"]} == {
        "leaf",
        "prior-support",
        "actual-owner",
        "year",
    }
    assert shared in pinned["dependency_sets"] and leaf in pinned["dependency_sets"]
    closure.validate(db.connection, pinned)


@pytest.mark.parametrize(
    "field,value", [("output_digest", "forged"), ("unit_id", "other"), ("recipe", {"forged": True})]
)
def test_every_selected_receipt_field_is_still_compared(graph_db, field, value):
    db, _, _ = graph_db
    add_generation(graph_db, "event", [])
    add_generation(graph_db, "year", [edge("event")], year=True)
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    forged = deepcopy(pinned)
    next(item for item in forged["selected"] if item["generation_id"] == "event")[field] = value
    forged["digest"] = digest({key: item for key, item in forged.items() if key != "digest"})
    with pytest.raises(ClosureError, match="selected_generation_receipt_mismatch"):
        closure.validate(db.connection, forged)


def test_a_conflicting_year_withholds_its_events_only_and_dancers_anchor(graph_db):
    """Plan F22 and D-0224: the closure anchors on every year in year order,
    then on every dancer. A year whose walk reaches a generation other than
    its scope's own anchor (here a superseded dancer, through its inventory)
    is skipped, and only its events are withheld; the superseded dancer is
    never published."""
    db, at, run = graph_db
    with db.transaction():
        for event, year in (("e25", 2025), ("e26", 2026)):
            db.connection.execute(
                "INSERT INTO events(event_id,series_id,name,year,wsdc_status,sources,source,"
                "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
                "VALUES (?,'s',?,?,'registry','[]','test','snap','1',?,?,?)",
                (event, event, year, at, at, run),
            )
    add_generation(graph_db, "d7-old", [], scope=("dancer", "7"))
    add_generation(graph_db, "d8", [], scope=("dancer", "8"))
    add_generation(graph_db, "inv-1", [edge("d7-old", "dancer", "7")], scope=("inventory", "all"))
    add_generation(graph_db, "inv-2", [], scope=("inventory", "all"))
    add_generation(graph_db, "d7-new", [], scope=("dancer", "7"))
    add_generation(graph_db, "g-e25", [], scope=("event", "e25"))
    add_generation(graph_db, "g-e26", [], scope=("event", "e26"))
    add_generation(
        graph_db,
        "y25",
        [edge("g-e25", "event", "e25"), edge("inv-1", "inventory", "all")],
        scope=("year", "2025"),
    )
    add_generation(
        graph_db,
        "y26",
        [edge("g-e26", "event", "e26"), edge("inv-2", "inventory", "all")],
        scope=("year", "2026"),
    )
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    selected = {item["generation_id"] for item in pinned["selected"]}
    # 2025 reaches the superseded d7-old; 2026 and the dancers' pointers anchor.
    assert selected == {"d7-new", "d8", "y26", "g-e26", "inv-2"}
    withheld = {
        (item["unit_kind"], item["unit_id"]): item["reason"]
        for item in pinned["inventory"]
        if item["status"] == "withheld" and item["stage"] == "project"
    }
    assert withheld[("year", "2025")] == "year_anchor_conflict"
    assert withheld[("event", "e25")] == "year_anchor_conflict"
    assert ("event", "e26") not in withheld
    # The conflict left nothing of the skipped anchor behind.
    assert not {"g-e25", "inv-1", "d7-old"} & selected
    closure.validate(db.connection, pinned)


def test_a_release_without_a_year_anchor_selects_nothing(graph_db):
    db, _, _ = graph_db
    add_generation(graph_db, "d8", [], scope=("dancer", "8"))
    add_generation(graph_db, "event", [])
    add_generation(graph_db, "history", [edge("event")], scope=("history", "all"))
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    assert pinned["selected"] == []
    # The retired history scope is neither selected nor withheld.
    assert not any(item["unit_kind"] == "history" for item in pinned["inventory"])
