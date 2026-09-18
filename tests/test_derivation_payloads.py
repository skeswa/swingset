"""Derivation output rows are stored once: migration 32, retain_output, delete gate."""

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset import cli
from swingset.state import db as db_module
from swingset.state import derivation_payload_migration as interning
from swingset.state import derivations
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 18, tzinfo=UTC)
# The shape every reader of retained output asks for; the real readers
# (closure_rows.reconstruct, derivations.current and the build receipt checks)
# run against a migrated database further down this file.
READER = (
    "SELECT ordinal,table_name,record_key,payload_json FROM derivation_rows "
    "WHERE generation_id=? ORDER BY ordinal"
)
SHARED = {"name_raw": "Shared Row", "event_id": "event-a"}


def label(rows: list[tuple[str, str, dict[str, object]]]) -> tuple[str, int]:
    output = hashlib.sha256()
    count = 0
    for table, key, payload in rows:
        count += 1
        stored = derivations.canonical(payload)
        output.update(derivations.canonical((table, key, stored)).encode() + b"\n")
    return output.hexdigest(), count


def candidate(directory: Path) -> tuple[str, str]:
    """A minimal candidate whose files satisfy the artifact validity check."""
    (directory / "data").mkdir(parents=True)
    (directory / "_meta").mkdir(parents=True)
    body = b"candidate rows"
    (directory / "data/rows.txt").write_bytes(body)
    manifest = directory / "_meta/manifest.json"
    manifest.write_text(json.dumps({"files": {"data/rows.txt": hashlib.sha256(body).hexdigest()}}))
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (directory / "BUILT").write_text(json.dumps({"manifest_hash": manifest_hash}))
    return str(directory.resolve()), manifest_hash


def fixture_generations(path: str, manifest_hash: str) -> list[dict[str, object]]:
    """Several generations across stages, sharing rows within and between them."""
    private = {"name_raw": "Private Row", "event_id": "event-a"}
    return [
        {
            "generation_id": "dg_event_a1",
            "scope": ("project", "event", "event-a"),
            "rows": [
                ("entries", '["a-1"]', dict(SHARED)),
                ("entries", '["a-2"]', dict(private)),
                ("judges", '["a-3"]', dict(SHARED)),
            ],
        },
        {
            "generation_id": "dg_event_a2",
            "scope": ("project", "event", "event-a"),
            "rows": [
                ("entries", '["a-1"]', dict(SHARED)),
                ("entries", '["a-2"]', {"name_raw": "Changed Row", "event_id": "event-a"}),
                ("judges", '["a-3"]', dict(SHARED)),
            ],
        },
        {
            "generation_id": "dg_event_b1",
            "scope": ("project", "event", "event-b"),
            "rows": [("entries", '["b-1"]', dict(SHARED))],
        },
        {
            "generation_id": "dg_link_a1",
            "scope": ("link", "event", "event-a"),
            "rows": [
                ("identity_links", '["a-1"]', {"subject_id": "a-1", "wsdc_id": 7}),
                ("entries", '["a-1"]', dict(SHARED)),
            ],
        },
        {
            "generation_id": "dg_empty",
            "scope": ("project", "event", "event-c"),
            "rows": [],
        },
        {
            "generation_id": "dg_build_1",
            "scope": ("build", "release", "all"),
            "rows": [
                (
                    "artifact",
                    "candidate-1",
                    {
                        "candidate_id": "candidate-1",
                        "path": path,
                        "manifest_hash": manifest_hash,
                        "content_hash": "content",
                    },
                )
            ],
        },
    ]


def populate(database: db_module.Database, generations: list[dict[str, object]]) -> None:
    conn = database.connection
    run = database.start_run(NOW)
    with database.transaction():
        conn.execute("INSERT INTO derivation_dependency_sets VALUES ('fixture-set','[]')")
        for generation in generations:
            stage, kind, unit = generation["scope"]
            conn.execute(
                "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
                "VALUES (?,?,?,?)",
                (stage, kind, unit, NOW.isoformat()),
            )
            rows = generation["rows"]
            output_digest, row_count = label(rows)
            conn.execute(
                "INSERT INTO derivation_generations VALUES (?,?,?,?,?,'{}','fixture-set',NULL,?,?,?,?)",
                (
                    generation["generation_id"],
                    stage,
                    kind,
                    unit,
                    "fingerprint-" + str(generation["generation_id"]),
                    output_digest,
                    row_count,
                    NOW.isoformat(),
                    run,
                ),
            )
            for ordinal, (table, key, payload) in enumerate(rows):
                conn.execute(
                    "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                    (
                        generation["generation_id"],
                        ordinal,
                        table,
                        key,
                        derivations.canonical(payload),
                    ),
                )
            conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=? "
                "WHERE stage=? AND unit_kind=? AND unit_id=?",
                (generation["generation_id"], stage, kind, unit),
            )


