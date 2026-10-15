"""H6/H7 exercise real archived inputs, crash boundaries, and competing work."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from swingset.admission.contracts import declare, evaluate, inspect
from swingset.admission.generations import begin_attempt, stage_generation
from swingset.admission.policy import activate_contract, record_review
from swingset.admission.report import Field, Guard
from swingset.admission.select import admit_generation
from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ParseContext, WatchSpec
from swingset.sources.eepro.adapter import RoundPage
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue

BODY = b"<table><tr><td>Novice Jack and Jill Finals</td></tr><tr><th>Place</th><th>Leader</th><th>Follower</th></tr><tr><td>1</td><td>Alice Example</td><td>Bob Example</td></tr></table>"


class Corpus:
    def __init__(self, db):
        self.db, self.conn = db, db.connection
        self.clock = FakeClock()
        self.run = db.start_run(self.clock.now())
        self.archive = Archive(db.state_dir)
        self.page = RoundPage()
        self.spec = WatchSpec(
            "",
            "eepro",
            "round",
            "GET",
            "https://eepro.com/results/test/final.htm",
            self.page.kind,
            source_ref="eepro:test",
        )
        upsert_watch(self.conn, self.spec, self.clock.now())

    def snapshot(self, name, body=BODY, *, spec=None, via="origin"):
        spec = spec or self.spec
        self.clock.sleep(1)
        now = self.clock.now().isoformat()
        sha = self.archive.store_body(body)
        self.conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification,via) VALUES (?,?,'GET',?,?,200,?,?,1,?,'Ok',?)",
            (name, spec.watch_id, spec.url, now, sha, len(body), self.run, via),
        )
        enqueue(self.conn, (WorkUnit("parse", "snapshot", name),), enqueued_at=now)
        return ParseContext(
            name, spec.watch_id, spec.url, spec.source, spec.parser, spec.source_ref, now
        )

    def stage(self, ctx, *, body=BODY, report_change=lambda r: r, result_change=lambda r: r):
        from swingset.sources import get_page_kind

        page = get_page_kind(ctx.kind)
        inputs = begin_attempt(self.conn, ctx, page.EXTRACT_VERSION, page.PARSER_VERSION)
        extract = page.extract(body)
        result = result_change(declare(page, ctx, extract, page.parse(extract, ctx)))
        report = report_change(inspect(ctx, body, extract, result))
        with self.db.transaction():
            generation = stage_generation(
                self.conn,
                self.archive,
                inputs,
                self.archive.store_extract(extract),
                result,
                report,
                now=ctx.fetched_at,
                run_id=self.run,
            )
        return generation, report

    def review(self, generation):
        # Explicit synthetic test adjudication; no production review is supplied.
        with self.db.transaction():
            receipt = record_review(
                self.conn,
                (generation,),
                reviewer="offline-test-reviewer",
                reviewed_at=self.clock.now().isoformat(),
                evidence="Synthetic fixture inspected in this offline acceptance test",
            )
            kind = self.conn.execute(
                "SELECT page_kind FROM source_generations WHERE generation_id=?", (generation,)
            ).fetchone()[0]
            activate_contract(self.conn, kind, receipt)

    def admit(self, generation, **kwargs):
        return admit_generation(
            self.db,
            self.archive,
            generation,
            now=self.clock.now().isoformat(),
            run_id=self.run,
            **kwargs,
        )


def test_shadow_retains_report_and_never_selects_output(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        generation, report = c.stage(ctx)
        assert not report.failures
        assert c.admit(generation) == "shadow"
        assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert (
            db.connection.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0]
            is None
        )
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 1
        stored = db.connection.execute(
            "SELECT manifest_json,result_json FROM source_generations"
        ).fetchone()
        assert json.loads(stored[0])[0]["snapshot_id"] == "one"
        assert len(json.loads(stored[1])["observations"]) == 1
        with pytest.raises(ValueError, match="reviewed corpus"):
            activate_contract(db.connection, ctx.kind, "unreviewed")


@pytest.mark.parametrize(
    "failure",
    [
        "missing_page",
        "repeated_page",
        "listing_changed",
        "critical_unknown",
        "missing_date",
        "manual_sentinel",
    ],
)
def test_critical_guard_blocks_complete_looking_replacement(tmp_path, failure):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        old_rows = tuple(tuple(r) for r in c.conn.execute("SELECT * FROM observations"))
        old_work = tuple(
            tuple(r) for r in c.conn.execute("SELECT * FROM pending_work WHERE stage='project'")
        )

        def change(report):
            coverage = report.coverage
            fields, guards = report.fields, ()
            sentinel = {}
            if failure == "missing_page":
                coverage = replace(coverage, expected_pages=(*coverage.expected_pages, "missing"))
            elif failure == "repeated_page":
                coverage = replace(
                    coverage,
                    observed_pages=coverage.observed_pages * 2,
                    expected_pages=coverage.expected_pages * 2,
                )
            elif failure == "listing_changed":
                coverage = replace(
                    coverage, mutable_listing=True, revision_before="a", revision_after="b"
                )
            elif failure == "critical_unknown":
                fields += (Field("new_critical_header", "unknown", "No interpretation contract"),)
            elif failure == "missing_date":
                guards = (Guard("required_date_missing", False, "Missing occurrence date"),)
            else:
                sentinel = {"sentinel_expected": "reviewed", "sentinel_actual": "changed"}
            return evaluate(
                report.page_kind,
                report.contract_version,
                fields,
                coverage,
                guards=guards,
                removal="watch",
                **sentinel,
            )

        second, report = c.stage(
            c.snapshot("two", BODY.replace(b"Alice", b"Changed")),
            body=BODY.replace(b"Alice", b"Changed"),
            report_change=change,
        )
        assert c.admit(second) in {"waiting_for_inputs", "needs_review"}
        assert report.failures
        assert tuple(tuple(r) for r in c.conn.execute("SELECT * FROM observations")) == old_rows
        assert (
            tuple(
                tuple(r) for r in c.conn.execute("SELECT * FROM pending_work WHERE stage='project'")
            )
            == old_work
        )
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == first
        )
        assert c.conn.execute(
            "SELECT report_json FROM source_generations WHERE generation_id=?", (second,)
        ).fetchone()


def test_admission_crash_rolls_back_pointer_observations_and_work(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        next_body = BODY.replace(b"Alice", b"Corrected")
        second, _ = c.stage(c.snapshot("two", next_body), body=next_body)
        before = {
            table: tuple(tuple(r) for r in c.conn.execute("SELECT * FROM " + table))
            for table in ("observations", "source_units", "pending_work", "revisions")
        }

        def crash(_):
            raise RuntimeError("simulated process interruption")

        with pytest.raises(RuntimeError, match="interruption"):
            c.admit(second, after_write=crash)
        for table, rows in before.items():
            assert tuple(tuple(r) for r in c.conn.execute("SELECT * FROM " + table)) == rows
        assert (
            c.conn.execute(
                "SELECT state FROM source_generations WHERE generation_id=?", (second,)
            ).fetchone()[0]
            == "staged"
        )
    with open_database(tmp_path) as db:
        assert (
            admit_generation(
                db, Archive(tmp_path), second, now=c.clock.now().isoformat(), run_id=c.run
            )
            == "accepted"
        )
        assert (
            db.connection.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0]
            == second
        )
        assert (
            "Corrected"
            in db.connection.execute("SELECT payload_json FROM observations").fetchone()[0]
        )
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM pending_work WHERE stage='parse'"
            ).fetchone()[0]
            == 0
        )
        # A crash after commit is a replay of the same complete selection.
        assert (
            admit_generation(
                db, Archive(tmp_path), second, now=c.clock.now().isoformat(), run_id=c.run
            )
            == "accepted"
        )


def test_an_input_outside_the_kinds_parse_recipe_does_not_supersede(tmp_path):
    """Admission currency compares only the page kind's parse inputs (D-0207)."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        generation, _ = c.stage(ctx)
        c.review(generation)
        c.conn.executemany(
            "INSERT INTO accepted_inputs VALUES ('pipeline',?,'new-digest')",
            (
                ("manual_extract",),
                ("overrides/event_aliases.csv",),
                ("recipe/code/project/event",),
                ("recipe/code/parse/another.kind",),
                ("version/repository",),
            ),
        )
        assert c.admit(generation) == "accepted"


