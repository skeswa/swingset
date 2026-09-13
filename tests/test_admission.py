"""H6/H7 exercise real archived inputs, crash boundaries, and competing work."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from swingset.admission.contracts import inspect
from swingset.admission.generations import begin_attempt, stage_generation
from swingset.admission.policy import activate_contract, record_review
from swingset.admission.report import Field, Guard, evaluate
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
        result = result_change(page.parse(extract, ctx))
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


@pytest.mark.parametrize("change", ["input", "work", "snapshot"])
def test_changed_desired_input_cannot_clear_new_work(tmp_path, change):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        ctx = c.snapshot("one")
        generation, _ = c.stage(ctx)
        c.review(generation)
        if change == "input":
            c.conn.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline','manual_extract','new-digest')"
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
    result = page.parse(page.extract(body), ctx)
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
    result = page.parse(extract, ctx)
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
    result = page.parse(extract, ctx)
    assert result.interpretation.coverage.listed_children == ("https://eepro.com/results/example/",)
    assert not inspect(ctx, body, extract, result).failures
    missing = replace(result, watches=())
    assert "coverage_child_mismatch" in inspect(ctx, body, extract, missing).failures
    extract[0]["date"] = None
    undated = page.parse(extract, ctx)
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
    parsed = page.parse(extract, ctx)
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
    wrong = inspect(ctx, body, incomplete, page.parse(incomplete, ctx))
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
