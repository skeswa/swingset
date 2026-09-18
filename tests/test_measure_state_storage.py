"""Tests for the step 1 state storage measurement tool."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint
from swingset.state.db import SCHEMA_VERSION, open_database

MODULE_PATH = Path("journal/tools/runtime/measure_state_storage.py")
SPEC = importlib.util.spec_from_file_location("measure_state_storage", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MEASURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEASURE)

NOW = "2026-09-18T00:00:00+00:00"
# Two payload bodies shared across six rows make the distinct-to-total ratio 2/6.
SHARED = ('{"a":1}', '{"b":2}')


def _rows_kind(connection) -> str:
    """derivation_rows is a table before the payload split and a view after it."""
    return str(
        connection.execute(
            "SELECT type FROM sqlite_master WHERE name='derivation_rows'"
        ).fetchone()[0]
    )


def _insert_rows(connection, generation, payloads):
    """Write output rows in whichever shape the current schema stores them."""
    for ordinal, payload in enumerate(payloads):
        key = f"{generation}-{ordinal}"
        if _rows_kind(connection) == "table":
            connection.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (generation, ordinal, "events", key, payload),
            )
            continue
        digest = hashlib.sha256(payload.encode()).hexdigest()
        connection.execute(
            "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (digest, payload)
        )
        connection.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
            (generation, ordinal, "events", key, digest),
        )


def _seed(connection, generation, stage, unit_kind, unit_id, payloads):
    connection.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES (?,?,?,?)",
        (stage, unit_kind, unit_id, NOW),
    )
    connection.execute(
        "INSERT OR IGNORE INTO derivation_dependency_sets VALUES (?,?)", ("ds_empty", "[]")
    )
    digest = hashlib.sha256(("|".join(payloads)).encode()).hexdigest()
    connection.execute(
        "INSERT INTO derivation_generations(generation_id,stage,unit_kind,unit_id,"
        "input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,"
        "output_digest,row_count,created_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            generation,
            stage,
            unit_kind,
            unit_id,
            "fp_" + generation,
            "{}",
            "ds_empty",
            None,
            digest,
            len(payloads),
            NOW,
            "run_measure",
        ),
    )
    _insert_rows(connection, generation, payloads)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    """A current-schema state whose generations share payload bodies."""
    directory = tmp_path / "state"
    with open_database(directory) as database:
        connection = database.connection
        with database.transaction():
            connection.execute(
                "INSERT INTO runs(run_id,started_at,dry_run) VALUES (?,?,0)",
                ("run_measure", NOW),
            )
            _seed(connection, "dg_one", "project", "calendar", "all", SHARED)
            _seed(connection, "dg_two", "project", "calendar", "all", SHARED)
            _seed(connection, "dg_three", "link", "event", "e1", SHARED)
    return directory


@pytest.fixture
def held_checkpoint(state: Path, tmp_path: Path) -> Path:
    """A copy of a sealed checkpoint, the shape an operator actually measures."""
    candidate = state / "candidates" / "cand_base"
    candidate.mkdir(parents=True)
    (candidate / "BUILT").write_text("{}")
    (candidate / "PUBLISHED").write_text('{"commit":"abc"}')
    (state / "baseline").symlink_to(Path("candidates/cand_base"))
    connection = sqlite3.connect(state / "state.sqlite")
    try:
        create_checkpoint(
            state,
            connection,
            tmp_path / "held",
            schema_version=SCHEMA_VERSION,
            versions={"test": "1"},
            input_bundle_hash=None,
        )
    finally:
        connection.close()
    verify_checkpoint(tmp_path / "held", maximum_schema_version=SCHEMA_VERSION)
    copy = tmp_path / "copy"
    shutil.copytree(tmp_path / "held", copy, symlinks=True)
    return copy


def test_report_counts_generations_rows_and_shared_payloads(state: Path) -> None:
    report = MEASURE.measure(state)

    scopes = {
        (item["stage"], item["unit_kind"]): item for item in report["derivations"]["by_scope"]
    }
    project = scopes[("project", "calendar")]
    assert project["generations"] == 2
    assert project["rows"] == 4
    assert project["declared_row_count"] == 4
    assert project["distinct_payload_sha256"] == 2
    assert project["distinct_to_total_rows"] == 0.5
    assert project["payload_bytes"] == 2 * sum(len(body) for body in SHARED)

    link = scopes[("link", "event")]
    assert (link["generations"], link["rows"], link["distinct_payload_sha256"]) == (1, 2, 2)

    totals = report["derivations"]["totals"]
    assert totals["generations"] == 3
    assert totals["rows"] == 6
    # Each scope keeps its own digest set, so the total is an upper bound: 2 + 2.
    assert totals["distinct_payload_sha256_upper_bound"] == 4
    assert report["derivations"]["rows_without_a_generation"] == 0


def test_profile_reads_the_inline_row_table_a_held_backup_still_has() -> None:
    """A held schema 29 backup stores payloads inline; step 1 measures that shape."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE derivation_generations (generation_id TEXT PRIMARY KEY, stage TEXT, "
        "unit_kind TEXT, unit_id TEXT, row_count INTEGER)"
    )
    connection.execute(
        "CREATE TABLE derivation_rows (generation_id TEXT, ordinal INTEGER, table_name TEXT, "
        "record_key TEXT, payload_json TEXT)"
    )
    for generation in ("dg_one", "dg_two"):
        connection.execute(
            "INSERT INTO derivation_generations VALUES (?,?,?,?,?)",
            (generation, "project", "calendar", "all", len(SHARED)),
        )
        for ordinal, payload in enumerate(SHARED):
            connection.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (generation, ordinal, "events", f"{generation}-{ordinal}", payload),
            )

    profile = MEASURE.derivation_profile(connection)

    assert profile["derivation_rows_kind"] == "table"
    scope = profile["by_scope"][0]
    assert (scope["generations"], scope["rows"], scope["distinct_payload_sha256"]) == (2, 4, 2)
    assert scope["distinct_to_total_rows"] == 0.5
    assert profile["rows_without_a_generation"] == 0


