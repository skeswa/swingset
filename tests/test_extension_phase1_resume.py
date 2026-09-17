"""Successor phase-one packet tests; disposable state and fake HTTP only."""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.history import intake
from swingset.history.catalog import Target, save_catalog
from swingset.schedule.cycle import versions
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.inputs import accept, capture

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "journal/tools/collection/prepare_extension_phase1_resume.py"
spec = importlib.util.spec_from_file_location("extension_phase1", PATH)
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)
NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
NIX_SOURCE = "/nix/store/" + "a" * 32 + "-source"


def frozen(tmp_path):
    source = tmp_path / "source"
    (source / "src/swingset/state").mkdir(parents=True)
    (source / "src/swingset/state/db.py").write_text(f"SCHEMA_VERSION = {SCHEMA_VERSION}\n")
    files = {"src/swingset/state/db.py": helper.sha(source / "src/swingset/state/db.py")}
    receipt = source / "extension-source.json"
    receipt.write_text(json.dumps({"files": files}))
    return source, helper.sha(receipt)


@pytest.fixture
def packet(tmp_path):
    source, sha = frozen(tmp_path)
    path = tmp_path / "packet"
    helper.build(
        source, NIX_SOURCE, "extension-source.json", sha, path, schema_version=SCHEMA_VERSION
    )
    return path / "packet.json"


def test_build_closed_packet_is_deterministic_and_has_original_scope(tmp_path):
    source, sha = frozen(tmp_path)
    for name in ["one", "two"]:
        result = helper.build(
            source,
            NIX_SOURCE,
            "extension-source.json",
            sha,
            tmp_path / name,
            schema_version=SCHEMA_VERSION,
        )
        assert result["network_requests"] == result["production_operations"] == 0
    assert (tmp_path / "one/packet.json").read_bytes() == (
        tmp_path / "two/packet.json"
    ).read_bytes()
    packet = helper.verify_packet(tmp_path / "one/packet.json", result["packet_sha256"])
    assert packet["schema"] == SCHEMA_VERSION
    base = helper.load_base(tmp_path / "one/packet.json")
    original = json.loads((tmp_path / "one/original-scope.json").read_bytes())
    assert set(original["original_targets"]) == base.TARGET_IDS and len(base.TARGET_IDS) == 17


@pytest.mark.parametrize("damage", ["extra", "changed", "linked", "schema14"])
def test_frozen_inventory_and_schema_cannot_be_replaced(tmp_path, damage):
    source, sha = frozen(tmp_path)
    if damage == "extra":
        (source / "unreviewed.py").write_text("new")
    elif damage == "linked":
        (source / "linked").symlink_to(source / "src/swingset/state/db.py")
    else:
        (source / "src/swingset/state/db.py").write_text("SCHEMA_VERSION = 14\n")
        if damage == "schema14":
            (source / "extension-source.json").write_text(
                json.dumps(
                    {
                        "files": {
                            "src/swingset/state/db.py": helper.sha(
                                source / "src/swingset/state/db.py"
                            )
                        }
                    }
                )
            )
            sha = helper.sha(source / "extension-source.json")
    with pytest.raises(ValueError):
        helper.verify_source(source, "extension-source.json", sha, SCHEMA_VERSION)


@pytest.mark.parametrize("damage", ["base", "runner", "scope", "extra"])
def test_altered_packet_cannot_expand_authority(packet, damage):
    sha = helper.sha(packet)
    file = {
        "base": "base.py",
        "runner": "runner.py",
        "scope": "original-scope.json",
        "extra": "unreviewed.py",
    }[damage]
    (packet.parent / file).write_text("changed")
    with pytest.raises(ValueError):
        helper.verify_packet(packet, sha)