@pytest.mark.parametrize("change", ["input", "work", "snapshot"])
def test_changed_desired_input_cannot_clear_new_work(tmp_path, change):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        generation, _ = c.stage(ctx)
        c.review(generation)
        if change == "input":
            c.conn.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline',?,'new-digest')",
                (f"recipe/code/parse/{ctx.kind}",),
            )
        elif change == "work":
            enqueue(
                c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01T00:00:00Z"
            )
        else:
            c.snapshot("two", BODY + b"\n")
        pending = tuple(tuple(r) for r in c.conn.execute("SELECT * FROM pending_work"))
        assert c.admit(generation) == "superseded"
        if change == "snapshot":
            pending = tuple(r for r in pending if r[2] != "one")
        assert tuple(tuple(r) for r in c.conn.execute("SELECT * FROM pending_work")) == pending
        assert c.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_authoritative_round_removal_is_scoped_to_its_watch(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        other = replace(c.spec, url="https://eepro.com/results/test/other.htm")
        upsert_watch(c.conn, other, c.clock.now())
        other_generation, _ = c.stage(c.snapshot("other", spec=other))
        assert c.admit(other_generation) == "accepted"
        rows = tuple(
            tuple(r)
            for r in c.conn.execute(
                "SELECT * FROM observations WHERE watch_id=?", (other.watch_id,)
            )
        )
        smaller = BODY.replace(
            b"<tr><td>1</td><td>Alice Example</td><td>Bob Example</td></tr>", b""
        )
        # An actually empty sheet is withheld: a surviving, smaller complete
        # enumeration is the positive removal control.
        larger = BODY.replace(
            b"</table>", b"<tr><td>2</td><td>Carol Example</td><td>Dan Example</td></tr></table>"
        )
        big, _ = c.stage(c.snapshot("big", larger), body=larger)
        assert c.admit(big) == "accepted"
        small, _ = c.stage(c.snapshot("small"))
        assert c.admit(small) == "accepted"
        assert (
            "Carol"
            not in c.conn.execute(
                "SELECT payload_json FROM observations WHERE watch_id=?", (c.spec.watch_id,)
            ).fetchone()[0]
        )
        assert (
            tuple(
                tuple(r)
                for r in c.conn.execute(
                    "SELECT * FROM observations WHERE watch_id=?", (other.watch_id,)
                )
            )
            == rows
        )
        empty, report = c.stage(c.snapshot("empty", smaller), body=smaller)
        assert report.failures
        assert c.admit(empty) != "accepted"


def test_real_registry_contract_and_miss_preserve_profile_and_placement_history(tmp_path):
    from swingset.project.registry import project_dancer
    from swingset.sources.wsdc_registry.adapter import SOURCE

    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = SOURCE.watch(1)
        upsert_watch(c.conn, spec, c.clock.now())
        original = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        found, report = c.stage(c.snapshot("found", original, spec=spec), body=original)
        assert not report.failures
        assert report.proposed_removal == "none"
        c.review(found)
        assert c.admit(found) == "accepted"
        projected = project_dancer(c.conn, "1", c.clock.now().isoformat(), c.run)
        assert len(projected.rows) > 1
        miss_body = Path(
            "src/swingset/sources/wsdc_registry/fixtures/lookup-1000000.body"
        ).read_bytes()
        miss, _ = c.stage(c.snapshot("miss", miss_body, spec=spec), body=miss_body)
        assert c.admit(miss) == "accepted"
        after = project_dancer(c.conn, "1", c.clock.now().isoformat(), c.run)
        assert {(type(r).__name__, r.key()) for r in after.rows} == {
            (type(r).__name__, r.key()) for r in projected.rows
        }
        assert {r.snapshot_id for r in after.rows} == {"found"}


def test_pipeline_enforcement_uses_staged_guards(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        ctx = c.snapshot("unknown", BODY.replace(b"<th>Place</th>", b"<th>Mystery</th>"))
        attempt = parse_snapshot(
            db, c.archive, WorkUnit("parse", "snapshot", ctx.snapshot_id), c.clock, c.run
        )
        assert not attempt.failed
        assert (
            c.conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (c.spec.watch_id,),
            ).fetchone()[0]
            == "one"
        )
        assert (
            c.conn.execute(
                "SELECT state FROM source_generations WHERE generation_id!=?", (first,)
            ).fetchone()[0]
            == "needs_review"
        )
        assert (
            c.conn.execute(
                "SELECT COUNT(*) FROM findings WHERE kind='admission_blocked'"
            ).fetchone()[0]
            == 1
        )


def test_phase_one_archive_staging_has_independent_snapshot_scopes(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = WatchSpec(
            "",
            "swingdancecouncil",
            "index",
            "GET",
            "https://swingdancecouncil.com/events.html",
            "swingdancecouncil.events",
        )
        upsert_watch(c.conn, spec, c.clock.now())
        first_body = (
            b"<table><tr><td>Jan 2 - 3, 2011</td><td>First Swing</td><td>Denver</td></tr></table>"
        )
        second_body = (
            b"<table><tr><td>Feb 4 - 5, 2012</td><td>Second Swing</td><td>Denver</td></tr></table>"
        )
        first_ctx = c.snapshot("old-capture", first_body, spec=spec, via="wayback")
        first, _ = c.stage(first_ctx, body=first_body)
        second, _ = c.stage(
            c.snapshot("new-capture", second_body, spec=spec, via="wayback"), body=second_body
        )
        assert {row[0] for row in c.conn.execute("SELECT unit_key FROM source_units")} == {
            f"{spec.watch_id}/old-capture",
            f"{spec.watch_id}/new-capture",
        }
        # Phase-one parser support does not manufacture a reviewed admission
        # contract. Both independent staged captures remain retained in shadow.
        assert c.admit(first) == c.admit(second) == "shadow"
        assert (
            c.conn.execute(
                "SELECT COUNT(*) FROM source_units WHERE accepted_generation_id IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        newer_desired = c.conn.execute(
            "SELECT desired_fingerprint FROM source_units WHERE unit_key=?",
            (f"{spec.watch_id}/new-capture",),
        ).fetchone()[0]
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "old-capture"),), enqueued_at="2099-01-01")
        c.stage(first_ctx, body=first_body)
        assert (
            c.conn.execute(
                "SELECT desired_fingerprint FROM source_units WHERE unit_key=?",
                (f"{spec.watch_id}/new-capture",),
            ).fetchone()[0]
            == newer_desired
        )


def test_artifact_loss_and_immutable_staging_preserve_prior_selection(tmp_path):
    import sqlite3

    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        changed = BODY + b"\n"
        second, _ = c.stage(c.snapshot("two", changed), body=changed)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.conn.execute(
                "UPDATE source_generations SET report_json='{}' WHERE generation_id=?", (second,)
            )
        sha = c.conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id='two'"
        ).fetchone()[0]
        c.archive.blob_path(sha).unlink()
        assert c.admit(second) == "needs_review"
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == first
        )
        assert (
            c.conn.execute(
                "SELECT reason FROM admission_decisions WHERE generation_id=? ORDER BY decision_id DESC",
                (second,),
            )
            .fetchone()[0]
            .startswith("manifest_artifact_invalid")
        )


def _labels(conn):
    return sorted(
        tuple(row)
        for row in conn.execute(
            "SELECT DISTINCT snapshot_id,parser_version,extract_version FROM observations"
        )
    )


def _build_support(conn, snapshot_id, parser_version):
    from swingset.build.partition_checks import SupportIndex, live_observations

    return (
        SupportIndex(conn)
        .matched(
            snapshot_id,
            str(parser_version),
            live_observations(conn, snapshot_id, str(parser_version)),
        )
        .state
    )


def test_readmission_with_equal_payload_rewrites_observation_labels(tmp_path, monkeypatch):
    """A new contract and extract version with the same payload relabels observations (D-0241)."""
    import swingset.admission.contracts as contracts
    from swingset.state.observations import store_parse_result

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        parser = str(c.page.PARSER_VERSION)
        old_extract = str(c.page.EXTRACT_VERSION)
        assert _labels(c.conn) == [("one", parser, old_extract)]
        assert _build_support(c.conn, "one", parser) == "accepted"
        payloads = [row[0] for row in c.conn.execute("SELECT payload_json FROM observations")]

        # A reviewed contract and extract bump whose parse output is unchanged.
        new_extract = str(int(old_extract) + 1)
        monkeypatch.setattr(type(c.page), "EXTRACT_VERSION", int(new_extract))
        real = contracts.contract_version
        monkeypatch.setattr(
            contracts,
            "contract_version",
            lambda kind: "next" if kind == c.page.kind else real(kind),
        )
        with c.db.transaction():
            enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="relabel")
        second, report = c.stage(ctx)
        assert report.contract_version == "next" and not report.failures
        c.review(second)
        # Before relabelling, the old generation no longer counts under the new contract.
        assert _build_support(c.conn, "one", parser) == "legacy_unassessed"
        revision = c.conn.execute(
            "SELECT value FROM revisions WHERE name='observations'"
        ).fetchone()[0]
        assert c.admit(second) == "accepted"
        assert _labels(c.conn) == [("one", parser, new_extract)]
        assert [row[0] for row in c.conn.execute("SELECT payload_json FROM observations")] == (
            payloads
        )
        assert (
            c.conn.execute("SELECT value FROM revisions WHERE name='observations'").fetchone()[0]
            == revision + 1
        )
        assert _build_support(c.conn, "one", parser) == "accepted"

        # The same payload under the same labels is still a no-op.
        before = tuple(tuple(row) for row in c.conn.execute("SELECT * FROM observations"))
        queued = c.conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0]
        with c.db.transaction():
            store_parse_result(
                c.conn,
                ctx,
                c.page.parse(c.page.extract(BODY), ctx),
                extract_version=new_extract,
                parser_version=parser,
                parsed_at=c.clock.now().isoformat(),
                run_id=c.run,
            )
        assert tuple(tuple(row) for row in c.conn.execute("SELECT * FROM observations")) == before
        assert c.conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == queued
        assert (
            c.conn.execute("SELECT value FROM revisions WHERE name='observations'").fetchone()[0]
            == revision + 1
        )


