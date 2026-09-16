"""Independent V4 policy, withdrawal and acquisition receipt regressions."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

spec = importlib.util.spec_from_file_location(
    "v4_candidate_acceptance",
    Path(__file__).parents[1] / "journal/tools/releases/v4_candidate_acceptance.py",
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def scoring():
    with duckdb.connect() as conn:
        for prefix in ("old", "new"):
            conn.execute(f"CREATE TABLE {prefix}_contests(contest_id VARCHAR,parse_status VARCHAR)")
            conn.execute(f"CREATE TABLE {prefix}_rounds(round_id VARCHAR,contest_id VARCHAR)")
            for table in ("entries", "placements"):
                conn.execute(f"CREATE TABLE {prefix}_{table}(contest_id VARCHAR)")
            for table in ("callbacks", "callback_marks", "final_marks"):
                conn.execute(f"CREATE TABLE {prefix}_{table}(round_id VARCHAR)")
        conn.execute(
            "INSERT INTO old_contests SELECT cast(i AS VARCHAR),'parsed' FROM range(67) t(i)"
        )
        conn.execute("INSERT INTO new_contests SELECT contest_id,'unsupported' FROM old_contests")
        conn.execute(
            "INSERT INTO old_rounds SELECT cast(i AS VARCHAR),cast(i%67 AS VARCHAR) FROM range(68) t(i)"
        )
        conn.execute("INSERT INTO old_entries SELECT '0' FROM range(1358)")
        conn.execute("INSERT INTO old_placements SELECT '0' FROM range(930)")
        conn.execute("INSERT INTO old_final_marks SELECT '0' FROM range(683)")
        impact = {
            "newly_unsupported": [{"contest_id": str(i)} for i in range(67)],
            "prior_rows_in_newly_unsupported_contests": {
                "rounds": 68,
                "entries": 1358,
                "placements": 930,
                "callbacks": 0,
                "callback_marks": 0,
                "final_marks": 683,
            },
        }
        yield conn, impact


def test_exact_reviewed_numeric_withdrawal_passes(scoring):
    conn, impact = scoring
    assert all(module.audit_unsupported(conn, impact)["checks"].values())


def test_changed_candidate_seal_emits_failed_receipt(tmp_path, monkeypatch):
    output = tmp_path / "audit.json"
    arguments = ["audit"]
    for name in (
        "baseline",
        "candidate",
        "state",
        "checkpoint-state",
        "bootstrap-receipt",
        "phase1",
        "impact",
    ):
        arguments.extend(["--" + name, str(tmp_path / name)])
    arguments.extend(["--output", str(output), "--expected-parent", "published"])
    monkeypatch.setattr("sys.argv", arguments)

    def changed_seal(_args):
        raise module.StaleCandidateError("candidate file closure changed after build")

    monkeypatch.setattr(module, "audit", changed_seal)
    with pytest.raises(SystemExit, match="1"):
        module.main()
    receipt = json.loads(output.read_text())
    assert receipt["passed"] is False
    assert receipt["error_type"] == "StaleCandidateError"
    assert "file closure" in receipt["error"]
    assert receipt["state_mutations"] == receipt["network_requests"] == 0


def test_audit_opens_checkpoint_without_wal_sidecars(tmp_path, monkeypatch):
    paths = {}
    for name in ("state", "checkpoint_state", "phase1", "baseline"):
        paths[name] = tmp_path / name
        paths[name].mkdir()
    for name in ("state", "checkpoint_state"):
        connection = sqlite3.connect(paths[name] / "state.sqlite")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE probe(value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('retained')")
        connection.commit()
        connection.close()
    original = (paths["checkpoint_state"] / "state.sqlite").read_bytes()
    for name in ("bootstrap_receipt", "impact"):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text("{}")
    (paths["phase1"] / "phase1-export.json").write_text("{}")
    (paths["baseline"] / "PUBLISHED").write_text("{}")
    connect = sqlite3.connect

    def read_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        assert connection.execute("SELECT value FROM probe").fetchone()[0] == "retained"
        return connection

    def stop_before_candidate(*_args):
        assert sorted(path.name for path in paths["checkpoint_state"].iterdir()) == ["state.sqlite"]
        raise ValueError("connection check complete")

    monkeypatch.syspath_prepend(str(Path(module.__file__).parent))
    monkeypatch.setattr(module.sqlite3, "connect", read_connection)
    monkeypatch.setattr(module, "load_candidate", stop_before_candidate)
    with pytest.raises(ValueError, match="connection check complete"):
        module.audit(SimpleNamespace(**paths))
    assert sorted(path.name for path in paths["checkpoint_state"].iterdir()) == ["state.sqlite"]
    assert (paths["checkpoint_state"] / "state.sqlite").read_bytes() == original


@pytest.mark.parametrize(
    "mutation,check",
    [
        (
            "UPDATE new_contests SET parse_status='parsed' WHERE contest_id='0'",
            "exact_unsupported_transition",
        ),
        ("INSERT INTO new_entries VALUES ('0')", "unsupported_results_withheld"),
        ("DELETE FROM old_final_marks", "baseline_impact_matches"),
    ],
)
def test_partial_or_changed_scoring_withdrawals_fail(scoring, mutation, check):
    conn, impact = scoring
    conn.execute(mutation)
    assert not module.audit_unsupported(conn, impact)["checks"][check]


def control_state():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE watches(watch_id TEXT,kind TEXT,url TEXT,current_observation_snapshot_id TEXT,fingerprint TEXT,extract_version TEXT);
    CREATE TABLE snapshots(snapshot_id TEXT);
    CREATE TABLE findings(finding_id TEXT,evidence_json TEXT,owner_kind TEXT,owner_id TEXT,closed_at TEXT);
    INSERT INTO watches VALUES ('existing','round','https://example/existing','old','old','1');
    INSERT INTO snapshots VALUES ('original');
    """)
    return conn