@pytest.fixture
def held_state(tmp_path, packet, monkeypatch):
    state = tmp_path / "state"
    clock = FakeClock(NOW)
    # Freeze a disposable test's captured identity once; concurrent agents may
    # edit the shared working tree while this module exercises stable inputs.
    import swingset.schedule.cycle as cycle
    import swingset.state.inputs as input_module

    fixed_versions, fixed_runtime = cycle.versions(), input_module.capture_runtime()
    monkeypatch.setattr(cycle, "versions", lambda: fixed_versions.copy())
    monkeypatch.setattr(sys.modules[__name__], "versions", lambda: fixed_versions.copy())
    monkeypatch.setattr(input_module, "capture_runtime", lambda: fixed_runtime.copy())
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout="\n".join(["inactive"] * 6)),
    )
    with open_database(state) as db:
        (state / "operator-hold").write_text("retained hold\n")
        candidate = state / "candidate"
        (candidate / "_meta").mkdir(parents=True)
        (candidate / "PUBLISHED").write_text(json.dumps({"commit": helper.BASELINE}))
        (candidate / "_meta/manifest.json").write_text("{}")
        (state / "baseline").symlink_to(candidate)
        bundle = capture(ROOT / "config", ROOT / "overrides", state, versions())
        accept(db, bundle, clock)
        expected = helper.expected_bundle(ROOT, ROOT / "config", ROOT / "overrides", NOW)
        original = json.loads((packet.parent / "original-scope.json").read_bytes())
        targets = tuple(Target(**row) for row in original["original_targets"].values())
        save_catalog(state / "phase1-catalog.json", targets)
        (state / "phase1-ledger.json").write_text(
            json.dumps({"targets": {t.target_id: {"status": "pending"} for t in targets}})
        )
    # Preparation uses a new read-only connection so its total_changes is zero.
    import sqlite3

    conn = sqlite3.connect(f"file:{state}/state.sqlite?mode=ro", uri=True, isolation_level=None)
    base = helper.load_base(packet)
    gate = helper.assemble(
        base,
        conn,
        state,
        ROOT,
        packet,
        helper.sha(packet),
        json.loads(packet.read_bytes()),
        expected,
        ROOT / "config",
        ROOT / "overrides",
        NOW,
    )
    assert conn.total_changes == 0
    conn.close()
    return state, clock, expected, base, gate, targets


def test_real_accepted_bundle_is_required_not_just_old_pointer(held_state):
    state, _, expected, _, _, _ = held_state
    with open_database(state) as db:
        helper.check_accepted(db.connection, state, expected, verify_bytes=True)
        db.connection.execute(
            "UPDATE accepted_inputs SET digest='old' WHERE input_name='recipe/runtime'"
        )
        with pytest.raises(ValueError, match="semantic input"):
            helper.check_accepted(db.connection, state, expected, verify_bytes=True)


@pytest.mark.parametrize("damage", ["pointer", "bundle_bytes", "foreign_input"])
def test_changed_input_authority_is_rejected(held_state, damage):
    state, _, expected, _, _, _ = held_state
    with open_database(state) as db:
        if damage == "pointer":
            db.connection.execute("UPDATE meta SET value='old' WHERE key='input_bundle_hash'")
        elif damage == "bundle_bytes":
            (state / "inputs" / expected["digest"] / "config/sources.toml").write_text("changed")
        else:
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline','unreviewed-input','new')"
            )
        with pytest.raises(ValueError):
            helper.check_accepted(db.connection, state, expected, verify_bytes=True)


@pytest.mark.parametrize(
    "damage", ["hold", "restore", "hold_bytes", "active_unit", "schema", "pragma"]
)
def test_live_operating_and_schema_interlocks(held_state, monkeypatch, damage):
    state, _, _, _, gate, _ = held_state
    with open_database(state) as db:
        if damage == "hold":
            (state / "operator-hold").unlink()
        elif damage == "restore":
            (state / "RESTORE_PENDING").touch()
        elif damage == "hold_bytes":
            (state / "operator-hold").write_text("changed")
        elif damage == "active_unit":
            monkeypatch.setattr(
                helper.subprocess,
                "run",
                lambda *a, **kw: SimpleNamespace(stdout="active\n" + "inactive\n" * 5),
            )
        elif damage == "schema":
            db.connection.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
        else:
            db.connection.execute("PRAGMA user_version=14")
        with pytest.raises(ValueError):
            helper.schema(db.connection, gate["schema"])
            helper.operating_guards(state, gate["operator_hold_sha256"], units=True)


