"""The mark-sweep collector across stored classes (architecture phase 4, WP5; D-0231).

Per class an unrooted old victim goes and a rooted or young one stays; a set a
staged run captured survives, or comes back in step B; collection is
idempotent; a plan is stable and a moved digest stops its apply; unindexed
files and partial generations' references are orphans; schema 35 collects
sets under `operator-hold` only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from swingset.build.closure_manifest import canonical
from swingset.state import collector, objects
from swingset.state import db as db_module
from swingset.state.collector import CollectError
from swingset.state.db import Database, open_database
from swingset.state.derivation_dependencies import store_set
from swingset.state.retention import legacy_manifest_file, manifest_file

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
OLD = (NOW - timedelta(days=3)).isoformat()
YOUNG = NOW.isoformat()


def sha(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def aged(path: Path, days: float = 3) -> None:
    stamp = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


@pytest.fixture
def db(tmp_path):
    with open_database(tmp_path / "state") as database:
        database.start_run(NOW)
        yield database


def run_id(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT run_id FROM runs LIMIT 1").fetchone()[0])


def add_set(conn, set_id: str, members: object = (), *, created_at: str | None = OLD) -> str:
    conn.execute(
        "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json,created_at) "
        "VALUES (?,?,?)",
        (set_id, canonical(list(members)) if not isinstance(members, str) else members, created_at),
    )
    return set_id


def add_generation(
    conn,
    generation_id: str,
    *,
    stage: str = "project",
    kind: str = "event",
    unit: str = "u",
    set_id: str = "empty",
    recipe: object = None,
    output: str = "d" * 64,
    rows: int = 0,
    created_at: str = OLD,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
        "VALUES ('empty','[]')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES (?,?,?,?)",
        (stage, kind, unit, OLD),
    )
    conn.execute(
        "INSERT INTO derivation_generations VALUES (?,?,?,?,'f',?,?,NULL,?,?,?,?)",
        (
            generation_id,
            stage,
            kind,
            unit,
            recipe if isinstance(recipe, str) else canonical(recipe or {}),
            set_id,
            output,
            rows,
            created_at,
            run_id(conn),
        ),
    )


def put(db: Database, cls: str, raw: bytes, *, stored_at: str = OLD, days: float = 3) -> str:
    store = objects.Store(db.state_dir)
    digest, stored = store.put_bytes(cls, raw)
    aged(store.path(cls, digest), days)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) VALUES (?,?,?,?,?)",
            (cls, digest, len(raw), stored, stored_at),
        )
    return digest


def indexed(db: Database, cls: str, digest: str) -> bool:
    found = db.connection.execute(
        "SELECT 1 FROM objects WHERE class=? AND digest=?", (cls, digest)
    ).fetchone()
    return found is not None and objects.Store(db.state_dir).exists(cls, digest)


def sets(conn) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT dependency_set_id FROM derivation_dependency_sets")
    }


def seed_source(conn, generation_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO watches(watch_id,source,kind,method,url,parser,state) "
        "VALUES ('w1','eepro','round','GET','https://example.test/r','round','live')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO source_units(unit_key,watch_id,page_kind) VALUES ('u1','w1','round')"
    )
    conn.execute(
        "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,"
        "input_fingerprint,manifest_json,recipe_json,result_json,report_json,created_at,run_id,"
        "state,removal_authority) VALUES (?,'u1','round','1','f','[]','{}','{}','{}',?,?,"
        "'superseded','none')",
        (generation_id, OLD, run_id(conn)),
    )


def sweep(db: Database, *classes: str, orphans: tuple[str, ...] = (), now: datetime = NOW):
    return collector.sweep(db, now=now, classes=classes, orphans=orphans)


# ---------------------------------------------------------------------------
# Object classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", collector.OBJECT_CLASSES)
def test_an_unrooted_old_object_goes_and_a_rooted_or_young_one_stays(db, cls):
    victim = put(db, cls, b"victim\n")
    rooted = put(db, cls, b"rooted\n")
    young = put(db, cls, b"young\n", stored_at=YOUNG, days=0)
    with db.transaction() as conn:
        if cls == "derivation_output":
            add_generation(conn, "dg_root", output=rooted)
        elif cls == "parse_body":
            seed_source(conn, "sg_root")
            conn.execute(
                "INSERT INTO source_generation_packs VALUES ('sg_root',?,?)", (rooted, OLD)
            )
        else:
            add_generation(conn, "dg_root", stage="build", kind="release")
            conn.execute("INSERT INTO label_body_packs VALUES ('dg_root',?,?)", (rooted, OLD))
    result = sweep(db, cls)
    # Only the old unrooted one is marked; the young one never is.
    assert result["planned"][cls] == 1
    assert result["classes"][cls]["removed"] == 1
    assert not indexed(db, cls, victim)
    assert not objects.Store(db.state_dir).path(cls, victim).exists()
    assert indexed(db, cls, rooted) and indexed(db, cls, young)
    # Idempotent: nothing more to collect.
    assert sweep(db, cls)["classes"][cls]["removed"] == 0


def test_an_object_named_after_the_mark_stays(db, monkeypatch):
    """The delete rechecks the root rule in its own transaction."""
    victim = put(db, "derivation_output", b"late\n")
    real = collector._sweep_objects

    def name_then_sweep(database, cls, before, stop):
        with database.transaction() as conn:
            add_generation(conn, "dg_late", output=victim)
        return real(database, cls, before, stop)

    monkeypatch.setattr(collector, "_sweep_objects", name_then_sweep)
    result = sweep(db, "derivation_output")
    assert result["planned"]["derivation_output"] == 1
    assert result["classes"]["derivation_output"]["removed"] == 0
    assert indexed(db, "derivation_output", victim)


# ---------------------------------------------------------------------------
# Release manifests
# ---------------------------------------------------------------------------


def candidate(db: Database, name: str, policy: dict, **markers: dict) -> Path:
    path = db.state_dir / "candidates" / name
    (path / "_meta").mkdir(parents=True)
    (path / "_meta" / "manifest.json").write_text(json.dumps({"release_policy": policy}))
    for marker, value in markers.items():
        (path / marker).write_text(json.dumps(value))
    return path


def test_release_manifests_follow_retained_built_and_published_candidates(db):
    from swingset.build import manifest as manifests

    def write(name: str) -> str:
        digest, _ = manifests.write(db, {"format": manifests.FORMAT, "name": name}, now=NOW)
        aged(objects.Store(db.state_dir).path("release_manifest", digest))
        with db.transaction() as conn:
            conn.execute(
                "UPDATE release_manifests SET created_at=? WHERE manifest_digest=?", (OLD, digest)
            )
            conn.execute(
                "UPDATE objects SET stored_at=? WHERE class='release_manifest' AND digest=?",
                (OLD, digest),
            )
        return digest

    old, built, published = write("old"), write("built"), write("published")
    young, _ = manifests.write(db, {"format": manifests.FORMAT, "name": "young"}, now=NOW)
    # The writer stores an object, its row and the release row.
    assert manifest_file(db.state_dir, young) == objects.Store(db.state_dir).path(
        "release_manifest", young
    )
    assert json.loads(manifest_file(db.state_dir, young).read_bytes())["name"] == "young"
    candidate(db, "cand_built", {"mode": "partitioned", "manifest_digest": built}, BUILT={})
    for index in range(4):
        # Four published releases, newest last: the newest three keep theirs.
        candidate(
            db,
            f"cand_pub{index}",
            {"mode": "closure"},
            PUBLISHED={
                "manifest_digest": published if index == 3 else write(f"pub{index}"),
                "verified_at": f"2026-09-0{index + 1}T00:00:00+00:00",
            },
        )
    # A phase 3 file nothing indexes, old: it goes too.
    legacy = "e" * 64
    path = legacy_manifest_file(db.state_dir, legacy)
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    aged(path)
    result = sweep(db, "release_manifest")
    left = {
        str(row[0])
        for row in db.connection.execute("SELECT manifest_digest FROM release_manifests")
    }
    oldest = json.loads((db.state_dir / "candidates" / "cand_pub0" / "PUBLISHED").read_text())
    assert old not in left and oldest["manifest_digest"] not in left
    assert {built, published, young} <= left
    assert not indexed(db, "release_manifest", old) and not path.exists()
    assert indexed(db, "release_manifest", built)
    assert result["classes"]["release_manifest"]["removed"] == 3


def test_storage_apply_moves_phase_3_manifest_files_into_objects(db):
    body = canonical({"format": "release-manifest-v1"}).encode()
    digest = sha(body)
    path = legacy_manifest_file(db.state_dir, digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    wrong = legacy_manifest_file(db.state_dir, "f" * 64)
    wrong.write_text("{}")
    assert manifest_file(db.state_dir, digest) == path
    moved = collector.move_manifests(db, now=NOW)
    assert moved == {"moved": 1, "bytes": len(body), "mismatched": 1}
    assert not path.exists() and wrong.exists()
    assert indexed(db, "release_manifest", digest)
    assert manifest_file(db.state_dir, digest).read_bytes() == body
    assert collector.move_manifests(db, now=NOW)["moved"] == 0


# ---------------------------------------------------------------------------
# Dependency sets
# ---------------------------------------------------------------------------


def named(set_id: str, key: str = "group") -> dict:
    return {"fingerprint": set_id, "key": key, "kind": "dependency_set"}


def test_every_kind_of_name_keeps_a_set_and_the_rest_goes(db):
    with db.transaction() as conn:
        add_set(conn, "nested", [{"kind": "input_row", "row": {"a": 1}}])
        add_set(conn, "deep", [{"kind": "input_row", "row": {"b": 2}}])
        add_set(conn, "inner", [named("deep")])
        add_set(conn, "by_generation", [named("nested"), named("inner", "other")])
        add_set(conn, "by_equivalence")
        add_set(conn, "by_signature", '{"proof":true}')
        add_set(conn, "continuity")
        add_set(conn, "public_closure", '{"baseline":null}')
        add_set(conn, "private_closure", '{"baseline":null}')
        add_set(conn, "member_closure", '{"baseline":null}')
        add_set(conn, "build_set", [{"fingerprint": "member_closure", "kind": "release_closure"}])
        add_set(conn, "by_candidate", '{"baseline":null}')
        add_set(conn, "young", [named("child_of_young")], created_at=YOUNG)
        add_set(conn, "child_of_young")
        add_set(conn, "victim", [named("only_from_victim")])
        add_set(conn, "only_from_victim")
        add_set(conn, "pre_36", created_at=None)
        add_generation(conn, "dg_a", set_id="by_generation")
        add_generation(
            conn,
            "dg_cal",
            kind="calendar",
            recipe={"continuity": {"dependency_set_id": "continuity"}},
        )
        add_generation(
            conn,
            "dg_build",
            stage="build",
            kind="release",
            set_id="build_set",
            recipe={
                "context": {
                    "release_closure": {
                        "digest": "public_closure",
                        "private_digest": "private_closure",
                    }
                }
            },
        )
        conn.execute(
            "INSERT INTO derivation_recipes VALUES ('r1',?)",
            (canonical({"continuity": {}}),),
        )
        conn.execute(
            "INSERT INTO derivation_equivalences VALUES ('project','event','u','f2','dg_a','r1',"
            "'by_equivalence',?,?)",
            (OLD, run_id(conn)),
        )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_signature='by_signature' "
            "WHERE stage='project' AND unit_kind='calendar'"
        )
        # A packed build recipe names its closure from its label_body object.
        add_set(conn, "packed_closure", '{"baseline":null}')
        recipe = canonical({"context": {"release_closure": {"private_digest": "packed_closure"}}})
        add_generation(conn, "dg_packed", stage="build", kind="release", unit="p", recipe=recipe)
        digest, stored = objects.Store(db.state_dir).put_bytes("label_body", recipe.encode())
        objects.record(conn, "label_body", digest, len(recipe), stored, NOW)
        conn.execute("INSERT INTO label_body_packs VALUES ('dg_packed',?,?)", (digest, OLD))
        conn.execute(
            "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) "
            "VALUES (1,'test',?)",
            (OLD,),
        )
        conn.execute(
            "UPDATE derivation_generations SET recipe_json=? WHERE generation_id='dg_packed'",
            (objects.STUB,),
        )
        conn.execute("DELETE FROM storage_eviction_authority")
        # Rooted, and the highest rowid at the mark: that row is never a victim.
        add_set(conn, "last", [])
        add_generation(conn, "dg_last", unit="v", set_id="last")
    candidate(db, "cand_rejected", {"closure": {"private_digest": "by_candidate"}}, REJECTED={})
    before = sets(db.connection)
    plan = collector.plan(db, now=NOW)
    assert plan["classes"]["dependency_set"]["victims"] == 3
    result = sweep(db, "dependency_set")
    assert result["classes"]["dependency_set"]["removed"] == 3
    assert before - sets(db.connection) == {"victim", "only_from_victim", "pre_36"}
    assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert sweep(db, "dependency_set")["classes"]["dependency_set"]["removed"] == 0
    # The guard is back: no set leaves outside the collector.
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM derivation_dependency_sets WHERE dependency_set_id='young'")


def test_the_highest_set_at_the_mark_is_never_deleted(db):
    """So a set inserted during the sweep always gets a rowid the catch-up reads."""
    with db.transaction() as conn:
        add_set(conn, "a_victim")
        add_set(conn, "z_last_victim")
    sweep(db, "dependency_set")
    assert sets(db.connection) == {"z_last_victim"}


def test_a_set_named_while_the_sweep_runs_stays(db, monkeypatch):
    """A generation and a new parent set committed after the mark name two victims."""
    with db.transaction() as conn:
        add_set(conn, "named_late")
        add_set(conn, "nested_late")
        add_set(conn, "plain_victim")
        add_set(conn, "zz_highest")
    real = collector._catch_up
    calls = []

    def commit_then_catch_up(conn, state_dir, marks):
        if not calls:
            add_generation(conn, "dg_late", set_id="named_late")
            store_set(conn, "parent_late", canonical([named("nested_late")]), created_at=YOUNG)
        calls.append(1)
        real(conn, state_dir, marks)

    monkeypatch.setattr(collector, "_catch_up", commit_then_catch_up)
    sweep(db, "dependency_set")
    assert {"named_late", "nested_late", "parent_late", "zz_highest"} <= sets(db.connection)
    assert "plain_victim" not in sets(db.connection)


def test_an_unreadable_candidate_manifest_keeps_every_set(db):
    with db.transaction() as conn:
        add_set(conn, "victim")
        add_set(conn, "zz")
    broken = db.state_dir / "candidates" / "cand_broken" / "_meta"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json")
    result = sweep(db, "dependency_set")
    assert result["classes"]["dependency_set"]["stopped"].startswith("refused")
    assert "victim" in sets(db.connection)


# ---------------------------------------------------------------------------
# A staged unit's sets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ahead", [0, 3], ids=["young", "collected-between-a-and-b"])
def test_a_set_captured_by_an_open_staged_unit_survives(tmp_path, monkeypatch, ahead):
    """Step A captures sets only step B names. They are young, so the collector
    keeps them; collected anyway (a clock three days on), B writes them again."""
    from test_chunked_completion import YEAR, arranged, run_year, stored, twin

    from swingset.build.closure_manifest import leaves
    from swingset.state import derivations

    fixture = arranged.__wrapped__(tmp_path / "arranged")
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    real = derivations._store_building
    swept: list[dict] = []

    def collect_between(database, deferred, label):
        before = sets(database.connection)
        result = collector.sweep(
            database,
            now=fixture.now + timedelta(days=ahead),
            classes=("dependency_set",),
            orphans=(),
        )
        swept.append({"result": result, "gone": before - sets(database.connection)})
        captured = set(deferred.selection.dependency_sets)
        assert captured, "A captured sets that only B names"
        if ahead:
            assert captured & swept[-1]["gone"]
        else:
            assert not captured & swept[-1]["gone"]
        return real(database, deferred, label)

    monkeypatch.setattr(derivations, "_store_building", collect_between)
    with open_database(twin(fixture, tmp_path / "state")) as db:
        outcome = run_year(db, fixture, tmp_path)
        assert not outcome.failed, outcome
        conn = db.connection
        generation = derivations.selected_generation(conn, YEAR)
        assert swept and stored(conn, str(generation))["refs"]
        set_id = conn.execute(
            "SELECT dependency_set_id FROM derivation_generations WHERE generation_id=?",
            (generation,),
        ).fetchone()[0]
        assert list(leaves(conn, set_id))
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# ---------------------------------------------------------------------------
# Orphans
# ---------------------------------------------------------------------------


def test_an_unindexed_file_and_a_stale_write_go_after_a_day(db):
    store = objects.Store(db.state_dir)
    old_digest, _ = store.put_bytes("derivation_output", b"unindexed\n")
    young_digest, _ = store.put_bytes("parse_body", b"young\n")
    indexed_digest = put(db, "derivation_output", b"indexed\n")
    with db.transaction() as conn:
        add_generation(conn, "dg", output=indexed_digest)
    aged(store.path("derivation_output", old_digest))
    writing = store.root / f"{objects.WRITING_PREFIX}stale"
    writing.write_bytes(b"partial")
    aged(writing)
    fresh = store.root / f"{objects.WRITING_PREFIX}fresh"
    fresh.write_bytes(b"partial")
    unknown = store.root / "derivation_output" / "zz" / "not-a-digest"
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"?")
    aged(unknown)
    result = sweep(db, orphans=("writing", "unindexed"))
    assert result["classes"]["unindexed"]["removed"] == 1
    assert result["classes"]["writing"]["removed"] == 1
    assert not store.path("derivation_output", old_digest).exists()
    assert store.exists("parse_body", young_digest)
    assert indexed(db, "derivation_output", indexed_digest)
    assert not writing.exists() and fresh.exists() and unknown.exists()


def add_refs(conn, generation_id: str, ordinals: range) -> None:
    for ordinal in ordinals:
        payload = sha(f"{generation_id}/{ordinal}")
        conn.execute(
            "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)",
            (payload, f'{{"o":{ordinal}}}'),
        )
        conn.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,'t',?,?)",
            (generation_id, ordinal, str(ordinal), payload),
        )


def test_an_old_unnamed_partial_generation_loses_its_references(db, monkeypatch):
    from swingset.state import derivations

    monkeypatch.setattr(derivations, "CHUNK_ROWS", 2)
    with db.transaction() as conn:
        add_generation(conn, "dg_partial", unit="a", rows=6)
        add_refs(conn, "dg_partial", range(5))
        add_generation(conn, "dg_pointer", unit="b", rows=3)
        add_refs(conn, "dg_pointer", range(2))
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id='dg_pointer' WHERE unit_id='b'"
        )
        add_generation(conn, "dg_young", unit="c", rows=3, created_at=YOUNG)
        add_refs(conn, "dg_young", range(1))
        add_generation(conn, "dg_whole", unit="d", rows=2)
        add_refs(conn, "dg_whole", range(2))
        # Each of these is old and partial, but something names it.
        add_generation(conn, "dg_building", unit="e", rows=3)
        add_refs(conn, "dg_building", range(1))
        conn.execute(
            "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
            "input_fingerprint,run_id,started_at,updated_at) "
            "VALUES ('dg_building','project','event','e','f',?,?,?)",
            (run_id(conn), OLD, OLD),
        )
        add_generation(conn, "dg_equivalent", unit="f", rows=3)
        add_refs(conn, "dg_equivalent", range(2))
        add_generation(conn, "dg_evicted", unit="g", rows=4)
        add_refs(conn, "dg_evicted", range(2))  # the prefix a stopped evict leaves
        conn.execute("INSERT INTO derivation_evicted VALUES ('dg_evicted',?)", (OLD,))
    db.connection.execute("PRAGMA foreign_keys=OFF")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO derivation_equivalences VALUES "
            "('project','event','f','fp','dg_equivalent','r','empty',?,?)",
            (OLD, run_id(conn)),
        )
    db.connection.execute("PRAGMA foreign_keys=ON")
    result = sweep(db, orphans=("partial_refs",))["classes"]["partial_refs"]
    assert result == {"removed": 1, "references": 5, "generations": 1, "payloads": 5}
    refs = dict(
        db.connection.execute(
            "SELECT generation_id,count(*) FROM derivation_row_refs GROUP BY generation_id"
        ).fetchall()
    )
    assert refs == {
        "dg_pointer": 2,
        "dg_young": 1,
        "dg_whole": 2,
        "dg_building": 1,
        "dg_equivalent": 2,
        "dg_evicted": 2,
    }
    # The label stays, partial: a run whose inputs return stores it again.
    assert tuple(
        db.connection.execute(
            "SELECT row_count FROM derivation_generations WHERE generation_id='dg_partial'"
        ).fetchone()
    ) == (6,)
    assert derivations.incomplete_generations(db.connection)["rows"] >= 1


# ---------------------------------------------------------------------------
# Plan and apply
# ---------------------------------------------------------------------------


def victims(db: Database) -> None:
    with db.transaction() as conn:
        add_set(conn, "victim")
        add_set(conn, "zz")
    put(db, "parse_body", b"unrooted\n")


def test_a_plan_is_stable_and_its_apply_is_idempotent(db):
    victims(db)
    first = collector.plan(db, now=NOW)
    assert first == collector.plan(db, now=NOW + timedelta(hours=3))
    assert first["classes"]["dependency_set"]["victims"] == 1
    assert first["classes"]["parse_body"]["victims"] == 1
    receipt = collector.apply(db, plan_digest=first["digest"], now=NOW)
    assert receipt["removed"]["dependency_set"]["removed"] == 1
    assert receipt["removed"]["parse_body"]["removed"] == 1
    assert Path(receipt["receipt"]).is_file()
    again = collector.apply(db, plan_digest=first["digest"], now=NOW)
    assert again["removed"] == receipt["removed"]
    note = db.connection.execute(
        "SELECT kind,finished_at IS NOT NULL FROM storage_applies WHERE plan_digest=?",
        (first["digest"],),
    ).fetchone()
    assert tuple(note) == ("collect", 1)
    after = collector.plan(db, now=NOW)
    assert all(entry["victims"] == 0 for entry in after["classes"].values())


def test_a_moved_digest_stops_the_apply_before_anything_goes(db):
    victims(db)
    planned = collector.plan(db, now=NOW)
    with db.transaction() as conn:
        add_set(conn, "another")
    before = sets(db.connection)
    with pytest.raises(CollectError, match="is not the plan of this state"):
        collector.apply(db, plan_digest=planned["digest"], now=NOW)
    assert sets(db.connection) == before


def test_the_command_plans_and_applies(tmp_path, capsys):
    from swingset import cli

    with open_database(tmp_path / "state") as db:
        db.start_run(NOW)
        victims(db)
    clock = ["--state", str(tmp_path / "state")]
    assert cli.main(["gc", "--collect", *clock]) == 1
    assert cli.main(["gc", "--collect", "--plan", *clock]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert Path(planned["plan"]).is_file()
    assert cli.main(["gc", "--collect", "--apply", planned["digest"], *clock]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["plan_digest"] == planned["digest"]


# ---------------------------------------------------------------------------
# Schemas 33 to 35 (the F39 backport)
# ---------------------------------------------------------------------------


@pytest.fixture
def schema_35(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 35)
    with open_database(tmp_path / "state") as database:
        assert database.schema_version == 35
        database.start_run(NOW)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES ('victim','[]'),('kept','[]'),('nested_victim','[]')"
            )
            conn.execute(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES ('parent_victim',?)",
                (canonical([named("nested_victim")]),),
            )
            add_generation(conn, "dg", set_id="kept")
            conn.execute(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
                "VALUES ('later','[]')"
            )
        yield database


def test_schema_35_collects_sets_only_under_operator_hold(schema_35):
    database = schema_35
    trigger = database.connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='derivation_dependency_no_delete'"
    ).fetchone()[0]
    with pytest.raises(CollectError, match="operator-hold"):
        collector.sweep(database, now=NOW, classes=("dependency_set",), orphans=())
    (database.state_dir / "operator-hold").write_text("test")
    result = collector.sweep(database, now=NOW)
    # victim, nested_victim, parent_victim, and `empty`, which nothing names.
    assert result["classes"]["dependency_set"]["removed"] == 4
    assert result["classes"]["derivation_output"]["stopped"] == "absent"
    assert sets(database.connection) == {"kept", "later"}
    assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        database.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='derivation_dependency_no_delete'"
        ).fetchone()[0]
        == trigger
    )
    with contextlib.suppress(sqlite3.IntegrityError), database.transaction() as conn:
        conn.execute("DELETE FROM derivation_dependency_sets WHERE dependency_set_id='later'")
    assert "later" in sets(database.connection)


def test_a_reused_object_is_young_again_and_never_names_a_removed_file(db):
    """A writer that reuses an old object's file moves its row's age up; one
    whose file a sweep removed meanwhile fails its transaction instead of
    naming a file that is gone (the collector moves victims aside inside its
    own transaction)."""
    store = objects.Store(db.state_dir)
    digest = put(db, "release_manifest", b'{"built":[]}')
    # The writer found the old file intact and kept it; the sweep runs before
    # the writer records its row.
    assert sweep(db, "release_manifest")["classes"]["release_manifest"]["removed"] == 1
    assert not store.exists("release_manifest", digest)
    assert not list(store.root.glob(f"{collector.COLLECTING_PREFIX}*"))
    with pytest.raises(objects.ObjectUnavailable, match="removed before its row"):
        with db.transaction() as conn:
            objects.record(conn, "release_manifest", digest, 12, 12, NOW)
    assert not indexed(db, "release_manifest", digest)
    # Written again, it is recorded young.
    again, stored = store.put_bytes("release_manifest", b'{"built":[]}')
    with db.transaction() as conn:
        objects.record(conn, "release_manifest", again, 12, stored, NOW)
    old = put(db, "parse_body", b"reused\n")
    with db.transaction() as conn:
        objects.record(conn, "parse_body", old, 7, 7, NOW)
    stamp = db.connection.execute(
        "SELECT stored_at FROM objects WHERE class='parse_body' AND digest=?", (old,)
    ).fetchone()[0]
    assert stamp == objects.stamp(NOW)
    assert sweep(db, "parse_body")["classes"]["parse_body"]["removed"] == 0


def test_manifests_and_artifacts_wait_while_a_backup_set_is_written(db):
    """A set copies the database, then lists files: a manifest removed in
    between would be named by its copy and absent from the set."""
    from swingset.state import rwlock

    digest = put(db, "release_manifest", b'{"built":[]}')
    held = rwlock.try_loop(db.state_dir, collector.BACKUP_LOOP)
    assert held is not None
    try:
        result = sweep(db, "release_manifest", "partition_artifact")
    finally:
        held.release()
    assert result["classes"]["release_manifest"]["stopped"] == "backup_running"
    assert indexed(db, "release_manifest", digest)
    assert sweep(db, "release_manifest")["classes"]["release_manifest"]["removed"] == 1


def test_an_object_reused_between_the_mark_and_the_sweep_stays(db, monkeypatch):
    """Marked as an old victim, then a writer finds its file intact, reuses it
    and records it (its row's age moves up to now): the sweep rechecks the
    age in its own transaction and keeps both the row and the file."""
    victim = put(db, "parse_body", b"reused after the mark\n")
    real = collector._sweep_objects

    def reuse_then_sweep(database, cls, before, stop):
        with database.transaction() as conn:
            objects.record(conn, "parse_body", victim, 22, 22, NOW)
        return real(database, cls, before, stop)

    monkeypatch.setattr(collector, "_sweep_objects", reuse_then_sweep)
    result = sweep(db, "parse_body")
    assert result["planned"]["parse_body"] == 1
    assert result["classes"]["parse_body"]["removed"] == 0
    assert indexed(db, "parse_body", victim)


def test_a_file_a_crash_left_aside_goes_after_a_day(db):
    """A crash after a sweep's commit leaves `.collecting-<class>-<digest>`."""
    store = objects.Store(db.state_dir)
    store.root.mkdir(parents=True, exist_ok=True)
    old = store.root / f"{collector.COLLECTING_PREFIX}parse_body-{'a' * 64}"
    old.write_bytes(b"aside")
    aged(old)
    young = store.root / f"{collector.COLLECTING_PREFIX}parse_body-{'b' * 64}"
    young.write_bytes(b"aside")
    result = sweep(db, orphans=("writing",))
    assert result["classes"]["writing"]["removed"] == 1
    assert not old.exists() and young.exists()


def test_a_sweep_that_rolls_back_puts_its_files_back(db, monkeypatch):
    store = objects.Store(db.state_dir)
    digest = put(db, "label_body", b"victim\n")
    real = collector._Moved.away

    def away_then_fail(self, cls, key):
        real(self, cls, key)
        assert not store.exists(cls, key)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(collector._Moved, "away", away_then_fail)
    result = sweep(db, "label_body")
    assert result["classes"]["label_body"]["stopped"] == ("error: OperationalError: disk I/O error")
    assert indexed(db, "label_body", digest)


def test_a_class_that_raises_is_isolated_and_the_rest_are_collected(db, monkeypatch):
    """One class's mark or sweep that raises stops only that class: it says
    why, the sweep is not complete, and maintain reports it failed."""
    from swingset.loops import maintain

    victims(db)
    label = put(db, "label_body", b"label victim\n")

    def broken_mark(conn, cls, before):
        if cls == "parse_body":
            raise ValueError("bad stored_at")
        return real_mark(conn, cls, before)

    def broken_sweep(database, before, stop):
        raise PermissionError(13, "Permission denied")

    real_mark = collector._mark_objects
    monkeypatch.setattr(collector, "_mark_objects", broken_mark)
    monkeypatch.setattr(collector, "_sweep_writing", broken_sweep)
    stale = objects.Store(db.state_dir).root / f"{objects.WRITING_PREFIX}stale"
    stale.write_bytes(b"x")
    aged(stale)
    result = maintain.sweep(db, now=NOW, stop=lambda: False)
    classes = result["classes"]
    assert classes["parse_body"]["stopped"] == "error: ValueError: bad stored_at"
    assert classes["writing"]["stopped"].startswith("error: PermissionError")
    assert classes["label_body"]["removed"] == 1 and not indexed(db, "label_body", label)
    assert classes["dependency_set"]["removed"] == 1
    assert result["complete"] is False and result["failed"] is True
    assert result["error"].startswith("parse_body: ValueError: bad stored_at; writing:")
