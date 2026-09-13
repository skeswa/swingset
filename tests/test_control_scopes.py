"""Control dependencies follow the actual unsplittable projection dispatch."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.control_scopes import for_requirement, for_unit, for_watch, unit_allowed
from swingset.state.controls import Selector, change_control
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def event(conn, identifier, sources):
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,2026,'2026-01-01','2026-01-03','active',?,?,'snapshot','1',?,?,'run')",
        (
            identifier,
            identifier,
            identifier,
            json.dumps(sources),
            sources[0],
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )


def observation(db, source, scope, identifier, *, body="a" * 64):
    watch = WatchSpec(
        f"watch-{source}-{identifier}",
        source,
        "round",
        "GET",
        f"https://{source}.example/{identifier}",
        f"{source}.round",
        source_ref=identifier,
    )
    upsert_watch(db.connection, watch, NOW)
    snapshot = f"snapshot-{source}-{identifier}"
    db.connection.execute(
        "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)",
        (NOW.isoformat(),),
    )
    db.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,run_id,body_sha256,http_status,body_bytes,content_changed,classification) VALUES (?,?,'GET',?,?,'run',?,200,0,1,'Found')",
        (snapshot, watch.watch_id, watch.url, NOW.isoformat(), body),
    )
    db.connection.execute(
        "INSERT INTO observations VALUES (?,?,?,'fixture',?,?,0,'1','1','{}')",
        (snapshot, watch.watch_id, snapshot, scope, identifier),
    )
    return watch, snapshot


def pause(state, kind, identifier):
    change_control(
        state,
        selector=Selector(kind, identifier),
        paused=True,
        actor="scope-test",
        reason="dependency paused",
        now=NOW,
    )


def test_mapped_source_event_cannot_rebuild_another_paused_source(tmp_path):
    with open_database(tmp_path) as db:
        observation(db, "eepro", "source_event", "shared")
        event(db.connection, "canonical", ["eepro", "wdr"])
        db.connection.execute(
            "INSERT INTO source_event_map VALUES ('eepro','shared','canonical','fixture',1)"
        )
        unit = WorkUnit("project", "source_event", "shared")
        assert for_unit(db.connection, unit).sources == frozenset({"eepro", "wdr"})
        pause(tmp_path, "source", "wdr")
        assert not unit_allowed(db.connection, unit, now=NOW)


@pytest.mark.parametrize("other_mapping", [False, True])
def test_unmapped_source_event_inherits_global_map_fallback(tmp_path, other_mapping):
    with open_database(tmp_path) as db:
        observation(db, "eepro", "source_event", "needs-map")
        event(db.connection, "other", ["wsdc_calendar", "wdr"])
        if other_mapping:
            # A different origin's same reference does not satisfy the eepro lookup.
            db.connection.execute(
                "INSERT INTO source_event_map VALUES ('wdr','needs-map','other','fixture',1)"
            )
        unit = WorkUnit("project", "source_event", "needs-map")
        scope = for_unit(db.connection, unit)
        assert {"eepro", "wdr", "wsdc_registry", "wsdc_calendar"} <= scope.sources
        assert {
            "round_observations",
            "source_event_mapping",
            "registry_event_association",
        } <= scope.kinds
        pause(tmp_path, "source", "wsdc_calendar")
        assert not unit_allowed(db.connection, unit, now=NOW)


def test_map_contains_inline_event_and_registry_repair_kinds_but_history_does_not_reparse_rounds(
    tmp_path,
):
    with open_database(tmp_path) as db:
        event(db.connection, "retained-only", ["wdr"])
        mapping = WorkUnit("project", "map", "all")
        history = WorkUnit("project", "history", "all")
        assert "wdr" in for_unit(db.connection, mapping).sources
        assert "wdr" in for_unit(db.connection, history).sources
        assert "round_observations" in for_unit(db.connection, mapping).kinds
        assert "round_observations" not in for_unit(db.connection, history).kinds
        pause(tmp_path, "kind", "round_observations")
        assert not unit_allowed(db.connection, mapping, now=NOW)
        assert unit_allowed(db.connection, history, now=NOW)


@pytest.mark.parametrize("kind,identifier", [("calendar", "live-calendar"), ("dancer", "42")])
def test_inline_registry_association_obeys_its_kind_and_calendar_source_pause(
    tmp_path, kind, identifier
):
    with open_database(tmp_path) as db:
        event(db.connection, "calendar-event", ["wsdc_calendar"])
        unit = WorkUnit("project", kind, identifier)
        scope = for_unit(db.connection, unit)
        assert {"wsdc_registry", "wsdc_calendar"} <= scope.sources
        assert "registry_event_association" in scope.kinds
        pause(tmp_path, "kind", "registry_event_association")
        assert not unit_allowed(db.connection, unit, now=NOW)


def test_link_propagates_all_event_sources_and_registry_dependency(tmp_path):
    with open_database(tmp_path) as db:
        event(db.connection, "shared", ["eepro", "wdr"])
        unit = WorkUnit("link", "event", "shared")
        scope = for_unit(db.connection, unit)
        assert scope.sources == frozenset({"eepro", "wdr", "wsdc_registry"})
        assert {"source_id_checked", "first_point_reconsideration"} <= scope.kinds
        pause(tmp_path, "source", "wsdc_registry")
        assert not unit_allowed(db.connection, unit, now=NOW)


def test_requirement_and_execution_use_identical_shared_scope(tmp_path):
    with open_database(tmp_path) as db:
        event(db.connection, "shared", ["eepro", "wdr"])
        unit = WorkUnit("link", "event", "shared")
        requirement = {
            "kind": "work_attempt",
            "evidence_json": json.dumps(
                {"stage": "link", "unit_kind": "event", "unit_id": "shared"}
            ),
        }
        assert for_requirement(db.connection, requirement) == for_unit(db.connection, unit)


def test_shared_artifact_requirement_propagates_every_origin(tmp_path):
    with open_database(tmp_path) as db:
        observation(db, "eepro", "source_event", "one")
        observation(db, "wdr", "source_event", "two")
        scope = for_requirement(
            db.connection,
            {"kind": "archive_artifact", "subject_id": "a" * 64, "evidence_json": "{}"},
        )
        assert scope.sources == frozenset({"eepro", "wdr"})
        assert scope.kinds == frozenset({"archive_artifact"})


def test_unknown_origin_fails_closed_and_host_pause_is_request_only(tmp_path):
    with open_database(tmp_path) as db:
        unknown = for_unit(db.connection, WorkUnit("parse", "snapshot", "missing"))
        assert unknown.all_sources
        watch, snapshot = observation(db, "eepro", "source_event", "known")
        request = for_watch(
            db.connection, source="eepro", watch_id=watch.watch_id, host="eepro.example"
        )
        parsed = for_unit(db.connection, WorkUnit("parse", "snapshot", snapshot))
        assert request.host == "eepro.example" and parsed.host is None
        change_control(
            tmp_path,
            selector=Selector("source", "wdr"),
            paused=True,
            actor="scope-test",
            reason="short hold",
            now=NOW,
            until=NOW + timedelta(seconds=1),
        )
        assert not unit_allowed(db.connection, WorkUnit("parse", "snapshot", "missing"), now=NOW)
        assert unit_allowed(
            db.connection, WorkUnit("parse", "snapshot", "missing"), now=NOW + timedelta(seconds=1)
        )


def test_finding_dependency_lookup_uses_bounded_indexes(tmp_path):
    with open_database(tmp_path) as db:
        plan = [
            str(row[3])
            for row in db.connection.execute(
                "EXPLAIN QUERY PLAN SELECT DISTINCT kind FROM findings WHERE closed_at IS NULL AND (watch_id=? OR snapshot_id=? OR subject_id=?)",
                ("scope", "scope", "scope"),
            )
        ]
        assert not any("SCAN findings" in line for line in plan), plan
        assert sum("SEARCH findings USING INDEX" in line for line in plan) == 3, plan


def test_doctor_distinguishes_intentional_pause_unpaused_stall_and_stuck_drain(tmp_path):
    from swingset.state.controls import ActionScope, admission
    from swingset.state.requirement_report import inventory
    from swingset.state.requirements import Requirement, reconcile_requirement

    with open_database(tmp_path) as db:
        run = db.start_run(NOW)
        paused = reconcile_requirement(
            db.connection,
            Requirement(
                "source_event_mapping", "paused", "eepro", "ready", "map retained event", {}
            ),
            NOW,
            run,
        )
        stalled = reconcile_requirement(
            db.connection,
            Requirement(
                "source_event_mapping", "stalled", "wdr", "ready", "map retained event", {}
            ),
            NOW,
            run,
        )
        with admission(
            db,
            action_id="draining",
            action_kind="project",
            scope=ActionScope(sources=frozenset({"eepro"})),
            now=NOW,
            run_id=run,
        ):
            pass
        pause(tmp_path, "source", "eepro")
        report = inventory(db.connection, NOW + timedelta(hours=2))
        assert {row["requirement_id"] for row in report["alerts"]} == {stalled}
        assert report["paused"] == 1 and report["eligible"] == 1
        assert report["controls"]["stuck_drain"]
        assert report["controls"]["draining_attempts"][0]["action_id"] == "draining"
        assert report["stale"]  # Intentional pause never conceals stale reconciliation.
        assert report["oldest_unresolved_age_seconds"] == 7200
        held = next(row for row in report["requirements"] if row["finding_id"] == paused)
        assert held["state"] == "ready" and held["paused"]
        assert held["age_seconds"] == 7200
        assert held["pauses"][0]["paused_duration_seconds"] == 7200
