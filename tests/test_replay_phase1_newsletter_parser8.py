"""Exact-scope tests for the offline phase-one newsletter parser-8 replay."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "journal/tools/admission/replay_phase1_newsletter_parser8.py"
SPEC = importlib.util.spec_from_file_location("phase1_newsletter_parser8", PATH)
assert SPEC is not None and SPEC.loader is not None
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)

PACKET = (
    ROOT
    / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/packet-005/packet.json"
)
PREPARE = (
    ROOT
    / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-004/prepare.json"
)
ACCEPT = (
    ROOT
    / "journal/evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-004/accept.json"
)
CATALOG = (
    ROOT
    / "journal/evidence/runtime/schema28-checkpoint-2026-09-17/production-004/phase1-catalog.json"
)
LEDGER = (
    ROOT
    / "journal/evidence/runtime/schema28-checkpoint-2026-09-17/production-004/phase1-ledger.json"
)


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_exact_packet_receipts_and_28_target_pairs_are_pinned():
    helper.verify_packet_and_receipts(PACKET, PREPARE, ACCEPT)
    ledger = json.loads(LEDGER.read_bytes())
    pairs = tuple(
        sorted(
            (row["target_id"], row["snapshot_id"])
            for row in ledger["targets"].values()
            if row["parser"] == helper.PARSER
        )
    )
    assert pairs == helper.TARGET_PAIRS
    assert len(pairs) == 28
    assert helper.hashlib.sha256(helper.canonical(pairs)).hexdigest() == helper.TARGETS_SHA256
    assert helper.sha(CATALOG) == helper.CATALOG_SHA256
    assert helper.sha(LEDGER) == helper.LEDGER_SHA256


def test_one_reviewed_newsletter_redirect_is_pinned_exactly():
    target = "0226ef50385bfa517c9ef79c"
    assert set(helper.TARGET_URL_REDIRECTS) == {target}
    assert target in dict(helper.TARGET_PAIRS)
    retained, resolved = helper.TARGET_URL_REDIRECTS[target]
    helper.verify_target_url_binding(target, retained, retained, resolved, resolved)


@pytest.mark.parametrize(
    "urls",
    [
        (
            "https://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "https://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "https://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
        ),
        (
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
        ),
        (
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "http://www.worldsdc.com/wp-content/uploads/2020/12/"
            "WSDC-Newsletter-Vol-6-April-9-2018.pdf",
            "https://www.worldsdc.com/wp-content/uploads/2020/12/wrong.pdf",
            "https://www.worldsdc.com/wp-content/uploads/2020/12/wrong.pdf",
        ),
    ],
)
def test_reviewed_newsletter_redirect_rejects_wrong_scheme_or_path(urls):
    with pytest.raises(ValueError, match="redirect binding differs"):
        helper.verify_target_url_binding("0226ef50385bfa517c9ef79c", *urls)


def test_no_other_newsletter_target_may_change_url():
    target = "0b9f1347030560c4c5502e28"
    retained = "http://www.worldsdc.com/newsletter.pdf"
    helper.verify_target_url_binding(target, retained, retained, retained, retained)
    with pytest.raises(ValueError, match="target URL differs"):
        helper.verify_target_url_binding(
            target,
            retained,
            retained,
            retained.replace("http://", "https://"),
            retained.replace("http://", "https://"),
        )


@pytest.mark.parametrize("name", ["packet", "prepare", "accept"])
def test_changed_or_linked_authority_receipt_is_rejected(tmp_path, name):
    paths = {"packet": PACKET, "prepare": PREPARE, "accept": ACCEPT}
    replacements = dict(paths)
    linked = tmp_path / name
    linked.symlink_to(paths[name])
    replacements[name] = linked
    with pytest.raises(ValueError, match="regular file"):
        helper.verify_packet_and_receipts(
            replacements["packet"], replacements["prepare"], replacements["accept"]
        )


def test_original_input004_and_an_unscoped_scratch_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="original input-004"):
        helper.verify_state_path(helper.ORIGINAL_INPUT004, tmp_path, tmp_path, tmp_path)
    with pytest.raises(ValueError, match="disposable /var/tmp"):
        helper.verify_state_path(tmp_path, tmp_path, tmp_path, tmp_path)


def test_fabricated_fresh_marker_cannot_self_assert_packet_derivation(tmp_path):
    common = {
        "format": "extension-input-scratch-v1",
        "checkpoint": str(helper.CHECKPOINT),
        "checkpoint_sha256": helper.CHECKPOINT_SHA256,
        "source": str(helper.SOURCE),
        "source_receipt_sha256": helper.SOURCE_RECEIPT_SHA256,
        "schema": 29,
        "protected": {"hosts": {"rows": 1, "sha256": "a" * 64}},
        "named_judges": {"judge": ["name", None]},
    }
    reference = {**common, "scratch": str(helper.ORIGINAL_INPUT004), "prepared_at": "old"}
    fresh = {**common, "scratch": str(tmp_path), "prepared_at": "new"}
    helper.verify_marker_derivation(fresh, reference, tmp_path)
    fresh["protected"] = {"hosts": {"rows": 1, "sha256": "b" * 64}}
    with pytest.raises(ValueError, match="pinned input-004"):
        helper.verify_marker_derivation(fresh, reference, tmp_path)


def test_audit_hook_denies_dns_socket_and_process_routes():
    program = f"""