def test_relabel_stores_an_accepted_result_again_when_only_labels_lag(tmp_path, capsys):
    """Item 21: observations kept from before D-0241 carry older labels than
    their accepted generation, and a repeated parse writes nothing.
    `swingset relabel` lists them and, with --apply, stores the accepted
    result again through the admission writer; a payload difference is left
    alone."""
    from swingset.admission import relabel
    from swingset.cli import main

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        parser = str(c.page.PARSER_VERSION)
        extract = str(c.page.EXTRACT_VERSION)
        assert [item.state for item in relabel.drift(c.conn)] == [relabel.CURRENT]
        payloads = [row[0] for row in c.conn.execute("SELECT payload_json FROM observations")]
        # The rows an older parser left behind: same payload, older labels.
        with c.db.transaction():
            c.conn.execute("UPDATE observations SET parser_version='0',extract_version='0'")
            c.conn.execute("DELETE FROM pending_work")
        assert _build_support(c.conn, "one", "0") == "legacy_unassessed"
        [found] = relabel.drift(c.conn)
        assert (found.state, found.stored, found.accepted) == (
            relabel.LABELS,
            (("one", "0", "0"),),
            ("one", extract, parser),
        )
        assert relabel.drift(c.conn, kinds=["scoringdance.event"]) == []
        revision = c.conn.execute(
            "SELECT value FROM revisions WHERE name='observations'"
        ).fetchone()[0]

    # The list opens read-only and writes nothing.
    assert main(["relabel", "--state", str(tmp_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["relabel"] == 1 and listed["kinds"] == {c.page.kind: {"labels": 1}}
    assert main(["relabel", "--state", str(tmp_path), "--apply"]) == 1
    capsys.readouterr()
    assert main(["relabel", "--state", str(tmp_path), "--apply", "--by", "test"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert (applied["total"], applied["rewritten"]) == (1, {c.page.kind: 1})
    with open_database(tmp_path) as db:
        conn = db.connection
        assert _labels(conn) == [("one", parser, extract)]
        assert [row[0] for row in conn.execute("SELECT payload_json FROM observations")] == (
            payloads
        )
        assert _build_support(conn, "one", parser) == "accepted"
        # Its project scopes are queued for derive, and observers see the change.
        assert (
            conn.execute("SELECT COUNT(*) FROM pending_work WHERE stage='project'").fetchone()[0]
            > 0
        )
        assert (
            conn.execute("SELECT value FROM revisions WHERE name='observations'").fetchone()[0]
            == revision + 1
        )
        summary = json.loads(
            conn.execute(
                "SELECT summary_json FROM runs WHERE summary_json LIKE '%relabel%'"
            ).fetchone()[0]
        )
        assert summary["total"] == 1 and summary["by"] == "test"
        assert [item.state for item in relabel.drift(conn)] == [relabel.CURRENT]
        # A changed payload is not a label: only a parse may change it.
        with db.transaction():
            conn.execute(
                "UPDATE observations SET payload_json=json_set(payload_json,'$.x',1),"
                "parser_version='0'"
            )
        before = tuple(tuple(row) for row in conn.execute("SELECT * FROM observations"))
        assert [item.state for item in relabel.drift(conn)] == [relabel.PAYLOAD]
        result = relabel.relabel(db, now="2026-10-14T00:00:00+00:00", run_id=c.run)
        assert result == {"rewritten": {}, "total": 0, "skipped": {"payload": 1}}
        assert tuple(tuple(row) for row in conn.execute("SELECT * FROM observations")) == before


def test_v1_bootstrap_is_unassessed_without_granted_authority(tmp_path, monkeypatch):
    import swingset.state.db as state_db
    from swingset.state.observations import store_parse_result

    monkeypatch.setattr(state_db, "SCHEMA_VERSION", 7)
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("legacy")
        result = c.page.parse(c.page.extract(BODY), ctx)
        store_parse_result(
            c.conn,
            ctx,
            result,
            extract_version=c.page.EXTRACT_VERSION,
            parser_version=c.page.PARSER_VERSION,
            parsed_at=ctx.fetched_at,
            run_id=c.run,
        )
        original = tuple(tuple(r) for r in c.conn.execute("SELECT * FROM observations"))
    monkeypatch.setattr(state_db, "SCHEMA_VERSION", 9)
    with open_database(tmp_path) as db:
        assert tuple(
            db.connection.execute(
                "SELECT legacy_state,legacy_snapshot_id,accepted_generation_id FROM source_units"
            ).fetchone()
        ) == ("legacy_unassessed", "legacy", None)
        assert db.connection.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 0
        assert (
            tuple(tuple(r) for r in db.connection.execute("SELECT * FROM observations")) == original
        )


def test_read_only_corpus_requires_real_external_review_and_detects_tampering(tmp_path):
    from swingset.admission.corpus import write_corpus
    from swingset.admission.policy import record_corpus_review

    state = tmp_path / "state"
    with open_database(state) as db:
        c = Corpus(db)
        c.snapshot("one")
        before = db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    packet = tmp_path / "packet"
    receipt = write_corpus((state,), packet, cutoff="2026-09-13T00:00:00Z")
    assert receipt["review_status"] == "unreviewed"
    assert receipt["input_mutations"] == receipt["promotions"] == receipt["network_requests"] == 0
    assert (packet / "index.html").exists()
    with open_database(state) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == before
        with pytest.raises(ValueError, match="Reviewer"):
            record_corpus_review(
                db.connection,
                packet,
                "eepro.round",
                expected_digest=receipt["reports_sha256"],
                reviewer="",
                reviewed_at="test",
                evidence="test",
            )
        reviewed = record_corpus_review(
            db.connection,
            packet,
            "eepro.round",
            expected_digest=receipt["reports_sha256"],
            reviewer="offline-test-reviewer",
            reviewed_at="test",
            evidence="Synthetic retained corpus reviewed in this test",
        )
        activate_contract(db.connection, "eepro.round", reviewed)
        assert (
            db.connection.execute("SELECT mode FROM admission_policies").fetchone()[0] == "enforce"
        )
        with (packet / "reports.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
        with pytest.raises((ValueError, KeyError)):
            record_corpus_review(
                db.connection,
                packet,
                "eepro.round",
                expected_digest=receipt["reports_sha256"],
                reviewer="offline-test-reviewer",
                reviewed_at="test",
                evidence="Synthetic test",
            )


def test_explicit_revocation_restores_predecessor_and_cannot_reaccept_same_inputs(tmp_path):
    from swingset.admission.select import revoke_generation

    with open_database(tmp_path) as db:
        c = Corpus(db)
        first, _ = c.stage(c.snapshot("one"))
        c.review(first)
        assert c.admit(first) == "accepted"
        changed = BODY.replace(b"Alice", b"Wrong")
        ctx = c.snapshot("two", changed)
        second, _ = c.stage(ctx, body=changed)
        assert c.admit(second) == "accepted"
        revoke_generation(
            db,
            second,
            reason="Synthetic adjudication proved wrong source interpretation",
            now=c.clock.now().isoformat(),
            run_id=c.run,
        )
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == first
        )
        assert "Alice" in c.conn.execute("SELECT payload_json FROM observations").fetchone()[0]
        assert c.admit(second) == "revoked"
        retry, _ = c.stage(ctx, body=changed)
        assert c.admit(retry) == "revoked"


@pytest.mark.parametrize(
    "name,passed",
    [
        ("scoringdance-final", True),
        ("scoringdance-prelim", True),
        ("eepro-callback", True),
        ("eepro-numeric", True),
        ("wdr-numeric", True),
        ("scoringdance-partner-quarter", True),
        ("scoringdance-partner-allamerican", True),
        ("scoringdance-two-table-partner", False),
        ("scoringdance-cja-semi", True),
        ("scoringdance-v1-final", True),
        ("scoringdance-dq-final", False),
        ("scoringdance-diss-prelim", True),
        ("scoringdance-anon-judges", True),
        ("wdr-svw", True),
    ],
)
def test_retained_column_accounting_is_explicit_without_guessing_numeric_prelim_scores(
    name, passed
):
    from swingset.sources import get_page_kind

    fixtures = Path("src/swingset/admission/fixtures")
    body = (fixtures / (name + ".body")).read_bytes()
    row = json.loads((fixtures / (name + ".json")).read_bytes())
    page = get_page_kind(row["page_kind"])
    ctx = ParseContext(
        row["snapshot_id"],
        row["watch_id"],
        row["url"],
        "eepro"
        if name.startswith("eepro")
        else "wdr"
        if name.startswith("wdr")
        else "scoringdance",
        row["page_kind"],
        "source:test",
        row["observed_at"],
    )
    extract = page.extract(body)
    result = declare(page, ctx, extract, page.parse(extract, ctx))
    assert result.interpretation is not None
    report = inspect(ctx, body, page.extract(body), result)
    assert (not report.failures) == passed
    if name == "eepro-numeric":
        assert any(f.disposition == "excluded" and f.path.endswith("=avg") for f in report.fields)
        assert result.observations[0].payload.scoring_method_raw == "Avg"
    if name == "wdr-numeric":
        assert report.proposed_removal == "none"
        assert any(
            f.disposition == "excluded" and "Average Raw Scores" in f.path for f in report.fields
        )
        assert any(
            f.disposition == "excluded" and "wdr_s_callback_unverified" in f.path
            for f in report.fields
        )


def _fixture_report(name, edit=lambda body: body):
    from swingset.sources import get_page_kind

    fixtures = Path("src/swingset/admission/fixtures")
    body = edit((fixtures / (name + ".body")).read_bytes())
    row = json.loads((fixtures / (name + ".json")).read_bytes())
    page = get_page_kind(row["page_kind"])
    ctx = ParseContext(
        row["snapshot_id"],
        row["watch_id"],
        row["url"],
        row["page_kind"].split(".", 1)[0],
        row["page_kind"],
        row["source_ref"],
        row["observed_at"],
    )
    extract = page.extract(body)
    result = declare(page, ctx, extract, page.parse(extract, ctx))
    return extract, result, inspect(ctx, body, extract, result)


def _dispositions(report):
    return {field.path: field.disposition for field in report.fields}


def test_scoringdance_round_contract_5_accounts_reviewed_columns():
    from swingset.admission.contracts import contract_version

    assert contract_version("scoringdance.round") == contract_version("wdr.rounds") == "5"
    assert contract_version("scoringdance.event") == contract_version("eepro.round") == "4"
    # A: a one-table couple round labels the partner column; both roles project.
    extract, result, report = _fixture_report("scoringdance-partner-quarter")
    assert report.contract_version == "5" and not report.failures
    headers = result.observations[0].payload.tables[0].headers
    assert [cell.text for cell in headers[:3]] == ["Bib Number", "Leader", "Follower"]
    # Two tables keep column 2 unlabelled: one stored page lists followers first.
    extract, result, report = _fixture_report("scoringdance-two-table-partner")
    assert report.failures == ("critical_unknown",)
    assert _dispositions(report)["table[1].header[2]="] == "unknown"
    assert [table["rows"][0][2]["text"] for table in extract] == ["", ""]
    # B and C: the chief-judge adjustment and its CJA badge are excluded.
    _, _, report = _fixture_report("scoringdance-cja-semi")
    assert not report.failures
    fields = _dispositions(report)
    assert fields["table[0].header[9]="] == fields["table[0].header[10]="] == "excluded"
    assert fields["table[0].remark.chief_judge_adjustment"] == "excluded"
    assert any(
        f.reason.startswith("Chief-judge adjustment")
        for f in report.fields
        if f.path.endswith("[9]=")
    )
    # A violation badge on a placed final row is already in the placement.
    _, _, report = _fixture_report("scoringdance-v1-final")
    assert not report.failures
    assert _dispositions(report)["table[0].remark.violation_badge"] == "excluded"
    # DISS on a row the source already shows as eliminated is consistent.
    _, _, report = _fixture_report("scoringdance-diss-prelim")
    assert not report.failures
    assert _dispositions(report)["table[0].remark.eliminated_outcome_note"] == "excluded"
    # T: anonymized per-page judge codes in a Champions final.
    _, _, report = _fixture_report("scoringdance-anon-judges")
    assert not report.failures
    fields = _dispositions(report)
    assert all(
        fields[f"table[0].header[{i}]={code}"] == "excluded"
        for i, code in enumerate(("to", "xbg", "iph", "qqk", "gei", "oli"), 3)
    )


def test_scoringdance_remark_outcome_conflict_blocks_the_round():
    _, _, report = _fixture_report("scoringdance-dq-final")
    assert report.failures == ("source_remark_outcome_conflict",)
    (guard,) = [g for g in report.guards if g.code == "source_remark_outcome_conflict"]
    assert "table[0] row 1 (bib 72, 1st): DQ Lead" in guard.detail
    assert "row 3 (bib 72, 3rd): DQ Lead" in guard.detail
    # The same DISS blocks once the row says it was called back.
    _, _, report = _fixture_report(
        "scoringdance-diss-prelim",
        lambda body: body.replace(
            b'data-state="" data-rownum="2"', b'data-state="CB" data-rownum="2"', 1
        ),
    )
    assert report.failures == ("source_remark_outcome_conflict",)


@pytest.mark.parametrize(
    "edit,path",
    [
        # An unseen remark stays unknown; new values block by default.
        (
            lambda body: body.replace(b">CJA</span>", b">New note</span>", 1),
            "table[0].remark=New note",
        ),
        # A non-numeric value after the sum is not an adjustment.
        (
            lambda body: body.replace(b'nowrap;">+2</span>', b'nowrap;">note</span>', 1),
            "table[0].header[9]=",
        ),
    ],
)
def test_scoringdance_unreviewed_remark_or_adjustment_stays_unknown(edit, path):
    _, _, report = _fixture_report("scoringdance-cja-semi", edit)
    assert report.failures == ("critical_unknown",)
    assert _dispositions(report)[path] == "unknown"


def test_wdr_swing_violation_warning_passes_and_placement_drops_block():
    _, _, report = _fixture_report("wdr-svw")
    assert report.contract_version == "5" and not report.failures
    _, _, report = _fixture_report("wdr-svw", lambda body: body.replace(b'"4|SVW"', b'"4|SV1"'))
    assert "round[27].cell.value=4|SV1" in _dispositions(report)


def test_wdr_attribute_group_text_ignores_extract_key_order():
    from swingset.admission.generations import serialize_result
    from swingset.fetch.archive import canonical
    from swingset.sources.wdr.adapter import RoundsPage

    page = RoundsPage()
    body = Path("src/swingset/admission/fixtures/wdr-svw.body").read_bytes()
    ctx = ParseContext(
        "snapshot",
        "watch",
        "https://example.test/rounds",
        "wdr",
        page.kind,
        "wdr:test",
        "2026-09-09",
    )
    fresh = page.extract(body)  # body key order: id, name, attributes
    stored = json.loads(canonical(fresh))  # an archived extract: keys sorted
    assert list(fresh["results"][27]["attributeGroup"]) == ["id", "name", "attributes"]
    first, second = page.parse(fresh, ctx), page.parse(stored, ctx)
    assert serialize_result(first) == serialize_result(second)
    (group,) = {
        table.attribute_group
        for o in first.observations
        for table in o.payload.tables
        if table.attribute_group is not None
    }
    assert group.startswith("{'attributes': [{'id': 1, 'name': 'SVW indicates")
    assert group.endswith("'id': 1, 'name': 'NASDE'}")


@pytest.mark.parametrize("mutation", ["method", "cell_type", "callback", "judge_score"])
def test_wdr_unknown_critical_values_block_admission(mutation):
    from swingset.sources.wdr.adapter import RoundsPage

    page = RoundsPage()
    body = Path("src/swingset/sources/wdr/fixtures/rounds-2026-09-09.body").read_bytes()
    extract = page.extract(body)
    raw = next(r for r in extract["results"] if r["roundSubHeader"].startswith("Sum of Yes"))
    row = raw["results"][0]["data"][1]
    if mutation == "method":
        raw["roundSubHeader"] = "Unreviewed score rule"
    elif mutation == "cell_type":
        row[0]["t"] = 999
    else:
        cell = next(c for c in row if c["t"] == (2 if mutation == "callback" else 9))
        cell["v"] = "NEW" if mutation == "callback" else "8.20"
    ctx = ParseContext(
        "snapshot",
        "watch",
        "https://example.test/rounds",
        "wdr",
        page.kind,
        "wdr:test",
        "2026-09-13T00:00:00Z",
    )
    result = declare(page, ctx, extract, page.parse(extract, ctx))
    assert "critical_unknown" in inspect(ctx, body, extract, result).failures


def test_baseline_support_distinguishes_selected_legacy_superseded_and_revoked(tmp_path):
    from swingset.admission.select import revoke_generation
    from swingset.admission.support import interpretation_support, selection_digest

    with open_database(tmp_path) as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("one"))
        initial = selection_digest(c.conn)
        c.review(generation)
        assert c.admit(generation) == "accepted"
        assert selection_digest(c.conn) != initial
        support = interpretation_support(c.conn, "one", parser_version=str(c.page.PARSER_VERSION))
        assert support["state"] == "accepted" and support["usable"]
        assert (
            interpretation_support(c.conn, "one", parser_version="obsolete")["state"]
            == "unassessed"
        )
        accepted_digest = selection_digest(c.conn)
        revoke_generation(
            db,
            generation,
            reason="Synthetic explicit revocation",
            now=c.clock.now().isoformat(),
            run_id=c.run,
        )
        assert selection_digest(c.conn) != accepted_digest
        support = interpretation_support(c.conn, "one", parser_version=str(c.page.PARSER_VERSION))
        assert support["state"] == "revoked" and not support["usable"]


def test_listing_independently_accounts_for_child_watch_loss_and_required_dates():
    from swingset.sources.eepro.adapter import IndexPage

    page = IndexPage()
    ctx = ParseContext(
        "s", "w", "https://eepro.com/results/event.php", "eepro", page.kind, None, "2026-09-13"
    )
    body = b'<a href="event.php?event=example"><div class="event-title">Example</div><div class="event-date">January 1, 2025</div></a>'
    extract = page.extract(body)
    result = declare(page, ctx, extract, page.parse(extract, ctx))
    assert result.interpretation.coverage.listed_children == ("https://eepro.com/results/example/",)
    assert not inspect(ctx, body, extract, result).failures
    missing = replace(result, watches=())
    assert "coverage_child_mismatch" in inspect(ctx, body, extract, missing).failures
    extract[0]["date"] = None
    undated = declare(page, ctx, extract, page.parse(extract, ctx))
    assert "required_date_missing" in inspect(ctx, body, extract, undated).failures


def test_new_lookup_does_not_relabel_retained_legacy_history_as_assessed(tmp_path):
    from swingset.sources.wsdc_registry.adapter import SOURCE

    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = SOURCE.watch(1)
        upsert_watch(c.conn, spec, c.clock.now())
        body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        old = c.snapshot("legacy", body, spec=spec)
        c.conn.execute(
            "INSERT INTO source_units(unit_key,watch_id,page_kind,legacy_snapshot_id,legacy_state) VALUES (?,?,?,'legacy','legacy_unassessed')",
            (spec.watch_id, spec.watch_id, spec.parser),
        )
        new_body = body + b"\n"
        newer, _ = c.stage(c.snapshot("newer", new_body, spec=spec), body=new_body)
        c.review(newer)
        assert c.admit(newer) == "accepted"
        assert (
            c.conn.execute(
                "SELECT legacy_state FROM source_units WHERE unit_key=?", (old.watch_id,)
            ).fetchone()[0]
            == "legacy_unassessed"
        )


def test_wdr_non_authoritative_replacement_cannot_drop_or_change_retained_rows(tmp_path):
    import copy

    from swingset.sources.wdr.adapter import RoundsPage

    page = RoundsPage()
    original = json.loads(
        Path("src/swingset/sources/wdr/fixtures/rounds-2026-09-09.body").read_bytes()
    )
    with open_database(tmp_path) as db:
        c = Corpus(db)
        c.spec = WatchSpec(
            "",
            "wdr",
            "event",
            "GET",
            "https://example.test/rounds",
            page.kind,
            source_ref="wdr:test",
        )
        upsert_watch(c.conn, c.spec, c.clock.now())
        first_body = json.dumps(original).encode()
        first, _ = c.stage(c.snapshot("first", first_body), body=first_body)
        c.review(first)
        assert c.admit(first) == "accepted"
        prior = tuple(
            c.conn.execute(
                "SELECT observation_id,payload_json FROM observations ORDER BY observation_id"
            )
        )
        for i, mutation in enumerate(("drop", "change")):
            changed = copy.deepcopy(original)
            rows = changed["data"]["scoresData"]["results"][0]["results"][0]["data"]
            if mutation == "drop":
                rows.pop()
            else:
                next(cell for cell in rows[1] if cell["t"] == 4)["v"] = "999999"
            body = json.dumps(changed).encode()
            generation, report = c.stage(c.snapshot(f"changed{i}", body), body=body)
            assert (
                not report.failures
            )  # New body alone is complete; prior claims still constrain it.
            attempt = parse_snapshot(
                db, c.archive, WorkUnit("parse", "snapshot", f"changed{i}"), c.clock, c.run
            )
            assert not attempt.failed
            staged = json.loads(
                c.conn.execute(
                    "SELECT report_json FROM source_generations WHERE generation_id=?",
                    (generation,),
                ).fetchone()[0]
            )
            assert "non_authoritative_row_loss" in staged["failures"]
            finding = c.conn.execute(
                "SELECT summary,evidence_json FROM findings WHERE owner_kind='admission' AND owner_id=? AND closed_at IS NULL",
                (c.spec.watch_id,),
            ).fetchone()
            assert (
                finding[0]
                == "Source generation retained without promotion: needs_review (non_authoritative_row_loss)"
            )
            assert json.loads(finding[1])["failures"] == ["non_authoritative_row_loss"]
            assert (
                tuple(
                    c.conn.execute(
                        "SELECT observation_id,payload_json FROM observations ORDER BY observation_id"
                    )
                )
                == prior
            )
        assert (
            c.conn.execute(
                "SELECT accepted_generation_id FROM source_units WHERE unit_key=?",
                (c.spec.watch_id,),
            ).fetchone()[0]
            == first
        )


def test_autoindex_raw_href_witness_detects_truncated_label_omissions():
    from swingset.sources.eepro.adapter import AutoIndexPage

    page = AutoIndexPage()
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    ctx = ParseContext(
        "snapshot",
        "watch",
        "http://eepro.com/results/freedomswing2019/",
        "eepro",
        page.kind,
        "eepro:freedomswing2019",
        "2026-09-13",
    )
    extract = page.extract(body)
    assert len(extract) == 7
    parsed = declare(page, ctx, extract, page.parse(extract, ctx))
    report = inspect(ctx, body, extract, parsed)
    assert report.contract_version == "5" and not report.failures
    # Both extractor and parser agree on a silently truncated five-file result.
    # The independent raw href witness must still reject that interpretation.
    incomplete = [
        item
        for item in extract
        if item["name"] not in {"jackandjillprelimssemis.html", "strictlyswingfinals.html"}
    ]
    assert len(incomplete) == 5
    wrong = inspect(
        ctx, body, incomplete, declare(page, ctx, incomplete, page.parse(incomplete, ctx))
    )
    assert {
        "autoindex_file_coverage",
        "coverage_child_mismatch",
        "coverage_count_mismatch",
        "declaration_mismatch",
    } <= set(wrong.failures)


def test_public_admission_reason_codes_never_include_dynamic_exception_details(tmp_path):
    from swingset.admission.support import generation_failure_codes

    with open_database(tmp_path) as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("one"))
        c.conn.execute(
            "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES (?,'needs_review',?,'2026-09-13','test')",
            (
                generation,
                "manifest_artifact_invalid:private /archive/person-name/body.json",
            ),
        )
        assert generation_failure_codes(c.conn, generation) == ("manifest_artifact_invalid",)


def _rows(conn, sql):
    return tuple(tuple(r) for r in conn.execute(sql))


def _runtime(conn, value):
    conn.execute(
        "INSERT INTO accepted_inputs(consumer,input_name,digest) VALUES ('pipeline','recipe/env',?) "
        "ON CONFLICT(consumer,input_name) DO UPDATE SET digest=excluded.digest",
        (value,),
    )


def test_identical_reparse_under_changed_runtime_cuts_off_to_accepted_generation(tmp_path):
    from swingset.admission.select import source_materialized_for

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        before = {
            table: _rows(c.conn, "SELECT * FROM " + table)
            for table in ("observations", "revisions", "admission_decisions")
        }
        project = _rows(c.conn, "SELECT * FROM pending_work WHERE stage!='parse'")
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        attempt = parse_snapshot(
            db, c.archive, WorkUnit("parse", "snapshot", "one"), c.clock, c.run
        )
        assert not attempt.failed and attempt.selection is None
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 1
        equivalences = _rows(
            c.conn, "SELECT unit_key,input_fingerprint,generation_id FROM source_equivalences"
        )
        assert len(equivalences) == 1 and equivalences[0][2] == first
        unit = c.conn.execute(
            "SELECT accepted_generation_id,desired_fingerprint FROM source_units"
        ).fetchone()
        assert tuple(unit) == (first, equivalences[0][1])
        assert source_materialized_for(c.conn, equivalences[0][0], first, unit[1])
        assert (
            c.conn.execute(
                "SELECT state FROM source_generations WHERE generation_id=?", (first,)
            ).fetchone()[0]
            == "accepted"
        )
        for table, rows in before.items():
            assert _rows(c.conn, "SELECT * FROM " + table) == rows, table
        assert _rows(c.conn, "SELECT * FROM pending_work WHERE stage!='parse'") == project
        assert (
            c.conn.execute("SELECT COUNT(*) FROM pending_work WHERE stage='parse'").fetchone()[0]
            == 0
        )
        # A repeat of the same fingerprint adds nothing.
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-02")
        assert c.stage(ctx)[0] == first
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 1


def test_with_cutoff_off_an_identical_reparse_stages_a_generation(tmp_path):
    from swingset import cutoff

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        with cutoff.configured(False):
            again, _ = c.stage(ctx)
        assert again != first
        assert c.admit(again) == "accepted"
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0


def test_changed_result_or_parser_label_still_writes_and_admits_a_generation(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        changed, _ = c.stage(
            ctx,
            result_change=lambda r: replace(r, legitimate_empty=not r.legitimate_empty),
        )
        assert changed != first
        assert c.admit(changed) == "accepted"
        # An identical result under a new parser label is a new generation too.
        monkeypatch.setattr(RoundPage, "PARSER_VERSION", RoundPage.PARSER_VERSION + 1)
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-02")
        relabeled, _ = c.stage(
            ctx,
            result_change=lambda r: replace(r, legitimate_empty=not r.legitimate_empty),
        )
        assert relabeled not in {first, changed}
        assert c.admit(relabeled) == "accepted"
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 3
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0]
            == relabeled
        )


def test_revoked_inputs_are_decided_by_admission_not_cut_off(tmp_path):
    from swingset.admission.select import revoke_generation

    def wrong(r):
        return replace(r, legitimate_empty=not r.legitimate_empty)

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        revoke_generation(
            db, first, reason="Synthetic explicit revocation", now=ctx.fetched_at, run_id=c.run
        )
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        retry, _ = c.stage(ctx)
        assert retry != first
        assert c.admit(retry) == "revoked"
        # The accepted generation matches, but the new fingerprint was revoked.
        second = c.snapshot("two")
        kept, _ = c.stage(second)
        assert c.admit(kept) == "accepted"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "two"),), enqueued_at="2099-01-02")
        bad, _ = c.stage(second, result_change=wrong)
        assert c.admit(bad) == "accepted"
        revoke_generation(
            db, bad, reason="Synthetic explicit revocation", now=ctx.fetched_at, run_id=c.run
        )
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == kept
        )
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "two"),), enqueued_at="2099-01-03")
        again, _ = c.stage(second)
        assert again not in {kept, bad}
        assert c.admit(again) == "revoked"
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0