def held_schema29(
    path: Path, monkeypatch: pytest.MonkeyPatch, generations: list[dict[str, object]]
) -> dict[str, list[tuple[object, ...]]]:
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 29)
        with open_database(path, lock=False) as database:
            populate(database, generations)
            assert database.schema_version == 29
            return {
                str(generation["generation_id"]): [
                    tuple(row)
                    for row in database.connection.execute(READER, (generation["generation_id"],))
                ]
                for generation in generations
            }


def labels(conn: sqlite3.Connection) -> dict[str, tuple[str, int]]:
    return {
        str(row[0]): (str(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT generation_id,output_digest,row_count FROM derivation_generations"
        )
    }


def test_migration_stores_shared_rows_once_and_keeps_every_label(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    before = held_schema29(tmp_path / "state", monkeypatch, generations)
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 29)
        with open_database(tmp_path / "state", lock=False) as database:
            before_labels = labels(database.connection)

    # D-0013: this test owns migration 32, so it opens at exactly schema 32.
    # A later migration is a later test's subject, not a change to this one.
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 32)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        assert database.schema_version == 32
        assert labels(conn) == before_labels
        # The old table is gone and derivation_rows is now a view over the two
        # new tables.
        assert (
            conn.execute("SELECT type FROM sqlite_master WHERE name='derivation_rows'").fetchone()[
                0
            ]
            == "view"
        )
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='derivation_rows_legacy'"
            ).fetchone()
            is None
        )
        # Every reader of the view sees byte-identical rows.
        after = {
            identifier: [tuple(row) for row in conn.execute(READER, (identifier,))]
            for identifier in before
        }
        assert after == before
        total = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        distinct = {
            derivations.canonical(payload)
            for generation in generations
            for _, _, payload in generation["rows"]
        }
        assert total == sum(len(generation["rows"]) for generation in generations)
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == len(
            distinct
        )
        assert total > len(distinct)
        # The shared row is stored once and referenced from six places in four
        # generations, across two stages.
        shared = hashlib.sha256(derivations.canonical(SHARED).encode()).hexdigest()
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_row_refs WHERE payload_sha256=?", (shared,)
            ).fetchone()[0]
            == 6
        )
        # Named readers still work through the view.
        from swingset.build import closure_manifest
        from swingset.build import generations as build_generations

        assert build_generations.completed(conn, "candidate-1", manifest_hash)
        assert (
            closure_manifest.generation(conn, "dg_build_1")["output_digest"]
            == (before_labels["dg_build_1"][0])
        )
        assert derivations._artifact_valid(conn, "dg_build_1")
        assert not derivations._artifact_valid(conn, "dg_event_a1")
        assert not derivations.current(conn, WorkUnit("project", "event", "event-a"))
        # retain_output accepts every migrated generation as already retained.
        for identifier in before:
            with database.transaction():
                derivations.retain_output(conn, identifier, ())
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_after_the_fill_keeps_the_old_rows_and_schema(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    before = held_schema29(tmp_path / "state", monkeypatch, generations)
    filled = []

    def interrupted(conn):
        interning.fill(conn)
        filled.append(conn.execute("SELECT count(*) FROM derivation_rows_legacy").fetchone()[0])
        raise RuntimeError("interrupted interning")

    with monkeypatch.context() as patch:
        patch.setattr(interning, "intern_derivation_payloads", interrupted)
        with pytest.raises(RuntimeError, match="interrupted interning"):
            open_database(tmp_path / "state", lock=False)
    assert filled == [sum(len(generation["rows"]) for generation in generations)]

    with sqlite3.connect(tmp_path / "state/state.sqlite") as conn:
        # Interning is the last migration, so the two before it committed and
        # only interning rolled back. That is the point of the order: schema 31
        # is a database an operator can run.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 31
        assert (
            conn.execute("SELECT type FROM sqlite_master WHERE name='derivation_rows'").fetchone()[
                0
            ]
            == "table"
        )
        for name in ("derivation_payloads", "derivation_row_refs", "derivation_rows_legacy"):
            assert (
                conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is None
            )
        assert {
            identifier: [tuple(row) for row in conn.execute(READER, (identifier,))]
            for identifier in before
        } == before
        # Every fingerprint still checks against the untouched rows.
        for generation in generations:
            stored = conn.execute(
                "SELECT output_digest,row_count FROM derivation_generations WHERE generation_id=?",
                (generation["generation_id"],),
            ).fetchone()
            assert tuple(stored) == label(generation["rows"])

    # The interrupted migration left a database that still migrates.
    # D-0013: this test owns migration 32, so it opens at exactly schema 32.
    # A later migration is a later test's subject, not a change to this one.
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 32)
    with open_database(tmp_path / "state", lock=False) as database:
        assert database.schema_version == 32


