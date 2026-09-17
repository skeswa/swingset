"""Legacy adoption grants no work and cannot rewrite paid usage or controls."""

import importlib.util
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.politeness import Gate, Wait
from swingset.state import db as database_module

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "legacy_baselines", ROOT / "journal/tools/runtime/legacy_spacing_baselines.py"
)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


@pytest.fixture(autouse=True)
def reviewed_spacing_helper_schema(monkeypatch):
    """This fixed operation helper applies only to its reviewed schema 28."""
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 28)


@pytest.fixture
def evidence():
    return HELPER.fixture_evidence(
        ROOT / "journal/evidence/admission/fixture-exception-2026-09-17",
        ROOT / "tests/fixtures/runtime/h16_hosts_20260916.toml",
    )


def populate(conn, evidence):
    conn.execute(
        "INSERT INTO hosts(host,next_allowed_at) VALUES (?,?)",
        (HELPER.HOST, "2026-09-17T18:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO host_budget VALUES (?,?,?,?)",
        (HELPER.HOST, evidence["day"], evidence["requests"], evidence["received_bytes"]),
    )
    conn.execute("INSERT INTO hosts(host) VALUES ('unproven.example')")
    conn.execute("INSERT INTO host_budget VALUES ('unproven.example',?,2,99)", (evidence["day"],))
    for row in evidence["admissions"]:
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO execution_admissions({columns}) VALUES({placeholders})",
            tuple(row.values()),
        )


def config():
    return Config({HELPER.HOST: HostConfig(min_gap_seconds=10)}, {})


def clock():
    return FakeClock(datetime.fromisoformat("2026-09-17T17:00:00+00:00"))


@pytest.mark.parametrize("schema", [14, 28])
def test_prepare_is_readonly_and_only_proves_the_exact_retained_archive_run(
    tmp_path, monkeypatch, evidence, schema
):
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", schema)
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        before = db.connection.total_changes
        with db.transaction(immediate=False):
            report = HELPER.prepare(db.connection, config(), evidence)
        assert db.connection.total_changes == before
        assert report["schema"] == schema
        hosts = {row["host"]: row for row in report["hosts"]}
        assert hosts[HELPER.HOST]["eligible"] and hosts[HELPER.HOST]["gap_seconds"] == 10
        assert not hosts["unproven.example"]["eligible"]
        assert evidence["robots"]["http_status"] == 404


def test_apply_preserves_hold_fields_budgets_controls_and_later_deadline(tmp_path, evidence):
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        proposal = HELPER.prepare(db.connection, config(), evidence)
        before = HELPER.protected(db.connection)
        db.connection.set_authorizer(HELPER.authorizer)
        c = clock()
        with db.transaction():
            ids = HELPER.apply(
                db.connection,
                config(),
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=c.now(),
                clock=c,
            )
        assert HELPER.protected(db.connection) == before
        row = db.connection.execute("SELECT * FROM host_request_spacing_baselines").fetchone()
        assert (
            row["reservation_id"] == ids[HELPER.HOST]
            and row["evidence_ref"] == "sha256:" + "a" * 64
        )
        assert not db.connection.execute(
            "SELECT 1 FROM host_request_spacing WHERE host='unproven.example'"
        ).fetchone()
        c.sleep(2)
        with db.transaction():
            assert (
                HELPER.apply(
                    db.connection,
                    config(),
                    proposal,
                    evidence,
                    proposal_sha256="a" * 64,
                    stopped_at=c.now() - timedelta(seconds=2),
                    clock=c,
                )
                == ids
            )
        assert (
            db.connection.execute("SELECT count(*) FROM host_request_spacing_baselines").fetchone()[
                0
            ]
            == 1
        )
        db.connection.set_authorizer(None)
        gate = Gate(db.connection, config(), c)
        assert gate.acquire(HELPER.HOST) == Wait(10)
        assert HELPER.protected(db.connection) == before