@pytest.mark.parametrize(
    "mutation,check",
    [
        (None, None),
        (
            "INSERT INTO watches VALUES ('new','round','https://example/new',NULL,NULL,NULL)",
            "acquisition_controls_preserved",
        ),
        (
            "UPDATE watches SET url='https://example/changed' WHERE watch_id='existing'",
            "acquisition_controls_preserved",
        ),
        (
            "INSERT INTO snapshots VALUES ('unreviewed-origin-fetch')",
            "only_phase1_snapshots_imported",
        ),
        ("DELETE FROM findings", "gated_intents_retained_without_controls"),
    ],
)
def test_acquisition_gate_checks_actual_state_not_only_receipt(mutation, check):
    baseline, state = control_state(), control_state()
    try:
        state.execute(
            "INSERT INTO watches VALUES ('calendar','index','https://example/calendar',NULL,NULL,NULL)"
        )
        state.execute("INSERT INTO snapshots VALUES ('phase1')")
        state.execute(
            "INSERT INTO findings VALUES ('finding',?,'acquisition_gate','gated',NULL)",
            (
                json.dumps(
                    {
                        "intended_watch_id": "gated",
                        "url": "https://example/gated",
                        "source_event_ref": "source:event",
                        "required_gates": ["G2", "G3"],
                    }
                ),
            ),
        )
        state.execute(
            "UPDATE watches SET current_observation_snapshot_id='new',fingerprint='new',extract_version='3' WHERE watch_id='existing'"
        )
        if mutation:
            state.execute(mutation)
        result = module.audit_acquisition(
            state,
            baseline,
            {
                "new_acquisition_watches": 0,
                "network_requests": 0,
                "stages": {"parse": {"gated_new_rounds": ["gated"]}},
            },
            {"snapshots": [{"snapshot_id": "phase1"}]},
        )
        if check is None:
            assert all(result["checks"].values())
        else:
            assert not result["checks"][check]
    finally:
        baseline.close()
        state.close()