def test_fresh_subset_gate_cannot_change_original_target_identity(held_state, packet):
    state, _, _, base, gate, targets = held_state
    with open_database(state) as db:
        assert len(helper.check_scope(base, db.connection, state, gate, packet)) == 17
        gate["original_targets"][targets[0].target_id]["url"] = "https://example.org/new"
        with pytest.raises(ValueError, match="original17"):
            helper.check_scope(base, db.connection, state, gate, packet)


@pytest.mark.parametrize("stop", ["hold", "inputs", "pause", "budget"])
def test_ordinary_intake_stops_after_robots_without_scope_or_budget_bypass(
    held_state, packet, monkeypatch, stop
):
    from swingset.state.controls import Selector, change_control

    state, clock, expected, base, gate, targets = held_state
    with open_database(state) as db:
        helper.guard_base(
            base, ROOT, state, gate, expected, ROOT / "config", ROOT / "overrides", packet
        )
        base.check_authority(db.connection, state, gate)
        chosen = {target.target_id: target for target in targets}
        client = base.scoped_client(
            intake.FetchClient, state, gate, chosen, monotonic=clock.monotonic
        )
        monkeypatch.setattr(intake, "FetchClient", client)
        requested = []

        def reply(request):
            requested.append(str(request.url))
            assert request.url.path == "/robots.txt"
            if stop == "hold":
                (state / "operator-hold").write_text("changed hold")
            elif stop == "inputs":
                db.connection.execute("UPDATE meta SET value='old' WHERE key='input_bundle_hash'")
            elif stop == "pause":
                change_control(
                    state,
                    selector=Selector("source", "wsdc_calendar"),
                    paused=True,
                    actor="offline test",
                    reason="test",
                    now=clock.now(),
                )
            return httpx.Response(404, content=b"")

        config = Config(
            {
                "web.archive.org": HostConfig(
                    min_gap_seconds=10, daily_request_budget=1 if stop == "budget" else 48
                )
            },
            {"wsdc_calendar": SourceConfig(True)},
        )
        ledger = intake.run_intake(
            db,
            state / "phase1-catalog.json",
            config=config,
            clock=clock,
            max_targets=17,
            wall_seconds=600,
            retry_failures=False,
            transport=httpx.MockTransport(reply),
        )
        assert requested == ["https://web.archive.org/robots.txt"]
        assert all(row["status"] == "pending" for row in ledger["targets"].values())
        assert db.connection.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM execution_admissions WHERE state='admitted'"
            ).fetchone()[0]
            == 0
        )
        assert db.connection.execute("SELECT COUNT(*) FROM history_acceptance").fetchone()[0] == 0


