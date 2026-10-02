"""Eviction, the one-time storage move, and incremental eviction (phase 4, WP3)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_h15_acceptance import add_dancer, drain_project, link_all
from test_h15_acceptance import source_fixture as source_fixture
from test_output_packs import remint
from test_release_closure_rows import baseline_events

from swingset.build import partitions
from swingset.cli import main
from swingset.state import objects, output_packs, retention, storage
from swingset.state.db import SCHEMA_VERSION, Database, open_database
from swingset.state.retention_apply import receipt_directory
from swingset.state.storage import StorageError
from swingset.state.work import WorkUnit

NOW = datetime(2026, 10, 1, tzinfo=UTC)
LATER = NOW + timedelta(days=2)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def empty_set(conn: sqlite3.Connection) -> str:
    identifier = hashlib.sha256(b"[]").hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
        "VALUES (?,'[]')",
        (identifier,),
    )
    return identifier


def label(
    database: Database,
    generation_id: str,
    unit: str,
    rows: list[tuple[str, str, dict[str, object]]],
    *,
    point: bool = False,
    stored: int | None = None,
    created: datetime = NOW,
) -> None:
    """A labelled project/event generation; ``stored`` keeps only the first references."""
    conn = database.connection
    run = database.start_run(NOW)
    output = hashlib.sha256()
    scope = ("project", "event", unit)
    with database.transaction():
        for ordinal, (table, key, payload) in enumerate(rows):
            text = canonical(payload)
            output.update(canonical((table, key, text)).encode() + b"\n")
            if stored is not None and ordinal >= stored:
                continue
            sha = hashlib.sha256(text.encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (sha, text))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (generation_id, ordinal, table, key, sha),
            )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
            "VALUES (?,?,?,?)",
            (*scope, NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,'{}',?,NULL,?,?,?,?)",
            (
                generation_id,
                *scope,
                "fingerprint-" + generation_id,
                empty_set(conn),
                output.hexdigest(),
                len(rows),
                created.isoformat(),
                run,
            ),
        )
        if point:
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=? "
                "WHERE stage=? AND unit_kind=? AND unit_id=?",
                (generation_id, *scope),
            )


def rows_of(
    name: str, count: int, *, shared: bool = False
) -> list[tuple[str, str, dict[str, object]]]:
    return [
        ("entries", f'["{name}-{index}"]', {"name_raw": "Shared" if shared else f"{name} {index}"})
        for index in range(count)
    ]


def building(database: Database, generation_id: str, unit: str) -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO derivation_building(generation_id,stage,unit_kind,unit_id,"
            "input_fingerprint,run_id,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (generation_id, "project", "event", unit, "f", "run", NOW.isoformat(), NOW.isoformat()),
        )


def populate(database: Database) -> None:
    """Every kind of generation the plan sorts: hot, partial, empty and evictable."""
    label(database, "dg_old", "event-a", rows_of("old", 4) + rows_of("s", 1, shared=True))
    label(
        database, "dg_cur", "event-a", rows_of("cur", 3) + rows_of("s", 1, shared=True), point=True
    )
    label(database, "dg_cold", "event-b", rows_of("cold", 6))
    label(database, "dg_part", "event-c", rows_of("part", 3), stored=1)
    label(database, "dg_build", "event-d", rows_of("build", 2))
    building(database, "dg_build", "event-d")
    label(database, "dg_empty", "event-e", [])


def everything(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str, str]]]:
    """Every complete generation's rows, read through the accessor."""
    identifiers = [
        str(row[0])
        for row in conn.execute("SELECT generation_id FROM derivation_generations ORDER BY 1")
    ]
    return {
        identifier: list(output_packs.rows(conn, identifier))
        for identifier in identifiers
        if output_packs.complete(conn, identifier)
    }


def refs(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute("SELECT * FROM derivation_row_refs ORDER BY 1,2")]


def payloads(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute("SELECT payload_sha256 FROM derivation_payloads ORDER BY 1")
    ]


def schema(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='derivation_row_refs' "
            "ORDER BY rowid"
        )
    ]


def marked(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in conn.execute("SELECT generation_id FROM derivation_evicted ORDER BY 1")
    ]