@pytest.mark.parametrize(
    "change",
    [
        "paid_request",
        "later_day",
        "configuration",
        "unsettled_control",
        "robots_metadata",
        "later_admission",
    ],
)
def test_apply_rejects_changed_proposal_evidence_and_never_debits(tmp_path, evidence, change):
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        proposal = HELPER.prepare(db.connection, config(), evidence)
        policy = config()
        if change == "paid_request":
            db.connection.execute(
                "UPDATE host_budget SET requests=requests+1 WHERE host=?", (HELPER.HOST,)
            )
        elif change == "later_day":
            db.connection.execute(
                "INSERT INTO host_budget VALUES (?,'2026-09-18',1,100)", (HELPER.HOST,)
            )
        elif change == "configuration":
            policy = Config({HELPER.HOST: HostConfig(min_gap_seconds=20)}, {})
        elif change == "unsettled_control":
            db.connection.execute(
                "UPDATE execution_admissions SET state='active' WHERE action_id=?",
                (evidence["admissions"][0]["action_id"],),
            )
        elif change == "robots_metadata":
            db.connection.execute("UPDATE hosts SET robots_status=200 WHERE host=?", (HELPER.HOST,))
        else:
            db.connection.execute(
                "UPDATE execution_admissions SET action_id='unreviewed_request' WHERE action_id=?",
                (evidence["admissions"][0]["action_id"],),
            )
        before = HELPER.protected(db.connection)
        with pytest.raises(ValueError), db.transaction():
            HELPER.apply(
                db.connection,
                policy,
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=clock().now(),
                clock=clock(),
            )
        assert HELPER.protected(db.connection) == before
        assert not db.connection.execute("SELECT 1 FROM host_request_spacing_baselines").fetchone()


def test_apply_authorizer_rejects_triggered_budget_writes_and_rolls_back(tmp_path, evidence):
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        proposal = HELPER.prepare(db.connection, config(), evidence)
        before = HELPER.protected(db.connection)
        db.connection.execute(
            "CREATE TRIGGER bad_baseline AFTER INSERT ON host_request_spacing_baselines BEGIN UPDATE host_budget SET requests=0; END"
        )
        db.connection.set_authorizer(HELPER.authorizer)
        with pytest.raises(sqlite3.DatabaseError), db.transaction():
            HELPER.apply(
                db.connection,
                config(),
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=clock().now(),
                clock=clock(),
            )
        assert HELPER.protected(db.connection) == before
        assert not db.connection.execute("SELECT 1 FROM host_request_spacing_baselines").fetchone()


def test_retained_fixture_bytes_are_verified_before_generating_authority(tmp_path):
    with pytest.raises((FileNotFoundError, ValueError)):
        HELPER.fixture_evidence(tmp_path, ROOT / "tests/fixtures/runtime/h16_hosts_20260916.toml")


def test_schema14_proposal_survives_separate_migration_but_cannot_apply_before_it(
    tmp_path, monkeypatch, evidence
):
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 14)
    c = clock()
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        proposal = HELPER.prepare(db.connection, config(), evidence)
        with pytest.raises(ValueError, match="exact schema28"), db.transaction():
            HELPER.apply(
                db.connection,
                config(),
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=c.now(),
                clock=c,
            )
        assert db.schema_version == 14
        assert not db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='host_request_spacing'"
        ).fetchone()
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 28)
    with database_module.open_database(tmp_path) as db:
        with db.transaction():
            HELPER.apply(
                db.connection,
                config(),
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=c.now(),
                clock=c,
            )
        assert (
            db.connection.execute(
                "SELECT gap_seconds FROM host_request_spacing WHERE host=?", (HELPER.HOST,)
            ).fetchone()[0]
            == 10
        )
        assert (
            db.connection.execute(
                "SELECT gap_seconds FROM host_request_spacing WHERE host='unproven.example'"
            ).fetchone()[0]
            is None
        )
        assert HELPER.protected(db.connection) == proposal["protected"]


