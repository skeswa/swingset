"""Tests for the disposable schema-29 source-generation compression benchmark."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PATH = ROOT / "journal/tools/runtime/benchmark_source_generation_compression.py"
SPEC = importlib.util.spec_from_file_location("benchmark_source_generation_compression", PATH)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)

DOCUMENTS = ("manifest_json", "recipe_json", "result_json", "report_json")


def _recipe_with_size(size: int) -> str:
    prefix = '{"context":{"snapshot_id":"boundary","source":"fixture"},"padding":"'
    suffix = '"}'
    recipe = prefix + ("x" * (size - len(prefix.encode()) - len(suffix.encode()))) + suffix
    assert len(recipe.encode()) == size
    return recipe


def _fixture_rows(*, malformed: bool = False) -> list[dict[str, str]]:
    rows = [
        {
            "manifest_json": '{ "label" : "café ☃", "escaped" : "\\u0000" }',
            "recipe_json": '{ "context" : { "snapshot_id" : "snapshot-a", "source" : "alpha" } }',
            "result_json": '{"text":"line\\nNUL:\\u0000","unicode":"雪"}',
            "report_json": ('{ "guards" : [ ], "fields" : [ "é" ], "proposed_removal" : "none" }'),
        },
        {
            "manifest_json": "{}",
            "recipe_json": '{"context":{"snapshot_id":"snapshot-b"}}',
            "result_json": "{}",
            "report_json": '{"proposed_removal":"none"}',
        },
        {
            "manifest_json": "{}",
            "recipe_json": '{"context":{"snapshot_id":"snapshot-c","source":42}}',
            "result_json": "{}",
            "report_json": '{"proposed_removal":"none"}',
        },
        *[
            {
                "manifest_json": "{}",
                "recipe_json": _recipe_with_size(size),
                "result_json": "{}",
                "report_json": '{"proposed_removal":"none"}',
            }
            for size in (65535, 65536, 65537)
        ],
    ]
    if malformed:
        rows.append(
            {
                "manifest_json": "{}",
                "recipe_json": '{"context":',
                "result_json": "{}",
                "report_json": '{"proposed_removal":"none"}',
            }
        )
    return rows


def make_schema29(path: Path, *, malformed: bool = False) -> tuple[Path, list[dict[str, str]]]:
    state = path / "state"
    state.mkdir(parents=True)
    database = state / "state.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        PRAGMA user_version=29;
        CREATE TABLE watches(watch_id TEXT PRIMARY KEY);
        CREATE TABLE runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE source_units (
            unit_key TEXT PRIMARY KEY,
            watch_id TEXT NOT NULL REFERENCES watches(watch_id),
            page_kind TEXT NOT NULL,
            desired_fingerprint TEXT,
            accepted_generation_id TEXT REFERENCES source_generations(generation_id),
            legacy_snapshot_id TEXT,
            legacy_state TEXT NOT NULL DEFAULT 'none'
                CHECK(legacy_state IN ('none','legacy_unassessed','assessed'))
        );
        CREATE INDEX source_units_watch_idx ON source_units(watch_id);
        CREATE INDEX source_units_accepted_idx ON source_units(accepted_generation_id);
        CREATE INDEX source_units_legacy_idx ON source_units(legacy_snapshot_id);
        CREATE TABLE source_generations (
            generation_id TEXT PRIMARY KEY,
            unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
            page_kind TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            recipe_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            report_json TEXT NOT NULL,
            previous_generation_id TEXT REFERENCES source_generations(generation_id),
            work_token TEXT,
            created_at TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            state TEXT NOT NULL CHECK(state IN (
                'staged','waiting_for_inputs','needs_review','accepted','superseded','revoked'
            )),
            removal_authority TEXT NOT NULL CHECK(removal_authority IN ('none','watch'))
        );
        CREATE INDEX source_generations_unit_idx
            ON source_generations(unit_key,created_at,generation_id);
        CREATE INDEX source_generations_previous_idx
            ON source_generations(previous_generation_id);
        CREATE INDEX source_generations_run_idx ON source_generations(run_id);
        CREATE INDEX source_generations_state_idx ON source_generations(state,page_kind);
        CREATE INDEX source_generations_retained_source ON source_generations(
            CASE WHEN json_valid(recipe_json) THEN
                CASE WHEN json_type(recipe_json,'$.context.source')='text'
                     THEN json_extract(recipe_json,'$.context.source') END
            END
        );
        CREATE TRIGGER source_generation_immutable BEFORE UPDATE OF
            unit_key,page_kind,contract_version,input_fingerprint,
            manifest_json,recipe_json,result_json,report_json,
            previous_generation_id,work_token,created_at,run_id,removal_authority
            ON source_generations
        BEGIN SELECT RAISE(ABORT,'source generation evidence is immutable'); END;
        CREATE TABLE history_dispatch_fence(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
        );
        INSERT INTO history_dispatch_fence VALUES(1,0);
        CREATE TRIGGER history_timing_source_generations_insert
            AFTER INSERT ON source_generations
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TRIGGER history_timing_source_generations_update
            AFTER UPDATE ON source_generations
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TRIGGER history_timing_source_generations_delete
            AFTER DELETE ON source_generations
        BEGIN UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1; END;
        CREATE TABLE unrelated_state(name TEXT PRIMARY KEY, body BLOB NOT NULL);
        INSERT INTO unrelated_state VALUES('kept',zeroblob(20000));
        CREATE TRIGGER unrelated_source_generation_reader AFTER INSERT ON unrelated_state
        BEGIN SELECT generation_id FROM source_generations LIMIT 1; END;
        CREATE TABLE unrelated_documents(
            manifest_json TEXT, recipe_json TEXT, result_json TEXT, report_json TEXT
        );
        INSERT INTO unrelated_documents VALUES('{}','{}','{}','{}');
        INSERT INTO runs VALUES('run');
        """
    )
    rows = _fixture_rows(malformed=malformed)
    for ordinal, row in enumerate(rows):
        unit = f"unit-{ordinal}"
        watch = f"watch-{ordinal}"
        work_token = f"work-{ordinal}"
        try:
            fingerprint = hashlib.sha256(
                BENCHMARK.canonical(
                    {
                        "manifest": json.loads(row["manifest_json"]),
                        "recipe": json.loads(row["recipe_json"]),
                    }
                )
            ).hexdigest()
            generation_id = (
                "gen_"
                + hashlib.sha256(
                    BENCHMARK.canonical(
                        {
                            "unit": unit,
                            "fingerprint": fingerprint,
                            "report": json.loads(row["report_json"]),
                            "result": row["result_json"],
                            "previous": None,
                            "work_token": work_token,
                        }
                    )
                ).hexdigest()
            )
        except json.JSONDecodeError:
            fingerprint = "unverifiable-fingerprint"
            generation_id = "unverifiable-generation"
        row["generation_id"] = generation_id
        connection.execute("INSERT INTO watches VALUES(?)", (watch,))
        connection.execute(
            "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES(?,?,'fixture.kind')",
            (unit, watch),
        )
        connection.execute(
            "INSERT INTO source_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["generation_id"],
                unit,
                "fixture.kind",
                "1",
                fingerprint,
                row["manifest_json"],
                row["recipe_json"],
                row["result_json"],
                row["report_json"],
                None,
                work_token,
                f"2026-09-20T00:00:{ordinal:02d}+00:00",
                "run",
                "accepted",
                "none",
            ),
        )
    connection.commit()
    connection.close()
    return state, rows