def hold(state: Path) -> None:
    (state / "operator-hold").write_text("test\n")


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_the_plan_is_stable_and_sorts_every_generation(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        before = refs(conn)
        first = storage.plan(conn, database.state_dir, now=NOW)
        second = storage.plan(conn, database.state_dir, now=NOW)
        assert first.content == second.content and first.digest == second.digest
        # Writing nothing: same rows, no temp tables left, no packs.
        assert refs(conn) == before
        assert conn.execute("SELECT count(*) FROM sqlite_temp_master").fetchone()[0] == 0
        assert not (database.state_dir / "objects").exists()
        content = first.content
        assert content["stages"] == ["derivation_output", "parse_body", "label_body"]
        assert content["pending_stages"] == []
        # dg_old, dg_cold and the empty one go; the pointer, the building one and
        # the partial one stay.
        assert content["evict"]["generations"] == 3
        assert content["evict"]["rows"] == 5 + 6
        assert content["evict"]["packs"] == 3
        assert content["keep"] | {"digest": None} == {
            "pointer": 1,
            "building": 1,
            "partial": 1,
            "unlabelled": 0,
            "references": 4 + 2 + 1,
            "digest": None,
        }
        evicted = hashlib.sha256()
        for generation_id in ("dg_cold", "dg_empty", "dg_old"):
            evicted.update(
                f"{generation_id}\t{output_packs.label(conn, generation_id)[0]}\n".encode()
            )
        assert content["evict"]["digest"] == evicted.hexdigest()
        estimate = first.estimate
        assert estimate is not None
        assert estimate["references"] == len(before)
        assert estimate["references_moved"] == 11
        assert estimate["packs_to_write"] == 3
        # Unique payloads of dg_old and dg_cold; the shared one stays.
        assert estimate["payloads_removed"] == 10
        path = storage.write_plan(database.state_dir, first)
        assert path == database.state_dir / "gc" / "plans" / f"storage-{first.digest}.json"
        assert json.loads(path.read_text())["digest"] == first.digest


def test_a_root_added_between_plan_and_apply_stops_it(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        written = storage.plan(conn, database.state_dir, now=NOW)
        # The cold generation becomes a current pointer.
        with database.transaction():
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id='dg_cold' "
                "WHERE unit_id='event-b'"
            )
        with pytest.raises(StorageError, match="not the plan of this state"):
            storage.apply(database, plan_digest=written.digest, now=NOW)
        assert conn.execute("SELECT count(*) FROM storage_applies").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM objects").fetchone()[0] == 0
        assert marked(conn) == []


def test_apply_needs_the_hold_and_the_writer_lock(tmp_path):
    state = tmp_path / "state"
    with open_database(state) as database:
        populate(database)
        written = storage.plan(database.connection, state, now=NOW)
        with pytest.raises(StorageError, match="operator-hold"):
            storage.apply(database, plan_digest=written.digest, now=NOW)
    hold(state)
    with open_database(state, lock=False) as unlocked:
        with pytest.raises(StorageError, match="writer lock"):
            storage.apply(unlocked, plan_digest=written.digest, now=NOW)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_moves_what_the_plan_named_and_is_idempotent(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        before = everything(conn)
        shape = schema(conn)
        kept = [row for row in refs(conn) if row[0] in {"dg_cur", "dg_part", "dg_build"}]
        written = storage.plan(conn, database.state_dir, now=NOW)
        receipt = storage.apply(database, plan_digest=written.digest, now=NOW)
        assert receipt["generations_evicted"] == 3
        assert receipt["references_kept"] == len(kept)
        assert receipt["payloads_removed"] == 10
        assert receipt["packs"] | {"raw_bytes": 0, "stored_bytes": 0} == {
            "written": 3,
            "kept": 0,
            "raw_bytes": 0,
            "stored_bytes": 0,
            "verified": 3,
            "repaired": 0,
        }
        assert receipt["room"]["secure_delete"] == 0
        assert receipt["room"]["needed_bytes"] >= storage.MOVE_MARGIN_BYTES
        assert receipt["history_fence_revision"][1] > receipt["history_fence_revision"][0]
        assert refs(conn) == kept
        assert marked(conn) == ["dg_cold", "dg_empty", "dg_old"]
        assert schema(conn) == shape
        assert everything(conn) == before
        for generation_id in ("dg_cur", "dg_build"):
            assert output_packs.resident(conn, generation_id)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # The rebuilt table still guards its rows.
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with database.transaction():
                conn.execute("DELETE FROM derivation_row_refs WHERE generation_id='dg_cur'")
        # Again: the recorded receipt, nothing moved.
        assert storage.apply(database, plan_digest=written.digest, now=LATER) == receipt
        path = receipt_directory(database.state_dir) / f"storage-{written.digest}.json"
        assert json.loads(path.read_text()) == receipt
        note = conn.execute("SELECT * FROM storage_applies").fetchall()
        assert len(note) == 1 and note[0]["kind"] == "storage"
        # A fresh plan has nothing left to move.
        after = storage.plan(conn, database.state_dir, now=NOW)
        assert after.content["evict"]["generations"] == 0
        assert after.content["evicted"]["generations"] == 3


def test_apply_repairs_a_pack_damaged_after_packing(tmp_path, monkeypatch):
    """`_verify_all` reads every pack before anything leaves the file; one
    damaged between packing and verifying is written again from its rows."""
    import gzip

    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        before = everything(conn)
        written = storage.plan(conn, database.state_dir, now=NOW)
        digest = output_packs.label(conn, "dg_cold")[0]

        def damage(name: str) -> None:
            if name == "packed":
                path = objects.store_for(conn).path(output_packs.CLASS, digest)
                path.write_bytes(gzip.compress(b"damaged\n", mtime=0))

        monkeypatch.setattr(storage, "_stage", damage)
        receipt = storage.apply(database, plan_digest=written.digest, now=NOW)
        assert receipt["packs"]["verified"] == 3 and receipt["packs"]["repaired"] == 1
        objects.store_for(conn).verify(output_packs.CLASS, digest)
        assert everything(conn) == before


def test_the_move_counts_the_zeroed_b_tree_when_secure_delete_is_on(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        written = storage.plan(conn, database.state_dir, now=NOW)
        fresh = written.content
        conn.execute("PRAGMA secure_delete=OFF")
        off = storage.move_need(conn, fresh, written.document())
        conn.execute("PRAGMA secure_delete=ON")
        on = storage.move_need(conn, fresh, written.document())
        assert off["secure_delete"] == 0 and on["secure_delete"] == 1
        assert on["reference_bytes"] > 0
        assert on["needed_bytes"] == off["needed_bytes"] + on["reference_bytes"]


KILL = """
import os, sys
from datetime import UTC, datetime
from swingset.state import storage
from swingset.state.db import open_database

where = sys.argv[3]
def stage(name):
    if name == where:
        os._exit(77)
storage._stage = stage
with open_database(sys.argv[1]) as database:
    storage.apply(database, plan_digest=sys.argv[2], now=datetime(2026, 10, 1, tzinfo=UTC))
"""


def killed(tmp_path: Path, state: Path, digest: str, where: str) -> None:
    script = tmp_path / "kill.py"
    script.write_text(KILL)
    done = subprocess.run(
        [sys.executable, str(script), str(state), digest, where], capture_output=True
    )
    assert done.returncode == 77, done.stderr.decode()


def test_a_kill_after_packing_leaves_only_extra_packs(tmp_path):
    state = tmp_path / "state"
    with open_database(state) as database:
        populate(database)
        hold(state)
        conn = database.connection
        before = (refs(conn), payloads(conn), everything(conn))
        digest = storage.plan(conn, state, now=NOW).digest
    killed(tmp_path, state, digest, "packed")
    with open_database(state) as database:
        conn = database.connection
        assert (refs(conn), payloads(conn), everything(conn)) == before
        assert marked(conn) == []
        packs = conn.execute("SELECT digest FROM objects WHERE class=?", (output_packs.CLASS,))
        assert len(packs.fetchall()) == 3
        note = conn.execute("SELECT finished_at FROM storage_applies").fetchone()
        assert note is not None and note[0] is None
        # The same digest resumes: packs are kept, the move runs once.
        receipt = storage.apply(database, plan_digest=digest, now=NOW)
        assert receipt["resumed"] is True
        assert receipt["packs"]["kept"] == 3 and receipt["packs"]["written"] == 0
        assert marked(conn) == ["dg_cold", "dg_empty", "dg_old"]
        assert everything(conn) == before[2]


@pytest.mark.parametrize("where", ["dropped", "marked", "finished"])
def test_a_kill_inside_the_move_rolls_back(tmp_path, where):
    state = tmp_path / "state"
    with open_database(state) as database:
        populate(database)
        hold(state)
        conn = database.connection
        before = (refs(conn), payloads(conn), schema(conn), everything(conn))
        digest = storage.plan(conn, state, now=NOW).digest
    killed(tmp_path, state, digest, where)
    with open_database(state) as database:
        conn = database.connection
        assert (refs(conn), payloads(conn), schema(conn), everything(conn)) == before
        assert marked(conn) == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not (receipt_directory(state) / f"storage-{digest}.json").exists()
        receipt = storage.apply(database, plan_digest=digest, now=NOW)
        assert receipt["generations_evicted"] == 3
        assert everything(conn) == before[3]


def test_after_apply_every_generation_reads_identically(source_fixture, tmp_path):
    """A real pipeline state: reads, the retention plan, the build and a checkpoint."""
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint

    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    remint(f, WorkUnit("project", "event", "event-a"))
    baseline = baseline_events(f, tmp_path / "baseline")
    conn = f.conn
    state = f.db.state_dir

    def build() -> dict[str, Any]:
        return partitions.build(
            f.db,
            history_start=f.bundle.config.history_start,
            baseline=baseline,
            run_id=f.corpus.run,
            now=f.corpus.clock.now(),
        )

    first = build()
    knobs = {"max_database_bytes": 1 << 40, "recent_window": 1, "collect_older_than": 86400.0}
    hold(state)
    before = everything(conn)
    totals = retention.plan(conn, state, **knobs).content["totals"]
    written = storage.plan(conn, state, now=NOW)
    assert written.content["evict"]["generations"] > 0
    receipt = storage.apply(f.db, plan_digest=written.digest, now=NOW)
    assert receipt["generations_evicted"] == written.content["evict"]["generations"]
    assert everything(conn) == before
    after = retention.plan(conn, state, **knobs).content
    for key in (
        "local_generations",
        "archivable_generations",
        "local_generations_by_root",
        "unknown_row_reference_generations",
    ):
        assert after["totals"][key] == totals[key]
    assert "incomplete_generations" not in after["totals"]
    assert after["residence"]["evicted_generations"] == receipt["generations_evicted"]
    assert after["residence"]["partial_generations"] == 0
    again = build()
    assert again["rebuilt"] == 0 and again.get("withheld", 0) == 0
    assert again["reused"] == first["partitions"]
    # An old-format checkpoint copies objects/ whole and restores readable.
    checkpoint = tmp_path / "checkpoint"
    create_checkpoint(
        state,
        conn,
        checkpoint,
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    assert any(
        name.startswith("objects/")
        for name in json.loads((checkpoint / "checkpoint.json").read_text())["files"]
    )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint, restored, maximum_schema_version=SCHEMA_VERSION)
    copy = sqlite3.connect(f"file:{restored / 'state.sqlite'}?mode=ro", uri=True)
    try:
        assert everything(copy) == before
        assert marked(copy) == marked(conn)
    finally:
        copy.close()


def test_the_storage_command_plans_and_applies(tmp_path, capsys):
    state = tmp_path / "state"
    with open_database(state) as database:
        populate(database)
    assert main(["storage", "plan", "--state", str(state)]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert Path(planned["plan"]).is_file()
    assert planned["estimate"]["packs_to_write"] == 3
    assert main(["storage", "apply", planned["digest"], "--state", str(state)]) == 1
    hold(state)
    assert main(["storage", "apply", "--state", str(state)]) == 1
    capsys.readouterr()
    assert main(["storage", "apply", planned["digest"], "--state", str(state)]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["plan_digest"] == planned["digest"]
    assert applied["generations_evicted"] == 3
    # A lost pack with no local set to put it back from: exit 1, named.
    with open_database(state) as database:
        digest = output_packs.label(database.connection, "dg_cold")[0]
        objects.store_for(database.connection).path(output_packs.CLASS, digest).unlink()
    assert main(["storage", "recover-objects", "--state", str(state)]) == 1
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["lost"] == 1 and recovered["lost_objects"][0]["digest"] == digest


# ---------------------------------------------------------------------------
# Incremental eviction
# ---------------------------------------------------------------------------


def test_incremental_evict_skips_hot_and_young_generations(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CHUNK_ROWS", 2)
    with open_database(tmp_path / "state") as database:
        populate(database)
        label(database, "dg_young", "event-f", rows_of("young", 2), created=LATER)
        conn = database.connection
        before = everything(conn)
        transactions: list[int] = []
        real = storage._shed

        def counted(*args: Any, **kwargs: Any) -> int:
            deleted = real(*args, **kwargs)
            transactions.append(deleted)
            return deleted

        monkeypatch.setattr(storage, "_shed", counted)
        # Three references: dg_cold (first by id) loses its top three, two then one.
        first = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=3)
        assert first["references"] == 3 and transactions == [2, 1]
        assert marked(conn) == ["dg_cold"]
        ordinals = [row[1] for row in refs(conn) if row[0] == "dg_cold"]
        assert ordinals == [0, 1, 2]
        assert list(output_packs.rows(conn, "dg_cold")) == before["dg_cold"]
        # A stopped generation pages in from its prefix.
        with database.transaction():
            assert output_packs.page_in(conn, "dg_cold", now=NOW)
        assert output_packs.resident(conn, "dg_cold") and marked(conn) == []
        transactions.clear()
        second = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=100)
        assert all(0 <= deleted <= 2 for deleted in transactions)
        assert second["generations"] == 3
        # Hot, partial and young generations keep their references.
        assert marked(conn) == ["dg_cold", "dg_empty", "dg_old"]
        kept = {row[0] for row in refs(conn)}
        assert kept == {"dg_cur", "dg_part", "dg_build", "dg_young"}
        assert everything(conn) == before
        # Unique payloads went; the one the pointer shares stayed.
        names = {
            json.loads(payload)["name_raw"]
            for (payload,) in conn.execute("SELECT payload_json FROM derivation_payloads")
        }
        assert "Shared" in names and not any(name.startswith(("old", "cold")) for name in names)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # Nothing old and cold is left.
        assert (
            storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=100)["generations"]
            == 0
        )


def test_incremental_evict_waits_out_a_publication(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(storage, "publication_active", lambda conn: True)
            result = storage.evict(database, now=LATER, budget_refs=100)
        assert result["reason"] == "publication"
        assert marked(database.connection) == []
        assert objects.Store(database.state_dir).root.exists() is False


def test_incremental_evict_finishes_a_stopped_generation_first(tmp_path, monkeypatch):
    """A generation a tick left part way stays marked with a prefix; the next
    tick sheds the rest of it before it starts another (`_leftovers`)."""
    monkeypatch.setattr(storage, "CHUNK_ROWS", 2)
    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        before = everything(conn)
        first = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=3)
        assert marked(conn) == ["dg_cold"]
        assert [row[1] for row in refs(conn) if row[0] == "dg_cold"] == [0, 1, 2]
        # It left dg_cold part way and did not reach dg_empty, dg_old and the
        # partial dg_part (counted until a pass reaches and skips it).
        assert first["backlog"] == 1 + 3
        second = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=3)
        assert second["references"] == 3
        assert [row for row in refs(conn) if row[0] == "dg_cold"] == []
        assert marked(conn) == ["dg_cold"]
        assert list(output_packs.rows(conn, "dg_cold")) == before["dg_cold"]


def test_a_shed_after_a_page_in_leaves_the_generation_alone(tmp_path, monkeypatch):
    """A page-in between two shed transactions takes the marker away; the
    next shed must not strip the now unmarked generation."""
    monkeypatch.setattr(storage, "CHUNK_ROWS", 2)
    with open_database(tmp_path / "state") as database:
        label(database, "dg_cold", "event-b", rows_of("cold", 6))
        conn = database.connection
        done: list[bool] = []

        def stop() -> bool:
            if output_packs.evicted(conn, "dg_cold") and not done:
                done.append(True)
                with database.transaction():
                    assert output_packs.page_in(conn, "dg_cold", now=NOW)
            return False

        result = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=100, stop=stop)
        assert result["skipped"] == 1 and result["generations"] == 0
        assert marked(conn) == []
        assert output_packs.complete(conn, "dg_cold") and output_packs.resident(conn, "dg_cold")


def test_a_damaged_shared_pack_is_written_again_and_eviction_goes_on(tmp_path):
    """Bit rot in a pack: the next eviction of a resident generation with the
    same output repairs it, and the other generations are evicted (I1)."""
    import gzip

    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        before = everything(conn)
        store = objects.store_for(conn)
        digest = output_packs.pack(conn, store, "dg_cold", now=NOW)
        path = store.path(output_packs.CLASS, digest)
        path.write_bytes(gzip.compress(b"damaged\n", mtime=0))
        result = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=100)
        assert result["repaired"] == 1 and result["failed"] == 0
        assert result["generations"] == 3 and result["backlog"] == 0
        assert marked(conn) == ["dg_cold", "dg_empty", "dg_old"]
        store.verify(output_packs.CLASS, digest)
        assert everything(conn) == before