def completed_generation(database, unit, rows, recipe):
    conn = database.connection
    with database.transaction():
        selection = derivations.capture(conn, unit, now=NOW, recipe=recipe)
        return derivations.complete(
            conn, selection, rows=iter(rows), now=NOW, run_id=database.start_run(NOW)
        )


def test_completion_of_ten_changed_rows_of_a_thousand_stores_ten_more_payloads(tmp_path):
    unit = WorkUnit("project", "event", "scale")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        first = [
            derivations.OutputRow("entries", f'["e{index}"]', {"entry_id": f"e{index}", "v": index})
            for index in range(1000)
        ]
        completed_generation(database, unit, first, {"recipe/test": "one"})
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == 1000
        changed = [
            derivations.OutputRow(
                "entries",
                f'["e{index}"]',
                {"entry_id": f"e{index}", "v": index + (10_000 if index < 10 else 0)},
            )
            for index in range(1000)
        ]
        completed_generation(database, unit, changed, {"recipe/test": "two"})
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == 1010
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == 2000


def test_retain_output_checks_the_label_and_repeats_without_writing(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    held_schema29(tmp_path / "state", monkeypatch, generations)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = [
            derivations.OutputRow(table, key, payload)
            for table, key, payload in fixture_generations(path, manifest_hash)[0]["rows"]
        ]
        # Retention writes permanent references, so it must share a transaction
        # it can roll back; an autocommit call would leave half a generation.
        with pytest.raises(ValueError, match="share the caller's transaction"):
            derivations.retain_output(conn, "dg_event_a1", rows)
        with pytest.raises(SupersededWorkError, match="recorded generation label"):
            with database.transaction():
                derivations.retain_output(conn, "dg_missing", rows)
        # A generation that already reads back whole is accepted, not rewritten.
        before = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        with database.transaction():
            derivations.retain_output(conn, "dg_event_a1", [])
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == before

        # A label with the right row count but a different digest is rejected, and
        # so is one with the right digest but a different row count.
        output_digest, row_count = label(generations[0]["rows"])
        for identifier, stored_digest, stored_count in (
            ("dg_digest", "0" * 64, row_count),
            ("dg_count", output_digest, row_count + 1),
        ):
            with database.transaction():
                conn.execute(
                    "INSERT INTO derivation_generations VALUES "
                    "(?,'project','event','event-a',?,'{}','fixture-set',NULL,?,?,?,?)",
                    (
                        identifier,
                        "fingerprint-" + identifier,
                        stored_digest,
                        stored_count,
                        NOW.isoformat(),
                        conn.execute("SELECT run_id FROM runs").fetchone()[0],
                    ),
                )
            with pytest.raises(SupersededWorkError, match="does not match its label"):
                with database.transaction():
                    derivations.retain_output(conn, identifier, rows)
            assert (
                conn.execute(
                    "SELECT count(*) FROM derivation_row_refs WHERE generation_id=?", (identifier,)
                ).fetchone()[0]
                == 0
            )


def test_payload_removal_needs_a_permission_row_in_the_same_transaction(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    held_schema29(tmp_path / "state", monkeypatch, generations)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        orphan = hashlib.sha256(b'{"unreferenced":true}').hexdigest()
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payloads VALUES (?,?)", (orphan, '{"unreferenced":true}')
            )
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads WHERE payload_sha256=?", (orphan,))
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_payloads WHERE payload_sha256=?", (orphan,)
            ).fetchone()[0]
            == 1
        )
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payload_removal_authority VALUES (1,'test plan',?)",
                (NOW.isoformat(),),
            )
            conn.execute("DELETE FROM derivation_payloads WHERE payload_sha256=?", (orphan,))
            conn.execute("DELETE FROM derivation_payload_removal_authority")
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_payloads WHERE payload_sha256=?", (orphan,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
        # Without the permission row the gate is closed again.
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")