@pytest.mark.parametrize("pressure", [False, True])
def test_actual_fetch_retry_spacing_and_parse_backpressure_are_preserved(
    held_state, packet, monkeypatch, pressure
):
    from swingset.state.work import WorkUnit, enqueue

    state, clock, expected, base, gate, targets = held_state
    body = (
        ROOT / "src/swingset/sources/wsdc_calendar/fixtures/calendar-20210415.html"
    ).read_bytes()
    with open_database(state) as db:
        helper.guard_base(
            base, ROOT, state, gate, expected, ROOT / "config", ROOT / "overrides", packet
        )
        base.check_authority(db.connection, state, gate)
        client = base.scoped_client(
            intake.FetchClient,
            state,
            gate,
            {t.target_id: t for t in targets},
            monotonic=clock.monotonic,
        )
        monkeypatch.setattr(intake, "FetchClient", client)
        if pressure:
            enqueue(
                db.connection,
                (WorkUnit("parse", "snapshot", f"retained-{i}") for i in range(1000)),
                enqueued_at=clock.now().isoformat(),
            )
        requests = []
        ends = []

        def reply(request):
            requests.append((str(request.url), clock.monotonic()))
            clock.sleep(2)
            ends.append(clock.monotonic())
            if request.url.path == "/robots.txt":
                return httpx.Response(404, content=b"")
            assert str(request.url) in {target.archive_url for target in targets}
            return httpx.Response(
                500 if len(requests) == 2 else 200, content=b"fail" if len(requests) == 2 else body
            )

        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10, daily_request_budget=3)},
            {"wsdc_calendar": SourceConfig(True)},
        )
        ledger = intake.run_intake(
            db,
            state / "phase1-catalog.json",
            config=config,
            clock=clock,
            max_targets=1,
            wall_seconds=600,
            retry_failures=False,
            transport=httpx.MockTransport(reply),
        )
        if pressure:
            assert requests == []
            assert all(row["status"] == "pending" for row in ledger["targets"].values())
            assert (
                db.connection.execute(
                    "SELECT COALESCE(SUM(requests),0) FROM host_budget"
                ).fetchone()[0]
                == 0
            )
        else:
            assert len(requests) == 3
            assert all(
                later[1] - earlier >= 10 for earlier, later in zip(ends, requests[1:], strict=False)
            )
            assert db.connection.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0] == 3
            assert sum(row["status"] == "parsed" for row in ledger["targets"].values()) == 1
            assert sum(row["status"] == "pending" for row in ledger["targets"].values()) == 16
            assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] > 0
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM execution_admissions WHERE state='admitted'"
            ).fetchone()[0]
            == 0
        )
        assert db.connection.execute("SELECT COUNT(*) FROM history_acceptance").fetchone()[0] == 0


def test_reviewed_partial_remainder_needs_fresh_gate_and_current_interpretation(held_state, packet):
    from swingset.sources import get_page_kind

    state, _, _, base, gate, targets = held_state
    target = targets[0]
    ledger_path = state / "phase1-ledger.json"
    ledger = json.loads(ledger_path.read_bytes())
    ledger["targets"][target.target_id] = {
        "status": "parsed",
        "parser_version": str(get_page_kind(target.parser).PARSER_VERSION),
    }
    ledger_path.write_text(json.dumps(ledger))
    with open_database(state) as db:
        with pytest.raises(ValueError, match="ledger changed"):
            helper.check_scope(base, db.connection, state, gate, packet)
        gate["ledger_sha256"] = helper.sha(ledger_path)
        gate["remaining_target_ids"].remove(target.target_id)
        gate["target_statuses"][target.target_id] = "parsed"
        assert len(helper.check_scope(base, db.connection, state, gate, packet)) == 16
        ledger["targets"][target.target_id]["parser_version"] = "old"
        ledger_path.write_text(json.dumps(ledger))
        gate["ledger_sha256"] = helper.sha(ledger_path)
        with pytest.raises(ValueError, match="offline replay"):
            helper.check_scope(base, db.connection, state, gate, packet)


@pytest.mark.parametrize(
    "kind,scope", [("host", "web.archive.org"), ("kind", "source_event_mapping"), ("all", "all")]
)
def test_paused_host_and_source_kind_controls_remain_ordinary_authority(
    held_state, packet, monkeypatch, kind, scope
):
    from swingset.state.controls import Selector, change_control

    state, clock, expected, base, gate, targets = held_state
    with open_database(state) as db:
        helper.guard_base(
            base, ROOT, state, gate, expected, ROOT / "config", ROOT / "overrides", packet
        )
        base.check_authority(db.connection, state, gate)
        change_control(
            state,
            selector=Selector(kind, scope),
            paused=True,
            actor="offline",
            reason="retained pause",
            now=clock.now(),
            hosts=("web.archive.org",),
        )
        client = base.scoped_client(
            intake.FetchClient,
            state,
            gate,
            {t.target_id: t for t in targets},
            monotonic=clock.monotonic,
        )
        monkeypatch.setattr(intake, "FetchClient", client)
        ledger = intake.run_intake(
            db,
            state / "phase1-catalog.json",
            config=Config({"web.archive.org": HostConfig()}, {"wsdc_calendar": SourceConfig(True)}),
            clock=clock,
            max_targets=17,
            wall_seconds=600,
            retry_failures=False,
            transport=httpx.MockTransport(lambda req: pytest.fail("paused host must not send")),
        )
        assert all(row["status"] == "pending" for row in ledger["targets"].values())
        assert (
            db.connection.execute("SELECT COALESCE(SUM(requests),0) FROM host_budget").fetchone()[0]
            == 0
        )


