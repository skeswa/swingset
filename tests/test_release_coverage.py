"""Pinned coverage distinguishes retained evidence from acquisition and identity."""

from datetime import UTC, datetime, timedelta

from swingset.build.builder import BuildInput
from swingset.build.coverage import enrich_coverage, health_token
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.state.db import open_database

NOW = datetime(2026, 9, 13, 10, tzinfo=UTC)
OLD = datetime(2019, 4, 1, tzinfo=UTC)


def fixture(db):
    conn = db.connection
    conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW.isoformat(),))
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state,current_observation_snapshot_id,ever_ok) VALUES ('watch','eepro','round','GET','https://example.test/round','eepro.round','eepro:event','live','old',1)"
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification,via,observed_at) VALUES ('old','watch','GET','https://example.test/round',?,200,'digest',10,1,'run','Ok','wayback',?)",
        (NOW.isoformat(), OLD.isoformat()),
    )
    tables = {name: [] for name in SCHEMAS}
    provenance = {"source": "eepro", "snapshot_id": "old", "parser_version": "1"}
    tables["events"] = [
        {
            "event_id": "event",
            "year": 2019,
            "coverage_tier": "sheets_partial",
            "held": "held",
            "date_precision": "day",
            **provenance,
        }
    ]
    tables["contests"] = [{"contest_id": "contest", "event_id": "event", **provenance}]
    tables["rounds"] = [{"round_id": "round", "contest_id": "contest", **provenance}]
    tables["entries"] = [
        {
            "entry_id": "entry",
            "event_id": "event",
            "contest_id": "contest",
            "wsdc_id": 1,
            **provenance,
        }
    ]
    tables["judges"] = [
        {
            "judge_id": "judge",
            "event_id": "event",
            "name_raw": "Named Without Number",
            "wsdc_id": None,
            **provenance,
        }
    ]
    tables["snapshots"] = [
        {
            "snapshot_id": "old",
            "source": "eepro",
            "observed_at": OLD,
            "fetched_at": NOW,
            "via": "wayback",
            "body_sha256": "digest",
            "http_status": 200,
        }
    ]
    tables["coverage"] = [
        {
            "year": 2019,
            "source": "eepro",
            "via": "wayback",
            "events": 1,
            "contests": 1,
            "rounds": 1,
            "entries": 1,
            "events_sheets_partial": 1,
            "events_day_precision": 1,
            "events_accepted": True,
            "expected_rounds": None,
            "parsed_rounds": 1,
            "last_changed_at": OLD,
        }
    ]
    return BuildInput(tables, SCHEMAS, PRIMARY_KEYS, {}, {}, "inputs")


def row(data, kind, *, source="eepro", via="wayback"):
    return next(
        item
        for item in data.tables["coverage"]
        if item["scope_kind"] == kind and item["source"] == source and item["via"] == via
    )


def test_existing_year_metrics_and_named_null_id_judge_are_preserved(tmp_path):
    with open_database(tmp_path) as db:
        data = fixture(db)
        enriched = enrich_coverage(
            db.connection, data, cutoff=NOW, sources=("eepro", "steprightsolutions")
        )
        year = row(enriched, "year")
        for key, value in data.tables["coverage"][0].items():
            assert year[key] == value
        assert year["discovered_units"] == year["acquired_units"] == year["interpreted_units"] == 1
        assert year["unassessed_units"] == 1
        assert year["identity_subjects"] == 2 and year["resolved_identities"] == 1
        assert year["discovery_denominator"] is None and year["discovery_universe"] == "unknown"
        assert year["acquisition_denominator"] == 1
        assert row(enriched, "event")["scope_id"] == "event"
        assert row(enriched, "source")["year"] is None
        unstarted = row(enriched, "source", source="steprightsolutions", via="unknown")
        assert unstarted["discovered_units"] == 0 and unstarted["discovery_denominator"] is None
        judge = enriched.tables["judges"][0]
        assert judge["name_raw"] == "Named Without Number" and judge["wsdc_id"] is None
        assert judge["scope_status"] == "legacy_unassessed"
        assert judge["evidence_observed_at"] == OLD
        assert "scope_status" not in data.tables["judges"][0]