def test_a_generation_that_cannot_be_packed_is_isolated_with_a_guard(tmp_path, monkeypatch):
    from swingset.state import guards

    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        real = output_packs.pack
        calls: list[str] = []

        def failing(conn_, store, generation_id, **options):
            calls.append(generation_id)
            if generation_id == "dg_cold":
                raise OSError(5, "Input/output error")
            return real(conn_, store, generation_id, **options)

        monkeypatch.setattr(output_packs, "pack", failing)
        later = LATER + timedelta(hours=1)
        for day in range(2):
            result = storage.evict(database, now=later + timedelta(days=day), budget_refs=100)
            assert result["failed"] == 1 and result["generations"] == 2 - 2 * day
            assert result["failures"][0]["generation_id"] == "dg_cold"
            assert "Input/output error" in result["failures"][0]["error"]
            assert result["backlog"] == 1
        assert marked(conn) == ["dg_empty", "dg_old"]
        row = guards.get(conn, storage.GUARD_SUBJECT, storage.EVICT_FAILED)
        assert row is not None and row.seen == 2
        # The guard's clock runs from the first failure, so [guard-expired]
        # names one that lasts a day.
        assert row.expires_at == guards.at(later + storage.GUARD_CLOCK)
        # Two failures: it waits two days, and is not packed meanwhile; the
        # guard stays, since it is still owed.
        calls.clear()
        result = storage.evict(database, now=later + timedelta(days=2), budget_refs=100)
        assert result["deferred"] == 1 and result["failed"] == 0 and calls == []
        assert result["backlog"] == 1
        assert guards.get(conn, storage.GUARD_SUBJECT, storage.EVICT_FAILED) is not None
        monkeypatch.setattr(output_packs, "pack", real)
        result = storage.evict(database, now=later + timedelta(days=3), budget_refs=100)
        assert result["failed"] == result["deferred"] == 0 and result["generations"] == 1
        assert guards.get(conn, storage.GUARD_SUBJECT, storage.EVICT_FAILED) is None
        retry = json.loads((database.state_dir / storage.RETRY_FILE).read_text())
        assert retry["generations"] == {}


