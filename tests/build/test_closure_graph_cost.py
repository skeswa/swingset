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


def add_generation(fixture, identifier, dependencies, *, continuity=None, history=False):
    db, at, run = fixture
    conn = db.connection
    dependency = digest(dependencies)
    recipe = {"continuity": {"dependency_set_id": continuity}} if continuity else {}
    kind, unit = ("history", "all") if history else ("event", identifier)
    with db.transaction():
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)",
            (dependency, canonical(dependencies)),
        )
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,?,?)",
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


def edge(identifier):
    return {
        "kind": "derivation",
        "key": ["project", "event", identifier],
        "generation_id": identifier,
    }


def test_wide_closure_enumerates_set_inventory_once_and_loads_each_receipt_once(
    graph_db, monkeypatch
):
    db, at, _ = graph_db
    count = 256
    for index in range(count):
        add_generation(graph_db, f"event-{index}", [{"kind": "version", "value": index}])
    add_generation(
        graph_db, "history", [edge(f"event-{index}") for index in range(count)], history=True
    )
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
    add_generation(graph_db, "history", [edge("prior-support"), edge("actual-owner")], history=True)
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    assert {item["generation_id"] for item in pinned["selected"]} == {
        "leaf",
        "prior-support",
        "actual-owner",
        "history",
    }
    assert shared in pinned["dependency_sets"] and leaf in pinned["dependency_sets"]
    closure.validate(db.connection, pinned)


@pytest.mark.parametrize(
    "field,value", [("output_digest", "forged"), ("unit_id", "other"), ("recipe", {"forged": True})]
)
def test_every_selected_receipt_field_is_still_compared(graph_db, field, value):
    db, _, _ = graph_db
    add_generation(graph_db, "event", [])
    add_generation(graph_db, "history", [edge("event")], history=True)
    pinned = closure.select(db.connection, cutoff=FakeClock().now()).manifest()
    forged = deepcopy(pinned)
    next(item for item in forged["selected"] if item["generation_id"] == "event")[field] = value
    forged["digest"] = digest({key: item for key, item in forged.items() if key != "digest"})
    with pytest.raises(ClosureError, match="selected_generation_receipt_mismatch"):
        closure.validate(db.connection, forged)