def test_a_removal_grant_cannot_outlive_its_transaction(tmp_path):
    """A grant that could commit would open the gate for every later process."""
    unit = WorkUnit("project", "event", "gate")
    rows = [derivations.OutputRow("entries", f'["g{index}"]', {"i": index}) for index in range(5)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        completed_generation(database, unit, rows, {"recipe/test": "one"})
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with database.transaction():
                conn.execute(
                    "INSERT INTO derivation_payload_removal_authority VALUES (1,'left behind',?)",
                    (NOW.isoformat(),),
                )
        # The failed commit is rolled back, not left open for the next caller.
        assert not conn.in_transaction
        assert (
            conn.execute("SELECT count(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == 5


def test_a_grant_written_with_foreign_keys_off_is_reported_and_cleared(tmp_path, capsys):
    """The one way to leave a grant behind is caught, not carried into a restore."""
    unit = WorkUnit("project", "event", "leak")
    rows = [derivations.OutputRow("entries", f'["l{index}"]', {"i": index}) for index in range(5)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        completed_generation(database, unit, rows, {"recipe/test": "one"})
        conn.execute("PRAGMA foreign_keys=OFF")
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payload_removal_authority VALUES (1,'leak',?)",
                (NOW.isoformat(),),
            )
        conn.execute("PRAGMA foreign_keys=ON")
        # Checkpoint verification and recovery both fail on this.
        assert conn.execute("PRAGMA foreign_key_check").fetchall()
        # Doctor reads the database read-only, so it reports the open gate.
        report = cli.doctor(argparse.Namespace(state=tmp_path / "state", config=Path("config")))
        assert [grant["reason"] for grant in report["derivation_payload_removal_grants"]] == [
            "leak"
        ]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        assert (
            conn.execute("SELECT count(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
    assert "derivation-payload-removal-grant-revoked" in capsys.readouterr().err


def test_completion_stores_rows_as_they_arrive_without_buffering_them(tmp_path):
    """A scope's whole output must never be held in memory to be fingerprinted."""
    unit = WorkUnit("project", "event", "stream")
    produced: list[int] = []
    stored_after: list[int] = []
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection

        def streamed():
            for index in range(200):
                produced.append(index)
                yield derivations.OutputRow("entries", f'["s{index}"]', {"i": index})

        run = database.start_run(NOW)
        with database.transaction():
            selection = derivations.capture(conn, unit, now=NOW, recipe={"recipe/test": "one"})
            conn.set_trace_callback(
                lambda sql: (
                    stored_after.append(len(produced))
                    if sql.startswith("INSERT INTO derivation_row_refs")
                    else None
                )
            )
            try:
                generation = derivations.complete(
                    conn, selection, rows=streamed(), now=NOW, run_id=run
                )
            finally:
                conn.set_trace_callback(None)
        # The first reference is written after the first row is produced, not
        # after the last one.
        assert stored_after[0] == 1
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == 200
        # The label is written from what was stored, so it cannot be written twice.
        with pytest.raises(SupersededWorkError, match="cannot relabel"):
            with database.transaction():
                derivations.retain_output(conn, generation, (), record_label=lambda _d, _c: None)


def measure(database) -> tuple[int, int]:
    conn = database.connection
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    used = int(conn.execute("SELECT coalesce(sum(pgsize),0) FROM dbstat").fetchone()[0])
    return used, (database.state_dir / "state.sqlite").stat().st_size


def test_dropping_the_old_table_frees_pages_without_shrinking_the_file(tmp_path, monkeypatch):
    """Bytes in use and file size are two numbers; only VACUUM moves the second."""
    repeated = {"name_raw": "Repeated " * 40, "event_id": "event-a"}
    generations = [
        {
            "generation_id": f"dg_bulk_{index}",
            "scope": ("project", "event", f"event-{index}"),
            "rows": [("entries", f'["{index}-{row}"]', dict(repeated)) for row in range(200)],
        }
        for index in range(12)
    ]
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 29)
        with open_database(tmp_path / "state", lock=False) as database:
            populate(database, generations)
            before_used, before_size = measure(database)

    # D-0013: this test owns migration 32, so it opens at exactly schema 32.
    # A later migration is a later test's subject, not a change to this one.
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 32)
    with open_database(tmp_path / "state", lock=False) as database:
        assert database.schema_version == 32
        after_used, after_size = measure(database)
        # One stored copy replaces 2,400 inline ones, so the pages holding data
        # shrink; the dropped pages stay in the file on the free list.
        assert after_used < before_used
        assert after_size >= before_size
        database.connection.execute("VACUUM")
        reclaimed_used, reclaimed_size = measure(database)
        assert reclaimed_size < after_size
        assert reclaimed_used <= after_used


def test_migration_refuses_to_drop_the_old_rows_when_a_label_does_not_match(tmp_path, monkeypatch):
    """The comparison, not only the fill, is what stands between a bad database
    and the last copy of every output row."""
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    before = held_schema29(tmp_path / "state", monkeypatch, generations)
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 29)
        with open_database(tmp_path / "state", lock=False) as database:
            # Labels are immutable, so the disagreement is made on the row side:
            # one stored row that the generation's fingerprint never counted.
            with database.transaction():
                database.connection.execute(
                    "INSERT INTO derivation_rows VALUES ('dg_event_a1',9,'entries','[\"a-9\"]',?)",
                    (derivations.canonical({"name_raw": "Unaccounted Row"}),),
                )

    with pytest.raises(RuntimeError, match="does not match the label of generation dg_event_a1"):
        open_database(tmp_path / "state", lock=False)

    with sqlite3.connect(tmp_path / "state/state.sqlite") as conn:
        # The migrations before interning committed; interning itself rolled back.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 31
        assert (
            conn.execute("SELECT type FROM sqlite_master WHERE name='derivation_rows'").fetchone()[
                0
            ]
            == "table"
        )
        for name in ("derivation_payloads", "derivation_row_refs", "derivation_rows_legacy"):
            assert (
                conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is None
            )
        # Every untouched generation still reads exactly as it did.
        assert {
            identifier: [tuple(row) for row in conn.execute(READER, (identifier,))]
            for identifier in before
            if identifier != "dg_event_a1"
        } == {
            identifier: rows for identifier, rows in before.items() if identifier != "dg_event_a1"
        }
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_rows WHERE generation_id='dg_event_a1'"
            ).fetchone()[0]
            == 4
        )
    # The refusal repeats rather than passing on a second attempt.
    with pytest.raises(RuntimeError, match="does not match the label of generation dg_event_a1"):
        open_database(tmp_path / "state", lock=False)