def test_actual_wrapper_to_base_main_preflight_is_read_only(held_state, tmp_path, monkeypatch):
    """Mock only immutable/live OS bindings; execute the actual sealed base main."""
    import shutil

    import swingset.state.db as db_module

    state, _, expected, _, gate, _ = held_state
    source = tmp_path / "full-source"
    (source / "src/swingset/state").mkdir(parents=True)
    shutil.copyfile(db_module.__file__, source / "src/swingset/state/db.py")
    shutil.copytree(ROOT / "config", source / "config")
    shutil.copytree(ROOT / "overrides", source / "overrides")
    receipt = source / "extension-source.json"
    receipt.write_text(
        json.dumps(
            {
                "files": {
                    p.relative_to(source).as_posix(): helper.sha(p)
                    for p in source.rglob("*")
                    if p.is_file()
                }
            }
        )
    )
    packet_root = tmp_path / "full-packet"
    helper.build(
        source,
        NIX_SOURCE,
        receipt.name,
        helper.sha(receipt),
        packet_root,
        schema_version=SCHEMA_VERSION,
    )
    packet = packet_root / "packet.json"
    packet_sha = helper.sha(packet)
    sealed = helper.verify_packet(packet, packet_sha)
    base = helper.load_base(packet)
    real_path = Path

    def bound_path(value):
        path = real_path(value)
        if path == real_path(NIX_SOURCE):
            return source
        if path == real_path(db_module.__file__):
            return source / "src/swingset/state/db.py"
        return path

    base.Path = bound_path
    # The immutable mount name is virtual in this disposable filesystem.
    base.str = lambda value: (
        NIX_SOURCE if isinstance(value, real_path) and value == source else str(value)
    )
    monkeypatch.setattr(helper, "runtime", lambda packet: source)
    monkeypatch.setattr(helper, "load_base", lambda packet: base)
    require = helper.require
    monkeypatch.setattr(
        helper,
        "require",
        lambda ok, reason: require(
            True if reason == "live coordinator state required" else ok, reason
        ),
    )
    gate.update(
        {
            name: sealed[name]
            for name in ["source", "source_receipt", "source_receipt_sha256", "schema"]
        }
    )
    gate["packet_sha256"] = packet_sha
    gate["expected_bundle"] = expected
    operations = state / "operations"
    operations.mkdir()
    gate_path, output = operations / "gate.json", operations / "preflight.json"
    helper.write_new(gate_path, gate)
    before = helper.sha(state / "state.sqlite")
    monkeypatch.setattr(
        intake, "run_intake", lambda *a, **kw: pytest.fail("preflight must not run intake")
    )
    helper.run(packet, packet_sha, gate_path, helper.sha(gate_path), output, False)
    assert helper.sha(state / "state.sqlite") == before
    report = json.loads(output.read_bytes())
    assert report["execute"] is False and report["schema"] == SCHEMA_VERSION
    assert len(report["targets"]) == 17 and report["request_cap"] == 48
    assert report["budget_before"] == [0, 0]
    assert not output.with_suffix(".json.finished.json").exists()
    assert (state / "operator-hold").read_text() == "retained hold\n"
