import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from test_identity_journal import NOW, decision, prepared
from test_link_service import Bundle
from test_project_event import EVENT

from swingset.clock import FakeClock
from swingset.link.decisions import DecisionResolver
from swingset.link.service import link_event
from swingset.state.db import open_database
from swingset.state.identity_journal import accept_journal, encode_journal, token
from swingset.state.identity_references import ReferenceReader, record_migration, retain_binding
from swingset.state.work import WorkUnit, enqueue

CLOCK = FakeClock(datetime(2026, 9, 13, tzinfo=UTC))


def setup(db):
    identifier, binding = prepared(db.connection)
    for number in (100, 101):
        db.connection.execute(
            "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,'Alex','Lee','alex lee',0,'leader','novice','advanced','novice','advanced','novice',1,'novice',1,2026,?,'t','test','snapshot','1','t','t','run_a')",
            (number, number),
        )
    return identifier, binding


def accept(db, *decisions):
    with db.transaction() as conn:
        accept_journal(conn, encode_journal(decisions), bundle_digest="fixture", now=NOW)


def run(db, event=EVENT):
    with db.transaction() as conn:
        enqueue(conn, (WorkUnit("link", "event", event),), enqueued_at=NOW)
    return link_event(db, event, Bundle(), CLOCK, "run_a")


def set_printed_id(db, number):
    row = db.connection.execute(
        "SELECT observation_id,payload_json FROM observations WHERE snapshot_id='snapshot'"
    ).fetchone()
    payload = json.loads(row[1])
    cell = payload["tables"][0]["rows"][0]["cells"][1]
    cell["attributes"] = [["data-wsdc", str(number)]] if number is not None else []
    db.connection.execute(
        "UPDATE observations SET payload_json=? WHERE observation_id=?",
        (json.dumps(payload), row[0]),
    )


def linked(db, identifier):
    return tuple(
        db.connection.execute(
            "SELECT wsdc_id,method,status FROM identity_links WHERE subject_id=?", (identifier,)
        ).fetchone()
    )