def test_generations_that_always_fail_never_starve_the_ones_after_them(tmp_path, monkeypatch):
    """Two generations that sort first and always fail, with `MAX_FAILURES`
    at two: the pass goes on past them, and later passes skip them while
    they wait out their retry window (I1)."""
    monkeypatch.setattr(storage, "MAX_FAILURES", 2)
    with open_database(tmp_path / "state") as database:
        populate(database)
        label(database, "dg_aa", "event-f", rows_of("aa", 2))
        label(database, "dg_ab", "event-g", rows_of("ab", 2))
        conn = database.connection
        real = output_packs.pack
        calls: list[str] = []

        def failing(conn_, store, generation_id, **options):
            calls.append(generation_id)
            if generation_id in {"dg_aa", "dg_ab"}:
                raise output_packs.GenerationUnavailable(f"{generation_id} does not match")
            return real(conn_, store, generation_id, **options)

        monkeypatch.setattr(output_packs, "pack", failing)
        later = LATER + timedelta(hours=1)
        first = storage.evict(database, now=later, budget_refs=10_000)
        assert first["failed"] == 2 and first["generations"] == 3
        assert marked(conn) == ["dg_cold", "dg_empty", "dg_old"]
        for day in range(1, 5):
            label(database, f"dg_z{day}", f"event-z{day}", rows_of(f"z{day}", 2))
            calls.clear()
            result = storage.evict(database, now=later + timedelta(days=day), budget_refs=10_000)
            assert f"dg_z{day}" in marked(conn)
            # Retried after one day, then two, then four.
            waiting = {1: False, 2: True, 3: False, 4: True}[day]
            assert (calls[:2] == ["dg_aa", "dg_ab"]) is not waiting
            assert result["deferred"] == (2 if waiting else 0)