def test_revocation_after_a_cutoff_also_revokes_the_equivalent_inputs(tmp_path):
    from swingset.admission.select import revoke_generation
    from swingset.schedule.event_evidence import (
        admission_reason,
        decode_generation,
        revoked_evidence,
    )

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        # A deploy re-parses the snapshot identically: the parse cuts off to
        # the accepted generation under the new fingerprint.
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        assert c.stage(ctx)[0] == first
        equivalent = c.conn.execute("SELECT desired_fingerprint FROM source_units").fetchone()[0]
        revoke_generation(
            db, first, reason="Synthetic explicit revocation", now=ctx.fetched_at, run_id=c.run
        )
        # The query every evidence reader uses (admission_reason, page
        # evidence, the pressure probe, the build) covers the equivalent
        # fingerprint: it names the same recipe, so the revoked generation's
        # own snapshot and parser label already match it, and the row is left
        # out rather than counted against the readers' budgets.
        revoked = [tuple(row) for row in c.conn.execute(revoked_evidence(c.conn))]
        assert [row[1] for row in revoked] == [
            c.conn.execute(
                "SELECT input_fingerprint FROM source_generations WHERE generation_id=?", (first,)
            ).fetchone()[0]
        ]
        # The build's copy reads the same query, capped at 128 rows.
        from swingset.build.event_coverage import _Reader

        assert _Reader(c.conn).revoked_rows() == revoked
        # The same inputs parse again: a new generation with the equivalent
        # fingerprint, which admission refuses as the revoked evidence.
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-02")
        again, _ = c.stage(ctx)
        assert again != first
        assert (
            c.conn.execute(
                "SELECT input_fingerprint FROM source_generations WHERE generation_id=?", (again,)
            ).fetchone()[0]
            == equivalent
        )
        value = decode_generation(
            c.conn.execute(
                "SELECT * FROM source_generations WHERE generation_id=?", (again,)
            ).fetchone(),
            again,
        )
        assert value["state"] != "revoked"
        assert admission_reason(c.conn, value) == "source_evidence_revoked"
        assert c.admit(again) == "revoked"