def test_autoindex_must_have_its_new_contract_review():
    state = sqlite3.connect(":memory:")
    state.row_factory = sqlite3.Row
    try:
        state.executescript(
            "CREATE TABLE admission_policies(page_kind TEXT,contract_version TEXT,mode TEXT,reviewed_report_digest TEXT); CREATE TABLE admission_reviews(report_digest TEXT,page_kind TEXT,contract_version TEXT,cohort_json TEXT,reviewer TEXT,evidence TEXT);"
        )
        receipt = {"policies": {}}
        for kind, version in module.POLICIES.items():
            state.execute(
                "INSERT INTO admission_policies VALUES (?,?,'enforce',?)", (kind, version, kind)
            )
            state.execute(
                "INSERT INTO admission_reviews VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    kind,
                    version,
                    json.dumps(
                        {"external_corpus": module.CORPUS5 if version == "5" else module.CORPUS4}
                    ),
                    "explicit-test-reviewer",
                    "synthetic reviewed corpus",
                ),
            )
            receipt["policies"][kind] = kind
        assert all(module.audit_policies(state, receipt)["checks"].values())
        state.execute(
            "UPDATE admission_reviews SET cohort_json=? WHERE page_kind='eepro.autoindex'",
            (json.dumps({"external_corpus": module.CORPUS4}),),
        )
        assert not module.audit_policies(state, receipt)["checks"]["exact_review_receipts"]
    finally:
        state.close()


@pytest.mark.parametrize(
    "mutation,check",
    [
        (None, None),
        (
            "UPDATE source_generations SET report_json='{\"failures\":[\"critical_unknown\"]}' WHERE generation_id='accepted'",
            "accepted_pointers_resolve_passing_contracts",
        ),
        (
            "UPDATE findings SET summary='Generic blocked'",
            "guarded_current_units_have_public_reasons",
        ),
        ("DELETE FROM observations", "failed_legacy_observations_preserved"),
    ],
)
def test_current_admission_pointer_failures_are_visible_and_preserve_legacy(mutation, check):
    state, baseline = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    state.row_factory = baseline.row_factory = sqlite3.Row
    with duckdb.connect() as public:
        try:
            for conn in (state, baseline):
                conn.executescript("""
                CREATE TABLE observations(watch_id TEXT,snapshot_id TEXT,kind TEXT,scope_kind TEXT,scope_id TEXT,seq INTEGER,extract_version TEXT,parser_version TEXT,payload_json TEXT);
                INSERT INTO observations VALUES ('watch','legacy','round_sheet','source_event','event',0,'1','1','{"retained":"source"}');
                """)
            state.executescript("""
            CREATE TABLE source_units(unit_key TEXT,watch_id TEXT,page_kind TEXT,accepted_generation_id TEXT,desired_fingerprint TEXT,legacy_snapshot_id TEXT,legacy_state TEXT);
            CREATE TABLE source_generations(generation_id TEXT,unit_key TEXT,state TEXT,contract_version TEXT,report_json TEXT,recipe_json TEXT,input_fingerprint TEXT,created_at TEXT);
            CREATE TABLE admission_policies(page_kind TEXT,contract_version TEXT,mode TEXT);
            CREATE TABLE findings(finding_id TEXT,kind TEXT,summary TEXT,owner_kind TEXT,owner_id TEXT,snapshot_id TEXT,closed_at TEXT);
            INSERT INTO admission_policies VALUES ('source.round','4','enforce');
            INSERT INTO source_units VALUES ('ok','okwatch','source.round','accepted','a',NULL,'none');
            INSERT INTO source_units VALUES ('blocked','watch','source.round',NULL,'b','legacy','legacy_unassessed');
            INSERT INTO source_generations VALUES ('accepted','ok','accepted','4','{"failures":[]}','{"context":{"snapshot_id":"ok"}}','a','2026');
            INSERT INTO source_generations VALUES ('failed','blocked','needs_review','4','{"failures":["non_authoritative_row_loss"]}','{"context":{"snapshot_id":"new"}}','b','2026');
            INSERT INTO findings VALUES ('finding','admission_blocked','Blocked: non_authoritative_row_loss','admission','blocked','new',NULL);
            """)
            public.execute(
                "CREATE TABLE new_review_queue(item_id VARCHAR,kind VARCHAR,summary VARCHAR)"
            )
            public.execute(
                "INSERT INTO new_review_queue VALUES ('finding','admission_blocked','Blocked: non_authoritative_row_loss')"
            )
            if mutation:
                state.execute(mutation)
            result = module.audit_admission_outputs(public, state, baseline)
            if check is None:
                assert all(result["checks"].values())
            else:
                assert not result["checks"][check]
        finally:
            state.close()
            baseline.close()