def test_a_full_disk_stops_the_pass_and_failures_charge_the_budget(tmp_path, monkeypatch):
    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        real = output_packs.pack

        def full(conn_, store, generation_id, **options):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(output_packs, "pack", full)
        later = LATER + timedelta(hours=1)
        result = storage.evict(database, now=later, budget_refs=100)
        # The first failure stops the pass: no other generation would escape it.
        assert result["failed"] == 1 and result["generations"] == 0
        assert result["backlog"] == 1 + 3
        # Not the generation's fault, so it waits out no retry window.
        assert not (database.state_dir / storage.RETRY_FILE).exists()

        def broken(conn_, store, generation_id, **options):
            if generation_id == "dg_cold":
                raise output_packs.GenerationUnavailable("dg_cold does not match its label")
            return real(conn_, store, generation_id, **options)

        monkeypatch.setattr(output_packs, "pack", broken)
        # dg_cold's six rows spend the budget of six: nothing after it is reached.
        result = storage.evict(database, now=later, budget_refs=6)
        assert result["failed"] == 1 and result["generations"] == 0 and marked(conn) == []


def test_a_lost_pack_of_an_evicted_generation_comes_back_from_a_local_set(tmp_path):
    from swingset.backup.segments import LOCAL_TRANSPORT, create_set
    from swingset.backup.transport import LocalDirectoryTransport
    from swingset.state import guards

    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        before = everything(conn)
        later = LATER + timedelta(hours=1)
        storage.evict(database, now=later, budget_refs=100)
        store = objects.store_for(conn)
        digest = output_packs.label(conn, "dg_cold")[0]
        path = store.path(output_packs.CLASS, digest)
        whole = path.read_bytes()
        path.unlink()
        # No set holds it yet: it is lost, counted, and holds the guard.
        result = storage.evict(database, now=later + timedelta(days=1), budget_refs=100)
        assert result["lost"] == 1 and result["restored"] == 0 and result["backlog"] == 1
        assert result["lost_objects"][0]["digest"] == digest
        assert guards.get(conn, storage.GUARD_SUBJECT, storage.EVICT_FAILED) is not None
        # A set taken while the file was there puts it back on the next pass.
        path.write_bytes(whole)
        create_set(
            database,
            LocalDirectoryTransport(database.state_dir / "checkpoints", name=LOCAL_TRANSPORT),
            now=later,
            versions={"swingset": "test"},
        )
        path.unlink()
        result = storage.evict(database, now=later + timedelta(days=2), budget_refs=100)
        assert result["restored"] == 1 and result["lost"] == 0
        assert path.read_bytes() == whole
        assert guards.get(conn, storage.GUARD_SUBJECT, storage.EVICT_FAILED) is None
        assert everything(conn) == before