import importlib.util, os, pathlib, socket, subprocess
path = pathlib.Path({str(PATH)!r})
spec = importlib.util.spec_from_file_location('offline_helper', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.install_offline_audit_hook()
events = []
for name, call in (
    ('getaddrinfo', lambda: socket.getaddrinfo('example.org', 443)),
    ('gethostbyname', lambda: socket.gethostbyname('example.org')),
    ('gethostbyaddr', lambda: socket.gethostbyaddr('127.0.0.1')),
    ('getnameinfo', lambda: socket.getnameinfo(('127.0.0.1', 443), 0)),
    ('socket', lambda: socket.socket()),
    ('popen', lambda: subprocess.Popen(['/bin/true'])),
    ('system', lambda: os.system('/bin/true')),
    ('spawn', lambda: os.spawnv(os.P_WAIT, '/bin/true', ['/bin/true'])),
    ('posix_spawn', lambda: os.posix_spawn('/bin/true', ['/bin/true'], os.environ)),
    ('exec', lambda: os.execv('/bin/true', ['/bin/true'])),
):
    try:
        call()
    except RuntimeError as error:
        events.append((name, str(error)))
print(events)
assert [name for name, _error in events] == [
    'getaddrinfo', 'gethostbyname', 'gethostbyaddr', 'getnameinfo', 'socket',
    'popen', 'system', 'spawn', 'posix_spawn', 'exec'
]
"""
    result = subprocess.run(
        [sys.executable, "-c", program], text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "socket.getaddrinfo" in result.stdout
    assert "socket.gethostbyname" in result.stdout
    assert "socket.gethostbyaddr" in result.stdout
    assert "socket.getnameinfo" in result.stdout
    assert "socket.__new__" in result.stdout
    assert "subprocess.Popen" in result.stdout
    assert "os.system" in result.stdout
    assert "os.posix_spawn" in result.stdout or "os.spawn" in result.stdout
    assert "os.exec" in result.stdout


def test_query_hash_is_order_independent_and_sensitive_to_values():
    first_conn = connection()
    second_conn = connection()
    for conn in (first_conn, second_conn):
        conn.execute(
            "CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT,raw BLOB,optional TEXT)"
        )
    rows = ((2, "b", b"\x00\xff", None), (1, "a", b"x" * 262_144, "present"))
    first_conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", rows)
    second_conn.executemany("INSERT INTO sample VALUES (?,?,?,?)", reversed(rows))
    first = helper.table_hash(first_conn, "sample")
    assert helper.table_hash(second_conn, "sample") == first
    first_conn.execute("UPDATE sample SET value='changed' WHERE id=1")
    second = helper.table_hash(first_conn, "sample")
    assert first["rows"] == second["rows"] == 2
    assert first["sha256"] != second["sha256"]


def test_table_hash_totally_orders_nullable_text_primary_key_ties():
    first = connection()
    second = connection()
    for conn in (first, second):
        conn.execute("CREATE TABLE sample(key TEXT PRIMARY KEY,payload TEXT)")
    rows = ((None, "z"), (None, "a"), ("key", "middle"))
    first.executemany("INSERT INTO sample VALUES (?,?)", rows)
    second.executemany("INSERT INTO sample VALUES (?,?)", reversed(rows))
    assert helper.table_hash(first, "sample") == helper.table_hash(second, "sample")


def test_table_hash_uses_binary_ties_for_nocase_columns_without_primary_key():
    first = connection()
    second = connection()
    for conn in (first, second):
        conn.execute("CREATE TABLE sample(value TEXT COLLATE NOCASE,payload TEXT)")
    rows = (("a", "lower"), ("A", "upper"), (None, "null"))
    first.executemany("INSERT INTO sample VALUES (?,?)", rows)
    second.executemany("INSERT INTO sample VALUES (?,?)", reversed(rows))
    assert helper.table_hash(first, "sample") == helper.table_hash(second, "sample")


def test_table_hash_normalizes_signed_zero_across_reverse_insertion():
    first = connection()
    second = connection()
    for conn in (first, second):
        conn.execute("CREATE TABLE sample(value)")
    rows = ((-0.0,), (+0.0,))
    first.executemany("INSERT INTO sample VALUES (?)", rows)
    second.executemany("INSERT INTO sample VALUES (?)", reversed(rows))
    assert helper.table_hash(first, "sample") == helper.table_hash(second, "sample")
    assert helper._normalize(-0.0) == helper._normalize(+0.0) == 0.0


def test_table_hash_orders_and_detects_infinity_changes():
    first = connection()
    second = connection()
    for conn in (first, second):
        conn.execute("CREATE TABLE sample(value)")
    rows = ((float("inf"),), (float("-inf"),), (1.0,))
    first.executemany("INSERT INTO sample VALUES (?)", rows)
    second.executemany("INSERT INTO sample VALUES (?)", reversed(rows))
    before = helper.table_hash(first, "sample")
    assert helper.table_hash(second, "sample") == before
    first.execute("UPDATE sample SET value=2.0 WHERE value=1.0")
    assert helper.table_hash(first, "sample") != before
    assert helper._normalize(float("inf")) == {"float": "+infinity"}
    assert helper._normalize(float("-inf")) == {"float": "-infinity"}
    assert helper._normalize(float("nan")) == {"float": "nan"}


def _tracked_disk_copy(tmp_path, monkeypatch):
    source_path = tmp_path / "source.sqlite"
    source = sqlite3.connect(source_path, isolation_level=None)
    source.row_factory = sqlite3.Row
    source.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,value TEXT)")
    source.execute("INSERT INTO sample VALUES (1,'original')")
    created = []
    real_mkdtemp = helper.tempfile.mkdtemp

    def tracked_mkdtemp(*args, **kwargs):
        path = Path(real_mkdtemp(*args, **kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setenv("TMPDIR", "/var/tmp")
    monkeypatch.setattr(helper.tempfile, "mkdtemp", tracked_mkdtemp)
    return source, created


def test_disk_backed_copy_leaves_source_unchanged_and_cleans_success(tmp_path, monkeypatch):
    source, created = _tracked_disk_copy(tmp_path, monkeypatch)
    with helper.disk_backed_database_copy(source, tmp_path) as copied:
        copied.connection.execute("UPDATE sample SET value='copy' WHERE id=1")
        assert copied.connection.execute("SELECT value FROM sample").fetchone()[0] == "copy"
    assert source.execute("SELECT value FROM sample").fetchone()[0] == "original"
    assert len(created) == 1 and not created[0].exists()
    source.close()


def test_disk_backed_copy_leaves_source_unchanged_and_cleans_failure(tmp_path, monkeypatch):
    source, created = _tracked_disk_copy(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        with helper.disk_backed_database_copy(source, tmp_path) as copied:
            copied.connection.execute("UPDATE sample SET value='copy' WHERE id=1")
            raise RuntimeError("injected copy failure")
    assert source.execute("SELECT value FROM sample").fetchone()[0] == "original"
    assert len(created) == 1 and not created[0].exists()
    source.close()


def test_disk_backed_copy_rejects_and_removes_symlink_substitution(tmp_path, monkeypatch):
    source = connection()
    source.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
    target = tmp_path / "target"
    target.mkdir()
    link = Path("/var/tmp") / f"swingset-phase1-parser8-test-{os.getpid()}"
    link.unlink(missing_ok=True)
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", "/var/tmp")
    monkeypatch.setattr(helper.tempfile, "mkdtemp", lambda **_kwargs: str(link))
    with pytest.raises(ValueError, match="directory differs"):
        with helper.disk_backed_database_copy(source, tmp_path):
            pass
    assert not link.exists() and target.is_dir()


def test_recovery_marker_exactly_binds_reviewed_before_state(tmp_path):
    seal = {
        "source": str(helper.SOURCE),
        "source_receipt_sha256": helper.SOURCE_RECEIPT_SHA256,
        "packet_sha256": helper.PACKET_SHA256,
        "checkpoint_sha256": helper.CHECKPOINT_SHA256,
        "input_bundle_sha256": helper.INPUT_BUNDLE_SHA256,
        "before_invariants": {"watches": {"rows": 1, "sha256": "a" * 64}},
        "before_tables": {"runs": {"rows": 1, "sha256": "b" * 64}},
        "expected_after_tables": {"runs": {"rows": 2, "sha256": "c" * 64}},
    }
    marker = helper.recovery_marker_document(
        tmp_path,
        tmp_path / "receipt.json",
        tmp_path / "ledger.json",
        "d" * 64,
        seal,
    )
    path = tmp_path / "pending.json"
    helper.verify_recovery_marker(path, marker)
    helper.verify_recovery_marker(path, marker)
    mutations = (
        ("before_state_sha256", "0" * 64),
        ("reviewed_before_state.source_receipt_sha256", "0" * 64),
        ("reviewed_before_state.packet_sha256", "0" * 64),
        ("reviewed_before_state.input_bundle_sha256", "0" * 64),
        ("reviewed_before_state.before_invariants.watches.sha256", "0" * 64),
        ("reviewed_before_state.before_tables.runs.sha256", "0" * 64),
    )
    for dotted, replacement in mutations:
        tampered = json.loads(helper.canonical(marker))
        parent = tampered
        parts = dotted.split(".")
        for part in parts[:-1]:
            parent = parent[part]
        parent[parts[-1]] = replacement
        path.write_bytes(helper.canonical(tampered) + b"\n")
        with pytest.raises(ValueError, match="authority differs"):
            helper.verify_recovery_marker(path, marker)


@pytest.mark.parametrize(
    "table",
    sorted(
        helper.MUTABLE_TABLES
        | {
            "event_progress_cursor",
            "event_pressure_subjects",
            "event_stage_operations",
        }
    ),
)
def test_exact_after_state_rejects_arbitrary_change_in_every_table_category(table):
    conn = connection()
    conn.execute(f'CREATE TABLE "{table}"(id TEXT PRIMARY KEY,value TEXT)')
    conn.execute(f"INSERT INTO \"{table}\" VALUES ('row','before')")
    expected = helper.all_table_hashes(conn)
    conn.execute(f"UPDATE \"{table}\" SET value='tampered' WHERE id='row'")
    with pytest.raises(ValueError, match="sealed derivation"):
        helper.require_exact_tables(conn, expected, "adversarial replay")


def test_changed_table_error_reports_only_unexpected_difference():
    before = {
        "runs": {"rows": 1, "sha256": "a"},
        "work_generations": {"rows": 1, "sha256": "b"},
        "unreviewed": {"rows": 1, "sha256": "c"},
    }
    after = {table: {"rows": 1, "sha256": "changed"} for table in before}
    with pytest.raises(ValueError, match="unexpected parser-mutated tables: unreviewed") as error:
        helper.changed_tables(before, after)
    assert "runs" not in str(error.value)
    assert "work_generations" not in str(error.value)


def test_snapshot_hash_allows_only_target_parse_metadata():
    conn = connection()
    conn.execute(
        "CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,body_sha256 TEXT,parser_version TEXT,parse_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO snapshots VALUES (?,?,?,?)",
        (("target", "a", "6", "ok"), ("other", "b", "6", "ok")),
    )
    before = helper.snapshot_hashes(conn, ("target",))
    conn.execute("UPDATE snapshots SET parser_version='8' WHERE snapshot_id='target'")
    assert helper.snapshot_hashes(conn, ("target",)) == before
    conn.execute("UPDATE snapshots SET parser_version='8' WHERE snapshot_id='other'")
    assert helper.snapshot_hashes(conn, ("target",)) != before


def test_watch_hash_allows_only_target_cache_columns():
    conn = connection()
    conn.executescript(
        """
        CREATE TABLE watches(
            watch_id TEXT PRIMARY KEY,state TEXT,current_observation_snapshot_id TEXT,
            extract_version TEXT,fingerprint TEXT,url TEXT
        );
        CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,watch_id TEXT);
        INSERT INTO watches VALUES ('target-watch','sealed','old','6','old-fingerprint','https://target');
        INSERT INTO watches VALUES ('other-watch','sealed','other','6','other-fingerprint','https://other');
        INSERT INTO snapshots VALUES ('target','target-watch');
        """
    )
    before = helper.watch_hashes(conn, ("target",))
    conn.execute(
        "UPDATE watches SET current_observation_snapshot_id='target',"
        "extract_version='8',fingerprint='new-fingerprint' WHERE watch_id='target-watch'"
    )
    assert helper.watch_hashes(conn, ("target",)) == before
    conn.execute("UPDATE watches SET state='gone' WHERE watch_id='target-watch'")
    changed = helper.watch_hashes(conn, ("target",))
    assert changed != before
    with pytest.raises(ValueError, match="target_immutable.sha256"):
        helper.require_same(before, changed, "watch invariant changed")
    conn.execute("UPDATE watches SET state='sealed' WHERE watch_id='target-watch'")
    conn.execute("UPDATE watches SET fingerprint='changed' WHERE watch_id='other-watch'")
    assert helper.watch_hashes(conn, ("target",)) != before


@pytest.mark.parametrize("via,unit_suffix", [("origin", ""), ("wayback", "/snapshot")])
def test_promotion_proof_uses_real_source_unit_semantics(via, unit_suffix):
    conn = connection()
    conn.executescript(
        """
        CREATE TABLE watches(watch_id TEXT PRIMARY KEY,parser TEXT,source TEXT,kind TEXT);
        CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,watch_id TEXT,parse_status TEXT,parser_version TEXT,parsed_at TEXT,via TEXT);
        CREATE TABLE source_units(unit_key TEXT PRIMARY KEY,accepted_generation_id TEXT,watch_id TEXT,page_kind TEXT);
        CREATE TABLE source_generations(generation_id TEXT PRIMARY KEY,unit_key TEXT,state TEXT,recipe_json TEXT,created_at TEXT,run_id TEXT,report_json TEXT,manifest_json TEXT,result_json TEXT);
        CREATE TABLE admission_policies(page_kind TEXT PRIMARY KEY,mode TEXT);
        CREATE TABLE observations(snapshot_id TEXT,parser_version TEXT);
        """
    )
    snapshot = helper.TARGET_PAIRS[0][1]
    target = helper.TARGET_PAIRS[0][0]
    unit = "watch" if not unit_suffix else f"watch/{snapshot}"
    created_at = "2026-09-17T11:00:00+00:00"
    recipe = json.dumps(
        {"parser_version": "8", "context": {"snapshot_id": snapshot}}, sort_keys=True
    )
    report = json.dumps(
        {
            "page_kind": helper.PARSER,
            "state": "needs_review",
            "failures": ["critical_unknown"],
        }
    )
    manifest = json.dumps(
        [
            {
                "slot": snapshot,
                "snapshot_id": snapshot,
                "watch_id": "watch",
                "parser_version": "8",
            }
        ]
    )
    result = json.dumps(
        {
            "observations": [
                {
                    "kind": "calendar_row",
                    "scope": {"kind": "calendar", "ref": "history"},
                    "payload": {"name_raw": "A"},
                },
                {
                    "kind": "calendar_row",
                    "scope": {"kind": "calendar", "ref": "history"},
                    "payload": {"name_raw": "B"},
                },
            ],
            "watches": [],
            "warnings": [],
            "legitimate_empty": False,
        }
    )
    conn.execute(
        "INSERT INTO watches VALUES ('watch',?,'wsdc_newsletter','index')", (helper.PARSER,)
    )
    conn.execute("INSERT INTO snapshots VALUES (?,'watch','ok','8','now',?)", (snapshot, via))
    conn.execute("INSERT INTO source_units VALUES (?,NULL,'watch',?)", (unit, helper.PARSER))
    conn.execute(
        "INSERT INTO source_generations VALUES ('gen',?,'needs_review',?,?, 'replay',?,?,?)",
        (unit, recipe, created_at, report, manifest, result),
    )
    conn.execute("INSERT INTO admission_policies VALUES (?,'shadow')", (helper.PARSER,))
    conn.execute("INSERT INTO observations VALUES ('other-snapshot','6')")
    receipt, promotion = helper.verify_promoted(conn, target, snapshot, "replay", created_at)
    assert receipt == {
        "status": "parsed",
        "snapshot_id": snapshot,
        "observations": 2,
        "parser_version": "8",
    }
    assert promotion["mode"] == "shadow"
    assert promotion["materialized_observations"]["rows"] == 0
    conn.execute("UPDATE source_generations SET result_json='{}' WHERE generation_id='gen'")
    with pytest.raises(ValueError, match="generation result differs"):
        helper.verify_promoted(conn, target, snapshot, "replay", created_at)
    conn.execute(
        "UPDATE source_generations SET result_json=?,manifest_json=? WHERE generation_id='gen'",
        (result, json.dumps([{**json.loads(manifest)[0], "snapshot_id": "foreign"}])),
    )
    with pytest.raises(ValueError, match="generation manifest differs"):
        helper.verify_promoted(conn, target, snapshot, "replay", created_at)
    conn.execute(
        "UPDATE source_generations SET manifest_json=? WHERE generation_id='gen'", (manifest,)
    )
    conn.execute(
        "INSERT INTO source_generations VALUES ('newer',?,'needs_review',?,"
        "'2026-09-17T11:00:01+00:00','other-run',?,?,?)",
        (unit, recipe, report, manifest, result),
    )
    with pytest.raises(ValueError, match="not created by this replay"):
        helper.verify_promoted(conn, target, snapshot, "replay", created_at)


@pytest.mark.parametrize(
    "mode,state,pointer,version,run_id,report_state",
    [
        ("paused", "staged", None, "8", "replay", "staged"),
        ("enforce", "accepted", "gen", "8", "replay", "accepted"),
        ("shadow", "staged", None, "7", "replay", "staged"),
        ("shadow", "needs_review", None, "8", "older-run", "needs_review"),
        ("shadow", "staged", None, "8", "replay", "needs_review"),
    ],
)
def test_promotion_proof_fails_closed(mode, state, pointer, version, run_id, report_state):
    conn = connection()
    conn.executescript(
        """
        CREATE TABLE watches(watch_id TEXT PRIMARY KEY,parser TEXT,source TEXT,kind TEXT);
        CREATE TABLE snapshots(snapshot_id TEXT PRIMARY KEY,watch_id TEXT,parse_status TEXT,parser_version TEXT,parsed_at TEXT,via TEXT);
        CREATE TABLE source_units(unit_key TEXT PRIMARY KEY,accepted_generation_id TEXT,watch_id TEXT,page_kind TEXT);
        CREATE TABLE source_generations(generation_id TEXT PRIMARY KEY,unit_key TEXT,state TEXT,recipe_json TEXT,created_at TEXT,run_id TEXT,report_json TEXT,manifest_json TEXT,result_json TEXT);
        CREATE TABLE admission_policies(page_kind TEXT PRIMARY KEY,mode TEXT);
        CREATE TABLE observations(snapshot_id TEXT,parser_version TEXT);
        """
    )
    snapshot = helper.TARGET_PAIRS[0][1]
    unit = "watch"
    created_at = "2026-09-17T11:00:00+00:00"
    recipe = json.dumps(
        {"parser_version": version, "context": {"snapshot_id": snapshot}}, sort_keys=True
    )
    report = json.dumps(
        {
            "page_kind": helper.PARSER,
            "state": report_state,
            "failures": [] if report_state == "staged" else ["critical_unknown"],
        }
    )
    manifest = json.dumps(
        [
            {
                "slot": snapshot,
                "snapshot_id": snapshot,
                "watch_id": "watch",
                "parser_version": "8",
            }
        ]
    )
    result = json.dumps(
        {"observations": [], "watches": [], "warnings": [], "legitimate_empty": True}
    )
    conn.execute(
        "INSERT INTO watches VALUES ('watch',?,'wsdc_newsletter','index')", (helper.PARSER,)
    )
    conn.execute(
        "INSERT INTO snapshots VALUES (?,'watch','ok',?,'now','origin')", (snapshot, version)
    )
    conn.execute("INSERT INTO source_units VALUES (?,?, 'watch',?)", (unit, pointer, helper.PARSER))
    conn.execute(
        "INSERT INTO source_generations VALUES ('gen',?,?,?,?,?,?,?,?)",
        (unit, state, recipe, created_at, run_id, report, manifest, result),
    )
    conn.execute("INSERT INTO admission_policies VALUES (?,?)", (helper.PARSER, mode))
    with pytest.raises(ValueError):
        helper.verify_promoted(conn, helper.TARGET_PAIRS[0][0], snapshot, "replay", created_at)


def test_non_target_ledger_hash_covers_exactly_185_records():
    ledger = json.loads(LEDGER.read_bytes())
    before = helper.non_target_ledger_hash(ledger)
    target_id = helper.TARGET_PAIRS[0][0]
    ledger["targets"][target_id]["parser_version"] = "8"
    assert helper.non_target_ledger_hash(ledger) == before
    non_target = next(key for key in ledger["targets"] if key not in dict(helper.TARGET_PAIRS))
    ledger["targets"][non_target]["status"] = "changed"
    assert helper.non_target_ledger_hash(ledger) != before


def _sealed_fresh_database(tmp_path, monkeypatch):
    from swingset.state import db as db_module
    from swingset.state.db import open_database

    # The replay tool is frozen at the schema it was reviewed against (D-0013).
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    state = tmp_path / "state"
    database = open_database(state)
    database.connection.execute(
        "INSERT INTO meta(key,value) VALUES ('input_bundle_hash',?)",
        (helper.INPUT_BUNDLE_SHA256,),
    )
    bundle = state / "inputs" / helper.INPUT_BUNDLE_SHA256
    bundle.mkdir(parents=True)
    payload = b"accepted\n"
    digest = helper.hashlib.sha256(payload).hexdigest()
    (bundle / "sentinel").write_bytes(payload)
    (bundle / "manifest.json").write_text(json.dumps({"sentinel": digest}))
    history = (
        "event_stage_operations",
        "event_progress_receipts",
        "event_accounting_receipts",
        "event_retirement_receipts",
        "source_event_retirement_receipts",
        "event_timing_history",
    )
    marker = {
        "prepared_at": "2026-09-17T12:00:00+00:00",
        "admission_highwater": 0,
        "policy_highwater": 0,
        "history_limits": {
            table: database.connection.execute(
                f'SELECT coalesce(max(rowid),0) FROM "{table}"'
            ).fetchone()[0]
            for table in history
        },
    }
    marker["protected"] = helper._packet_protected(database.connection, marker)
    (state / "extension-input-scratch.json").write_text(json.dumps(marker))
    return state, database, marker


def test_fabricated_or_operated_scratch_fails_before_replay(tmp_path, monkeypatch):
    state, database, marker = _sealed_fresh_database(tmp_path, monkeypatch)
    helper.verify_database_authority(
        database.connection, state, require_fresh=True, verify_bundle=False
    )
    marker["protected"]["hosts"]["sha256"] = "0" * 64
    (state / "extension-input-scratch.json").write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="protected before-state"):
        helper.verify_database_authority(
            database.connection, state, require_fresh=True, verify_bundle=False
        )
    marker["protected"] = helper._packet_protected(database.connection, marker)
    (state / "extension-input-scratch.json").write_text(json.dumps(marker))
    database.connection.execute(
        "INSERT INTO runs VALUES ('dirty','2026-09-17T13:00:00+00:00',NULL,1,NULL)"
    )
    with pytest.raises(ValueError, match="operated after packet preparation"):
        helper.verify_database_authority(
            database.connection, state, require_fresh=True, verify_bundle=False
        )
    database.close()


def _transaction_fixture(tmp_path, monkeypatch):
    from swingset.clock import FakeClock
    from swingset.state.db import open_database

    state = tmp_path / "transaction"
    database = open_database(state)
    baseline_run = database.start_run(datetime(2026, 9, 17, 10, tzinfo=UTC), dry_run=True)
    pairs = (("target-1", "snapshot-1"), ("target-2", "snapshot-2"))
    monkeypatch.setattr(helper, "TARGET_PAIRS", pairs)
    for index, (_target, snapshot) in enumerate(pairs, 1):
        watch = f"watch-{index}"
        database.connection.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                watch,
                "wsdc_newsletter",
                "index",
                "GET",
                f"https://example/{index}",
                helper.PARSER,
                "sealed",
            ),
        )
        database.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification,parser_version,parse_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot,
                watch,
                "GET",
                f"https://example/{index}",
                "2026-09-17T10:00:00Z",
                200,
                1,
                1,
                baseline_run,
                "Ok",
                "6",
                "ok",
            ),
        )
    catalog_body = b"catalog\n"
    (state / "phase1-catalog.json").write_bytes(catalog_body)
    monkeypatch.setattr(helper, "CATALOG_SHA256", helper.hashlib.sha256(catalog_body).hexdigest())
    ledger = {
        "targets": {
            target: {"target_id": target, "snapshot_id": snapshot, "parser_version": "6"}
            for target, snapshot in pairs
        }
    }
    original_ledger = helper.canonical(ledger)

    def promoted(conn, target, snapshot, _run_id, _created_at):
        version = conn.execute(
            "SELECT parser_version FROM snapshots WHERE snapshot_id=?", (snapshot,)
        ).fetchone()[0]
        helper.require(version == "8", "not promoted")
        return (
            {"snapshot_id": snapshot, "parser_version": "8", "status": "empty", "observations": 0},
            {"target_id": target},
        )

    monkeypatch.setattr(helper, "verify_promoted", promoted)
    clock = FakeClock(datetime(2026, 9, 17, 11, tzinfo=UTC))
    before_invariants = helper.invariant_hashes(database.connection, tuple(p[1] for p in pairs))
    before_tables = helper.all_table_hashes(database.connection)
    return state, database, pairs, ledger, original_ledger, clock, before_invariants, before_tables


def test_non_target_hash_excludes_only_real_ordinary_target_unit(tmp_path, monkeypatch):
    state, database, pairs, *_rest = _transaction_fixture(tmp_path, monkeypatch)
    snapshots = tuple(snapshot for _target, snapshot in pairs)
    before = helper.non_target_mutable_hashes(database.connection, snapshots)
    database.connection.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES ('watch-1','watch-1',?)",
        (helper.PARSER,),
    )
    assert helper.non_target_mutable_hashes(database.connection, snapshots) == before
    database.connection.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES ('foreign','watch-1',?)",
        (helper.PARSER,),
    )
    assert helper.non_target_mutable_hashes(database.connection, snapshots) != before
    database.close()
    assert state.is_dir()


def test_target_watch_owns_old_snapshot_observation_cache(tmp_path, monkeypatch):
    _state, database, pairs, *_rest = _transaction_fixture(tmp_path, monkeypatch)
    conn = database.connection
    run_id = conn.execute("SELECT run_id FROM runs ORDER BY started_at LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
        "VALUES ('other-watch','wsdc_newsletter','index','GET','https://other',?,'sealed')",
        (helper.PARSER,),
    )
    for snapshot, watch, url in (
        ("old-target-snapshot", "watch-1", "https://target/old"),
        ("other-snapshot", "other-watch", "https://other"),
    ):
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
            "body_bytes,content_changed,run_id,classification,parser_version,parse_status) "
            "VALUES (?,?,'GET',?,'2026-09-17T09:00:00Z',200,1,1,?,'Ok','6','ok')",
            (snapshot, watch, url, run_id),
        )
        conn.execute(
            "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,"
            "scope_id,seq,extract_version,parser_version,payload_json) "
            "VALUES (?,?,?,'calendar_row','calendar','history',0,'1','6','{}')",
            ("observation-" + snapshot, watch, snapshot),
        )
    snapshots = tuple(snapshot for _target, snapshot in pairs)
    before = helper.non_target_mutable_hashes(conn, snapshots)
    conn.execute(
        "UPDATE observations SET payload_json='{\"changed\":true}' "
        "WHERE snapshot_id='old-target-snapshot'"
    )
    assert helper.non_target_mutable_hashes(conn, snapshots) == before
    conn.execute(
        "UPDATE observations SET payload_json='{\"changed\":true}' "
        "WHERE snapshot_id='other-snapshot'"
    )
    assert helper.non_target_mutable_hashes(conn, snapshots) != before
    database.close()


def test_queue_generation_changes_require_target_owned_pending_transition(tmp_path, monkeypatch):
    _state, database, pairs, *_rest = _transaction_fixture(tmp_path, monkeypatch)
    conn = database.connection
    snapshots = tuple(snapshot for _target, snapshot in pairs)
    conn.execute(
        "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,"
        "scope_id,seq,extract_version,parser_version,payload_json) "
        "VALUES ('target-observation','watch-1','snapshot-1','calendar_row','calendar',"
        "'history',0,'1','6','{}')"
    )
    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
        "VALUES ('project','event','foreign','before')"
    )
    before_pending = helper.queue_rows(conn, "pending_work")
    before_generations = helper.queue_rows(conn, "work_generations")
    before_projects = helper.target_project_queue_keys(conn, snapshots)

    conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
        "VALUES ('project','calendar','history','after')"
    )
    after_pending = helper.queue_rows(conn, "pending_work")
    after_generations = helper.queue_rows(conn, "work_generations")
    helper.verify_queue_transition(
        before_pending,
        after_pending,
        before_generations,
        after_generations,
        snapshots,
        before_projects,
        helper.target_project_queue_keys(conn, snapshots),
    )

    conn.execute(
        "UPDATE work_generations SET retry_generation=1 "
        "WHERE stage='project' AND unit_kind='event' AND unit_id='foreign'"
    )
    with pytest.raises(ValueError, match="pending-work trigger: project/event/foreign"):
        helper.verify_queue_transition(
            before_pending,
            after_pending,
            before_generations,
            helper.queue_rows(conn, "work_generations"),
            snapshots,
            before_projects,
            helper.target_project_queue_keys(conn, snapshots),
        )
    conn.execute(
        "UPDATE work_generations SET retry_generation=0 "
        "WHERE stage='project' AND unit_kind='event' AND unit_id='foreign'"
    )
    conn.execute(
        "UPDATE pending_work SET enqueued_at='after' "
        "WHERE stage='project' AND unit_kind='event' AND unit_id='foreign'"
    )
    with pytest.raises(ValueError, match="pending-work transition: project/event/foreign"):
        helper.verify_queue_transition(
            before_pending,
            helper.queue_rows(conn, "pending_work"),
            before_generations,
            helper.queue_rows(conn, "work_generations"),
            snapshots,
            before_projects,
            helper.target_project_queue_keys(conn, snapshots),
        )
    database.close()


def test_parser_failure_rolls_back_every_sqlite_change(tmp_path, monkeypatch):
    (
        state,
        database,
        pairs,
        ledger,
        original_ledger,
        clock,
        before_invariants,
        before_tables,
    ) = _transaction_fixture(tmp_path, monkeypatch)
    attempts = 0

    def parse(db, _archive, unit, _clock, _run):
        nonlocal attempts
        attempts += 1
        db.connection.execute(
            "UPDATE snapshots SET parser_version='8',parsed_at='now' WHERE snapshot_id=?",
            (unit.unit_id,),
        )
        if attempts == 2:
            raise ValueError("injected second-target failure")
        return SimpleNamespace(failed=False, selection=None)

    with pytest.raises(ValueError, match="injected"):
        helper.apply_database_transaction(
            database,
            state,
            tuple(),
            original_ledger,
            before_invariants,
            before_tables,
            clock,
            parse,
            lambda *_args, **_kwargs: None,
            lambda *_args, **_kwargs: {},
        )
    assert [
        row[0]
        for row in database.connection.execute(
            "SELECT parser_version FROM snapshots ORDER BY snapshot_id"
        )
    ] == ["6", "6"]
    assert helper.all_table_hashes(database.connection) == before_tables
    assert ledger == json.loads(original_ledger)
    database.close()


def test_maintenance_transaction_rolls_back_baseexception_before_connection_use():
    class Abort(BaseException):
        pass

    conn = connection()
    conn.execute("CREATE TABLE sample(value TEXT)")
    with pytest.raises(Abort):
        with helper.maintenance_transaction(conn):
            conn.execute("INSERT INTO sample VALUES ('uncommitted')")
            raise Abort()
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0


def test_after_table_validation_failure_rolls_back_before_commit(tmp_path, monkeypatch):
    (
        _state,
        database,
        _pairs,
        _ledger,
        original_ledger,
        clock,
        before_invariants,
        before_tables,
    ) = _transaction_fixture(tmp_path, monkeypatch)

    def parse(db, _archive, unit, _clock, _run):
        db.connection.execute(
            "UPDATE snapshots SET parser_version='8',parsed_at='now' WHERE snapshot_id=?",
            (unit.unit_id,),
        )
        return SimpleNamespace(failed=False, selection=None)

    with pytest.raises(ValueError, match="sealed dry derivation") as error:
        helper.apply_database_transaction(
            database,
            database.state_dir,
            tuple(),
            original_ledger,
            before_invariants,
            before_tables,
            clock,
            parse,
            lambda *_args, **_kwargs: None,
            lambda *_args, **_kwargs: {},
            before_tables,
        )
    assert "history_dispatch_fence.sha256" in str(error.value)
    assert "runs.sha256" in str(error.value)
    assert "snapshots.sha256" in str(error.value)
    assert not database.connection.in_transaction
    assert helper.all_table_hashes(database.connection) == before_tables
    assert database.connection.execute("SELECT 1").fetchone()[0] == 1
    database.close()


def test_fixed_replay_repeats_identical_after_hashes_on_exact_copies(tmp_path, monkeypatch):
    from swingset.state.db import Database

    (
        state,
        database,
        _pairs,
        _ledger,
        original_ledger,
        clock,
        before_invariants,
        before_tables,
    ) = _transaction_fixture(tmp_path, monkeypatch)

    def parse(db, _archive, unit, fixed_clock, run_id):
        now = fixed_clock.now().isoformat()
        db.connection.execute(
            "UPDATE snapshots SET parser_version='8',parsed_at=? WHERE snapshot_id=?",
            (now, unit.unit_id),
        )
        db.connection.execute(
            "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "VALUES ('parse','snapshot',?,?) "
            "ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET enqueued_at=excluded.enqueued_at",
            (unit.unit_id, now),
        )
        assert run_id == "run_20260917T110000Z"
        return SimpleNamespace(failed=False, selection=None)

    after_states = []
    for _index in range(2):
        clone = connection()
        clone.execute("PRAGMA foreign_keys=ON")
        database.connection.backup(clone)
        clone_database = Database(state, clone, None)
        helper.install_deterministic_sql_clock(clone, clock.now())
        after, run_id = helper.apply_database_transaction(
            clone_database,
            state,
            tuple(),
            original_ledger,
            before_invariants,
            before_tables,
            clock,
            parse,
            lambda *_args, **_kwargs: None,
            lambda *_args, **_kwargs: {},
        )
        assert run_id == "run_20260917T110000Z"
        after_states.append(after)
        clone_database.close()
    assert after_states[0] == after_states[1]
    database.close()


def test_success_commits_all_targets_then_seals_new_ledger(tmp_path, monkeypatch):
    (
        state,
        database,
        pairs,
        _ledger,
        original_ledger,
        clock,
        before_invariants,
        before_tables,
    ) = _transaction_fixture(tmp_path, monkeypatch)

    def parse(db, _archive, unit, _clock, _run):
        db.connection.execute(
            "UPDATE snapshots SET parser_version='8',parsed_at='now' WHERE snapshot_id=?",
            (unit.unit_id,),
        )
        return SimpleNamespace(failed=False, selection=None)

    import swingset.state.write_deadline as write_deadline

    def deadline_entered(_conn):
        raise AssertionError("worker write deadline entered maintenance replay")

    monkeypatch.setattr(write_deadline, "bounded_write", deadline_entered)

    _after, replay_run_id = helper.apply_database_transaction(
        database,
        state,
        tuple(),
        original_ledger,
        before_invariants,
        before_tables,
        clock,
        parse,
        lambda *_args, **_kwargs: None,
        lambda *_args, **_kwargs: {},
    )
    assert helper._target_phase(database.connection) == "committed"
    after_tables = helper.all_table_hashes(database.connection)
    assert set(helper.changed_tables(before_tables, after_tables)) == {
        "history_dispatch_fence",
        "runs",
        "snapshots",
    }
    replacements = {
        target: helper.verify_promoted(
            database.connection,
            target,
            snapshot,
            replay_run_id,
            clock.now().isoformat(),
        )[0]
        for target, snapshot in pairs
    }
    updated, body = helper._updated_ledger(original_ledger, replacements)
    sealed = state / "phase1-ledger-parser8.json"
    helper._write_once(sealed, body, "sealed ledger")
    helper._write_once(sealed, body, "sealed ledger resume")
    assert json.loads(sealed.read_bytes()) == updated
    assert json.loads(original_ledger)["targets"] != updated["targets"]
    sealed.write_text("tampered")
    with pytest.raises(ValueError, match="differs"):
        helper._write_once(sealed, body, "sealed ledger")
    database.close()


def test_full_replay_resumes_from_exact_committed_derivation(tmp_path, monkeypatch):
    (
        state,
        database,
        pairs,
        _ledger,
        _original_ledger,
        _clock,
        before_invariants,
        before_tables,
    ) = _transaction_fixture(tmp_path, monkeypatch)
    ledger = {
        "targets": {
            **{
                target: {"target_id": target, "snapshot_id": snapshot, "parser_version": "6"}
                for target, snapshot in pairs
            },
            **{
                f"other-{index}": {"target_id": f"other-{index}", "status": "parsed"}
                for index in range(185)
            },
        }
    }
    ledger_body = helper.canonical(ledger) + b"\n"
    (state / "phase1-ledger.json").write_bytes(ledger_body)
    monkeypatch.setattr(helper, "LEDGER_SHA256", helper.hashlib.sha256(ledger_body).hexdigest())
    marker_body = b"fresh marker\n"
    (state / "extension-input-scratch.json").write_bytes(marker_body)
    monkeypatch.setattr(
        helper, "SCRATCH_MARKER_SHA256", helper.hashlib.sha256(marker_body).hexdigest()
    )

    conn = database.connection
    conn.execute("BEGIN")
    for _target, snapshot in pairs:
        conn.execute(
            "UPDATE snapshots SET parser_version='8',parsed_at='sealed' WHERE snapshot_id=?",
            (snapshot,),
        )
    expected_after_tables = helper.all_table_hashes(conn)
    conn.rollback()
    assert helper.all_table_hashes(conn) == before_tables
    database.close()

    before_seal = {
        "format": "phase1-newsletter-parser8-before-v1",
        "helper_sha256": helper.sha(PATH),
        "state": str(state),
        "source": str(helper.SOURCE),
        "source_receipt_sha256": helper.SOURCE_RECEIPT_SHA256,
        "packet_sha256": helper.PACKET_SHA256,
        "checkpoint": str(helper.CHECKPOINT),
        "checkpoint_sha256": helper.CHECKPOINT_SHA256,
        "reference_marker_sha256": helper.SCRATCH_MARKER_SHA256,
        "fresh_marker_sha256": helper.SCRATCH_MARKER_SHA256,
        "fresh_prepare_receipt_sha256": "1" * 64,
        "fresh_accept_receipt_sha256": "2" * 64,
        "input_helper_sha256": helper.INPUT_HELPER_SHA256,
        "input_bundle_sha256": helper.INPUT_BUNDLE_SHA256,
        "catalog_sha256": helper.CATALOG_SHA256,
        "ledger_sha256": helper.LEDGER_SHA256,
        "target_pairs_sha256": helper.TARGETS_SHA256,
        "operation_at": "2026-09-17T11:00:00+00:00",
        "replay_run_id": "run_20260917T110000Z",
        "before_invariants": before_invariants,
        "before_tables": before_tables,
        "expected_after_tables": expected_after_tables,
        "network_requests": 0,
        "database_changes": 0,
    }
    before_path = state / "phase1-newsletter-parser8-before.json"
    before_path.write_bytes(helper.canonical(before_seal) + b"\n")
    before_sha = helper.sha(before_path)
    output = state / "phase1-newsletter-parser8-receipt.json"
    ledger_output = state / "phase1-ledger-parser8.json"

    monkeypatch.setattr(helper, "verify_catalog_and_ledger", lambda _state: (tuple(), ledger))
    monkeypatch.setattr(
        helper,
        "verify_targets_and_bodies",
        lambda _conn, _state, _catalog, _ledger: {snapshot: "body" for _, snapshot in pairs},
    )
    monkeypatch.setattr(helper, "verify_database_authority", lambda *_args, **_kwargs: None)

    def apply(db, *_args):
        with db.transaction():
            for _target, snapshot in pairs:
                db.connection.execute(
                    "UPDATE snapshots SET parser_version='8',parsed_at='sealed' WHERE snapshot_id=?",
                    (snapshot,),
                )
            helper.require_exact_tables(db.connection, expected_after_tables, "test dry derivation")
        return expected_after_tables, "run_20260917T110000Z"

    def promoted(conn, target, snapshot, _run_id, _created_at):
        assert (
            conn.execute(
                "SELECT parser_version FROM snapshots WHERE snapshot_id=?", (snapshot,)
            ).fetchone()[0]
            == "8"
        )
        return (
            {"snapshot_id": snapshot, "parser_version": "8", "status": "empty", "observations": 0},
            {"target_id": target, "snapshot_id": snapshot, "mode": "shadow"},
        )

    monkeypatch.setattr(helper, "apply_database_transaction", apply)
    monkeypatch.setattr(helper, "verify_promoted", promoted)
    first = helper.replay(state, output, ledger_output, before_path, before_sha)
    second = helper.replay(state, output, ledger_output, before_path, before_sha)
    assert first == second
    assert helper.sha(output) == helper.hashlib.sha256(helper.canonical(first) + b"\n").hexdigest()
    assert (state / "phase1-newsletter-parser8-replay.pending.json").is_file()