def _relabeled_equivalence(c, monkeypatch, ctx, revoked_id):
    """Stage `ctx` under another parser label, then record its fingerprint as
    equivalent to `revoked_id`. A cutoff keeps labels byte-equal, so no real
    equivalence differs in label from its generation; the query keeps such a
    row anyway, and only that row can name the new generation."""
    from swingset.sources import get_page_kind

    monkeypatch.setattr(get_page_kind(ctx.kind), "PARSER_VERSION", "relabeled")
    enqueue(c.conn, (WorkUnit("parse", "snapshot", ctx.snapshot_id),), enqueued_at="2099-01-03")
    other, _ = c.stage(ctx)
    row = c.conn.execute(
        "SELECT unit_key,input_fingerprint,recipe_json FROM source_generations "
        "WHERE generation_id=?",
        (other,),
    ).fetchone()
    c.conn.execute(
        "INSERT INTO source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,"
        "work_token,created_at,run_id) VALUES (?,?,?,?,NULL,?,?)",
        (*tuple(row)[:2], revoked_id, row[2], c.clock.now().isoformat(), c.run),
    )
    return other


def test_the_build_withholds_a_generation_equivalent_to_revoked_evidence(tmp_path, monkeypatch):
    """The build's event-coverage reader reads the revoked evidence through the
    shared query: a generation whose fingerprint was cut off to a revoked one
    is withheld, as admission refuses it (D-0205)."""
    from swingset.admission.select import revoke_generation
    from swingset.build.event_coverage import _Reader, _Unavailable

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        other = _relabeled_equivalence(c, monkeypatch, ctx, first)
        revoke_generation(
            db, first, reason="Synthetic explicit revocation", now=ctx.fetched_at, run_id=c.run
        )
        with pytest.raises(_Unavailable, match="^source_evidence_revoked$"):
            _Reader(c.conn).parent(other, "2999-01-01T00:00:00+00:00")


