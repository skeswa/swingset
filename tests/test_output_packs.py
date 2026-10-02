"""Output packs, the one accessor and page-in (architecture phase 4, WP2)."""

from __future__ import annotations

import ast
import contextlib
import gzip
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_h15_acceptance import add_dancer, drain_project, link_all
from test_h15_acceptance import source_fixture as source_fixture
from test_release_closure_rows import baseline_events

from swingset.build import partitions
from swingset.state import derivations, objects, output_packs, retention
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import Database, open_database
from swingset.state.output_packs import GenerationUnavailable
from swingset.state.work import WorkUnit

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[1] / "src" / "swingset"


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


def write(
    database: Database,
    generation_id: str,
    scope: tuple[str, str, str],
    rows: list[tuple[str, str, dict[str, object]]],
    *,
    stored: int | None = None,
    point: bool = True,
) -> tuple[str, int]:
    """A labelled generation; ``stored`` keeps only the first references (a partial one)."""
    conn = database.connection
    run = database.start_run(NOW)
    output = hashlib.sha256()
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
                NOW.isoformat(),
                run,
            ),
        )
        if point:
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=? "
                "WHERE stage=? AND unit_kind=? AND unit_id=?",
                (generation_id, *scope),
            )
    return output.hexdigest(), len(rows)


def evict(database: Database, generation_id: str) -> None:
    """Pack a generation and move its references out, as `storage apply` will (WP3)."""
    conn = database.connection
    stamp = objects.stamp(NOW)
    with database.transaction():
        output_packs.pack(conn, objects.store_for(conn), generation_id, now=NOW)
        conn.execute(
            "INSERT INTO storage_eviction_authority(singleton,reason,granted_at) "
            "VALUES (1,'test',?)",
            (stamp,),
        )
        conn.execute("DELETE FROM derivation_row_refs WHERE generation_id=?", (generation_id,))
        conn.execute("INSERT INTO derivation_evicted VALUES (?,?)", (generation_id, stamp))
        conn.execute("DELETE FROM storage_eviction_authority")


def refs(conn: sqlite3.Connection, generation_id: str) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT ordinal,table_name,record_key,payload_sha256 FROM derivation_row_refs "
            "WHERE generation_id=? ORDER BY ordinal",
            (generation_id,),
        )
    ]


def marked(conn: sqlite3.Connection, generation_id: str) -> bool:
    return output_packs.evicted(conn, generation_id)


SAMPLE = [
    ("entries", '["a-1"]', {"name_raw": "Zoë Ångström", "note": "line\nbreak\u2028sep"}),
    ("entries", '["a-2"]', {"name_raw": "Plain", "event_id": "event-a"}),
    ("judges", '["a-3"]', {"name_raw": "Plain", "event_id": "event-a"}),
]


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------


def test_a_pack_is_the_label_stream_of_every_generation(source_fixture):
    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    write(f.db, "dg_empty", ("project", "event", "event-empty"), [])
    write(
        f.db,
        "dg_build",
        ("build", "release", "all"),
        [("artifact", "candidate-1", {"candidate_id": "candidate-1", "path": "/nowhere"})],
    )
    conn = f.conn
    store = objects.store_for(conn)
    labels = conn.execute(
        "SELECT generation_id,stage,unit_kind,output_digest,row_count FROM derivation_generations"
    ).fetchall()
    kinds = {(str(stage), str(kind)) for _, stage, kind, _, _ in labels}
    assert {
        ("project", "inventory"),
        ("project", "mapping"),
        ("project", "resolution"),
        ("project", "event"),
        ("project", "source_event"),
        ("project", "dancer"),
        ("project", "year"),
        ("link", "event"),
        ("build", "release"),
    } <= kinds
    assert any(int(row_count) == 0 for *_, row_count in labels)
    with f.db.transaction():
        for generation_id, _, _, output_digest, row_count in labels:
            assert output_packs.pack(conn, store, generation_id, now=NOW) == output_digest
            lines = list(store.lines(output_packs.CLASS, output_digest))
            assert len(lines) == row_count
            assert hashlib.sha256(b"".join(lines)).hexdigest() == output_digest
            raw = conn.execute(
                "SELECT raw_bytes FROM objects WHERE class=? AND digest=?",
                (output_packs.CLASS, output_digest),
            ).fetchone()[0]
            assert raw == sum(len(line) for line in lines)
            # Idempotent: a second pack keeps the indexed object.
            assert output_packs.pack(conn, store, generation_id, now=NOW) == output_digest
    distinct = {str(row[3]) for row in labels}
    assert conn.execute(
        "SELECT count(*) FROM objects WHERE class=?", (output_packs.CLASS,)
    ).fetchone()[0] == len(distinct)