def test_a_pack_whose_rows_do_not_match_the_label_is_never_indexed(tmp_path, monkeypatch):
    with open_database(tmp_path / "state") as database:
        populate(database)
        conn = database.connection
        real = output_packs._view

        def changed(conn_, generation_id):
            for table, key, payload in real(conn_, generation_id):
                yield table, key, payload.replace("cold", "warm")

        monkeypatch.setattr(output_packs, "_view", changed)
        with pytest.raises(output_packs.GenerationUnavailable, match="does not match its label"):
            output_packs.pack(conn, objects.store_for(conn), "dg_cold", now=NOW)
        assert conn.execute("SELECT count(*) FROM objects").fetchone()[0] == 0
        # Eviction isolates it and leaves its references; the others go.
        result = storage.evict(database, now=LATER + timedelta(hours=1), budget_refs=100)
        assert result["failed"] == 1 and result["generations"] == 2
        assert [item["generation_id"] for item in result["failures"]] == ["dg_cold"]
        assert marked(conn) == ["dg_empty", "dg_old"]
        assert len([row for row in refs(conn) if row[0] == "dg_cold"]) == 6


def test_apply_removes_exactly_the_payloads_it_counts(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        before = len(payloads(conn))
        revision = int(conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0])
        written = storage.plan(conn, database.state_dir, now=NOW)
        receipt = storage.apply(database, plan_digest=written.digest, now=NOW)
        assert len(payloads(conn)) == before - receipt["payloads_removed"] == before - 10
        orphans = conn.execute(
            "SELECT count(*) FROM derivation_payloads p WHERE NOT EXISTS("
            "SELECT 1 FROM derivation_row_refs r WHERE r.payload_sha256=p.payload_sha256)"
        ).fetchone()[0]
        assert orphans == 0
        # The move bumps the history fence once itself (no trigger fires on a
        # dropped table); each payload delete bumps it through its trigger.
        start, end = receipt["history_fence_revision"]
        assert start == revision
        fence = int(conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0])
        assert end == fence == start + 1 + receipt["payloads_removed"]