def test_equivalences_do_not_spend_the_revoked_evidence_budgets(tmp_path, monkeypatch):
    """Each deploy that re-parses a unit adds an equivalence. Twenty revoked
    generations re-parsed seven times each must not exhaust the readers' row
    budgets, or every event's coverage evidence fails (phase 1 review)."""
    from swingset.admission.select import revoke_generation
    from swingset.build.event_coverage import _Reader, _Unavailable
    from swingset.schedule.event_evidence import revoked_evidence

    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        relabeled = _relabeled_equivalence(c, monkeypatch, ctx, first)
        revoke_generation(
            db, first, reason="Synthetic explicit revocation", now=ctx.fetched_at, run_id=c.run
        )
        template = dict(
            c.conn.execute(
                "SELECT * FROM source_generations WHERE generation_id=?", (first,)
            ).fetchone()
        )

        def revoked(start, count):
            for index in range(start, start + count):
                row = {
                    **template,
                    "generation_id": f"gen_copy_{index}",
                    "input_fingerprint": f"copy-{index}",
                }
                c.conn.execute(
                    f"INSERT INTO source_generations({','.join(row)}) "
                    f"VALUES ({','.join('?' * len(row))})",
                    tuple(row.values()),
                )
                for deploy in range(7):
                    recipe = json.loads(template["recipe_json"])
                    recipe["accepted_inputs"] = {"recipe/env": f"deploy-{deploy}"}
                    c.conn.execute(
                        "INSERT INTO source_equivalences(unit_key,input_fingerprint,generation_id,"
                        "recipe_json,work_token,created_at,run_id) VALUES (?,?,?,?,NULL,?,?)",
                        (
                            template["unit_key"],
                            f"copy-{index}-{deploy}",
                            row["generation_id"],
                            json.dumps(recipe),
                            c.clock.now().isoformat(),
                            c.run,
                        ),
                    )

        revoked(0, 19)
        rows = [tuple(row) for row in c.conn.execute(revoked_evidence(c.conn))]
        # One row per revoked generation, plus the relabeled equivalence.
        assert len(rows) == 21
        assert sum("relabeled" in row[2] for row in rows) == 1
        assert len(_Reader(c.conn).revoked_rows()) == 21
        # The budget of 128 rows still holds: 128 revoked generations and
        # the relabeled equivalence pass it.
        revoked(19, 108)
        with pytest.raises(_Unavailable, match="event_evidence_row_budget"):
            _Reader(c.conn).revoked_rows()
        assert relabeled


def _blocking(failure):
    """A report change that holds a generation: needs_review or waiting_for_inputs."""

    def block(report):
        fields, guards = report.fields, ()
        if failure == "critical_unknown":
            fields += (Field("new_critical_header", "unknown", "No interpretation contract"),)
        elif failure == "missing_date":
            guards = (Guard("required_date_missing", False, "Missing occurrence date"),)
        else:
            guards = (Guard("manifest_artifact_missing", False, "Restore the input first"),)
        return evaluate(
            report.page_kind,
            report.contract_version,
            fields,
            report.coverage,
            guards=guards,
            removal="watch",
        )

    return block


def _held_unit(c, monkeypatch, failure):
    """An enforced kind and a second watch whose parse is held by `failure`."""
    import swingset.schedule.parse as parse_module

    control, _ = c.stage(c.snapshot("control"))
    c.review(control)
    assert c.admit(control) == "accepted"
    other = replace(c.spec, url="https://eepro.com/results/test/other.htm")
    upsert_watch(c.conn, other, c.clock.now())
    c.snapshot("other", spec=other)
    block = _blocking(failure)
    monkeypatch.setattr(parse_module, "inspect", lambda *args: block(inspect(*args)))
    return WorkUnit("parse", "snapshot", "other")


def _parse(db, c, unit):
    return parse_snapshot(db, c.archive, unit, c.clock, c.run)


@pytest.mark.parametrize(
    "failure,state",
    [
        ("critical_unknown", "needs_review"),
        ("missing_date", "needs_review"),
        ("artifact_missing", "waiting_for_inputs"),
    ],
)
def test_a_held_unit_that_parses_identically_keeps_its_generation(
    tmp_path, monkeypatch, failure, state
):
    """R3: 0.85% of re-parses repeated a held generation byte for byte. The
    repeat is held in the same state without a new generation or decision:
    the reviewed contract and the report's own failures decide it (D-0212)."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        unit = _held_unit(c, monkeypatch, failure)
        first = _parse(db, c, unit)
        assert not first.failed and first.selection == state
        held = _rows(c.conn, "SELECT generation_id FROM source_generations WHERE state!='accepted'")
        assert len(held) == 1
        decisions = _rows(c.conn, "SELECT * FROM admission_decisions")
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-01")
        again = _parse(db, c, unit)
        assert not again.failed and again.selection == state
        assert (
            _rows(c.conn, "SELECT generation_id FROM source_generations WHERE state!='accepted'")
            == held
        )
        assert _rows(c.conn, "SELECT generation_id FROM source_equivalences") == held
        assert _rows(c.conn, "SELECT * FROM admission_decisions") == decisions
        # The work token is completed as a fresh held parse's would be.
        assert not c.conn.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id='other'"
        ).fetchone()
        # A repeat at the same fingerprint (a retry) is held too.
        enqueue(c.conn, (unit,), enqueued_at="2099-01-02")
        assert _parse(db, c, unit).selection == state
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 2
        # With cutoff off, every repeat is staged and decided again.
        from swingset import cutoff

        enqueue(c.conn, (unit,), enqueued_at="2099-01-03")
        with cutoff.configured(False):
            assert _parse(db, c, unit).selection == state
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 3


def test_a_held_repeat_that_admission_would_decide_differently_is_staged(tmp_path, monkeypatch):
    """Held for an unreviewed contract, then the contract is reviewed: the same
    bytes now wait for inputs, so the repeat is staged and admitted afresh."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        unit = _held_unit(c, monkeypatch, "artifact_missing")
        c.conn.execute("UPDATE admission_policies SET reviewed_report_digest=NULL")
        assert _parse(db, c, unit).selection == "needs_review"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-01")
        # Still unreviewed: the same bytes are held for review again.
        assert _parse(db, c, unit).selection == "needs_review"
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 1
        policy = c.conn.execute(
            "SELECT reviewed_report_digest FROM admission_policies WHERE page_kind=?",
            (c.spec.parser,),
        ).fetchone()
        assert policy[0] is None
        c.conn.execute("UPDATE admission_policies SET reviewed_report_digest='reviewed'")
        _runtime(c.conn, "runtime-3")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-02")
        assert _parse(db, c, unit).selection == "waiting_for_inputs"
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 3
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 1