def evict(database, generation_id: str) -> None:
    """Remove one generation's payload bytes the way plan step 4 will."""
    conn = database.connection
    with database.transaction():
        conn.execute(
            "INSERT INTO derivation_payload_removal_authority VALUES (1,'test plan',?)",
            (NOW.isoformat(),),
        )
        conn.execute(
            "DELETE FROM derivation_payloads WHERE payload_sha256 IN "
            "(SELECT payload_sha256 FROM derivation_row_refs WHERE generation_id=?)",
            (generation_id,),
        )
        conn.execute("DELETE FROM derivation_payload_removal_authority")


def test_referenced_payload_bytes_can_be_removed_and_brought_back(tmp_path):
    """Removal is the point of the gate, and retain_output is the way back."""
    unit = WorkUnit("project", "event", "restore")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = [
            derivations.OutputRow("entries", f'["r{index}"]', {"entry_id": f"r{index}"})
            for index in range(5)
        ]
        generation = completed_generation(database, unit, rows, {"recipe/test": "one"})
        pointer = conn.execute(
            "SELECT materialized_generation_id FROM derivation_scopes WHERE unit_id='restore'"
        ).fetchone()[0]
        # Without the permission row these bytes cannot go, referenced or not.
        with pytest.raises(sqlite3.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
        evict(database, generation)
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_row_refs WHERE generation_id=?", (generation,)
            ).fetchone()[0]
            == 5
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM derivation_rows WHERE generation_id=?", (generation,)
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # Wrong bytes are refused against the label, not written.
        wrong = [
            derivations.OutputRow(
                "entries", f'["r{index}"]', {"entry_id": f"r{index}", "changed": True}
            )
            for index in range(5)
        ]
        with pytest.raises(SupersededWorkError, match="does not match its label"):
            with database.transaction():
                derivations.retain_output(conn, generation, wrong)
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == 0
        # The right bytes come back. A row the references do not name is ignored
        # rather than stored, so restoring leaves nothing ownerless behind.
        extra = derivations.OutputRow("entries", '["not-mine"]', {"entry_id": "not-mine"})
        with database.transaction():
            derivations.retain_output(conn, generation, [*rows, extra])
        assert [tuple(row) for row in conn.execute(READER, (generation,))] == [
            (index, "entries", f'["r{index}"]', derivations.canonical({"entry_id": f"r{index}"}))
            for index in range(5)
        ]
        assert conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0] == 5
        # Restoring moved no pointer and no scheduling state.
        assert (
            conn.execute(
                "SELECT materialized_generation_id FROM derivation_scopes WHERE unit_id='restore'"
            ).fetchone()[0]
            == pointer
        )


