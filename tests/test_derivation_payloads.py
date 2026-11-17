"""Derivation output rows are stored once: migration 32, retain_output, delete gate."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset import cli
from swingset.state import db as db_module
from swingset.state import derivations, engine
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 18, tzinfo=UTC)
# The shape every reader of retained output asks for; the real readers
# (closure_rows.reconstruct, derivations.current and the build receipt checks)
# run against a migrated database further down this file.
READER = (
    "SELECT ordinal, table_name, record_key, payload_json FROM derivation_rows WHERE "
    "generation_id = %s ORDER BY ordinal NULLS FIRST"
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
        conn.execute(
            "INSERT INTO derivation_dependency_sets (dependency_set_id, manifest_json) VALUES ('fixture-set', '[]')"
        )
        for generation in generations:
            stage, kind, unit = generation["scope"]
            conn.execute(
                (
                    "INSERT INTO derivation_scopes (stage, unit_kind, unit_id, registered_at) VALUES (%s, %s, "
                    "%s, %s) ON CONFLICT DO NOTHING"
                ),
                (stage, kind, unit, NOW.isoformat()),
            )
            rows = generation["rows"]
            output_digest, row_count = label(rows)
            conn.execute(
                (
                    "INSERT INTO derivation_generations (generation_id, stage, unit_kind, unit_id, "
                    "input_fingerprint, recipe_json, dependency_set_id, previous_generation_id, output_digest, "
                    "row_count, created_at, run_id) VALUES (%s, %s, %s, %s, %s, '{}', 'fixture-set', NULL, %s, "
                    "%s, %s, %s)"
                ),
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
                body = derivations.canonical(payload)
                digest = hashlib.sha256(body.encode()).hexdigest()
                conn.execute(
                    "INSERT INTO derivation_payloads(payload_sha256,payload_json) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (digest, body),
                )
                conn.execute(
                    "INSERT INTO derivation_row_refs(generation_id,ordinal,table_name,record_key,payload_sha256) VALUES(%s,%s,%s,%s,%s)",
                    (generation["generation_id"], ordinal, table, key, digest),
                )
            conn.execute(
                (
                    "UPDATE derivation_scopes SET materialized_generation_id = %s WHERE stage = %s AND unit_kind "
                    "= %s AND unit_id = %s"
                ),
                (generation["generation_id"], stage, kind, unit),
            )


def retained_state(path: Path, generations: list[dict[str, object]]) -> None:
    """Seed native schema37 rows independently of the retention implementation."""
    with open_database(path, lock=False) as database:
        populate(database, generations)


def labels(conn: engine.Connection) -> dict[str, tuple[str, int]]:
    return {
        str(row[0]): (str(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT generation_id, output_digest, row_count FROM derivation_generations"
        )
    }


def test_migration_stores_shared_rows_once_and_keeps_every_label(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_migration_stores_shared_rows_once_and_keeps_every_label",
    )


def test_migration_failure_after_the_fill_keeps_the_old_rows_and_schema(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_migration_failure_after_the_fill_keeps_the_old_rows_and_schema",
    )


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
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 1000
        changed = [
            derivations.OutputRow(
                "entries",
                f'["e{index}"]',
                {"entry_id": f"e{index}", "v": index + (10_000 if index < 10 else 0)},
            )
            for index in range(1000)
        ]
        completed_generation(database, unit, changed, {"recipe/test": "two"})
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 1010
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 2000


def test_retain_output_checks_the_label_and_repeats_without_writing(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    retained_state(tmp_path / "state", generations)
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
        before = conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0]
        with database.transaction():
            derivations.retain_output(conn, "dg_event_a1", [])
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == before

        # A label with the right row count but a different digest is rejected, and
        # so is one with the right digest but a different row count.
        output_digest, row_count = label(generations[0]["rows"])
        for identifier, stored_digest, stored_count in (
            ("dg_digest", "0" * 64, row_count),
            ("dg_count", output_digest, row_count + 1),
        ):
            with database.transaction():
                conn.execute(
                    (
                        "INSERT INTO derivation_generations (generation_id, stage, unit_kind, unit_id, "
                        "input_fingerprint, recipe_json, dependency_set_id, previous_generation_id, output_digest, "
                        "row_count, created_at, run_id) VALUES (%s, 'project', 'event', 'event-a', %s, '{}', "
                        "'fixture-set', NULL, %s, %s, %s, %s)"
                    ),
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
                    "SELECT COUNT(*) FROM derivation_row_refs WHERE generation_id = %s",
                    (identifier,),
                ).fetchone()[0]
                == 0
            )


def test_payload_removal_needs_a_permission_row_in_the_same_transaction(tmp_path, monkeypatch):
    path, manifest_hash = candidate(tmp_path / "candidate")
    generations = fixture_generations(path, manifest_hash)
    retained_state(tmp_path / "state", generations)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        orphan = hashlib.sha256(b'{"unreferenced":true}').hexdigest()
        with database.transaction():
            conn.execute(
                "INSERT INTO derivation_payloads (payload_sha256, payload_json) VALUES (%s, %s)",
                (orphan, '{"unreferenced":true}'),
            )
        with pytest.raises(engine.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads WHERE payload_sha256 = %s", (orphan,))
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM derivation_payloads WHERE payload_sha256 = %s", (orphan,)
            ).fetchone()[0]
            == 1
        )
        with database.transaction():
            conn.execute(
                (
                    "INSERT INTO derivation_payload_removal_authority (singleton, reason, granted_at) VALUES (1, "
                    "'test plan', %s)"
                ),
                (NOW.isoformat(),),
            )
            conn.execute("DELETE FROM derivation_payloads WHERE payload_sha256 = %s", (orphan,))
            conn.execute("DELETE FROM derivation_payload_removal_authority")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM derivation_payloads WHERE payload_sha256 = %s", (orphan,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
        # Without the permission row the gate is closed again.
        with pytest.raises(engine.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")


def test_a_removal_grant_cannot_outlive_its_transaction(tmp_path):
    """A grant that could commit would open the gate for every later process."""
    unit = WorkUnit("project", "event", "gate")
    rows = [derivations.OutputRow("entries", f'["g{index}"]', {"i": index}) for index in range(5)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        completed_generation(database, unit, rows, {"recipe/test": "one"})
        with pytest.raises(engine.IntegrityError, match="23503"):
            with database.transaction():
                conn.execute(
                    (
                        "INSERT INTO derivation_payload_removal_authority (singleton, reason, granted_at) VALUES (1, "
                        "'left behind', %s)"
                    ),
                    (NOW.isoformat(),),
                )
        # The failed commit is rolled back, not left open for the next caller.
        assert not conn.in_transaction
        assert (
            conn.execute("SELECT COUNT(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with pytest.raises(engine.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 5


def test_a_grant_written_with_foreign_keys_off_is_reported_and_cleared(tmp_path, legacy_sqlite):
    """Keep the old recovery case and prove current workers cannot bypass FKs."""
    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_a_grant_written_with_foreign_keys_off_is_reported_and_cleared",
    )
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        completed_generation(
            database,
            WorkUnit("project", "event", "guard"),
            [derivations.OutputRow("entries", '["guard"]', {"entry_id": "guard"})],
            {"recipe/test": "one"},
        )
        with pytest.raises(engine.DatabaseError, match="42501"):
            conn.execute("SET session_replication_role=replica")
        assert (
            conn.execute("SELECT COUNT(*) FROM derivation_payload_removal_authority").fetchone()[0]
            == 0
        )
    report = cli.doctor(argparse.Namespace(state=tmp_path / "state", config=Path("config")))
    assert report["derivation_payload_removal_grants"] == []
    with open_database(tmp_path / "state", lock=False) as database:
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM derivation_payload_removal_authority"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(engine.IntegrityError, match="requires authority"):
            database.connection.execute("DELETE FROM derivation_payloads")


def test_completion_streams_bounded_batches_without_buffering_the_whole_output(tmp_path):
    """One pass reaches storage before exhausting the output, with fewer round trips."""
    unit = WorkUnit("project", "event", "stream")
    produced: list[int] = []
    stored_after: list[int] = []
    write_statements: list[str] = []
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection

        class Stream:
            iterations = 0

            def __iter__(self):
                self.iterations += 1
                assert self.iterations == 1
                for index in range(700):
                    produced.append(index)
                    yield derivations.OutputRow("entries", f'["s{index}"]', {"i": index})

        stream = Stream()

        run = database.start_run(NOW)
        with database.transaction():
            selection = derivations.capture(conn, unit, now=NOW, recipe={"recipe/test": "one"})
            from postgres_statements import observing

            def store_statement(sql, parameters):
                if (
                    "INSERT INTO derivation_payloads" in sql
                    or "INSERT INTO derivation_row_refs" in sql
                ):
                    write_statements.append(sql)
                if (
                    "INSERT INTO derivation_row_refs (generation_id, ordinal, table_name, record_key, payload_sha256)"
                    in sql
                ):
                    stored_after.append(len(produced))

            with observing(conn, store_statement):
                generation = derivations.complete(conn, selection, rows=stream, now=NOW, run_id=run)
        assert 0 < stored_after[0] <= 256 < len(produced)
        assert all(
            0 < end - start <= 256
            for start, end in zip([0, *stored_after[:-1]], stored_after, strict=True)
        )
        assert stream.iterations == 1
        assert len(write_statements) < len(produced) / 32
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 700
        # The label is written from what was stored, so it cannot be written twice.
        with pytest.raises(SupersededWorkError, match="cannot relabel"):
            with database.transaction():
                derivations.retain_output(conn, generation, (), record_label=lambda _d, _c: None)


def test_batched_retention_preserves_ordered_bytes_labels_and_shared_membership(tmp_path):
    bodies = [
        '{"count":1,"flag":true,"name":"Zoë 東京","none":null,"ratio":1.0}',
        '{"count":0,"flag":false,"name":"😀","none":null,"ratio":-0.0}',
        '{"items":[1,true,null,2.5],"name":"é"}',
    ]
    rows = [
        derivations.OutputRow("entries", f'["r{index}"]', json.loads(bodies[index % 3]))
        for index in range(600)
    ]
    expected = [(index, "entries", f'["r{index}"]', bodies[index % 3]) for index in range(600)]
    # Fixed label of the literal UTF-8 stream, including 1.0, -0.0 and decomposed é.
    golden = ("4bbb0a7c2973acdc9f64d9ec833dcbc83a18e72a13f2f0eb89ee19d55b34b06c", 600)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        generations = [
            completed_generation(
                database, WorkUnit("project", "event", name), rows, {"recipe/test": "one"}
            )
            for name in ("first", "second")
        ]
        assert generations[0] != generations[1]
        for name, generation in zip(("first", "second"), generations, strict=True):
            assert [tuple(row) for row in conn.execute(READER, (generation,))] == expected
            assert derivations._recorded_label(conn, generation) == golden
            assert (
                derivations.selected_generation(conn, WorkUnit("project", "event", name))
                == generation
            )
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 1200


def test_retention_batches_bound_utf8_bytes_and_write_an_oversized_row_alone(tmp_path):
    from postgres_statements import observing

    values = ["é" * 200_000, "東京" * 60_000, "😀" * 100_000, "x" * (1024 * 1024), "tail"]
    bodies = [
        json.dumps({"value": value}, separators=(",", ":"), ensure_ascii=False) for value in values
    ]
    rows = [
        derivations.OutputRow("entries", str(index), {"value": value})
        for index, value in enumerate(values)
    ]
    writes: list[list[str]] = []

    def storage(sql, parameters):
        if "INSERT INTO derivation_payloads" in sql:
            payloads = [value for value in parameters if isinstance(value, str) and value in bodies]
            assert 0 < len(payloads) <= 256
            size = sum(len(value.encode()) for value in payloads)
            assert size <= 1024 * 1024 or len(payloads) == 1
            writes.append(payloads)

    with open_database(tmp_path / "state", lock=False) as database:
        with observing(database.connection, storage):
            generation = completed_generation(
                database, WorkUnit("project", "event", "bytes"), rows, {"recipe/test": "one"}
            )
        assert [body for batch in writes for body in batch] == bodies
        assert [bodies[3]] in writes
        assert [
            tuple(row)[3] for row in database.connection.execute(READER, (generation,))
        ] == bodies


@pytest.mark.parametrize("duplicate_at", [1, 256])
def test_duplicate_retained_keys_roll_back_every_batch(tmp_path, duplicate_at):
    rows = [derivations.OutputRow("entries", str(index), {"index": index}) for index in range(257)]
    rows[duplicate_at] = derivations.OutputRow("entries", "0", {"index": duplicate_at})
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        tables = (
            "derivation_payloads",
            "derivation_row_refs",
            "derivation_generations",
            "derivation_scopes",
            "runs",
        )
        before = [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables]
        with pytest.raises(engine.IntegrityError):
            completed_generation(
                database, WorkUnit("project", "event", "duplicate"), rows, {"recipe/test": "one"}
            )
        assert [
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
        ] == before


def test_later_retention_iterator_failure_preserves_exception_and_rolls_back(tmp_path):
    primary = RuntimeError("producer interrupted")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        tables = (
            "derivation_payloads",
            "derivation_row_refs",
            "derivation_generations",
            "derivation_scopes",
            "runs",
        )
        before = [conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables]

        def rows():
            for index in range(270):
                yield derivations.OutputRow("entries", str(index), {"index": index})
            assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 256
            raise primary

        with pytest.raises(RuntimeError) as caught:
            completed_generation(
                database, WorkUnit("project", "event", "failure"), rows(), {"recipe/test": "one"}
            )
        assert caught.value is primary
        assert [
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
        ] == before


def test_retention_checks_the_deferred_label_after_all_batches(tmp_path):
    rows = [derivations.OutputRow("entries", str(index), {"shared": True}) for index in range(257)]
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        recorded = []

        def omit_label(digest, count):
            recorded.append((digest, count))
            assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 257
            assert derivations._recorded_label(conn, "unlabelled") is None

        with pytest.raises(SupersededWorkError, match="does not match its label"):
            with database.transaction():
                derivations.retain_output(conn, "unlabelled", rows, record_label=omit_label)
        assert len(recorded) == 1 and recorded[0][1] == 257
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 0


def test_dropping_the_old_table_frees_pages_without_shrinking_the_file(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_dropping_the_old_table_frees_pages_without_shrinking_the_file",
    )


def test_migration_refuses_to_drop_the_old_rows_when_a_label_does_not_match(legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_migration_refuses_to_drop_the_old_rows_when_a_label_does_not_match",
    )


def evict(database, generation_id: str) -> None:
    """Remove one generation's payload bytes the way plan step 4 will."""
    conn = database.connection
    with database.transaction():
        conn.execute(
            (
                "INSERT INTO derivation_payload_removal_authority (singleton, reason, granted_at) VALUES (1, "
                "'test plan', %s)"
            ),
            (NOW.isoformat(),),
        )
        conn.execute(
            (
                "DELETE FROM derivation_payloads WHERE payload_sha256 IN (SELECT payload_sha256 FROM "
                "derivation_row_refs WHERE generation_id = %s)"
            ),
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
            "SELECT materialized_generation_id FROM derivation_scopes WHERE unit_id = 'restore'"
        ).fetchone()[0]
        # Without the permission row these bytes cannot go, referenced or not.
        with pytest.raises(engine.IntegrityError, match="requires authority"):
            conn.execute("DELETE FROM derivation_payloads")
        evict(database, generation)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM derivation_row_refs WHERE generation_id = %s", (generation,)
            ).fetchone()[0]
            == 5
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM derivation_rows WHERE generation_id = %s", (generation,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT bool_and(convalidated) FROM pg_constraint WHERE contype='f'"
            ).fetchone()[0]
            is True
        )
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
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 0
        # The right bytes come back. A row the references do not name is ignored
        # rather than stored, so restoring leaves nothing ownerless behind.
        extra = derivations.OutputRow("entries", '["not-mine"]', {"entry_id": "not-mine"})
        with database.transaction():
            derivations.retain_output(conn, generation, [*rows, extra])
        assert [tuple(row) for row in conn.execute(READER, (generation,))] == [
            (index, "entries", f'["r{index}"]', derivations.canonical({"entry_id": f"r{index}"}))
            for index in range(5)
        ]
        assert conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0] == 5
        # Restoring moved no pointer and no scheduling state.
        assert (
            conn.execute(
                "SELECT materialized_generation_id FROM derivation_scopes WHERE unit_id = 'restore'"
            ).fetchone()[0]
            == pointer
        )