def test_the_accessor_reads_an_evicted_generation_byte_identically(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        label = write(database, "dg_a", ("project", "event", "event-a"), SAMPLE, point=False)
        before = list(output_packs.rows(conn, "dg_a"))
        one = output_packs.row(conn, "dg_a", "entries", '["a-1"]')
        table = list(output_packs.table_rows(conn, "dg_a", "entries"))
        assert output_packs.resident(conn, "dg_a") and output_packs.complete(conn, "dg_a")
        evict(database, "dg_a")
        assert refs(conn, "dg_a") == []
        assert not output_packs.resident(conn, "dg_a")
        assert output_packs.evicted(conn, "dg_a") and output_packs.complete(conn, "dg_a")
        assert list(output_packs.rows(conn, "dg_a")) == before
        assert output_packs.row(conn, "dg_a", "entries", '["a-1"]') == one
        assert output_packs.row(conn, "dg_a", "entries", '["missing"]') is None
        assert list(output_packs.table_rows(conn, "dg_a", "entries")) == table
        output = hashlib.sha256()
        for item in before:
            output.update(canonical(item).encode() + b"\n")
        assert (output.hexdigest(), len(before)) == label
        # The derivation readers see the same rows.
        assert list(derivations._without(conn, "dg_a", {})) == before


def test_a_truncated_wrong_or_missing_pack_raises(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        write(database, "dg_a", ("project", "event", "event-a"), SAMPLE, point=False)
        write(
            database,
            "dg_b",
            ("project", "event", "event-b"),
            [("entries", '["b-1"]', {"name_raw": "Other"})],
            point=False,
        )
        evict(database, "dg_a")
        store = objects.store_for(conn)
        digest, _ = output_packs.label(conn, "dg_a")
        path = store.path(output_packs.CLASS, digest)
        good = path.read_bytes()

        def read() -> None:
            list(output_packs.rows(conn, "dg_a"))

        # Truncated: the first two lines only.
        lines = gzip.decompress(good).splitlines(keepends=True)
        path.write_bytes(gzip.compress(b"".join(lines[:2]), mtime=0))
        with pytest.raises(GenerationUnavailable):
            read()
        with pytest.raises(GenerationUnavailable):
            output_packs.row(conn, "dg_a", "entries", '["a-1"]')
        # Wrong: another generation's pack under this name.
        with database.transaction():
            output_packs.pack(conn, store, "dg_b", now=NOW)
        other = store.path(output_packs.CLASS, output_packs.label(conn, "dg_b")[0])
        path.write_bytes(other.read_bytes())
        with pytest.raises(GenerationUnavailable):
            read()
        # Not gzip at all.
        path.write_bytes(b"not a pack")
        with pytest.raises(GenerationUnavailable):
            read()
        # Missing.
        path.unlink()
        with pytest.raises(GenerationUnavailable):
            read()
        # A page-in from a bad pack leaves nothing behind.
        path.write_bytes(gzip.compress(b"".join(lines[:2]), mtime=0))
        with pytest.raises(GenerationUnavailable):
            with database.transaction():
                output_packs.page_in(conn, "dg_a")
        assert refs(conn, "dg_a") == [] and marked(conn, "dg_a")
        path.write_bytes(good)
        assert len(list(output_packs.rows(conn, "dg_a"))) == len(SAMPLE)


def test_a_partial_generation_is_never_packed(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        write(database, "dg_p", ("project", "event", "event-p"), SAMPLE, stored=1, point=False)
        with pytest.raises(GenerationUnavailable):
            with database.transaction():
                output_packs.pack(conn, objects.store_for(conn), "dg_p", now=NOW)
        assert conn.execute("SELECT count(*) FROM objects").fetchone()[0] == 0
        with pytest.raises(GenerationUnavailable):
            list(output_packs.rows(conn, "dg_p"))


def test_page_in_is_bounded_resumable_and_drops_the_marker_when_whole(tmp_path):
    rows = [("entries", f'["e{index}"]', {"entry_id": f"e{index}"}) for index in range(5)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        write(database, "dg_a", ("project", "event", "event-a"), rows, point=False)
        whole = refs(conn, "dg_a")
        evict(database, "dg_a")
        progress = []
        while True:
            with database.transaction():
                done = output_packs.page_in(conn, "dg_a", limit=2, now=NOW)
            progress.append(len(refs(conn, "dg_a")))
            if done:
                break
        assert progress == [2, 4, 5]
        assert refs(conn, "dg_a") == whole
        assert not marked(conn, "dg_a") and output_packs.resident(conn, "dg_a")
        # A whole generation pages in as a no-op.
        with database.transaction():
            assert output_packs.page_in(conn, "dg_a")
        # The marker leaves only when the view reads back as the label.
        evict(database, "dg_a")
        real = output_packs._view

        def changed(conn_, generation_id):
            for table, key, payload in real(conn_, generation_id):
                yield table, key, payload.replace("e4", "e5")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(output_packs, "_view", changed)
            with pytest.raises(GenerationUnavailable, match="paged-in generation"):
                with database.transaction():
                    output_packs.page_in(conn, "dg_a", now=NOW)
        assert refs(conn, "dg_a") == [] and marked(conn, "dg_a")
        # The marker cannot leave without the authority.
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            with database.transaction():
                conn.execute("DELETE FROM derivation_evicted")


# ---------------------------------------------------------------------------
# Completeness (F33)
# ---------------------------------------------------------------------------


def test_incomplete_generations_ignore_evicted_ones_and_count_real_partials(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        write(database, "dg_whole", ("project", "event", "event-a"), SAMPLE, point=False)
        write(database, "dg_evicted", ("project", "event", "event-b"), SAMPLE, point=False)
        write(
            database, "dg_partial", ("project", "event", "event-c"), SAMPLE, stored=1, point=False
        )
        evict(database, "dg_evicted")
        assert derivations.incomplete_generations(conn) == {"rows": 1, "refs": 1}
        chosen = {
            str(row[0])
            for row in conn.execute(
                "SELECT generation_id FROM derivation_generations WHERE 1"
                + derivations.unfinished_excluded(conn)
            )
        }
        assert chosen == {"dg_whole", "dg_evicted"}
        assert derivations._partial_label(conn, "dg_evicted") is None
        assert derivations._partial_label(conn, "dg_partial") is not None
        label = output_packs.label(conn, "dg_evicted")
        derivations._check_stored(conn, "dg_evicted", label)
        with pytest.raises(SupersededWorkError):
            derivations._check_stored(conn, "dg_partial", output_packs.label(conn, "dg_partial"))
        # retain_output writes nothing over an evicted generation.
        with database.transaction():
            derivations.retain_output(conn, "dg_evicted", ())
        assert refs(conn, "dg_evicted") == []
        content = retention.plan(
            conn,
            database.state_dir,
            max_database_bytes=1 << 40,
            recent_window=0,
            collect_older_than=86400.0,
        ).content
        lists = {row["generation_id"]: row["list"] for row in content["generations"]}
        assert lists == {
            "dg_whole": "archivable",
            "dg_evicted": "archivable",
            "dg_partial": "incomplete",
        }
        residence = content["residence"]
        assert residence["evicted_generations"] == 1
        assert residence["partial_generations"] == 1
        assert residence["resident_generations"] == 1
        assert residence["packs"] == 1 and residence["pack_bytes"] > 0


# ---------------------------------------------------------------------------
# Reuse pages in (F34)
# ---------------------------------------------------------------------------

UNIT = WorkUnit("project", "event", "reuse")


def complete(database: Database, rows, recipe) -> str:
    conn = database.connection
    with database.transaction():
        selection = derivations.capture(conn, UNIT, now=NOW, recipe=recipe)
        return derivations.complete(
            conn, selection, rows=list(rows), now=NOW, run_id=database.start_run(NOW)
        )


def output(count: int) -> list[derivations.OutputRow]:
    return [
        derivations.OutputRow("entries", f'["e{index}"]', {"entry_id": f"e{index}"})
        for index in range(count)
    ]


def test_reuse_of_an_evicted_small_generation_pages_it_in_on_the_complete_path(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = output(4)
        generation = complete(database, rows, {"recipe/test": "one"})
        whole = refs(conn, generation)
        # A current pointer never is evicted; this is the reuse's worst case.
        evict(database, generation)
        assert refs(conn, generation) == []
        assert complete(database, rows, {"recipe/test": "one"}) == generation
        assert refs(conn, generation) == whole
        assert not marked(conn, generation)
        assert derivations.selected_generation(conn, UNIT) == generation
        assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == 1
        # A cutoff against the resident kept generation is unchanged: it keeps
        # the pointer and records an equivalence, with no page-in.
        calls: list[str] = []
        real = output_packs.page_in

        def spy(conn, generation_id, **kwargs):
            calls.append(generation_id)
            return real(conn, generation_id, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(output_packs, "page_in", spy)
            assert complete(database, rows, {"recipe/test": "two"}) == generation
        assert calls == []
        assert conn.execute("SELECT count(*) FROM derivation_equivalences").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == 1


@contextlib.contextmanager
def per_transaction(monkeypatch, conn) -> Iterator[list[int]]:
    """References inserted by each top-level write transaction."""
    counts: list[int] = []
    original = Database.transaction

    @contextlib.contextmanager
    def measured(self, **kwargs):
        top = not self.connection.in_transaction
        before = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        with original(self, **kwargs) as inner:
            yield inner
        if top:
            after = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
            counts.append(after - before)

    monkeypatch.setattr(Database, "transaction", measured)
    yield counts


def staged(database: Database, rows, recipe):
    conn = database.connection
    run_id = database.start_run(NOW)
    with derivations.chunking(conn, UNIT) as chunk:
        with database.transaction(), derivations.group(conn):
            selection = derivations.capture(conn, UNIT, now=NOW, recipe=recipe)
            assert derivations.stage(
                conn, selection, output=lambda reader: iter(rows), now=NOW, run_id=run_id
            )
        return derivations.finish_building(database, chunk.deferred)


def test_reuse_of_an_evicted_large_generation_pages_in_chunk_by_chunk_on_the_staged_path(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = output(8)
        generation = complete(database, rows, {"recipe/test": "one"})
        whole = refs(conn, generation)
        evict(database, generation)
        # Step C alone never pages in more than a chunk: it sends the run back.
        with monkeypatch.context() as patch:
            patch.setattr(derivations, "_page_in_building", lambda *a, **k: None)
            with pytest.raises(SupersededWorkError, match="evicted generation paging in"):
                staged(database, rows, {"recipe/test": "one"})
        assert refs(conn, generation) == [] and marked(conn, generation)
        with database.transaction():
            conn.execute("DELETE FROM derivation_building")
        with per_transaction(monkeypatch, conn) as counts:
            assert staged(database, rows, {"recipe/test": "one"}) == generation
        assert max(counts) <= 3 and sum(counts) == 8
        assert refs(conn, generation) == whole
        assert not marked(conn, generation)
        assert derivations.selected_generation(conn, UNIT) == generation
        assert conn.execute("SELECT count(*) FROM derivation_building").fetchone()[0] == 0


def test_a_return_to_a_large_evicted_generation_is_staged(tmp_path, monkeypatch):
    """The current pointer is small, but a generation of the unit it may
    return to (a run that stored it and never flipped) is evicted and over
    `CHUNK_ROWS`: the completion is staged, so a page-in goes a chunk per
    write (step B) rather than whole inside one transaction."""
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        complete(database, output(2), {"recipe/test": "one"})
        rows = [("entries", f'["e{index}"]', {"entry_id": f"e{index}"}) for index in range(8)]
        write(
            database, "dg_unflipped", (UNIT.stage, UNIT.unit_kind, UNIT.unit_id), rows, point=False
        )
        with derivations.chunking(conn, UNIT):
            assert not derivations.stageable(conn, UNIT)
        evict(database, "dg_unflipped")
        with derivations.chunking(conn, UNIT):
            assert derivations.stageable(conn, UNIT)


def lose_pack(conn: sqlite3.Connection, generation: str) -> None:
    objects.store_for(conn).path(
        output_packs.CLASS, output_packs.label(conn, generation)[0]
    ).unlink()


def test_a_reuse_rebuilds_a_lost_pack_on_the_complete_path(tmp_path):
    """The only repair of a lost pack of an evicted generation: a unit that
    reuses it computes the same output, which puts the references back and
    writes the pack again for every label that shares it."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = output(4)
        generation = complete(database, rows, {"recipe/test": "one"})
        whole = refs(conn, generation)
        evict(database, generation)
        lose_pack(conn, generation)
        assert not output_packs.pack_intact(conn, generation)
        assert complete(database, rows, {"recipe/test": "one"}) == generation
        assert refs(conn, generation) == whole
        assert not marked(conn, generation) and output_packs.pack_intact(conn, generation)
        assert derivations.selected_generation(conn, UNIT) == generation


def test_a_reuse_rebuilds_a_lost_pack_chunk_by_chunk_on_the_staged_path(tmp_path, monkeypatch):
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 3)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = output(8)
        generation = complete(database, rows, {"recipe/test": "one"})
        whole = refs(conn, generation)
        evict(database, generation)
        lose_pack(conn, generation)
        # Step A's building row names the generation through B, so the
        # evictor counts it hot and never sheds what B just put back.
        from swingset.state import storage

        hot: list[bool] = []
        real = output_packs.rebuild

        def spy(conn_, generation_id, rows_, **options):
            hot.append(storage._is_hot(conn_, generation_id))
            return real(conn_, generation_id, rows_, **options)

        monkeypatch.setattr(output_packs, "rebuild", spy)
        with per_transaction(monkeypatch, conn) as counts:
            assert staged(database, rows, {"recipe/test": "one"}) == generation
        assert hot and all(hot)
        assert max(counts) <= 3 and sum(counts) == 8
        assert refs(conn, generation) == whole
        assert not marked(conn, generation) and output_packs.pack_intact(conn, generation)
        assert derivations.selected_generation(conn, UNIT) == generation


def test_a_rebuild_from_other_rows_is_refused_and_changes_nothing(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        generation = complete(database, output(4), {"recipe/test": "one"})
        evict(database, generation)
        lose_pack(conn, generation)
        with pytest.raises(GenerationUnavailable, match="does not match its label"):
            with database.transaction():
                output_packs.rebuild(conn, generation, derivations._offered(output(5)), now=NOW)
        assert refs(conn, generation) == [] and marked(conn, generation)
        assert not output_packs.pack_intact(conn, generation)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def remint(f, unit: WorkUnit) -> str:
    """A new generation of ``unit`` with renamed entrants and the old inputs; links re-run.

    Written as a receipt, as `tests/build/test_partitions.remint` does: only
    the output changes, so the unit stays current.
    """
    conn = f.conn
    current = derivations.selected_generation(conn, unit)
    assert current is not None
    fingerprint, recipe, dependency_set = conn.execute(
        "SELECT input_fingerprint,recipe_json,dependency_set_id FROM derivation_generations "
        "WHERE generation_id=?",
        (current,),
    ).fetchone()
    f.corpus.clock.sleep(1)
    created = "dg_remint_" + hashlib.sha256(current.encode()).hexdigest()[:16]
    output = hashlib.sha256()
    count = 0
    with f.db.transaction():
        for ordinal, (table, key, raw) in enumerate(list(output_packs.rows(conn, current))):
            payload = json.loads(raw)
            if table == "entries":
                payload = {**payload, "name_raw": "Renamed Entrant"}
            text = canonical(payload)
            sha = hashlib.sha256(text.encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (sha, text))
            conn.execute(
                "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
                (created, ordinal, table, key, sha),
            )
            output.update(canonical((table, key, text)).encode() + b"\n")
            count += 1
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                created,
                unit.stage,
                unit.unit_kind,
                unit.unit_id,
                fingerprint,
                recipe,
                dependency_set,
                current,
                output.hexdigest(),
                count,
                f.corpus.clock.now().isoformat(),
                f.corpus.run,
            ),
        )
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=? "
            "WHERE stage=? AND unit_kind=? AND unit_id=?",
            (created, unit.stage, unit.unit_kind, unit.unit_id),
        )
    link_all(f)
    return created


def test_the_partitioned_build_reads_only_resident_generations(source_fixture, tmp_path):
    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    baseline = baseline_events(f, tmp_path / "baseline")

    def build() -> dict[str, object]:
        return partitions.build(
            f.db,
            history_start=f.bundle.config.history_start,
            baseline=baseline,
            run_id=f.corpus.run,
            now=f.corpus.clock.now(),
        )

    conn = f.conn
    # A superseded event generation: the event's output changes, the link re-runs.
    remint(f, WorkUnit("project", "event", "event-a"))
    first = build()
    assert first.get("withheld", 0) == 0
    hot = {
        str(row[0])
        for row in conn.execute(
            "SELECT materialized_generation_id FROM derivation_scopes "
            "WHERE materialized_generation_id IS NOT NULL "
            "UNION SELECT generation_id FROM derivation_building"
        )
    }
    cold = [
        str(row[0])
        for row in conn.execute("SELECT generation_id FROM derivation_generations")
        if str(row[0]) not in hot
    ]
    assert cold, "the fixture must leave superseded generations"
    for generation_id in cold:
        evict(f.db, generation_id)
    read: list[tuple[str, bool]] = []
    real = output_packs.rows

    def spy(conn, generation_id, **kwargs):
        read.append((generation_id, output_packs.resident(conn, generation_id)))
        return real(conn, generation_id, **kwargs)

    def no_pack(*args, **kwargs):
        raise AssertionError("the build read a pack")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(output_packs, "rows", spy)
        patch.setattr(output_packs, "_pack", no_pack)
        assert partitions._evicted_pointers(conn) == frozenset()
        # Selection without remembered event lists reads every event generation.
        partitions.select(conn)
        again = build()
    assert read and all(resident for _, resident in read)
    assert {generation_id for generation_id, _ in read} <= hot
    assert again["rebuilt"] == 0 and again["reused"] == first["partitions"]
    # A current pointer found evicted (an invariant broken) is read through
    # the accessor, never taken as empty: nothing changes.
    year = derivations.selected_generation(conn, WorkUnit("project", "year", "2026"))
    assert year is not None
    evict(f.db, year)
    assert partitions._evicted_pointers(conn) == frozenset({year})
    third = build()
    assert third["rebuilt"] == 0 and third.get("withheld", 0) == 0


def test_a_hold_on_an_evicted_generation_is_accepted(tmp_path):
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        write(database, "dg_a", ("project", "event", "event-a"), SAMPLE, point=False)
        evict(database, "dg_a")
        [residency] = retention.residency(conn, ["dg_a"])
        assert residency.evicted and residency.packed and residency.local
        held = retention.add_hold(
            database.state_dir, conn, who="test", why="evicted", now=NOW, generations=["dg_a"]
        )
        assert held["generation_ids"] == ["dg_a"]
        # Without its pack the generation is not local, and a hold is refused.
        store = objects.store_for(conn)
        store.path(output_packs.CLASS, output_packs.label(conn, "dg_a")[0]).unlink()
        with pytest.raises(retention.RetentionError, match="not local"):
            retention.add_hold(
                database.state_dir, conn, who="test", why="gone", now=NOW, generations=["dg_a"]
            )


def test_the_build_artifact_readers_read_an_evicted_build_generation(tmp_path):
    from swingset.build import generations as build_generations

    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        payload = {"candidate_id": "candidate-1", "path": "/nowhere", "manifest_hash": "m"}
        write(
            database,
            "dg_build",
            ("build", "release", "all"),
            [("artifact", "candidate-1", payload)],
            point=False,
        )
        assert build_generations.completed(conn, "candidate-1", "m")
        evict(database, "dg_build")
        assert build_generations.completed(conn, "candidate-1", "m")
        assert not build_generations.completed(conn, "candidate-1", "other")


# ---------------------------------------------------------------------------
# Who may name the row tables
# ---------------------------------------------------------------------------

#: Modules that may name `derivation_rows` or `derivation_row_refs` in SQL.
#: Everything else reads output through `output_packs` (F34).
ALLOWED = {
    "state/derivations.py",
    "state/output_packs.py",
    "state/derivation_payload_migration.py",
    "state/retention.py",
    "state/retention_apply.py",
    # The one-time move and incremental eviction (WP3).
    "state/storage.py",
    # Partial generations' references, collected (WP5, D-0231).
    "state/collector.py",
    "state/publication_fence.py",
    # The two current-pointer joins, `_refs` and `_dancer_rows`.
    "build/partitions.py",
}
TABLES = re.compile(r"\bderivation_row(s|_refs)\b")


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _naming(tree: ast.AST) -> list[ast.Constant]:
    skip = _docstrings(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and TABLES.search(node.value)
    ]


def test_only_allowlisted_modules_name_the_row_tables_in_sql():
    named = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        if _naming(tree):
            named[path.relative_to(SRC).as_posix()] = True
    assert set(named) - ALLOWED == set()
    # In the partitioned build, only the two current-pointer joins do.
    tree = ast.parse((SRC / "build" / "partitions.py").read_text())
    functions = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        and any(node in _naming(function) for node in ast.walk(function))
    }
    assert functions == {"_refs", "_dancer_rows"}