def test_readers_of_an_evicted_generation_see_nothing_rather_than_short_rows(tmp_path, monkeypatch):
    """The view's join drops a row whose bytes are gone, so every reader that
    counts rows against a label refuses instead of using a partial answer."""
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    held_schema29(tmp_path / "state", monkeypatch, generations)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        from swingset.build import generations as build_generations

        assert build_generations.completed(conn, "candidate-1", manifest_hash)
        assert derivations._artifact_valid(conn, "dg_build_1")
        evict(database, "dg_build_1")
        assert list(conn.execute(READER, ("dg_build_1",))) == []
        assert not build_generations.completed(conn, "candidate-1", manifest_hash)
        assert not derivations._artifact_valid(conn, "dg_build_1")
        with pytest.raises(SupersededWorkError, match="does not match its label"):
            with database.transaction():
                derivations.retain_output(conn, "dg_build_1", ())


def test_the_scope_pointer_is_not_a_reader_of_the_view(tmp_path):
    """current() answers from the pointer and its signature, not from the rows.

    Plan step 3 is what keeps a current generation's bytes local. The row-count
    and digest checks that catch missing bytes belong to readers of the
    derivation_rows view, and this is not one of them.
    """
    unit = WorkUnit("project", "event", "pointer")
    rows = [derivations.OutputRow("entries", f'["p{index}"]', {"i": index}) for index in range(5)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        run = database.start_run(NOW)
        with database.transaction():
            selection = derivations.capture(conn, unit, now=NOW)
            generation = derivations.complete(conn, selection, rows=iter(rows), now=NOW, run_id=run)
        assert derivations._current(conn, unit) is True
        evict(database, generation)
        assert list(conn.execute(READER, (generation,))) == []
        assert derivations._current(conn, unit) is True


def test_repeat_of_identical_inputs_does_not_read_the_whole_generation_again(tmp_path):
    """An unchanged recomputation must not pay to re-hash what it did not change."""
    unit = WorkUnit("project", "event", "repeat")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        rows = [
            derivations.OutputRow("entries", f'["r{index}"]', {"entry_id": f"r{index}"})
            for index in range(50)
        ]
        first = completed_generation(database, unit, rows, {"recipe/test": "one"})
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            repeat = completed_generation(database, unit, rows, {"recipe/test": "one"})
        finally:
            conn.set_trace_callback(None)
        assert repeat == first
        assert conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0] == 50
        # Not one stored row is read or written again: no view join, no count
        # over the references, nothing.
        assert not [
            statement
            for statement in statements
            if "derivation_rows" in statement
            or "derivation_row_refs" in statement
            or "derivation_payloads" in statement
        ]