def test_readers_of_an_evicted_generation_see_nothing_rather_than_short_rows(tmp_path, monkeypatch):
    """The view's join drops a row whose bytes are gone, so every reader that
    counts rows against a label refuses instead of using a partial answer."""
    path, manifest_hash = candidate(tmp_path / "state" / "candidates" / "candidate-1")
    generations = fixture_generations(path, manifest_hash)
    retained_state(tmp_path / "state", generations)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        from swingset.build import generations as build_generations

        assert build_generations.completed(conn, "candidate-1", manifest_hash)
        assert derivations._artifact_valid(conn, "dg_build_1")
        evict(database, "dg_build_1")
        assert list(conn.execute(READER, ("dg_build_1",))) == []
        assert not build_generations.completed(conn, "candidate-1", manifest_hash)
        assert not derivations._artifact_valid(conn, "dg_build_1")
        # The one accessor (phase 4) refuses too: references without their
        # bytes and no eviction marker is neither resident nor evicted.
        from swingset.state import output_packs

        assert not output_packs.complete(conn, "dg_build_1")
        with pytest.raises(output_packs.GenerationUnavailable):
            list(output_packs.rows(conn, "dg_build_1"))
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
        from postgres_statements import observing

        statements = []
        with observing(conn, lambda sql, parameters: statements.append((sql, parameters))):
            repeat = completed_generation(database, unit, rows, {"recipe/test": "one"})
        assert repeat == first
        assert conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0] == 50
        # Not one stored row is read or written again: no view join, no count
        # over the references, nothing. One index probe of the last ordinal
        # says the generation is whole, not a partial staged one (D-0217).
        probe = "SELECT 1 AS _value_0 FROM derivation_row_refs WHERE generation_id = %s AND ordinal = %s"
        touched = [
            (statement, parameters)
            for statement, parameters in statements
            if "pg_catalog" not in statement
            and (
                "derivation_rows" in statement
                or "derivation_row_refs" in statement
                or "derivation_payloads" in statement
            )
        ]
        assert touched == [(probe, (first, 49))]