def test_omitted_scope_is_named_and_link_withholding_keeps_structural_facts(tmp_path):
    with open_database(tmp_path) as db:
        data = fixture(db)
        closure = {
            "inventory": [
                {
                    "stage": "link",
                    "unit_kind": "event",
                    "unit_id": "event",
                    "status": "withheld",
                    "reason": "compatible_link_generation_unavailable",
                },
                {
                    "stage": "project",
                    "unit_kind": "event",
                    "unit_id": "absent-event",
                    "status": "unavailable",
                    "reason": "unmaterialized_scope",
                },
            ]
        }
        result = enrich_coverage(
            db.connection, data, cutoff=NOW, closure=closure, sources=("eepro",)
        )
        assert result.tables["entries"][0]["scope_status"] == "identity_withheld"
        assert result.tables["events"][0]["scope_status"] == "legacy_unassessed"
        absent = next(
            item
            for item in result.tables["coverage"]
            if item["scope_kind"] == "event" and item["scope_id"] == "absent-event"
        )
        assert absent["events"] == 0 and absent["scope_status"] == "unavailable"
        assert absent["missing_scopes"] == ["project:event:absent-event"]
        assert absent["scope_reasons"] == ["unmaterialized_scope"]
        assert absent["discovery_denominator"] is None
        assert absent["evidence_cutoff"] == NOW


def test_failed_check_does_not_erase_retained_acquisition_or_refresh_claim_time(tmp_path):
    with open_database(tmp_path) as db:
        data = fixture(db)
        db.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('error','watch','GET','https://example.test/round',?,503,0,0,'run','ServerError')",
            ((NOW + timedelta(seconds=1)).isoformat(),),
        )
        first = enrich_coverage(db.connection, data, cutoff=NOW, sources=("eepro",))
        later = enrich_coverage(
            db.connection, data, cutoff=NOW + timedelta(seconds=2), sources=("eepro",)
        )
        assert row(later, "source", via="wayback")["acquired_units"] == 1
        assert later.tables["entries"] == first.tables["entries"]
        assert later.tables["entries"][0]["evidence_observed_at"] == OLD


def test_health_cadence_ignores_identical_success_poll_times_but_not_failures(tmp_path):
    with open_database(tmp_path) as db:
        fixture(db)
        conn = db.connection
        conn.execute(
            "INSERT INTO registry_verifications(watch_id,checked_at,http_status,body_sha256,snapshot_id,outcome,usable,reason) VALUES ('watch',?,200,'digest','old','found',1,'verified')",
            (NOW.isoformat(),),
        )
        token = health_token(conn, now=NOW)
        conn.execute(
            "INSERT INTO registry_verifications(watch_id,checked_at,http_status,body_sha256,snapshot_id,outcome,usable,reason) VALUES ('watch',?,304,'digest','old','found',1,'verified')",
            ((NOW + timedelta(minutes=1)).isoformat(),),
        )
        assert health_token(conn, now=NOW + timedelta(minutes=1)) == token
        assert health_token(conn, now=NOW + timedelta(days=1)) != token
        conn.execute(
            "INSERT INTO registry_verifications(watch_id,checked_at,http_status,usable,reason) VALUES ('watch',?,503,0,'content_check_failed')",
            ((NOW + timedelta(minutes=2)).isoformat(),),
        )
        assert health_token(conn, now=NOW + timedelta(minutes=2)) != token


