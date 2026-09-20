"""Focused tests for the bounded source-generation JSON profiler."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import zlib
from pathlib import Path

import pytest

MODULE_PATH = Path("journal/tools/runtime/profile_source_generation_json.py")
SPEC = importlib.util.spec_from_file_location("profile_source_generation_json", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)

SECRETS = (
    "private_key_name_9",
    "https://private.example/person/47",
    "person-identity-47",
    "sha256-secret-digest-47",
    "generation-secret-47",
    "café-秘密",
)


def compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    connection = sqlite3.connect(directory / "state.sqlite")
    connection.executescript(
        """
        PRAGMA user_version=29;
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version','29');
        CREATE TABLE source_generations(
            generation_id TEXT PRIMARY KEY,
            page_kind TEXT NOT NULL,
            state TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            recipe_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            report_json TEXT NOT NULL
        );
        CREATE INDEX source_generations_state_idx
        ON source_generations(state,page_kind);
        """
    )
    manifest = compact({SECRETS[0]: SECRETS[3]})
    accepted_item = ["pipeline", "recipe/runtime", SECRETS[3]]
    recipe = {
        "accepted_inputs": [accepted_item],
        "archive_url": SECRETS[1],
    }
    result = compact({"identity": SECRETS[2], "label": SECRETS[5]})
    report = compact(
        {
            "fields": [{"path": SECRETS[2], "disposition": "used"}],
            "guards": [{"code": "private", "passed": True, "detail": SECRETS[1]}],
        }
    )
    rows = (
        (SECRETS[4], "fixture.kind", "accepted", manifest, compact(recipe), result, report),
        (
            "generation-secret-48",
            "fixture.other",
            "needs_review",
            manifest,
            json.dumps(recipe, ensure_ascii=False),
            result,
            compact({"fields": "wrong-type"}),
        ),
    )
    connection.executemany("INSERT INTO source_generations VALUES (?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    return directory


def test_profile_reports_only_fixed_aggregate_structure(state: Path) -> None:
    report = PROFILE.profile(state)
    manifest_body = compact({SECRETS[0]: SECRETS[3]}).encode()
    recipe_value = {
        "accepted_inputs": [["pipeline", "recipe/runtime", SECRETS[3]]],
        "archive_url": SECRETS[1],
    }
    recipe_bodies = (compact(recipe_value).encode(), json.dumps(recipe_value).encode())
    result_body = compact({"identity": SECRETS[2], "label": SECRETS[5]}).encode()
    report_bodies = (
        compact(
            {
                "fields": [{"path": SECRETS[2], "disposition": "used"}],
                "guards": [{"code": "private", "passed": True, "detail": SECRETS[1]}],
            }
        ).encode(),
        compact({"fields": "wrong-type"}).encode(),
    )

    assert report["preflight"]["rows"] == 2
    manifest = report["documents"]["manifest_json"]
    assert manifest["rows"] == 2
    assert manifest["distinct_documents"] == 1
    assert manifest["unique_logical_utf8_bytes"] == len(manifest_body)
    assert manifest["duplicate_logical_utf8_bytes"] == manifest["unique_logical_utf8_bytes"]
    assert manifest["canonical_byte_identical_rows"] == 2
    assert manifest["zlib6_bytes"] == 2 * len(zlib.compress(manifest_body, level=6))
    assert manifest["unique_zlib6_bytes"] == len(zlib.compress(manifest_body, level=6))

    recipe = report["documents"]["recipe_json"]
    assert recipe["distinct_documents"] == 2
    assert recipe["canonical_byte_identical_rows"] == 1
    assert recipe["zlib6_bytes"] == sum(len(zlib.compress(body, level=6)) for body in recipe_bodies)
    assert recipe["unique_zlib6_bytes"] == recipe["zlib6_bytes"]

    result = report["documents"]["result_json"]
    assert result["logical_utf8_bytes"] == 2 * len(result_body)
    assert result["logical_utf8_bytes"] > 2 * len(result_body.decode())

    paths = {item["path"]: item for item in report["structural_paths"]}
    accepted = paths["recipe_json.accepted_inputs[]"]
    item = PROFILE.canonical(["pipeline", "recipe/runtime", SECRETS[3]])
    assert accepted["rows_with_array"] == 2
    assert accepted["occurrences"] == 2
    assert accepted["distinct_items"] == 1
    assert accepted["logical_utf8_bytes"] == 2 * len(item)
    assert accepted["unique_logical_utf8_bytes"] == len(item)
    assert accepted["zlib6_bytes"] == 2 * len(zlib.compress(item, level=6))
    fields = paths["report_json.fields[]"]
    assert (fields["rows_with_array"], fields["rows_wrong_type"]) == (1, 1)
    guards = paths["report_json.guards[]"]
    assert (guards["rows_with_array"], guards["rows_missing"]) == (1, 1)

    cohorts = {(item["page_kind"], item["state"]): item for item in report["cohorts"]}
    assert set(cohorts) == {("fixture.kind", "accepted"), ("fixture.other", "needs_review")}
    expected_rows = {
        ("fixture.kind", "accepted"): {
            "manifest_json": manifest_body,
            "recipe_json": recipe_bodies[0],
            "result_json": result_body,
            "report_json": report_bodies[0],
        },
        ("fixture.other", "needs_review"): {
            "manifest_json": manifest_body,
            "recipe_json": recipe_bodies[1],
            "result_json": result_body,
            "report_json": report_bodies[1],
        },
    }
    for key, bodies in expected_rows.items():
        assert cohorts[key]["rows"] == 1
        for column, body in bodies.items():
            assert cohorts[key]["documents"][column] == {
                "logical_utf8_bytes": len(body),
                "zlib6_bytes": len(zlib.compress(body, level=6)),
            }
    assert report["dbstat"]["source_generations_table_bytes"] > 0
    assert report["dbstat"]["source_generations_index_bytes"] > 0

    serialized = json.dumps(report, sort_keys=True)
    for secret in SECRETS:
        assert secret not in serialized
    assert "generation-secret-48" not in serialized


def test_profile_is_immutable_and_records_matching_signatures(state: Path) -> None:
    database = state / "state.sqlite"
    before = database.read_bytes()

    report = PROFILE.profile(state)

    assert database.read_bytes() == before
    assert report["source"]["signature_before"] == report["source"]["signature_after"]
    assert report["source"]["unchanged"] is True
    assert report["source"]["database_path"] == str(database.resolve())
    assert report["source"]["root_path"] == str(state.resolve())
    assert report["source"]["kind"] == "state"
    assert report["source"]["schema_version"] == 29
    assert report["source"]["user_version"] == 29
    assert report["source"]["sidecars_before"] == report["source"]["sidecars_after"]
    assert report["source"]["sidecars_after"] == {
        "journal_bytes": 0,
        "journal_exists": False,
        "shm_bytes": 0,
        "shm_exists": False,
        "wal_bytes": 0,
        "wal_exists": False,
    }
    assert report["tool_sha256"] == hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest()
    assert report["runtime"]["python_version"]
    assert report["runtime"]["sqlite_version"] == sqlite3.sqlite_version
    assert report["runtime"]["hostname"]
    assert report["runtime"]["platform"]
    assert report["runtime"]["zlib_runtime_version"] == zlib.ZLIB_RUNTIME_VERSION
    assert "independent zlib stream" in report["compression"]["framing"]


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"max_rows": 1}, "row cap exceeded"),
        ({"max_bytes": 1}, "byte cap exceeded"),
        ({"max_row_bytes": 1}, "row-byte cap exceeded"),
    ],
)
def test_profile_refuses_caps_before_reading_rows(
    state: Path, limits: dict[str, int], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def forbidden(_connection):
        nonlocal called
        called = True
        raise AssertionError("row stream must not start")

    monkeypatch.setattr(PROFILE, "_documents", forbidden)
    with pytest.raises(ValueError, match=message):
        PROFILE.profile(state, **limits)
    assert called is False


def test_run_writes_new_report_and_small_receipt_outside_source(
    state: Path, tmp_path: Path
) -> None:
    output = tmp_path / "results" / "report.json"
    receipt = tmp_path / "results" / "receipt.json"
    command = ["profile_source_generation_json.py", "--state", str(state)]

    PROFILE.run(state, output=output, receipt=receipt, command=command)

    full = json.loads(output.read_text())
    small = json.loads(receipt.read_text())
    assert full["format"] == PROFILE.REPORT_FORMAT
    assert small["format"] == PROFILE.RECEIPT_FORMAT
    assert small["cohort_count"] == 2
    assert "cohorts" not in small
    assert full["invocation"]["command"] == command
    assert small["invocation"]["command"] == command
    assert small["tool_sha256"] == full["tool_sha256"]
    assert small["compression"] == full["compression"]
    assert small["runtime"] == full["runtime"]
    assert receipt.stat().st_size < 64 * 1024
    serialized = output.read_text() + receipt.read_text()
    assert all(secret not in serialized for secret in SECRETS)

    with pytest.raises(ValueError, match="must be new"):
        PROFILE.run(state, output=output, receipt=tmp_path / "another.json")
    with pytest.raises(ValueError, match="outside the measured state"):
        PROFILE.run(
            state,
            output=state / "report.json",
            receipt=tmp_path / "unused-receipt.json",
        )


def test_live_root_and_sqlite_sidecars_are_hard_fences(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(PROFILE, "PROTECTED_ROOTS", (str(tmp_path / "live"),))
    live = tmp_path / "live" / "copy"
    live.mkdir(parents=True)
    (live / "state.sqlite").write_bytes((state / "state.sqlite").read_bytes())
    with pytest.raises(ValueError, match="live state"):
        PROFILE.profile(live)

    monkeypatch.setattr(PROFILE, "PROTECTED_ROOTS", ("/var/lib/swingset",))
    wal = Path(str(state / "state.sqlite") + "-wal")
    wal.write_bytes(b"pending")
    with pytest.raises(ValueError, match="write-ahead log"):
        PROFILE.profile(state)
    wal.unlink()
    shm = Path(str(state / "state.sqlite") + "-shm")
    shm.touch()
    with pytest.raises(ValueError, match="shared-memory"):
        PROFILE.profile(state)
    shm.unlink()
    journal = Path(str(state / "state.sqlite") + "-journal")
    journal.touch()
    with pytest.raises(ValueError, match="rollback journal"):
        PROFILE.profile(state)


def test_writer_starting_during_profile_is_rejected_after_close(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = state / "state.sqlite"
    setup = sqlite3.connect(database)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    setup.close()
    assert not Path(str(database) + "-shm").exists()

    writers: list[sqlite3.Connection] = []
    original = PROFILE._dbstat

    def start_writer(connection, path):
        result = original(connection, path)
        writer = sqlite3.connect(path)
        writer.execute(
            "INSERT INTO source_generations VALUES (?,?,?,?,?,?,?)",
            (
                "late-generation",
                "fixture.kind",
                "accepted",
                "{}",
                '{"accepted_inputs":[]}',
                "{}",
                '{"fields":[],"guards":[]}',
            ),
        )
        writer.commit()
        writers.append(writer)
        return result

    monkeypatch.setattr(PROFILE, "_dbstat", start_writer)
    try:
        with pytest.raises(ValueError, match="write-ahead log after profiling"):
            PROFILE.profile(state)
        assert Path(str(database) + "-wal").stat().st_size > 0
        assert Path(str(database) + "-shm").exists()
    finally:
        for writer in writers:
            writer.close()


def test_main_database_mutation_during_profile_is_rejected(
    state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PROFILE._dbstat

    def mutate_after_measurement(connection, path):
        result = original(connection, path)
        writer = sqlite3.connect(path)
        writer.execute(
            "UPDATE source_generations SET page_kind='fixture.KIND' WHERE generation_id=?",
            (SECRETS[4],),
        )
        writer.commit()
        writer.close()
        return result

    monkeypatch.setattr(PROFILE, "_dbstat", mutate_after_measurement)
    with pytest.raises(ValueError, match="database changed while it was read"):
        PROFILE.profile(state)


def test_distinct_documents_are_raw_bytes_even_with_text_collation(tmp_path: Path) -> None:
    state = tmp_path / "collated"
    state.mkdir()
    connection = sqlite3.connect(state / "state.sqlite")
    connection.execute(
        "CREATE TABLE source_generations("
        "generation_id TEXT PRIMARY KEY,page_kind TEXT,state TEXT,"
        "manifest_json TEXT COLLATE NOCASE,recipe_json TEXT COLLATE NOCASE,"
        "result_json TEXT COLLATE NOCASE,report_json TEXT COLLATE NOCASE)"
    )
    upper = '{"value":"A"}'
    lower = '{"value":"a"}'
    for generation, body in (("one", upper), ("two", lower)):
        connection.execute(
            "INSERT INTO source_generations VALUES (?,?,?,?,?,?,?)",
            (generation, "fixture.kind", "accepted", body, "{}", "{}", "{}"),
        )
    connection.commit()
    connection.close()

    report = PROFILE.profile(state)

    manifest = report["documents"]["manifest_json"]
    assert manifest["distinct_documents"] == 2
    assert manifest["unique_logical_utf8_bytes"] == len(upper.encode()) + len(lower.encode())
    assert manifest["unique_zlib6_bytes"] == len(zlib.compress(upper.encode(), level=6)) + len(
        zlib.compress(lower.encode(), level=6)
    )


def test_invalid_json_error_does_not_echo_the_source_value(state: Path) -> None:
    database = state / "state.sqlite"
    connection = sqlite3.connect(database)
    invalid = "private malformed body ["
    connection.execute(
        "UPDATE source_generations SET report_json=? WHERE generation_id=?",
        (invalid, SECRETS[4]),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="invalid JSON in report_json at row 1") as failure:
        PROFILE.profile(state)
    assert invalid not in str(failure.value)