def test_profile_reports_nothing_when_a_copy_predates_derivations() -> None:
    connection = sqlite3.connect(":memory:")

    profile = MEASURE.derivation_profile(connection)

    assert profile["derivation_rows_kind"] is None
    assert profile["by_scope"] == []
    assert profile["totals"]["rows"] == 0


def test_source_generation_json_profile_reports_skewed_logical_utf8_lengths() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE source_generations("
        "manifest_json TEXT,recipe_json TEXT,result_json TEXT,report_json TEXT)"
    )
    manifests = ("x", "é", "12345", "y" * 100, "z" * 1000)
    for ordinal, manifest in enumerate(manifests):
        connection.execute(
            "INSERT INTO source_generations VALUES (?,?,?,?)",
            (manifest, "{}", "r" * ordinal, "report"),
        )
    objects = MEASURE.storage_objects(connection)

    profile = MEASURE.source_generation_json_profile(connection, objects)

    assert profile["present"] is True
    assert profile["measurement"] == "logical UTF-8 bytes; not physical SQLite page bytes"
    assert profile["physical_table_bytes"] > 0
    columns = {item["name"]: item for item in profile["columns"]}
    manifest = columns["manifest_json"]
    # The second value is two UTF-8 bytes even though Python and SQLite both
    # expose it as one character.
    assert manifest["logical_utf8_bytes"] == 1 + 2 + 5 + 100 + 1000
    assert manifest["distinct_byte_lengths"] == 5
    assert manifest["min_logical_utf8_bytes"] == 1
    assert manifest["p50_logical_utf8_bytes"] == 5
    assert manifest["p90_logical_utf8_bytes"] == 1000
    assert manifest["p99_logical_utf8_bytes"] == 1000
    assert manifest["max_logical_utf8_bytes"] == 1000
    assert columns["recipe_json"]["distinct_byte_lengths"] == 1
    assert columns["result_json"]["min_logical_utf8_bytes"] == 0
    # Only sizes and distribution cross the connection boundary; no source body
    # or digest of a source body appears in the report.
    rendered = json.dumps(profile, sort_keys=True)
    assert "z" * 1000 not in rendered
    assert "y" * 100 not in rendered