def run_benchmark(source: Path, root: Path):
    return BENCHMARK.run(
        source,
        scratch=root / "scratch",
        output=root / "report.json",
        receipt=root / "receipt.json",
        timing_repeats=1,
        command=["benchmark", "fixture"],
    )


def _document_stream_digest(rows: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["generation_id"]):
        for field in ("generation_id", *DOCUMENTS):
            body = row[field].encode()
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(body)
    return digest.hexdigest()


def test_codec_round_trips_exact_bytes_and_rejects_bad_frames() -> None:
    raw = b'{  "noncanonical" : "\xe9\x9b\xaa", "nul" : "\\u0000" }\x00'
    compressed = BENCHMARK.compress_document(raw)

    assert BENCHMARK.decode_document(compressed, expected_bytes=len(raw), max_bytes=len(raw)) == raw
    with pytest.raises(ValueError, match="document exceeds decompressed byte cap"):
        BENCHMARK.decode_document(compressed, expected_bytes=len(raw), max_bytes=len(raw) - 1)
    with pytest.raises(ValueError, match="decompressed document length mismatch"):
        BENCHMARK.decode_document(compressed, expected_bytes=len(raw) + 1, max_bytes=len(raw) + 1)
    for broken in (compressed[:-1], compressed + b"trailing", b"not-zlib"):
        with pytest.raises(ValueError, match="invalid compressed document"):
            BENCHMARK.decode_document(broken, expected_bytes=len(raw), max_bytes=len(raw) + 20)