def test_exact_dcn_followup_replaces_stale_eight_request_proof_without_refunding(
    tmp_path, evidence
):
    current = HELPER.dcn_evidence(
        ROOT / "journal/evidence/admission/dcn-index-fixture-2026-09-17", evidence
    )
    assert current["requests"] == 10 and current["received_bytes"] == 2934701
    assert current["original_effective_gap_seconds"] == 10
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, current)
        assert not next(
            row
            for row in HELPER.prepare(db.connection, config(), evidence)["hosts"]
            if row["host"] == HELPER.HOST
        )["eligible"]
        proposal = HELPER.prepare(db.connection, config(), current)
        assert next(row for row in proposal["hosts"] if row["host"] == HELPER.HOST)["eligible"]
        before = HELPER.protected(db.connection)
        with db.transaction():
            HELPER.apply(
                db.connection,
                config(),
                proposal,
                current,
                proposal_sha256="b" * 64,
                stopped_at=clock().now(),
                clock=clock(),
            )
        assert HELPER.protected(db.connection) == before
        assert tuple(
            db.connection.execute(
                "SELECT requests,bytes FROM host_budget WHERE host=?", (HELPER.HOST,)
            ).fetchone()
        ) == (10, 2934701)


@pytest.mark.parametrize("stamp", ["2026-09-17T09:00:00-07:00", "invalid-time"])
def test_later_request_with_offset_or_unknown_time_blocks_original_proof(tmp_path, evidence, stamp):
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        extra = dict(evidence["admissions"][0], action_id="unexpected", admitted_at=stamp)
        db.connection.execute(
            f"INSERT INTO execution_admissions({','.join(extra)}) VALUES({','.join('?' for _ in extra)})",
            tuple(extra.values()),
        )
        assert (
            HELPER.assess_archive(db.connection, evidence)
            == "request admission history differs from the retained fixture run"
        )


def test_dcn_proof_rejects_missing_or_changed_retained_controls(tmp_path, evidence):
    with pytest.raises((FileNotFoundError, ValueError)):
        HELPER.dcn_evidence(tmp_path, evidence)


def test_trigger_cannot_grant_spacing_to_an_unreviewed_host(tmp_path, evidence):
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        proposal = HELPER.prepare(db.connection, config(), evidence)
        db.connection.execute(
            "CREATE TRIGGER bad_scope AFTER INSERT ON host_request_spacing_baselines BEGIN INSERT INTO host_request_spacing(host,reservation_id,gap_seconds,reserved_at,released_at) SELECT 'unproven.example','unreviewed_'||reservation_id,5,stopped_at,stopped_at FROM host_request_spacing_baselines WHERE host=NEW.host; END"
        )
        db.connection.set_authorizer(HELPER.authorizer)
        with pytest.raises(sqlite3.DatabaseError), db.transaction():
            HELPER.apply(
                db.connection,
                config(),
                proposal,
                evidence,
                proposal_sha256="a" * 64,
                stopped_at=clock().now(),
                clock=clock(),
            )
        assert not db.connection.execute("SELECT 1 FROM host_request_spacing_baselines").fetchone()
        assert not db.connection.execute(
            "SELECT 1 FROM host_request_spacing WHERE host='unproven.example'"
        ).fetchone()


def test_unreviewed_schema29_is_rejected_without_state_changes(tmp_path, evidence, monkeypatch):
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 29)
    with database_module.open_database(tmp_path) as db:
        populate(db.connection, evidence)
        before = db.connection.total_changes
        with pytest.raises(ValueError, match="schema14 or schema28"):
            HELPER.prepare(db.connection, config(), evidence)
        assert db.connection.total_changes == before
        assert not db.connection.execute("SELECT 1 FROM host_request_spacing_baselines").fetchone()