def test_costly_index_profiles_include_implicit_and_expression_definitions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "indexes.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE derivation_rows(
            generation_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            record_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(generation_id,ordinal),
            UNIQUE(generation_id,table_name,record_key)
        );
        CREATE INDEX derivation_rows_expression
        ON derivation_rows(lower(record_key), ordinal DESC)
        WHERE table_name='events';
        INSERT INTO derivation_rows VALUES ('g',0,'events','MixedCase','{}');
        """
    )
    connection.commit()
    objects = MEASURE.storage_objects(connection)

    profiles = {item["table"]: item for item in MEASURE.index_profiles(connection, objects)}

    rows = profiles["derivation_rows"]
    assert rows["present"] is True
    assert rows["kind"] == "table"
    assert rows["physical_index_bytes"] == sum(
        item["bytes"]
        for item in objects
        if item["kind"] == "index" and item["table"] == "derivation_rows"
    )
    indexes = {item["name"]: item for item in rows["indexes"]}
    expression = indexes["derivation_rows_expression"]
    assert expression["physical_bytes"] > 0
    assert expression["unique"] is False
    assert expression["partial"] is True
    assert expression["origin"] == "c"
    assert expression["definition_source"] == "sqlite_schema.sql"
    assert "lower(record_key)" in expression["definition_sql"]
    key_columns = [item for item in expression["columns"] if item["key"]]
    assert key_columns == [
        {
            "sequence": 0,
            "column_id": -2,
            "name": None,
            "kind": "expression",
            "descending": False,
            "collation": "BINARY",
            "key": True,
        },
        {
            "sequence": 1,
            "column_id": 1,
            "name": "ordinal",
            "kind": "column",
            "descending": True,
            "collation": "BINARY",
            "key": True,
        },
    ]
    implicit = [item for item in rows["indexes"] if item["origin"] in {"pk", "u"}]
    assert len(implicit) == 2
    assert all(item["definition_sql"] is None for item in implicit)
    assert all(item["definition_source"] == "implicit table constraint" for item in implicit)


def test_storage_attribution_profiles_report_missing_tables_without_error() -> None:
    connection = sqlite3.connect(":memory:")
    objects = MEASURE.storage_objects(connection)

    source_json = MEASURE.source_generation_json_profile(connection, objects)
    indexes = MEASURE.index_profiles(connection, objects)

    assert source_json == {
        "table": "source_generations",
        "present": False,
        "measurement": "logical UTF-8 bytes; not physical SQLite page bytes",
        "physical_table_bytes": 0,
        "physical_index_bytes": 0,
        "rows": 0,
        "logical_utf8_bytes": 0,
        "columns": [
            {"name": name, "present": False} for name in MEASURE.SOURCE_GENERATION_JSON_COLUMNS
        ],
    }
    assert [item["table"] for item in indexes] == list(MEASURE.COSTLY_INDEX_TABLES)
    assert all(item["present"] is False and item["indexes"] == [] for item in indexes)


def test_every_table_and_index_is_accounted_for(state: Path) -> None:
    report = MEASURE.measure(state)

    accounting = report["accounting"]
    assert accounting["accounted"] is True
    assert accounting["unaccounted_pages"] == 0
    assert accounting["used_bytes"] + accounting["freelist_bytes"] == accounting["page_count_bytes"]
    kinds = {item["kind"] for item in report["storage"]}
    assert kinds <= {"table", "index", "schema"}
    assert "unknown" not in kinds
    names = {item["name"] for item in report["storage"]}
    assert "derivation_generations" in names
    assert any(item["kind"] == "index" for item in report["storage"])


def test_accounting_closes_when_a_drop_leaves_pages_on_the_free_list(tmp_path: Path) -> None:
    """Step 2 drops a table, so free-list bytes are the number that must add up."""
    database = tmp_path / "dropped.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE bulk(id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany("INSERT INTO bulk(body) VALUES (?)", [("x" * 400,) for _ in range(2000)])
    connection.execute("CREATE TABLE keeper(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.execute("DROP TABLE bulk")
    connection.commit()
    connection.close()

    report = MEASURE.measure(database)

    accounting = report["accounting"]
    assert accounting["freelist_count"] > 100
    assert accounting["freelist_bytes"] == accounting["freelist_count"] * accounting["page_size"]
    assert accounting["accounted"] is True
    assert accounting["used_bytes"] + accounting["freelist_bytes"] == accounting["page_count_bytes"]
    # The file keeps the freed pages: bytes in use are far below the file size.
    assert accounting["used_bytes"] < report["database"]["file_bytes"]


def test_history_and_derivation_tables_report_rows_and_bytes(state: Path) -> None:
    report = MEASURE.measure(state)

    history = {item["name"]: item for item in report["history_tables"]}
    for name in ("control_events", "work_attempts", "runs", "findings", "finding_support"):
        assert history[name]["present"] is True
        assert history[name]["kind"] == "table"
        assert history[name]["total_bytes"] >= 0
    assert history["runs"]["rows"] == 1
    assert any(name.startswith("identity_") for name in history)
    assert history["identity_decisions"]["present"] is True

    derivations = {item["name"]: item for item in report["derivation_tables"]}
    assert derivations["derivation_generations"]["rows"] == 3
    assert derivations["derivation_generations"]["kind"] == "table"
    assert derivations["derivation_generations"]["table_bytes"] > 0
    assert derivations["derivation_generations"]["index_bytes"] > 0
    # Whichever shape stores the rows, the tool counts and sizes it.
    holder = (
        "derivation_rows"
        if report["derivations"]["derivation_rows_kind"] == "table"
        else ("derivation_row_refs")
    )
    assert derivations[holder]["present"] is True
    assert derivations[holder]["rows"] == 6
    assert derivations[holder]["table_bytes"] > 0
    # A table that became a view is present with zero pages of its own, never absent:
    # a before-and-after comparison has to tell "gone" from "now a view".
    rows_entry = derivations["derivation_rows"]
    assert rows_entry["kind"] == report["derivations"]["derivation_rows_kind"]
    assert rows_entry["present"] is True
    if rows_entry["kind"] == "view":
        assert rows_entry["table_bytes"] == 0
        assert rows_entry["rows"] == 6
    # A name the schema does not have at all is reported absent, with no counts.
    # Step 2 renames derivation_rows to derivation_rows_legacy and then drops it,
    # so "dropped" and "never existed" have to look different from "empty".
    legacy = derivations["derivation_rows_legacy"]
    assert legacy == {"name": "derivation_rows_legacy", "present": False, "kind": None}
    assert derivations["derivation_input_versions"]["present"] is True

    source_json = report["source_generation_json"]
    assert source_json["present"] is True
    assert source_json["rows"] == 0
    assert source_json["logical_utf8_bytes"] == 0
    assert all(item["present"] is True for item in source_json["columns"])

    costly = {item["table"]: item for item in report["costly_index_profiles"]}
    # Current schema 32 exposes the stable name as a view and stores the rows in
    # the two split tables. The schema-29 inline-table shape is covered above.
    assert costly["derivation_rows"]["kind"] == "view"
    assert costly["derivation_rows"]["indexes"] == []
    assert costly["derivation_row_refs"]["kind"] == "table"
    assert costly["derivation_row_refs"]["indexes"]
    assert costly["derivation_payloads"]["kind"] == "table"
    assert costly["source_generations"]["kind"] == "table"


def test_measurement_never_writes_to_the_database(state: Path) -> None:
    database = state / "state.sqlite"
    before = database.read_bytes()

    report = MEASURE.measure(state)

    assert report["database_unchanged"] is True
    assert report["unchanged_check"] == "size-mtime-inode"
    assert database.read_bytes() == before
    # A read-only reader must not grow the write-ahead log or the shared-memory file.
    assert MEASURE.sidecar_bytes(database) == {"wal_bytes": 0, "shm_bytes": 0}


def test_the_database_is_read_whole_only_when_hashing_is_asked_for(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hashing a multi-gigabyte copy is the opt-in cost, so it happens only then."""
    calls: list[Path] = []
    original = MEASURE.sha
    monkeypatch.setattr(MEASURE, "sha", lambda path: calls.append(path) or original(path))

    MEASURE.run(state, output=None, receipt=None, hash_database=False, scratch=None)
    assert calls == []

    receipt = tmp_path / "receipt.json"
    MEASURE.run(state, output=None, receipt=receipt, hash_database=True, scratch=None)
    # One read before and one after; the receipt reuses that digest instead of a third.
    assert len(calls) == 2
    assert (
        json.loads(receipt.read_text())["inputs"]["database_sha256"]
        == hashlib.sha256((state / "state.sqlite").read_bytes()).hexdigest()
    )