@pytest.mark.parametrize("change", ["pointer", "reference"])
def test_a_change_between_verify_and_the_move_stops_it(tmp_path, monkeypatch, change):
    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        before = (refs(conn), payloads(conn))
        written = storage.plan(conn, database.state_dir, now=NOW)

        def stage(name: str) -> None:
            if name != "verified":
                return
            with database.transaction():
                if change == "pointer":
                    conn.execute(
                        "UPDATE derivation_scopes SET materialized_generation_id='dg_cold' "
                        "WHERE unit_id='event-b'"
                    )
                else:
                    text = canonical({"name_raw": "part 1"})
                    sha = hashlib.sha256(text.encode()).hexdigest()
                    conn.execute(
                        "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (sha, text)
                    )
                    conn.execute(
                        "INSERT INTO derivation_row_refs VALUES "
                        "('dg_part',1,'entries','[\"part-1\"]',?)",
                        (sha,),
                    )

        monkeypatch.setattr(storage, "_stage", stage)
        message = "hot set moved" if change == "pointer" else "kept references moved"
        with pytest.raises(StorageError, match=message):
            storage.apply(database, plan_digest=written.digest, now=NOW)
        assert marked(conn) == []
        assert len(refs(conn)) == len(before[0]) + (change == "reference")
        assert conn.execute("SELECT finished_at FROM storage_applies").fetchone()[0] is None