def test_selected_accepted_generation_remains_interpreted_when_newer_input_is_blocked(tmp_path):
    from dataclasses import replace

    from test_admission import Corpus

    from swingset.admission.report import Guard

    with open_database(tmp_path) as db:
        data = fixture(db)
        corpus = Corpus(db)
        context = corpus.snapshot("accepted")
        generation, report = corpus.stage(context)
        assert not report.failures
        corpus.review(generation)
        assert corpus.admit(generation) == "accepted"
        tables = dict(data.tables)
        for name in ("events", "contests", "rounds", "entries", "judges"):
            tables[name] = [{**item, "snapshot_id": context.snapshot_id} for item in tables[name]]
        tables["snapshots"] = [
            dict(item)
            for item in db.connection.execute(
                "SELECT s.*,w.source FROM snapshots s JOIN watches w USING(watch_id)"
            )
        ]
        data = replace(data, tables=tables)
        selected = {
            "source_support": [
                {
                    "snapshot_id": context.snapshot_id,
                    "state": "accepted",
                    "source_generations": [{"generation_id": generation}],
                }
            ]
        }
        before = enrich_coverage(
            db.connection, data, cutoff=NOW, closure=selected, sources=("eepro",)
        )
        assert row(before, "event", via="origin")["interpreted_units"] == 1
        newer, _ = corpus.stage(
            corpus.snapshot("newer"),
            report_change=lambda report: replace(
                report,
                guards=(
                    *report.guards,
                    Guard("critical_unknown", False, "synthetic incomplete source"),
                ),
            ),
        )
        assert corpus.admit(newer) == "needs_review"
        after = enrich_coverage(
            db.connection, data, cutoff=NOW, closure=selected, sources=("eepro",)
        )
        event = row(after, "event", via="origin")
        assert event["interpreted_units"] == 1 and event["unassessed_units"] == 0
        assert event["withheld_units"] == 1 and "interpretation_withheld" in event["scope_reasons"]
        assert after.tables["entries"] == before.tables["entries"]
        assert after.tables["entries"][0]["snapshot_id"] == "accepted"


def test_dancer_omissions_publish_counts_without_registry_identifiers(tmp_path):
    with open_database(tmp_path) as db:
        data = fixture(db)
        closure = {
            "inventory": [
                {
                    "stage": "project",
                    "unit_kind": "dancer",
                    "unit_id": "987654321",
                    "status": "unavailable",
                    "reason": "unmaterialized_scope",
                }
            ]
        }
        result = enrich_coverage(
            db.connection, data, cutoff=NOW, closure=closure, sources=("eepro",)
        )
        source = row(result, "source", source="wsdc_registry", via="unknown")
        assert source["missing_scopes"] == ["project:dancer"]
        assert source["unavailable_scopes"] == 1
        assert "987654321" not in str(result.tables["coverage"])


def test_selected_mapping_and_verification_cutoff_do_not_follow_newer_state(tmp_path):
    from dataclasses import replace

    with open_database(tmp_path) as db:
        data = fixture(db)
        tables = dict(data.tables)
        tables["events"] = [
            *tables["events"],
            {**tables["events"][0], "event_id": "newer-event", "snapshot_id": "newer-evidence"},
        ]
        data = replace(data, tables=tables)
        db.connection.execute(
            "INSERT INTO source_event_map VALUES ('eepro','eepro:event','newer-event','explicit',1)"
        )
        for checked, status, usable, parser, body in [
            (NOW - timedelta(minutes=1), 200, 1, "1", "digest"),
            (NOW + timedelta(minutes=1), 200, 1, "1", "digest"),
            (NOW - timedelta(seconds=10), 200, 1, "2", "digest"),
            (NOW - timedelta(seconds=5), 200, 1, "1", "different-content"),
            (NOW, 503, 0, "1", "digest"),
        ]:
            db.connection.execute(
                "INSERT INTO registry_verifications(watch_id,checked_at,http_status,body_sha256,snapshot_id,parser_version,outcome,usable,reason) VALUES ('watch',?,?,?,'old',?,'found',?,'synthetic verification')",
                (checked.isoformat(), status, body, parser, usable),
            )
        result = enrich_coverage(
            db.connection,
            data,
            cutoff=NOW,
            sources=("eepro",),
            selected_mapping=(
                {"source": "eepro", "source_ref": "eepro:event", "event_id": "event"},
            ),
        )
        selected = next(
            item
            for item in result.tables["coverage"]
            if item["scope_kind"] == "event" and item["scope_id"] == "event"
        )
        newer = next(
            item
            for item in result.tables["coverage"]
            if item["scope_kind"] == "event" and item["scope_id"] == "newer-event"
        )
        assert selected["discovered_units"] == 1 and newer["discovered_units"] == 0
        assert selected["usable_verified_at"] == NOW - timedelta(minutes=1)
        assert selected["evidence_observed_at"] == OLD
        assert result.tables["entries"][0]["evidence_observed_at"] == OLD