def test_run_writes_report_and_receipt(state: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "report.json"
    receipt = tmp_path / "out" / "receipt.json"

    MEASURE.run(
        state,
        output=output,
        receipt=receipt,
        hash_database=True,
        scratch=None,
        command=["measure_state_storage.py", "--state", str(state)],
        code_revision="abc123",
    )

    written = json.loads(output.read_text())
    assert written["format"] == MEASURE.REPORT_FORMAT
    compact = json.loads(receipt.read_text())
    assert compact["format"] == MEASURE.RECEIPT_FORMAT
    assert (
        compact["inputs"]["database_sha256"]
        == hashlib.sha256((state / "state.sqlite").read_bytes()).hexdigest()
    )
    # A receipt read months later has to name what was measured and what produced it.
    assert compact["inputs"]["code_revision"] == "abc123"
    assert compact["inputs"]["command"][0].endswith("measure_state_storage.py")
    assert compact["inputs"]["source_kind"] == "state"
    assert compact["inputs"]["source_checkpoint_manifest_sha256"] is None
    assert compact["limits"] == list(MEASURE.LIMITS)
    assert compact["findings"]
    assert compact["results"]["backup_timing"] is None
    assert compact["results"]["source_generation_json"]["measurement"].startswith(
        "logical UTF-8 bytes"
    )
    assert compact["results"]["costly_index_profiles"]
    # Compact receipts retain index costs and key terms, while exact SQL and
    # auxiliary rowid terms remain in the full report.
    assert all(
        "definition_sql" not in index
        for table in compact["results"]["costly_index_profiles"]
        for index in table["indexes"]
    )
    assert len(compact["results"]["largest_objects"]) <= MEASURE.RECEIPT_TOP_OBJECTS
    assert receipt.stat().st_size < 64 * 1024


def test_receipt_names_the_held_checkpoint_it_measured(
    held_checkpoint: Path, tmp_path: Path
) -> None:
    receipt = tmp_path / "receipt.json"

    MEASURE.run(held_checkpoint, output=None, receipt=receipt, hash_database=False, scratch=None)

    inputs = json.loads(receipt.read_text())["inputs"]
    assert inputs["source_kind"] == "checkpoint"
    assert (
        inputs["source_checkpoint_manifest_sha256"]
        == hashlib.sha256((held_checkpoint / "checkpoint.json").read_bytes()).hexdigest()
    )


def test_receipt_omits_the_database_hash_unless_asked(state: Path, tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"

    MEASURE.run(state, output=None, receipt=receipt, hash_database=False, scratch=None)

    assert json.loads(receipt.read_text())["inputs"]["database_sha256"] is None


def test_time_backup_creates_verifies_and_restores_into_scratch(
    state: Path, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratch"
    before = (state / "state.sqlite").read_bytes()

    report = MEASURE.run(state, output=None, receipt=None, hash_database=False, scratch=scratch)

    timing = report["backup_timing"]
    assert timing["order"] == "backup-then-restore"
    assert timing["checkpoint_file_count"] >= 1
    assert timing["checkpoint_bytes"] > 0
    assert timing["restored_bytes"] > 0
    assert timing["total_seconds"] >= 0
    assert timing["network_requests"] == 0
    assert (scratch / "checkpoint" / "checkpoint.json").is_file()
    assert (scratch / "restored" / "state.sqlite").is_file()
    assert (state / "state.sqlite").read_bytes() == before
    assert any("File size" in note for note in report["findings"])
    assert any("Backup timing" in note for note in report["findings"])


def test_time_backup_restores_a_copied_held_checkpoint_before_backing_it_up(
    held_checkpoint: Path, tmp_path: Path
) -> None:
    """The documented target is a copied checkpoint, which is not a state directory.

    Backing one up directly copies its own checkpoint.json into the new
    checkpoint and then overwrites it, so verification fails. Restoring first
    also puts back the baseline symlink, without which every candidate file
    would be left out of the timed backup.
    """
    scratch = tmp_path / "scratch"

    report = MEASURE.run(
        held_checkpoint, output=None, receipt=None, hash_database=False, scratch=scratch
    )

    timing = report["backup_timing"]
    assert "error" not in timing
    assert timing["order"] == "restore-then-backup"
    assert timing["source_kind"] == "checkpoint"
    assert timing["restore_seconds"] >= 0
    assert timing["create_seconds"] >= 0
    assert timing["verify_seconds"] >= 0
    assert (scratch / "restored" / "baseline").is_symlink()
    manifest = json.loads((scratch / "checkpoint" / "checkpoint.json").read_text())
    assert "candidates/cand_base/BUILT" in manifest["files"]
    assert "checkpoint.json" not in manifest["files"]
    verify_checkpoint(scratch / "checkpoint", maximum_schema_version=SCHEMA_VERSION)


def test_time_backup_refuses_without_room_for_the_whole_state_tree(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run writes two copies of the tree, not two copies of the .sqlite file."""
    (state / "extracts").mkdir()
    (state / "extracts" / "bulk").write_bytes(b"\0" * (4 * 1024 * 1024))
    scratch = tmp_path / "scratch"
    file_bytes = (state / "state.sqlite").stat().st_size
    tree_bytes = MEASURE._tree_bytes(state)
    assert tree_bytes > file_bytes

    class Usage:
        # Enough for twice the database file, nowhere near twice the tree.
        free = 2 * file_bytes

    monkeypatch.setattr(MEASURE.shutil, "disk_usage", lambda _path: Usage)
    with pytest.raises(ValueError, match="free disk"):
        MEASURE.time_backup(state, scratch)
    assert not scratch.exists()


def test_time_backup_refuses_scratch_inside_the_measured_state(state: Path) -> None:
    with pytest.raises(ValueError, match="outside the measured state"):
        MEASURE.time_backup(state, state / "scratch")


def test_time_backup_refuses_a_scratch_directory_that_already_exists(
    state: Path, tmp_path: Path
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with pytest.raises(ValueError, match="must be new"):
        MEASURE.time_backup(state, scratch)


def test_a_timing_failure_still_leaves_the_measurement_written(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measuring a 5 GB copy is the expensive half; a timing failure must not lose it."""
    scratch = tmp_path / "scratch"
    output = tmp_path / "report.json"
    receipt = tmp_path / "receipt.json"

    class Usage:
        free = 1

    monkeypatch.setattr(MEASURE.shutil, "disk_usage", lambda _path: Usage)
    report = MEASURE.run(
        state, output=output, receipt=receipt, hash_database=False, scratch=scratch
    )

    assert "free disk" in report["backup_timing"]["error"]
    assert json.loads(output.read_text())["accounting"]["accounted"] is True
    compact = json.loads(receipt.read_text())
    assert compact["results"]["derivation_totals"]["rows"] == 6
    assert any("Backup timing failed" in note for note in compact["findings"])
    # A failed optional leg is a failed gate, and the gate is what the exit
    # status follows.
    assert compact["gates"]["backup_timing"] is False
    assert compact["gates"]["passed"] is False


def test_open_readonly_refuses_a_nonempty_write_ahead_log(state: Path) -> None:
    Path(str(state / "state.sqlite") + "-wal").write_bytes(b"pending")

    with pytest.raises(ValueError, match="write-ahead log") as error:
        MEASURE.open_readonly(state / "state.sqlite")
    # A plain PRAGMA wal_checkpoint reuses the file instead of truncating it, so
    # the message has to name the form that actually clears the refusal.
    assert "wal_checkpoint(TRUNCATE)" in str(error.value)


def test_open_readonly_refuses_a_database_a_connection_still_holds(state: Path) -> None:
    """A live directory whose log was truncated still has a shared-memory file."""
    database = state / "state.sqlite"
    Path(str(database) + "-shm").write_bytes(b"\0" * 32768)

    with pytest.raises(ValueError, match="shared-memory file"):
        MEASURE.open_readonly(database)


def test_open_readonly_refuses_an_empty_shared_memory_file(state: Path) -> None:
    """SQLite creates the file and sizes it afterwards, so a copy can catch it empty."""
    database = state / "state.sqlite"
    Path(str(database) + "-shm").write_bytes(b"")
    assert MEASURE.sidecar_bytes(database)["shm_bytes"] == 0

    with pytest.raises(ValueError, match="shared-memory file"):
        MEASURE.open_readonly(database)


def test_resolve_database_refuses_the_live_state_directory(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "/var/lib/swingset" in MEASURE.PROTECTED_ROOTS
    monkeypatch.setattr(MEASURE, "PROTECTED_ROOTS", (str(tmp_path),))

    with pytest.raises(ValueError, match="live state"):
        MEASURE.resolve_database(state)
    with pytest.raises(ValueError, match="live state"):
        MEASURE.measure(state)


def test_resolve_database_accepts_a_directory_or_a_file(state: Path) -> None:
    directory = MEASURE.resolve_database(state)
    explicit = MEASURE.resolve_database(state / "state.sqlite")

    assert directory == explicit
    with pytest.raises(ValueError, match="no state database"):
        MEASURE.resolve_database(state / "missing")


def test_resolve_destination_refuses_the_live_root_the_copy_and_an_existing_path(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every path the tool writes to is fenced, not only --scratch."""
    state_dir = MEASURE.resolve_database(state)[0]
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(MEASURE, "PROTECTED_ROOTS", (str(live),))

    with pytest.raises(ValueError, match="live state"):
        MEASURE.resolve_destination(live / "report.json", "--output", state_dir, must_be_new=True)
    with pytest.raises(ValueError, match="outside the measured state"):
        MEASURE.resolve_destination(state / "report.json", "--receipt", state_dir, must_be_new=True)
    with pytest.raises(ValueError, match="must not contain the measured state"):
        MEASURE.resolve_destination(state.parent, "scratch", state_dir, must_be_new=True)
    existing = tmp_path / "already.json"
    existing.write_text("{}")
    with pytest.raises(ValueError, match="must be new"):
        MEASURE.resolve_destination(existing, "--output", state_dir, must_be_new=True)


def test_run_refuses_to_write_inside_the_copy_it_measures(held_checkpoint: Path) -> None:
    """A stray file in a sealed copy stops it verifying against its own manifest."""
    with pytest.raises(ValueError, match="outside the measured state"):
        MEASURE.run(
            held_checkpoint,
            output=held_checkpoint / "report.json",
            receipt=None,
            hash_database=False,
            scratch=None,
        )

    assert not (held_checkpoint / "report.json").exists()
    verify_checkpoint(held_checkpoint, maximum_schema_version=SCHEMA_VERSION)


def test_run_refuses_a_scratch_tree_under_the_live_root(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--time-backup writes two whole copies; neither may land in production."""
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(MEASURE, "PROTECTED_ROOTS", (str(live),))

    with pytest.raises(ValueError, match="live state"):
        MEASURE.run(state, output=None, receipt=None, hash_database=False, scratch=live / "timing")
    with pytest.raises(ValueError, match="live state"):
        MEASURE.time_backup(state, live / "timing")

    assert not (live / "timing").exists()


def test_a_destination_is_checked_before_the_measurement_runs(
    state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a multi-gigabyte copy the measurement is the hours; do not spend them first."""
    output = tmp_path / "report.json"
    output.write_text("{}")
    measured: list[Path] = []
    monkeypatch.setattr(MEASURE, "measure", lambda target, **kwargs: measured.append(target) or {})

    with pytest.raises(ValueError, match="must be new"):
        MEASURE.run(state, output=output, receipt=None, hash_database=False, scratch=None)

    assert measured == []
    assert output.read_text() == "{}"


def _seed_missing_payload(connection, generation: str, resident: str, absent: str) -> None:
    """One generation declaring two rows, one of whose payload bytes are not local."""
    connection.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES ('project','calendar','gap',?)",
        (NOW,),
    )
    connection.execute("INSERT OR IGNORE INTO derivation_dependency_sets VALUES ('ds_empty','[]')")
    connection.execute(
        "INSERT INTO derivation_generations(generation_id,stage,unit_kind,unit_id,"
        "input_fingerprint,recipe_json,dependency_set_id,previous_generation_id,"
        "output_digest,row_count,created_at,run_id) "
        "VALUES (?,'project','calendar','gap',?,'{}','ds_empty',NULL,'digest',2,?,'run_measure')",
        (generation, "fp_" + generation, NOW),
    )
    for ordinal, payload in enumerate((resident, absent)):
        digest = hashlib.sha256(payload.encode()).hexdigest()
        if ordinal == 0:
            connection.execute(
                "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)", (digest, payload)
            )
        connection.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,'events',?,?)",
            (generation, ordinal, f"{generation}-{ordinal}", digest),
        )


@pytest.fixture
def state_with_an_archived_payload(state: Path) -> Path:
    """A generation whose payload bytes are gone, which is what step 4 leaves."""
    with open_database(state) as database:
        with database.transaction():
            _seed_missing_payload(database.connection, "dg_gap", '{"kept":1}', '{"gone":2}')
    return state


def test_declared_rows_are_compared_with_the_rows_that_read_back(
    state_with_an_archived_payload: Path, tmp_path: Path
) -> None:
    """A row whose payload is not local vanishes from the view; say so, do not pass."""
    receipt = tmp_path / "receipt.json"

    report = MEASURE.run(
        state_with_an_archived_payload,
        output=None,
        receipt=receipt,
        hash_database=False,
        scratch=None,
    )

    totals = report["derivations"]["totals"]
    assert totals["declared_row_count"] == 8
    assert totals["rows"] == 7
    gap = {(item["stage"], item["unit_kind"]): item for item in report["derivations"]["by_scope"]}[
        ("project", "calendar")
    ]
    assert (gap["declared_row_count"], gap["rows"]) == (6, 5)
    assert report["gates"]["declared_rows_match"] is False
    assert report["gates"]["passed"] is False
    assert any("a difference of 1" in note for note in report["findings"])
    assert any("Gates failed: declared_rows_match" in note for note in report["findings"])
    # The retained receipt has to carry both halves of the comparison.
    compact = json.loads(receipt.read_text())
    assert compact["results"]["derivation_totals"]["declared_row_count"] == 8
    assert compact["results"]["derivation_totals"]["rows"] == 7
    assert compact["gates"]["passed"] is False


def test_gates_pass_on_a_whole_copy(state: Path) -> None:
    report = MEASURE.measure(state)

    assert report["gates"] == {
        "page_accounting": True,
        "declared_rows_match": True,
        "rows_all_name_a_generation": True,
        "backup_timing": None,
        "passed": True,
    }
    assert any(note == "Every gate passed." for note in report["findings"])


def test_a_failed_accounting_check_fails_the_gates(state: Path) -> None:
    """Page accounting is a step 1 gate, so it must decide the exit status too."""
    report = MEASURE.measure(state)
    report["accounting"]["accounted"] = False

    gates = MEASURE.gate_results(report)

    assert gates["page_accounting"] is False
    assert gates["passed"] is False
    assert MEASURE.failed_gates(gates) == ["page_accounting"]


def test_the_receipt_keeps_the_per_stage_ratios(state: Path, tmp_path: Path) -> None:
    """The full report is deleted with the scratch tree; these numbers must outlive it."""
    receipt = tmp_path / "receipt.json"

    MEASURE.run(state, output=None, receipt=receipt, hash_database=False, scratch=None)

    results = json.loads(receipt.read_text())["results"]
    scopes = {(item["stage"], item["unit_kind"]): item for item in results["derivations_by_scope"]}
    assert scopes[("project", "calendar")]["distinct_to_total_rows"] == 0.5
    assert scopes[("project", "calendar")]["rows"] == 4
    assert scopes[("link", "event")]["distinct_payload_sha256"] == 2
    assert results["derivation_scopes_truncated"] is False
    assert results["rows_without_a_generation"] == 0
    assert receipt.stat().st_size < 64 * 1024


def test_the_receipt_scope_table_is_capped(state: Path) -> None:
    """A receipt stays small whatever it measured."""
    by_scope = [{"stage": "s", "unit_kind": f"u{index}", "rows": index} for index in range(300)]

    kept = MEASURE.receipt_scopes(by_scope)

    assert len(kept) == MEASURE.RECEIPT_MAX_SCOPES
    assert min(int(item["rows"]) for item in kept) == 300 - MEASURE.RECEIPT_MAX_SCOPES