@pytest.mark.parametrize("fast", [True, False])
def test_a_drop_under_fast_delete_does_not_zero_pages_through_the_wal(tmp_path, fast):
    """W2: with `secure_delete` on (the worker's SQLite is built so), dropping
    a table writes a WAL as large as the table; `disk.fast_delete` does not."""
    from swingset.state import disk

    conn = sqlite3.connect(tmp_path / "w2.sqlite", isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("CREATE TABLE big(value TEXT)")
    conn.executemany("INSERT INTO big VALUES (?)", (("x" * 4000,) for _ in range(1000)))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    table_bytes = 1000 * 4000
    wal = tmp_path / "w2.sqlite-wal"
    if fast:
        with disk.fast_delete(conn) as secure:
            assert secure == 0
            conn.execute("DROP TABLE big")
        assert int(conn.execute("PRAGMA secure_delete").fetchone()[0]) == 1
        assert wal.stat().st_size < table_bytes // 10
    else:
        conn.execute("DROP TABLE big")
        assert wal.stat().st_size > table_bytes
    conn.close()


def test_apply_truncates_the_wal_and_refuses_without_room(tmp_path, monkeypatch):
    from swingset.state import disk

    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        written = storage.plan(conn, database.state_dir, now=NOW)
        storage.write_plan(database.state_dir, written)
        real = disk.room

        def short(directory, **options):
            found = real(directory, **options)
            return disk.Room(1_000, found.quota_free, found.quota_read_at, False)

        monkeypatch.setattr(disk, "room", short)
        with pytest.raises(StorageError, match="the move needs about"):
            storage.apply(database, plan_digest=written.digest, now=NOW)
        assert conn.execute("SELECT count(*) FROM storage_applies").fetchone()[0] == 0
        assert marked(conn) == []
        quota = tmp_path / "swingset-disk"
        quota.write_text(f"0 {1 << 50} 2026-01-01T00:00:00Z\n")
        monkeypatch.setattr(disk, "room", real)
        monkeypatch.setattr(disk, "QUOTA_FILE", quota)
        with pytest.raises(StorageError, match="sudo swingset-disk"):
            storage.apply(database, plan_digest=written.digest, now=NOW)
        monkeypatch.setattr(disk, "QUOTA_FILE", tmp_path / "absent")
        receipt = storage.apply(database, plan_digest=written.digest, now=NOW)
        assert receipt["room"]["reference_bytes"] == written.estimate["reference_bytes"]
        assert (database.state_dir / "state.sqlite-wal").stat().st_size == 0


def test_a_plan_written_before_midnight_applies_after_it(tmp_path):
    with open_database(tmp_path / "state") as database:
        populate(database)
        hold(database.state_dir)
        conn = database.connection
        late = datetime(2026, 10, 1, 23, 50, tzinfo=UTC)
        written = storage.plan(conn, database.state_dir, now=late)
        storage.write_plan(database.state_dir, written)
        receipt = storage.apply(database, plan_digest=written.digest, now=late + timedelta(hours=1))
        assert receipt["plan_digest"] == written.digest