def test_hold_requires_explicit_supersession_and_retains_history(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        positive = decision(binding, "same_person")
        accept(db, positive)
        run(db)
        assert linked(db, identifier) == (100, "manual", "confirmed")
        hold = decision(binding, "hold_unlinked", "NONE", "hold", positive.decision_id)
        accept(db, positive, hold)
        run(db)
        assert linked(db, identifier) == (None, "none", "unmatched")
        replacement = decision(binding, "same_person", "101", "replacement", "hold")
        accept(db, positive, hold, replacement)
        run(db)
        assert linked(db, identifier) == (101, "manual", "confirmed")
        history = db.connection.execute(
            "SELECT state,resolution_json FROM identity_link_history WHERE subject_id=? ORDER BY history_id",
            (identifier,),
        ).fetchall()
        assert [r[0] for r in history] == [
            "accepted",
            "superseded",
            "revoked",
            "superseded",
            "accepted",
        ]
        assert json.loads(history[2][1])["decisions"] == ["hold"]


@pytest.mark.parametrize("route", ["scoring", "source_id", "registry_placement", "bib_reuse"])
def test_negative_applies_to_every_route_but_retains_candidate_evidence(tmp_path, route):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        accept(db, decision(binding))
        if route == "registry_placement":
            contest = db.connection.execute(
                "SELECT contest_id FROM entries WHERE entry_id=?", (identifier,)
            ).fetchone()[0]
            round_id = db.connection.execute(
                "SELECT round_id FROM rounds WHERE contest_id=?", (contest,)
            ).fetchone()[0]
            db.connection.execute(
                "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,leader_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place',?,?,?,1,?,'','eepro','snapshot','1','t','t','run_a')",
                (round_id, contest, EVENT, identifier),
            )
            db.connection.execute(
                "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,series_name_raw,event_month,event_id,result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (100,'leader','wcs','novice','slug-summer-hummer','Summer Hummer','2026-08',?,'1',1,'test','snapshot','1','t','t','run_a')",
                (EVENT,),
            )
        if route == "bib_reuse":
            # A second spelling/round with the same original bib must share restrictions.
            columns = [r[1] for r in db.connection.execute("PRAGMA table_info(entries)")]
            row = list(
                db.connection.execute(
                    "SELECT * FROM entries WHERE entry_id=?", (identifier,)
                ).fetchone()
            )
            row[columns.index("entry_id")] = "duplicate"
            db.connection.execute(
                "INSERT INTO entries VALUES (" + ",".join("?" for _ in row) + ")", row
            )
        if route == "source_id":
            set_printed_id(db, 100)
        run(db)
        assert linked(db, identifier)[0] != 100
        assert (
            db.connection.execute(
                "SELECT count(*) FROM link_candidates WHERE subject_id=? AND wsdc_id=100",
                (identifier,),
            ).fetchone()[0]
            == 1
        )
        if route in {"source_id", "registry_placement"}:
            assert linked(db, identifier)[0] is None
            evidence = db.connection.execute(
                "SELECT evidence_json FROM findings WHERE kind='identity_decision' AND subject_id=? AND closed_at IS NULL",
                (identifier,),
            ).fetchone()
            assert "new_evidence_conflicts_with_pair_restriction" in evidence[0]
        else:
            assert (
                linked(db, identifier)[0] == 101
            )  # Pair rejection never rejects another identity.


def test_conflicting_manual_positive_source_and_negative_all_withhold(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        positive = decision(binding, "same_person", "101", "positive")
        accept(db, positive)
        set_printed_id(db, 100)
        run(db)
        assert linked(db, identifier)[0] is None
        set_printed_id(db, None)
        negative = decision(binding, "different_person", "101", "negative")
        accept(db, positive, negative)
        run(db)
        assert linked(db, identifier)[0] is None
        replacement = decision(binding, "same_person", "101", "reviewed-again", "negative")
        accept(db, positive, negative, replacement)
        run(db)
        assert linked(db, identifier)[0] == 101


def test_event_remap_preserves_rejection_and_baseline_locator(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        accept(db, decision(binding))
        run(db)
        cursor = db.connection.execute("SELECT * FROM entries WHERE entry_id=?", (identifier,))
        baseline = dict(zip((r[0] for r in cursor.description), cursor.fetchone(), strict=True))
        contest = db.connection.execute(
            "SELECT name_raw FROM contests WHERE contest_id=?", (baseline["contest_id"],)
        ).fetchone()[0]
        from swingset.project.contests import project_event
        from swingset.project.writer import Projection, replace_scope

        new_event = "2026-08-remapped-series"
        cursor = db.connection.execute("SELECT * FROM events WHERE event_id=?", (EVENT,))
        event = list(cursor.fetchone())
        event[0] = new_event
        db.connection.execute(
            "INSERT INTO events VALUES (" + ",".join("?" for _ in event) + ")", event
        )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE source_event_map SET event_id=? WHERE event_id=?", (new_event, EVENT)
            )
            replace_scope(
                conn,
                scope_kind="event",
                scope_id=EVENT,
                projection=Projection(),
                run_id="run_a",
                projected_at=NOW,
            )
            replace_scope(
                conn,
                scope_kind="event",
                scope_id=new_event,
                projection=project_event(conn, new_event, NOW, "run_a"),
                run_id="run_a",
                projected_at=NOW,
            )
        new_id = db.connection.execute("SELECT entry_id FROM entries").fetchone()[0]
        refs = ReferenceReader(db.connection).for_subject("entry", new_id)
        assert refs[0].reference == binding.reference
        assert (
            ReferenceReader(db.connection)
            .for_record("entry", baseline, contest_name=contest)[0]
            .reference
            == binding.reference
        )
        run(db, new_event)
        assert linked(db, new_id)[0] == 101
        assert not DecisionResolver(db.connection).resolve((binding.reference,)).allows(100)
        assert (
            db.connection.execute(
                "SELECT state FROM identity_link_resolutions WHERE subject_id=?", (identifier,)
            ).fetchone()[0]
            == "superseded"
        )


def test_ambiguous_reference_migration_holds_until_reviewed_continuity(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        negative = decision(binding)
        accept(db, negative)
        target = replace(binding, reference=replace(binding.reference, contest="renumbered"))
        other = replace(
            target, reference=replace(target.reference, participant="entry:leader:bib:256")
        )
        with db.transaction() as conn:
            for item in (binding, target, other):
                retain_binding(conn, item, now=NOW)
            record_migration(
                conn,
                migration_id="split",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id, other.reference.ref_id),
                status="ambiguous",
                evidence="fixture:ambiguous-renumbering",
                reason="Ambiguous source replacement",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        for ref in (target.reference, other.reference):
            resolved = DecisionResolver(db.connection).resolve((ref,))
            assert resolved.hold_subject and not resolved.allows(101)
        with db.transaction() as conn:
            record_migration(
                conn,
                migration_id="resolved",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:reviewed-renumbering",
                reason="Reviewed unique row",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
                supersedes="split",
            )
        resolved = DecisionResolver(db.connection).resolve((target.reference,))
        assert not resolved.allows(100) and resolved.allows(101)


def test_source_row_replacement_and_missing_reference_withhold(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        accept(db, decision(binding, "same_person"))
        run(db)
        resolver = DecisionResolver(db.connection)
        replacement = replace(binding, semantic_hash="a" * 64)
        problem = resolver.binding_problem((replacement,))
        assert problem == "source_row_replacement_requires_reference_migration"
        assert resolver.resolve((binding.reference,), reference_problem=problem).hold_subject
        # Remove the actual current source rows while retaining the canonical
        # subject and its old reference binding. This changes a real input;
        # merely mocking a different reader under identical inputs would be a
        # nondeterministic materialization, which H15 must reject.
        db.connection.execute(
            "DELETE FROM observations WHERE snapshot_id=?", (binding.snapshot_id,)
        )
        run(db)
        assert linked(db, identifier)[0] is None
        with db.transaction() as conn:
            record_migration(
                conn,
                migration_id="replacement",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(binding.reference.ref_id,),
                status="approved",
                evidence=json.dumps({"semantic_hash": replacement.semantic_hash}),
                reason="Reviewed same participant",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        assert DecisionResolver(db.connection).binding_problem((replacement,)) is None


def test_concurrent_acceptance_cannot_restore_old_link_or_clear_relink_work(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        positive = decision(binding, "same_person")
        accept(db, positive)
        run(db)
        from swingset.link import service

        actual_complete = service.complete

        def interleave(database, work, write):
            accept(
                db,
                positive,
                decision(binding, "different_person", "100", "revoked", positive.decision_id),
            )
            return actual_complete(database, work, write)

        with patch.object(service, "complete", side_effect=interleave):
            assert run(db) is False
        assert db.connection.execute(
            "SELECT 1 FROM pending_work WHERE stage='link' AND unit_id=?", (EVENT,)
        ).fetchone()
        assert (
            db.connection.execute(
                "SELECT journal_generation FROM identity_link_resolutions WHERE subject_id=?",
                (identifier,),
            ).fetchone()[0]
            < token(db.connection).generation
        )
        run(db)
        assert linked(db, identifier)[0] != 100
        assert not db.connection.execute(
            "SELECT 1 FROM pending_work WHERE stage='link' AND unit_id=?", (EVENT,)
        ).fetchone()


def test_named_judge_without_wsdc_number_remains_named_and_unmatched(tmp_path):
    with open_database(tmp_path) as db:
        setup(db)
        run(db)
        row = db.connection.execute("SELECT judge_id,name_raw,wsdc_id FROM judges").fetchone()
        assert tuple(row[1:]) == ("Jane Doe", None)
        assert linked(db, row[0]) == (None, "none", "unmatched")
        assert (
            db.connection.execute(
                "SELECT count(*) FROM link_candidates WHERE subject_kind='judge'"
            ).fetchone()[0]
            == 0
        )


def test_reviewed_positives_cannot_claim_distinct_bibs_in_one_contest(tmp_path):
    from test_project_event import add, sheet

    from swingset.project.contests import project_event
    from swingset.project.writer import replace_scope
    from swingset.sources.records import Cell

    with open_database(tmp_path) as db:
        first_id, binding = setup(db)
        payload = sheet("prelim", name="Alex Lee")
        row = replace(
            payload.tables[0].rows[0], cells=(Cell("256"), *payload.tables[0].rows[0].cells[1:])
        )
        payload = replace(
            payload, tables=(replace(payload.tables[0], rows=(*payload.tables[0].rows, row)),)
        )
        add(db.connection, "two-bibs", "snapshot-two", payload, NOW)
        replace_scope(
            db.connection,
            scope_kind="event",
            scope_id=EVENT,
            projection=project_event(db.connection, EVENT, NOW, "run_a"),
            run_id="run_a",
            projected_at=NOW,
        )
        ids = [
            r[0] for r in db.connection.execute("SELECT entry_id FROM entries ORDER BY entry_id")
        ]
        assert len(ids) == 2
        refs = [ReferenceReader(db.connection).for_subject("entry", key)[0] for key in ids]
        accept(
            db,
            *(
                decision(ref, "same_person", "100", f"positive-{index}")
                for index, ref in enumerate(refs)
            ),
        )
        run(db)
        assert all(linked(db, key)[0] is None for key in ids)
        assert (
            db.connection.execute(
                "SELECT count(*) FROM findings WHERE kind='identity_decision' AND closed_at IS NULL"
            ).fetchone()[0]
            == 2
        )


def test_pair_insufficient_evidence_does_not_become_transitive_rejection(tmp_path):
    with open_database(tmp_path) as db:
        identifier, binding = setup(db)
        accept(db, decision(binding, "insufficient_evidence"))
        run(db)
        assert linked(db, identifier)[0] == 101
        resolved = DecisionResolver(db.connection).resolve((binding.reference,))
        assert resolved.review_required and not resolved.hold_subject
        assert not resolved.allows(100) and resolved.allows(101)


def test_cross_reference_supersession_requires_approved_migration(tmp_path):
    with open_database(tmp_path) as db:
        _, binding = setup(db)
        first = decision(binding)
        accept(db, first)
        target = replace(binding, reference=replace(binding.reference, contest="renumbered"))
        positive = decision(target, "same_person", "100", "positive", first.decision_id)
        with pytest.raises(ValueError, match="reference migration"):
            accept(db, first, positive)
        with db.transaction() as conn:
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="move",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:explicit-continuity",
                reason="Reviewed new source locator",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        accept(db, first, positive)
        resolved = DecisionResolver(db.connection).resolve((target.reference,))
        assert resolved.allows(100) and resolved.positive_wsdc_id == 100


def test_unexplained_renumbering_after_remap_cannot_drop_a_restriction(tmp_path):
    from swingset.project.writer import Projection, replace_scope

    with open_database(tmp_path) as db:
        _, binding = setup(db)
        accept(db, decision(binding))
        run(db)
        replace_scope(
            db.connection,
            scope_kind="event",
            scope_id=EVENT,
            projection=Projection(),
            run_id="run_a",
            projected_at=NOW,
        )
        target = replace(
            binding,
            reference=replace(
                binding.reference, contest="new-sheet", participant="entry:leader:bib:900"
            ),
            subject_id="new-canonical-subject",
        )
        resolver = DecisionResolver(db.connection)
        problem = resolver.binding_problem((target,))
        assert problem == "orphaned_source_reference_requires_migration"
        assert resolver.resolve(
            (target.reference,), reference_problem=problem, source_wsdc_id=100
        ).hold_subject
        with db.transaction() as conn:
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="renumber",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:reviewed-renumbering",
                reason="Explicit source continuity",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        resolver = DecisionResolver(db.connection)
        assert resolver.binding_problem((target,)) is None
        assert not resolver.resolve((target.reference,)).allows(100)


def test_baseline_old_reference_sees_decision_superseded_on_new_reference(tmp_path):
    with open_database(tmp_path) as db:
        _, binding = setup(db)
        first = decision(binding, "same_person")
        accept(db, first)
        target = replace(binding, reference=replace(binding.reference, contest="renumbered"))
        with db.transaction() as conn:
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="move",
                from_ref_id=binding.reference.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:explicit-continuity",
                reason="Reviewed source locator",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        accept(db, first, decision(target, "same_person", "101", "new-positive", first.decision_id))
        baseline_resolution = DecisionResolver(db.connection).resolve((binding.reference,))
        assert not baseline_resolution.allows(100)
        assert baseline_resolution.positive_wsdc_id == 101


def test_separate_source_names_with_pair_bibs_keep_role_ownership(tmp_path):
    from test_project_event import add

    from swingset.project.contests import project_event
    from swingset.project.writer import replace_scope
    from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet

    with open_database(tmp_path) as db:
        setup(db)
        table = ResultTable(
            "final",
            (Cell("Bib"), Cell("Leader"), Cell("Follower"), Cell("Place")),
            (
                ResultRow(
                    (
                        Cell("27/90"),
                        Cell("Danya Svir", (("data-wsdc", "100"),)),
                        Cell("Other Person", (("data-wsdc", "101"),)),
                        Cell("1"),
                    )
                ),
            ),
        )
        payload = RoundSheet(
            "round_sheet",
            "scoringdance:event",
            "final",
            "Country Jack & Jill All American",
            "Final",
            (table,),
        )
        add(
            db.connection,
            "pair-bib",
            "pair-snapshot",
            payload,
            NOW,
            source="scoringdance",
            source_ref="scoringdance:event",
            parser="scoringdance.round",
        )
        db.connection.execute(
            "INSERT INTO source_event_map VALUES ('scoringdance','scoringdance:event',?,'override',1)",
            (EVENT,),
        )
        replace_scope(
            db.connection,
            scope_kind="event",
            scope_id=EVENT,
            projection=project_event(db.connection, EVENT, NOW, "run_a"),
            run_id="run_a",
            projected_at=NOW,
        )
        for identifier, role, bib in db.connection.execute(
            "SELECT entry_id,role,bib FROM entries WHERE snapshot_id='pair-snapshot'"
        ):
            refs = ReferenceReader(db.connection).for_subject("entry", identifier)
            assert len(refs) == 1
            assert refs[0].reference.participant == f"entry:{role}:bib:{bib}"
            assert bib == ("27" if role == "leader" else "90")
            assert refs[0].locator["source_wsdc_id"] == (100 if role == "leader" else 101)
