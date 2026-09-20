"""Tests for the disposable schema-29 derivation row layout benchmark."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PATH = ROOT / "journal/tools/runtime/benchmark_derivation_row_layout.py"
SPEC = importlib.util.spec_from_file_location("benchmark_derivation_row_layout", PATH)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


def _output_digest(rows: list[tuple[str, str, str]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(BENCHMARK.canonical(row).encode() + b"\n")
    return digest.hexdigest()


def make_schema29(path: Path, *, corrupt_label: bool = False, payload_type: str = "TEXT") -> Path:
    directory = path / "state"
    directory.mkdir(parents=True)
    database = directory / "state.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        f"""
        PRAGMA foreign_keys=ON;
        PRAGMA user_version=29;
        CREATE TABLE derivation_generations (
            generation_id TEXT PRIMARY KEY,
            output_digest TEXT NOT NULL,
            row_count INTEGER NOT NULL
        );
        CREATE TABLE derivation_rows (
            generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id)
                DEFERRABLE INITIALLY DEFERRED,
            ordinal INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            record_key TEXT NOT NULL,
            payload_json {payload_type} NOT NULL,
            PRIMARY KEY(generation_id,ordinal),
            UNIQUE(generation_id,table_name,record_key)
        );
        CREATE TRIGGER derivation_row_no_update BEFORE UPDATE ON derivation_rows
        BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
        CREATE TRIGGER derivation_row_no_delete BEFORE DELETE ON derivation_rows
        BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
        CREATE TABLE history_dispatch_fence (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            revision INTEGER NOT NULL
        );
        INSERT INTO history_dispatch_fence VALUES (1,0);
        CREATE TRIGGER history_timing_derivation_rows_insert AFTER INSERT ON derivation_rows
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TRIGGER history_timing_derivation_rows_update AFTER UPDATE ON derivation_rows
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TRIGGER history_timing_derivation_rows_delete AFTER DELETE ON derivation_rows
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TABLE unrelated_state(name TEXT PRIMARY KEY, body BLOB NOT NULL);
        INSERT INTO unrelated_state VALUES ('kept', zeroblob(20000));
        """
    )
    fixtures = {
        "dg_alpha": [
            ("artifact", "candidate-alpha", '{"candidate_id":"candidate-alpha"}'),
            ("events", "event-1", '{"title":"Alpha"}'),
            ("events", "event-2", '{"title":"Beta"}'),
        ],
        "dg_beta": [
            ("artifact", "candidate-beta", '{"candidate_id":"candidate-beta"}'),
            ("events", "event-3", '{"title":"Gamma"}'),
        ],
    }
    for generation_id, rows in fixtures.items():
        connection.execute("BEGIN")
        for ordinal, (table, key, payload) in enumerate(rows):
            # The real writer relies on the deferred FK and inserts rows first.
            connection.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (generation_id, ordinal, table, key, payload),
            )
        digest = "0" * 64 if corrupt_label and generation_id == "dg_beta" else _output_digest(rows)
        connection.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?)",
            (generation_id, digest, len(rows)),
        )
        connection.commit()
    connection.close()
    return directory


def run_benchmark(source: Path, root: Path):
    return BENCHMARK.run(
        source,
        scratch=root / "scratch",
        output=root / "report.json",
        receipt=root / "receipt.json",
        timing_repeats=1,
        command=["benchmark", "fixture"],
    )


def test_full_database_layouts_preserve_content_constraints_and_triggers(tmp_path: Path) -> None:
    source = make_schema29(tmp_path / "input")
    database = source / "state.sqlite"
    signature = BENCHMARK.file_signature(database)

    report = run_benchmark(source, tmp_path / "result")

    assert report["format"] == "derivation-row-layout-benchmark-v2"
    assert report["gates"]["passed"] is True
    assert report["source"]["opened"] == "mode=ro&immutable=1"
    assert report["source"]["signature_before"] == signature
    assert report["source"]["signature_after"] == signature
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()
    assert not Path(str(database) + "-journal").exists()
    assert report["runtime"]["tool_sha256"] == BENCHMARK.file_sha256(PATH)
    assert report["runtime"]["sqlite_version"] == sqlite3.sqlite_version
    assert report["source"]["row_table_metadata"]["foreign_keys"] == [
        {
            "id": 0,
            "sequence": 0,
            "table": "derivation_generations",
            "from": "generation_id",
            "to": "generation_id",
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
            "match": "NONE",
        }
    ]

    baseline = report["layouts"]["baseline"]
    candidate = report["layouts"]["without_rowid"]
    assert baseline["content"] == candidate["content"] == report["source"]["content"]
    assert baseline["storage"]["derivation_rows"]["without_rowid"] is False
    assert candidate["storage"]["derivation_rows"]["without_rowid"] is True
    assert baseline["storage"]["freelist_pages"] == 0
    assert candidate["storage"]["freelist_pages"] == 0
    assert baseline["constraints"]["passed"] is True
    assert candidate["constraints"]["passed"] is True
    assert candidate["constraints"]["history_fence"] == {
        "relevant": True,
        "insert_bumped_revision": True,
    }
    assert candidate["constraints"]["deferred_foreign_key"] == {
        "insert_allowed_before_label": True,
        "commit_rejected_without_label": True,
        "error": "FOREIGN KEY constraint failed",
        "expected_error": "FOREIGN KEY constraint failed",
    }
    for layout in (baseline, candidate):
        for probe in (
            "duplicate_ordinal",
            "duplicate_logical_key",
            "update_immutable",
            "delete_immutable",
        ):
            assert layout["constraints"][probe]["error_type"] == "IntegrityError"
            assert layout["constraints"][probe]["matches_expected"] is True
    assert baseline["triggers"] == candidate["triggers"] == report["source"]["triggers"]

    for layout in (baseline, candidate):
        assert Path(layout["path"]).is_file()
        assert layout["integrity"] == {
            "integrity_check": ["ok"],
            "foreign_key_check": [],
            "passed": True,
        }
        assert len(layout["queries"]) == len(BENCHMARK.QUERY_SPECS)
        assert all(query["plan"] and query["seconds"] for query in layout["queries"])
        compressed = layout["compressed_backup_proxy"]
        assert compressed["kind"] == "deterministic-gzip-whole-database-proxy"
        assert Path(compressed["path"]).stat().st_size == compressed["bytes"]
        assert (
            compressed["source_database_sha256"] == layout["inspected_database_signature"]["sha256"]
        )
        assert compressed["source_unchanged_while_compressing"] is True

        with sqlite3.connect(layout["path"]) as connection:
            assert connection.execute(
                "SELECT length(body) FROM unrelated_state WHERE name='kept'"
            ).fetchone() == (20000,)

    saved = report["comparison"]["whole_database_bytes_saved"]
    assert saved == baseline["storage"]["file_bytes"] - candidate["storage"]["file_bytes"]
    repeated_gzip = BENCHMARK.deterministic_gzip(
        Path(baseline["path"]), tmp_path / "result/scratch/baseline-repeat.sqlite.gz"
    )
    assert repeated_gzip["sha256"] == baseline["compressed_backup_proxy"]["sha256"]
    assert (tmp_path / "result/report.json").is_file()
    receipt = json.loads((tmp_path / "result/receipt.json").read_text())
    assert receipt["format"] == "derivation-row-layout-benchmark-receipt-v2"
    assert receipt["gates"]["passed"] is True
    assert receipt["production_operations"] == 0
    assert receipt["limit"].startswith("gzip is a deterministic")
    assert receipt["runtime"] == report["runtime"]


@pytest.mark.parametrize("destination", ["scratch", "output", "receipt"])
def test_every_destination_is_new_and_outside_source(tmp_path: Path, destination: str) -> None:
    source = make_schema29(tmp_path / "input")
    paths = {
        "scratch": tmp_path / "scratch",
        "output": tmp_path / "report.json",
        "receipt": tmp_path / "receipt.json",
    }
    if destination == "scratch":
        paths[destination].mkdir()
    elif destination == "output":
        paths[destination] = source / "report.json"
    else:
        paths[destination].write_text("existing")

    with pytest.raises(ValueError):
        BENCHMARK.run(
            source,
            scratch=paths["scratch"],
            output=paths["output"],
            receipt=paths["receipt"],
            timing_repeats=1,
        )

    assert not (tmp_path / "scratch/baseline.sqlite").exists()


def test_source_under_configured_live_root_is_refused(tmp_path: Path, monkeypatch) -> None:
    live = tmp_path / "live"
    source = make_schema29(live)
    monkeypatch.setattr(BENCHMARK, "PROTECTED_ROOTS", (str(live),))

    with pytest.raises(ValueError, match="live state"):
        run_benchmark(source, tmp_path / "result")


def test_source_with_rollback_journal_is_refused_before_scratch(tmp_path: Path) -> None:
    source = make_schema29(tmp_path / "input")
    Path(str(source / "state.sqlite") + "-journal").write_bytes(b"closed-copy-journal")

    with pytest.raises(ValueError, match="rollback journal"):
        run_benchmark(source, tmp_path / "result")

    assert not (tmp_path / "result/scratch").exists()


def test_wrong_database_error_does_not_satisfy_constraint_probe() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE sample(value TEXT);
        CREATE TRIGGER reject_sample BEFORE INSERT ON sample
        BEGIN SELECT RAISE(ABORT,'wrong rejection'); END;
        """
    )

    result = BENCHMARK._savepoint_rejection(
        connection,
        "wrong_error",
        "INSERT INTO sample VALUES (?)",
        ("value",),
        "expected rejection",
    )

    assert result["rejected"] is True
    assert result["error_type"] == "IntegrityError"
    assert result["error"] == "wrong rejection"
    assert result["matches_expected"] is False