# The pre-interning shape of retained output, copied from migration 0014 and the
# derivation_rows part of migration 0029. Interning is the last migration, so
# schema 31 is exactly this shape with everything else in place. Putting a real
# pipeline database back into it is the only way to run migration 32 on rows the
# pipeline itself wrote.
SCHEMA_31_ROWS = """
DROP VIEW derivation_rows;
DROP TABLE derivation_row_refs;
DROP TABLE derivation_payloads;
DROP TABLE derivation_payload_removal_authority;
DROP TABLE derivation_payload_removal_grant;
CREATE TABLE derivation_rows (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id) DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
);
CREATE TRIGGER derivation_row_no_update BEFORE UPDATE ON derivation_rows
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_row_no_delete BEFORE DELETE ON derivation_rows
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER history_timing_derivation_rows_insert AFTER INSERT ON derivation_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_rows_update AFTER UPDATE ON derivation_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_rows_delete AFTER DELETE ON derivation_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
"""


def derivation_row_schema(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE tbl_name='derivation_rows' "
            "OR name='derivation_rows' ORDER BY name"
        )
    ]


def put_back_to_schema_31(database: db_module.Database) -> None:
    """Rewrite an interned database into exactly the shape migration 32 expects."""
    from swingset.state.publication_fence import install_publication_fences

    conn = database.connection
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with database.transaction():
            interned = [tuple(row) for row in conn.execute("SELECT * FROM derivation_rows")]
            conn.executescript(SCHEMA_31_ROWS)
            conn.executemany("INSERT INTO derivation_rows VALUES (?,?,?,?,?)", interned)
            # Every migration refreshes these, so schema 31 had them on the table.
            install_publication_fences(conn)
            conn.execute("UPDATE meta SET value='31' WHERE key='schema_version'")
            conn.execute("PRAGMA user_version=31")
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def test_a_real_release_rebuilds_the_same_way_after_the_migration(
    source_fixture, tmp_path, monkeypatch
):
    """Plan step 6's gate, on rows the pipeline wrote: a database that came
    through the migration selects the same closure and reconstructs the same
    rows as it did before."""
    import shutil

    from test_h15_acceptance import drain_project
    from test_release_closure_rows import baseline_events

    from swingset.build.closure import select
    from swingset.build.closure_manifest import ClosureError, digest
    from swingset.build.closure_rows import reconstruct

    fixture = source_fixture
    units = drain_project(fixture)
    assert units
    baseline = baseline_events(fixture, tmp_path / "baseline")
    closure = select(fixture.conn, cutoff=fixture.corpus.clock.now(), baseline=baseline)
    before_manifest = digest(closure.manifest())
    with reconstruct(
        fixture.conn, closure, directory=tmp_path / "before", baseline=baseline
    ) as rows:
        before = {name: list(rows.iter_table(name)) for name in rows.table_names()}
    assert before["entries"]
    before_current = {
        unit: derivations.current(fixture.conn, unit) for unit in dict.fromkeys(units)
    }
    assert any(before_current.values())

    fixture.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    held = tmp_path / "held"
    shutil.copytree(fixture.db.state_dir, held)
    with open_database(held, lock=False) as database:
        put_back_to_schema_31(database)
        assert database.schema_version == 31
        rewritten = derivation_row_schema(database.connection)
    # The rewrite is the real schema 31, not an approximation of it.
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "SCHEMA_VERSION", 31)
        with open_database(tmp_path / "empty31", lock=False) as reference:
            assert rewritten == derivation_row_schema(reference.connection)

    # D-0013: this test owns migration 32, so it opens at exactly schema 32.
    # A later migration is a later test's subject, not a change to this one.
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 32)
    with open_database(held, lock=False) as migrated:
        conn = migrated.connection
        assert migrated.schema_version == 32
        assert (
            digest(select(conn, cutoff=fixture.corpus.clock.now(), baseline=baseline).manifest())
            == before_manifest
        )
        with reconstruct(conn, closure, directory=tmp_path / "after", baseline=baseline) as rows:
            after = {name: list(rows.iter_table(name)) for name in rows.table_names()}
        assert after == before
        assert {unit: derivations.current(conn, unit) for unit in before_current} == before_current
        references = conn.execute("SELECT count(*) FROM derivation_row_refs").fetchone()[0]
        payloads = conn.execute("SELECT count(*) FROM derivation_payloads").fetchone()[0]
        assert 0 < payloads <= references
        # A generation whose bytes were evicted is refused by the rebuild rather
        # than silently rebuilt short.
        evict(migrated, closure.selected[0]["generation_id"])
        with pytest.raises(ClosureError, match="selected_output_digest_mismatch"):
            with reconstruct(conn, closure, directory=tmp_path / "evicted", baseline=baseline):
                pass