def test_a1_layout_preserves_exact_documents_expressions_and_constraints(tmp_path: Path) -> None:
    source, rows = make_schema29(tmp_path / "input")
    source_database = source / "state.sqlite"
    source_signature = BENCHMARK.file_signature(source_database)

    report = run_benchmark(source, tmp_path / "result")

    assert report["gates"]["passed"] is True
    assert report["source"]["signature_before"] == source_signature
    assert report["source"]["signature_after"] == source_signature
    assert report["runtime"]["tool_sha256"] == BENCHMARK.file_sha256(PATH)
    assert report["command"] == ["benchmark", "fixture"]
    baseline = report["layouts"]["baseline"]
    candidate = report["layouts"]["compressed"]
    expected_digest = _document_stream_digest(rows)
    assert baseline["content"]["document_stream_sha256"] == expected_digest
    assert candidate["content"]["document_stream_sha256"] == expected_digest
    assert candidate["content"] == baseline["content"] == report["source"]["content"]
    assert candidate["integrity"] == {
        "integrity_check": ["ok"],
        "foreign_key_check": [],
        "passed": True,
    }
    assert candidate["constraints"]["passed"] is True

    candidate_path = Path(candidate["path"])
    with sqlite3.connect(candidate_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        assert (
            connection.execute(
                "SELECT instr(upper(sql),'WITHOUT ROWID')>0 FROM sqlite_schema "
                "WHERE type='table' AND name='source_generation_documents'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM source_generation_documents").fetchone()[
            0
        ] == len(rows)
        assert (
            connection.execute(
                "SELECT length(body) FROM unrelated_state WHERE name='kept'"
            ).fetchone()[0]
            == 20000
        )
        stored = {
            row["generation_id"]: row
            for row in connection.execute("SELECT * FROM source_generations")
        }
        document_rows = {
            row["generation_id"]: row
            for row in connection.execute("SELECT * FROM source_generation_documents")
        }
        for source_row in rows:
            row = stored[source_row["generation_id"]]
            documents = document_rows[source_row["generation_id"]]
            for name in ("manifest", "recipe", "result", "report"):
                raw = source_row[f"{name}_json"].encode()
                assert documents[f"{name}_bytes"] == len(raw)
                assert bytes(documents[f"{name}_sha256"]) == hashlib.sha256(raw).digest()
                assert (
                    BENCHMARK.decode_document(
                        bytes(documents[f"{name}_zlib"]),
                        expected_bytes=len(raw),
                        max_bytes=len(raw),
                    )
                    == raw
                )
            recipe = source_row["recipe_json"]
            expected_valid = int(
                connection.execute("SELECT json_valid(?)", (recipe,)).fetchone()[0]
            )
            expected_snapshot = connection.execute(
                "SELECT CASE WHEN json_valid(?) THEN json_extract(?,'$.context.snapshot_id') END",
                (recipe, recipe),
            ).fetchone()[0]
            expected_source = connection.execute(
                "SELECT CASE WHEN json_valid(?) THEN CASE "
                "WHEN json_type(?,'$.context.source')='text' "
                "THEN json_extract(?,'$.context.source') END END",
                (recipe, recipe, recipe),
            ).fetchone()[0]
            assert row["recipe_bytes"] == len(recipe.encode())
            assert row["recipe_valid"] == expected_valid
            assert row["recipe_snapshot_id"] == expected_snapshot
            assert row["recipe_source"] == expected_source

        assert (
            connection.execute(
                "SELECT count(*) FROM source_generations WHERE recipe_source IS NULL"
            ).fetchone()[0]
            == 2
        )
        source_plan = " ".join(
            row[3]
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT generation_id FROM source_generations "
                "WHERE recipe_source='alpha'"
            )
        )
        assert "source_generations_retained_source" in source_plan

        mutable = rows[0]["generation_id"]
        connection.execute(
            "UPDATE source_generations SET state='superseded' WHERE generation_id=?", (mutable,)
        )
        with pytest.raises(sqlite3.IntegrityError, match="source generation evidence is immutable"):
            connection.execute(
                "UPDATE source_generations SET recipe_bytes=0 WHERE generation_id=?", (mutable,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE source_generation_documents SET recipe_zlib=x'00' WHERE generation_id=?",
                (mutable,),
            )

        deleted = rows[-1]["generation_id"]
        unit_key = connection.execute(
            "SELECT unit_key FROM source_generations WHERE generation_id=?", (deleted,)
        ).fetchone()[0]
        connection.execute("DELETE FROM source_generations WHERE generation_id=?", (deleted,))
        assert (
            connection.execute(
                "SELECT count(*) FROM source_generation_documents WHERE generation_id=?", (deleted,)
            ).fetchone()[0]
            == 0
        )
        connection.execute("DELETE FROM source_units WHERE unit_key=?", (unit_key,))
        connection.commit()

    receipt = json.loads((tmp_path / "result/receipt.json").read_text())
    assert receipt["gates"]["passed"] is True
    assert receipt["command"] == ["benchmark", "fixture"]
    assert receipt["runtime"] == report["runtime"]


def test_malformed_recipe_is_preserved_exactly_and_fails_identity_gate(tmp_path: Path) -> None:
    source, rows = make_schema29(tmp_path / "input", malformed=True)

    report = run_benchmark(source, tmp_path / "result")

    assert report["gates"]["passed"] is False
    assert report["gates"]["checks"]["source_generation_ids_and_fingerprints_recompute"] is False
    assert report["gates"]["checks"]["baseline_content_matches_source"] is True
    assert report["gates"]["checks"]["candidate_content_matches_source"] is True
    source_content = report["source"]["content"]
    candidate_content = report["layouts"]["compressed"]["content"]
    assert source_content["input_fingerprint_mismatch_count"] == 1
    assert source_content["generation_or_authority_mismatch_count"] == 1
    assert candidate_content["document_stream_sha256"] == _document_stream_digest(rows)
    candidate_path = Path(report["layouts"]["compressed"]["path"])
    with sqlite3.connect(candidate_path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = connection.execute(
            "SELECT recipe_bytes,recipe_valid,recipe_snapshot_id,recipe_source "
            "FROM source_generations WHERE generation_id='unverifiable-generation'"
        ).fetchone()
        document = connection.execute(
            "SELECT recipe_zlib,recipe_bytes,recipe_sha256 "
            "FROM source_generation_documents WHERE generation_id='unverifiable-generation'"
        ).fetchone()
        assert metadata is not None and document is not None
        raw = b'{"context":'
        assert tuple(metadata) == (len(raw), 0, None, None)
        assert bytes(document["recipe_sha256"]) == hashlib.sha256(raw).digest()
        assert (
            BENCHMARK.decode_document(
                bytes(document["recipe_zlib"]),
                expected_bytes=document["recipe_bytes"],
                max_bytes=document["recipe_bytes"],
            )
            == raw
        )


@pytest.mark.parametrize("destination", ["scratch", "output", "receipt"])
def test_destinations_must_be_new_and_outside_source(tmp_path: Path, destination: str) -> None:
    source, _ = make_schema29(tmp_path / "input")
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


def test_live_source_and_cli_bad_input_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = make_schema29(tmp_path / "input")
    monkeypatch.setattr(BENCHMARK, "PROTECTED_ROOTS", (str(tmp_path / "input"),))
    with pytest.raises(ValueError, match="live state"):
        run_benchmark(source, tmp_path / "live-result")

    monkeypatch.setattr(BENCHMARK, "PROTECTED_ROOTS", ("/var/lib/swingset",))
    process = subprocess.run(
        [sys.executable, str(PATH), "--source", str(tmp_path / "missing")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode != 0
    assert "--scratch" in process.stderr
    assert "--output" in process.stderr
    assert "--receipt" in process.stderr


@pytest.mark.parametrize(
    ("suffix", "message"),
    [("-wal", "write-ahead log"), ("-shm", "shared-memory"), ("-journal", "rollback journal")],
)
def test_source_sidecars_are_refused_before_outputs(
    tmp_path: Path, suffix: str, message: str
) -> None:
    source, _ = make_schema29(tmp_path / "input")
    Path(str(source / "state.sqlite") + suffix).write_bytes(b"pending")

    with pytest.raises(ValueError, match=message):
        run_benchmark(source, tmp_path / "result")

    assert not (tmp_path / "result/scratch").exists()