def test_a_parse_failure_that_repeats_keeps_its_generation(tmp_path, monkeypatch):
    """A parse that fails the same way under a new runtime stages nothing new."""
    from swingset.sources.base import ParseError

    with open_database(tmp_path) as db:
        c = Corpus(db)
        _held_unit(c, monkeypatch, "critical_unknown")

        def fail(self, extract, ctx):
            raise ParseError("layout changed")

        monkeypatch.setattr(RoundPage, "parse", fail)
        unit = WorkUnit("parse", "snapshot", "other")
        assert _parse(db, c, unit).failed
        count = c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0]
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-01")
        assert _parse(db, c, unit).failed
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == count
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 1


def test_shadow_phase_one_index_reparse_cuts_off(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = WatchSpec(
            "",
            "swingdancecouncil",
            "index",
            "GET",
            "https://swingdancecouncil.com/events.html",
            "swingdancecouncil.events",
        )
        upsert_watch(c.conn, spec, c.clock.now())
        body = (
            b"<table><tr><td>Jan 2 - 3, 2011</td><td>First Swing</td><td>Denver</td></tr></table>"
        )
        ctx = c.snapshot("capture", body, spec=spec, via="wayback")
        first, _ = c.stage(ctx, body=body)
        assert c.admit(first) == "shadow"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "capture"),), enqueued_at="2099-01-01")
        again, _ = c.stage(ctx, body=body)
        assert again == first
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 1
        assert (
            c.conn.execute("SELECT generation_id FROM source_equivalences").fetchone()[0] == first
        )
        # The next repeat finds the same generation through the equivalence.
        _runtime(c.conn, "runtime-3")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "capture"),), enqueued_at="2099-01-02")
        assert c.stage(ctx, body=body)[0] == first
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 2
        changed = body.replace(b"Denver", b"Boulder")
        c.archive.store_body(changed)
        assert c.stage(ctx, body=changed)[0] != first


def test_a_generation_current_through_an_equivalence_is_decided_on_its_merits(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = WatchSpec(
            "",
            "swingdancecouncil",
            "index",
            "GET",
            "https://swingdancecouncil.com/events.html",
            "swingdancecouncil.events",
        )
        upsert_watch(c.conn, spec, c.clock.now())
        body = (
            b"<table><tr><td>Jan 2 - 3, 2011</td><td>First Swing</td><td>Denver</td></tr></table>"
        )
        ctx = c.snapshot("capture", body, spec=spec, via="wayback")
        first, _ = c.stage(ctx, body=body)
        assert c.admit(first) == "shadow"
        # A shadow re-parse under another runtime cuts off to the same
        # generation; the unit's desired fingerprint is now the equivalent one.
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "capture"),), enqueued_at="2099-01-01")
        assert c.stage(ctx, body=body)[0] == first
        desired = c.conn.execute("SELECT desired_fingerprint FROM source_units").fetchone()[0]
        own = c.conn.execute(
            "SELECT input_fingerprint FROM source_generations WHERE generation_id=?", (first,)
        ).fetchone()[0]
        assert desired != own
        # The runtime goes back and the kind becomes enforced: the generation
        # is a parse of the desired inputs through its equivalence, so it is
        # decided on its merits, not superseded.
        c.conn.execute("DELETE FROM accepted_inputs WHERE input_name='recipe/env'")
        token = c.conn.execute(
            "SELECT work_token FROM source_generations WHERE generation_id=?", (first,)
        ).fetchone()[0]
        c.conn.execute(
            "INSERT INTO pending_work VALUES ('parse','snapshot','capture',?) "
            "ON CONFLICT DO UPDATE SET enqueued_at=excluded.enqueued_at",
            (token,),
        )
        c.conn.execute(
            "UPDATE admission_policies SET mode='enforce',policy_revision='enforced' "
            "WHERE page_kind='swingdancecouncil.events'"
        )
        assert c.admit(first) not in {"superseded", "shadow"}


def _snapshot_status(conn, snapshot_id):
    return tuple(
        conn.execute(
            "SELECT parse_status,extract_status FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    )


def test_a_repaired_parse_that_cuts_off_restores_the_snapshot_status(tmp_path, monkeypatch):
    """A parser regression marks the snapshot failed; the fix deploy parses
    it identically and cuts off to the accepted generation. The snapshot is
    parsed again, and a registry check reads it as usable (not
    `interpretation_failed`), without a new generation."""
    from swingset.sources.base import ParseError
    from swingset.sources.wsdc_registry.adapter import SOURCE, DancerPage
    from swingset.state.verification import record_check

    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = SOURCE.watch(1)
        upsert_watch(c.conn, spec, c.clock.now())
        body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        ctx = c.snapshot("found", body, spec=spec)
        unit = WorkUnit("parse", "snapshot", "found")
        # The registry is ORDERED_EXTRACT: every attempt extracts from the body.
        assert not _parse(db, c, unit).failed
        first, token = c.conn.execute(
            "SELECT generation_id,work_token FROM source_generations"
        ).fetchone()
        c.review(first)
        enqueue(c.conn, (unit,), enqueued_at=token)
        assert c.admit(first) == "accepted"
        record_check(
            c.conn,
            c.archive,
            watch_id=spec.watch_id,
            checked_at=ctx.fetched_at,
            http_status=200,
            body_sha256=c.archive.store_body(body),
            successful=True,
            snapshot_id="found",
        )

        def usable():
            return _rows(
                c.conn, "SELECT usable,reason FROM registry_verifications ORDER BY verification_id"
            )

        assert usable() == ((1, "verified"),)
        original = DancerPage.parse

        def regression(self, extract, ctx):
            raise ParseError("regression")

        monkeypatch.setattr(DancerPage, "parse", regression)
        _runtime(c.conn, "runtime-bad")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-01")
        assert _parse(db, c, unit).failed
        assert _snapshot_status(c.conn, "found")[0] == "failed"
        assert usable() == ((0, "interpretation_failed"),)
        monkeypatch.setattr(DancerPage, "parse", original)
        _runtime(c.conn, "runtime-fixed")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-02")
        repaired = _parse(db, c, unit)
        assert not repaired.failed
        assert _snapshot_status(c.conn, "found") == ("ok", "ok")
        assert usable() == ((1, "verified"),)
        # Cut off: the failure's generation and the accepted one, no third.
        assert (
            c.conn.execute(
                "SELECT COUNT(*) FROM source_generations WHERE state='accepted'"
            ).fetchone()[0]
            == 1
        )
        assert (
            c.conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == first
        )
        assert _rows(c.conn, "SELECT generation_id FROM source_equivalences")[-1] == (first,)


def test_an_identical_reparse_of_a_superseded_snapshot_does_not_cut_off(tmp_path):
    """A re-parse of a snapshot the watch no longer selects writes no
    equivalence and leaves the unit's desired fingerprint alone."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        first, _ = c.stage(ctx)
        c.review(first)
        assert c.admit(first) == "accepted"
        desired = c.conn.execute("SELECT desired_fingerprint FROM source_units").fetchone()[0]
        c.snapshot("two")
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        assert c.stage(ctx)[0] != first
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0
        assert (
            c.conn.execute("SELECT desired_fingerprint FROM source_units").fetchone()[0] == desired
        )


def test_only_a_held_newest_generation_is_kept_through_the_held_path(tmp_path):
    """D-0212 keeps a newest generation only in `needs_review` or
    `waiting_for_inputs`. An undecided `staged` generation (admission never
    ran, as after a crash) is not a held repeat: the parse stages a new one."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        control, _ = c.stage(c.snapshot("control"))
        c.review(control)
        assert c.admit(control) == "accepted"
        other = replace(c.spec, url="https://eepro.com/results/test/other.htm")
        upsert_watch(c.conn, other, c.clock.now())
        ctx = c.snapshot("other", spec=other)
        first, report = c.stage(ctx)
        assert report.state == "staged"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "other"),), enqueued_at="2099-01-01")
        again, _ = c.stage(ctx)
        assert again != first
        assert c.conn.execute("SELECT COUNT(*) FROM source_equivalences").fetchone()[0] == 0