def test_a_real_release_rebuilds_the_same_way_after_the_migration(
    source_fixture, tmp_path, monkeypatch, legacy_sqlite
):
    """Plan step 6's gate, on rows the pipeline wrote: a database that came
    through the migration selects the same closure and reconstructs the same
    rows as it did before."""
    from test_h15_acceptance import drain_project
    from test_release_closure_rows import baseline_events

    from swingset.build.closure import select
    from swingset.build.closure_manifest import ClosureError, digest
    from swingset.build.closure_rows import reconstruct

    legacy_sqlite.assert_case(
        "test_derivation_payloads.py",
        "test_a_real_release_rebuilds_the_same_way_after_the_migration",
    )
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

    from swingset.state.postgres_copy import Copies

    held = tmp_path / "held"
    with Copies(fixture.db.state_dir) as copies:
        copies.freeze(fixture.conn, held)
        with open_database(held, lock=False) as migrated:
            conn = migrated.connection
            assert migrated.schema_version == db_module.SCHEMA_VERSION
            assert (
                digest(
                    select(conn, cutoff=fixture.corpus.clock.now(), baseline=baseline).manifest()
                )
                == before_manifest
            )
            with reconstruct(
                conn, closure, directory=tmp_path / "after", baseline=baseline
            ) as rows:
                after = {name: list(rows.iter_table(name)) for name in rows.table_names()}
            assert after == before
            assert {
                unit: derivations.current(conn, unit) for unit in before_current
            } == before_current
            references = conn.execute("SELECT COUNT(*) FROM derivation_row_refs").fetchone()[0]
            payloads = conn.execute("SELECT COUNT(*) FROM derivation_payloads").fetchone()[0]
            assert 0 < payloads <= references
            # A generation whose bytes were evicted is refused by the rebuild rather
            # than silently rebuilt short.
            evict(migrated, closure.selected[0]["generation_id"])
            with pytest.raises(ClosureError, match="selected_output_digest_mismatch"):
                with reconstruct(conn, closure, directory=tmp_path / "evicted", baseline=baseline):
                    pass