def test_content_profile_counts_all_mismatches_but_caps_examples() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE derivation_generations(
            generation_id TEXT PRIMARY KEY, output_digest TEXT, row_count INTEGER
        );
        CREATE TABLE derivation_rows(
            generation_id TEXT, ordinal INTEGER, table_name TEXT,
            record_key TEXT, payload_json TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO derivation_generations VALUES (?,?,0)",
        [(f"dg_{ordinal:02d}", "not-the-empty-digest") for ordinal in range(25)],
    )

    profile = BENCHMARK.content_profile(connection)

    assert profile["output_label_mismatch_count"] == 25
    assert len(profile["output_label_mismatch_examples"]) == 20
    assert profile["output_label_mismatch_examples_truncated"] is True


def test_source_table_schema_drift_is_refused(tmp_path: Path) -> None:
    source = make_schema29(tmp_path / "input", payload_type="BLOB")

    with pytest.raises(ValueError, match="definition differs"):
        run_benchmark(source, tmp_path / "result")

    assert not (tmp_path / "result/scratch").exists()


def test_bad_output_label_fails_the_equivalence_gate_but_writes_report(tmp_path: Path) -> None:
    source = make_schema29(tmp_path / "input", corrupt_label=True)

    report = run_benchmark(source, tmp_path / "result")

    assert report["gates"]["passed"] is False
    assert report["gates"]["checks"]["source_output_labels_match_rows"] is False
    assert report["source"]["content"]["output_label_mismatch_count"] == 1
    # Copy equivalence is still exact; the gate distinguishes source corruption
    # from a layout transformation error.
    assert report["gates"]["checks"]["baseline_content_matches_source"] is True
    assert report["gates"]["checks"]["candidate_content_matches_source"] is True
    assert json.loads((tmp_path / "result/report.json").read_text())["gates"]["passed"] is False