def test_an_ordered_json_extract_is_parsed_fresh_so_a_reparse_cuts_off(tmp_path):
    """A stored extract is key-sorted JSON. The registry's parse follows the
    body's key order, so a re-parse from the stored extract (an environment
    or parser-only change leaves the extract scope alone) differed from the
    accepted generation and minted a new one: about 23% of registry and some
    WDR snapshots on the 16:10 copy. Such kinds always extract fresh."""
    from swingset.fetch.archive import canonical
    from swingset.sources import get_page_kind
    from swingset.sources.wsdc_registry.adapter import SOURCE

    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = SOURCE.watch(1)
        upsert_watch(c.conn, spec, c.clock.now())
        body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        ctx = c.snapshot("found", body, spec=spec)
        page = get_page_kind(ctx.kind)
        extract = page.extract(body)
        # The fixture is one whose order matters.
        assert page.parse(extract, ctx) != page.parse(json.loads(canonical(extract)), ctx)
        assert all(
            getattr(get_page_kind(kind), "ORDERED_EXTRACT", False)
            for kind in ("wsdc_registry.dancer", "wdr.rounds", "wdr.awards")
        )
        unit = WorkUnit("parse", "snapshot", "found")
        assert not _parse(db, c, unit).failed
        first, token = c.conn.execute(
            "SELECT generation_id,work_token FROM source_generations"
        ).fetchone()
        c.review(first)
        enqueue(c.conn, (unit,), enqueued_at=token)
        assert c.admit(first) == "accepted"
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (unit,), enqueued_at="2099-01-01")
        assert not _parse(db, c, unit).failed
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 1
        assert _rows(c.conn, "SELECT generation_id FROM source_equivalences") == ((first,),)


CALENDAR_FIXTURES = Path("src/swingset/sources/wsdc_calendar/fixtures")


def _calendar_row(name, date="Jan 2 - 3, 2027", *, classes=" ", kind="Registry Event", flag="USA"):
    return (
        f'<tr class="{classes}"><td>{date}</td><td><div class="event_name">'
        f'<a href="https://{name.lower()}.test">{name}</a></div>'
        f'<div class="event_type">{kind}</div></td><td>Denver, CO, USA</td>'
        f'<td><a href="?country={flag}">flag</a></td></tr>'
    )


def _calendar(*rows):
    return ("<table>" + "".join(rows) + "</table>").encode()


def _calendar_report(body, extract=None):
    from swingset.sources.wsdc_calendar.adapter import EventsPage

    page = EventsPage()
    ctx = ParseContext(
        "s", "w", "https://worldsdc.com/events/", "wsdc_calendar", page.kind, None, "2026-09-09"
    )
    extract = page.extract(body) if extract is None else extract
    return inspect(ctx, body, extract, declare(page, ctx, extract, page.parse(extract, ctx)))


@pytest.mark.parametrize(
    ("name", "rows"),
    [
        ("calendar-2026-09-09.html", 172),
        ("calendar-20260813-zstd.body", 169),
        ("calendar-20161113.html", 127),
        ("calendar-20210415.html", 500),
        ("calendar-2016-literals.html", 67),
        ("calendar-2019-map.html", 105),
        ("calendar-2017-single-dates.html", 117),
    ],
)
def test_calendar_contract_admits_every_retained_layout(name, rows):
    """Each layout's listings are counted from the raw body, not the extractor (D-0257)."""
    report = _calendar_report((CALENDAR_FIXTURES / name).read_bytes())
    assert report.contract_version == "1" and not report.failures
    assert report.coverage.source_count == report.coverage.interpreted_count == rows
    assert report.proposed_removal == "watch"


def test_calendar_contract_blocks_lost_rows_unreviewed_marks_and_unknown_dates():
    body = _calendar(_calendar_row("First"), _calendar_row("Second"))
    assert not _calendar_report(body).failures
    from swingset.sources.wsdc_calendar.adapter import EventsPage

    extract = EventsPage().extract(body)
    # A row the extractor lost is still counted from the body.
    assert "coverage_count_mismatch" in _calendar_report(body, extract[:1]).failures
    # A row class or listing type nobody reviewed could change a row's status.
    for row in (
        _calendar_row("Second", classes="event-postponed"),
        _calendar_row("Second", kind="Festival"),
        _calendar_row("Second", date="Spring 2027"),
    ):
        report = _calendar_report(_calendar(_calendar_row("First"), row))
        assert "critical_unknown" in report.failures and report.state == "needs_review"
    # A printed span that ends before it starts withholds only that listing.
    report = _calendar_report(
        _calendar(_calendar_row("First"), _calendar_row("Second", date="Feb 13 - 9, 2027"))
    )
    assert not report.failures and report.coverage.interpreted_count == 2
    assert any(f.path == "warning:impossible_listing_date" for f in report.fields)
    # An unknown flag drops only the row's country and stays in the report.
    report = _calendar_report(_calendar(_calendar_row("First", flag="Atlantis")))
    assert not report.failures
    assert any(f.disposition == "unknown" and not f.critical for f in report.fields)
    # An empty listing is no witness that the calendar is empty.
    empty = b'<html><div class="event-map">Events Calendar</div></html>'
    assert _calendar_report(empty).state == "waiting_for_inputs"


def test_calendar_captures_admit_and_a_live_capture_keeps_archived_rows(tmp_path):
    """A Wayback capture read on the live watch is its own unit; the next live
    capture replaces only live rows, and the build matches the capture's rows,
    stored as history, against its generation (D-0257)."""
    with open_database(tmp_path) as db:
        c = Corpus(db)
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://worldsdc.com/events/",
            "wsdc_calendar.events",
        )
        upsert_watch(c.conn, spec, c.clock.now())
        archived = _calendar(_calendar_row("Summer", date="Aug 6 - 9, 2026"))
        first = _calendar(_calendar_row("First"))
        second = _calendar(_calendar_row("Second"))
        old, _ = c.stage(c.snapshot("capture", archived, spec=spec, via="wayback"), body=archived)
        live, _ = c.stage(c.snapshot("live-1", first, spec=spec), body=first)
        c.review(old)
        assert c.admit(old) == c.admit(live) == "accepted"
        scopes = dict(
            c.conn.execute("SELECT snapshot_id,scope_id FROM observations ORDER BY snapshot_id")
        )
        assert scopes == {"capture": "wsdc-history", "live-1": "wsdc"}
        assert _build_support(c.conn, "capture", 7) == "accepted"
        assert _build_support(c.conn, "live-1", 7) == "accepted"
        newer, _ = c.stage(c.snapshot("live-2", second, spec=spec), body=second)
        assert c.admit(newer) == "accepted"
        assert dict(
            c.conn.execute("SELECT snapshot_id,scope_id FROM observations ORDER BY snapshot_id")
        ) == {"capture": "wsdc-history", "live-2": "wsdc"}


def test_contract_command_shows_activates_and_pauses_a_reviewed_kind(tmp_path, capsys):
    """`swingset contract` wraps what activated contract 5 by hand: preflight
    counts, a reviewed cohort, activation under the exclusive lock, and the
    counts after, as one receipt-ready JSON."""
    from swingset.cli import main

    with open_database(tmp_path) as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("one"))
        c.conn.execute("DELETE FROM pending_work")
    state = ["--state", str(tmp_path)]
    kind = c.page.kind
    assert main(["contract", "status", "--kind", kind, *state]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["mode"], shown["code_contract_version"]) == ("shadow", "4")
    assert shown["generations"] == {"4:staged": 1}
    assert shown["failures_under_code_version"] == {"(none)": 1}
    # Activation needs who reviewed, where the review is, and the cohort.
    activate = ["contract", "activate", "--kind", kind, *state]
    assert main([*activate, "--generation", generation]) == 1
    # A kind whose code has no reviewed contract cannot be activated.
    assert (
        main(
            [
                "contract",
                "activate",
                "--kind",
                "wsdc_newsletter.events",
                *state,
                "--generation",
                generation,
                "--by",
                "test",
                "--evidence",
                "review",
            ]
        )
        == 1
    )
    capsys.readouterr()
    assert (
        main([*activate, "--generation", generation, "--by", "test", "--evidence", "review"]) == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["before"]["mode"] == "shadow" and receipt["after"]["mode"] == "enforce"
    assert receipt["after"]["policy"]["contract_version"] == "4"
    assert receipt["queued_parse"] == 1 and receipt["cohort"] == [generation]
    assert receipt["after"]["reviews"][0]["digest"] == receipt["review_digest"]
    assert main(["contract", "pause", "--kind", kind, *state, "--by", "test"]) == 0
    paused = json.loads(capsys.readouterr().out)
    assert paused["after"]["mode"] == "paused"
    # Pausing withdraws nothing: the accepted generations stay.
    assert "keep publishing" in paused["not_undone"]


def test_a_review_names_a_packed_report_instead_of_failing_on_its_stub(tmp_path):
    """A cohort generation whose body was packed (phase 4) holds a stub report;
    `record_review` says so and writes nothing, instead of a bare KeyError."""
    from swingset.state import body_packs

    with open_database(tmp_path) as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("one"))
        from swingset.state import objects

        now = c.clock.now()
        digest, _ = body_packs.write_source(c.conn, objects.store_for(c.conn), generation, now=now)
        with db.transaction() as conn:
            assert body_packs.stub_source(conn, generation, digest, now=now)
        with pytest.raises(ValueError, match="packed report"):
            with db.transaction():
                record_review(
                    c.conn,
                    (generation,),
                    reviewer="test",
                    reviewed_at=c.clock.now().isoformat(),
                    evidence="review",
                )
        assert c.conn.execute("SELECT COUNT(*) FROM admission_reviews").fetchone()[0] == 0