def test_withheld_identity_is_visible_without_changing_named_subject(tmp_path):
    from dataclasses import replace

    with open_database(tmp_path) as db:
        data = fixture(db)
        tables = dict(data.tables)
        tables["entries"] = [
            {
                **tables["entries"][0],
                "wsdc_id": None,
                "link_status": "unmatched",
                "name_raw": "Retained source name",
            }
        ]
        tables["identity_links"] = [
            {
                "subject_kind": "entry",
                "subject_id": "entry",
                "wsdc_id": 1,
                "acceptance_state": "withheld",
            }
        ]
        result = enrich_coverage(
            db.connection, replace(data, tables=tables), cutoff=NOW, sources=("eepro",)
        )
        entry = result.tables["entries"][0]
        assert entry["name_raw"] == "Retained source name" and entry["wsdc_id"] is None
        assert entry["scope_status"] == "identity_withheld"
        assert row(result, "year")["withheld_identities"] == 1
        assert row(result, "year")["withheld_units"] == 0


def test_final_counts_follow_historical_suppression_without_refreshing_evidence(tmp_path):
    from copy import deepcopy
    from dataclasses import replace

    from swingset.build.coverage import refresh_identity_counts
    from swingset.build.suppression import apply_suppressions

    with open_database(tmp_path) as db:
        data = fixture(db)
        tables = deepcopy(data.tables)
        tables["entries"][0].update(name_raw="Former Name", entry_id="event/L-name-former-name")
        tables["judges"][0].update(
            name_raw="Former Name", judge_id="event/judge/former-name", wsdc_id=1
        )
        selectors = [{"wsdc_id": 999}]
        apply_suppressions(tables, selectors)
        data = enrich_coverage(
            db.connection, replace(data, tables=tables), cutoff=NOW, sources=("eepro",)
        )
        rows = deepcopy(data.tables)
        before = deepcopy(rows["coverage"])
        assert all(item["resolved_identities"] == 2 for item in before)
        policy = apply_suppressions(rows, selectors)
        history = [
            {
                "table": "dancers",
                "record_key": "[999]",
                "field": "name_raw",
                "old_value": "Former Name",
                "new_value": "Current Name",
            }
        ]
        for _ in range(4):
            changed = policy.discover_history(history)
            policy.apply(rows)
            if not changed:
                break
        else:
            raise AssertionError("historical suppression did not settle")
        refresh_identity_counts(rows)
        assert rows["entries"][0]["wsdc_id"] is None
        assert rows["entries"][0]["entry_id"].startswith("suppressed-")
        assert rows["judges"][0]["wsdc_id"] is None
        assert rows["judges"][0]["judge_id"].startswith("suppressed-")
        for old, new in zip(before, rows["coverage"], strict=True):
            assert new["identity_subjects"] == 2 and new["resolved_identities"] == 0
            assert new["scope_status"] == "mapped"
            for field in (
                "events",
                "contests",
                "rounds",
                "entries",
                "evidence_cutoff",
                "evidence_observed_at",
                "usable_verified_at",
                "health_as_of",
                "last_changed_at",
                "scope_reasons",
                "discovery_denominator",
            ):
                assert new[field] == old[field]
        once = deepcopy(rows)
        refresh_identity_counts(rows)
        assert rows == once


def test_final_counts_remove_registry_only_membership_without_erasing_retained_units(tmp_path):
    from copy import deepcopy
    from dataclasses import replace

    from swingset.build.coverage import refresh_identity_counts
    from swingset.build.suppression import apply_suppressions

    with open_database(tmp_path) as db:
        data = fixture(db)
        tables = deepcopy(data.tables)
        tables["registry_placements"] = [
            {"wsdc_id": 999, "event_id": "event", "source": "wsdc_registry", "snapshot_id": "old"}
        ]
        data = enrich_coverage(
            db.connection,
            replace(data, tables=tables),
            cutoff=NOW,
            sources=("eepro", "wsdc_registry"),
        )
        rows = deepcopy(data.tables)
        apply_suppressions(rows, [{"wsdc_id": 999}])
        refresh_identity_counts(rows)
        assert rows["registry_placements"] == []
        registry = [item for item in rows["coverage"] if item["source"] == "wsdc_registry"]
        assert registry and all(
            item["events"] == item["events_sheets_partial"] == item["events_day_precision"] == 0
            for item in registry
        )
        eepro = [item for item in rows["coverage"] if item["source"] == "eepro"]
        assert all(
            item["events"] == item["entries"] == item["resolved_identities"] == 1 for item in eepro
        )
        assert all(item["identity_subjects"] == 2 and item["acquired_units"] == 1 for item in eepro)