def stage_then_stop(database, unit, rows, recipe, *, stop_at: int):
    """A staged completion whose step B stops in chunk ``stop_at`` (WP4)."""
    conn = database.connection
    run_id = database.start_run(NOW)
    real = derivations._store_chunk
    calls = 0

    def store(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == stop_at:
            raise RuntimeError("stopped in step B")
        yield from real(*args, **kwargs)

    with derivations.chunking(conn, unit) as chunk:
        with database.transaction(), derivations.group(conn):
            selection = derivations.capture(conn, unit, now=NOW, recipe=recipe)
            assert derivations.stage(
                conn, selection, output=lambda reader: iter(rows), now=NOW, run_id=run_id
            )
        derivations._store_chunk = store
        try:
            with pytest.raises(RuntimeError, match="stopped in step B"):
                derivations.finish_building(database, chunk.deferred)
        finally:
            derivations._store_chunk = real
    return selection.generation_id


def test_a_single_path_completion_finishes_a_generation_a_staged_run_left(tmp_path, monkeypatch):
    unit = WorkUnit("project", "event", "staged")
    rows = [
        derivations.OutputRow("entries", f'["e{index}"]', {"entry_id": f"e{index}"})
        for index in range(5)
    ]
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 2)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        generation = stage_then_stop(database, unit, rows, {"recipe/test": "one"}, stop_at=2)
        assert tuple(
            conn.execute(
                "SELECT label_rows, rows_stored FROM derivation_building WHERE generation_id = %s",
                (generation,),
            ).fetchone()
        ) == (5, 2)
        # The partial generation is labelled but selected by nothing.
        assert derivations.selected_generation(conn, unit) is None
        assert completed_generation(database, unit, rows, {"recipe/test": "one"}) == generation
        assert derivations.selected_generation(conn, unit) == generation
        assert [
            tuple(row)
            for row in conn.execute(
                (
                    "SELECT ordinal, record_key FROM derivation_row_refs WHERE generation_id = %s ORDER BY "
                    "ordinal NULLS FIRST"
                ),
                (generation,),
            )
        ] == [(index, f'["e{index}"]') for index in range(5)]
        assert conn.execute("SELECT COUNT(*) FROM derivation_building").fetchone()[0] == 0


def test_a_stored_row_that_differs_on_resume_is_nondeterministic(tmp_path, monkeypatch):
    unit = WorkUnit("project", "event", "staged")
    rows = [
        derivations.OutputRow("entries", f'["e{index}"]', {"entry_id": f"e{index}"})
        for index in range(5)
    ]
    monkeypatch.setattr(derivations, "CHUNK_ROWS", 2)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        generation = stage_then_stop(database, unit, rows, {"recipe/test": "one"}, stop_at=2)
        changed = [
            derivations.OutputRow(row.table, row.key, {**row.payload, "v": 1}) for row in rows
        ]
        with pytest.raises(derivations.NondeterministicOutputError):
            completed_generation(database, unit, changed, {"recipe/test": "one"})
        assert derivations.selected_generation(conn, unit) is None
        assert (
            conn.execute(
                "SELECT rows_stored FROM derivation_building WHERE generation_id = %s",
                (generation,),
            ).fetchone()[0]
            == 2
        )
