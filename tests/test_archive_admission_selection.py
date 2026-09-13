"""Synthetic archive fallback candidates exercise real staging and admission."""

from dataclasses import replace

import pytest
from test_admission import BODY, Corpus

from swingset.admission.generations import begin_attempt, selected_snapshot
from swingset.state.db import open_database


def capture(corpus, identifier, timestamp, *, body=BODY, requested=None, final=None):
    url = requested or f"https://web.archive.org/web/{timestamp}id_/{corpus.spec.url}"
    corpus.conn.execute(
        "UPDATE watches SET archive_url=? WHERE watch_id=?", (url, corpus.spec.watch_id)
    )
    context = corpus.snapshot(identifier, body, via="wayback")
    observed = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}T00:00:00+00:00"
    corpus.conn.execute(
        "UPDATE snapshots SET requested_archive_url=?,archive_url=?,observed_at=?,captured_at=? WHERE snapshot_id=?",
        (url, final or url, observed, observed, identifier),
    )
    return replace(context, fetched_at=observed)


def accepted(corpus):
    return corpus.conn.execute(
        "SELECT accepted_generation_id FROM source_units WHERE unit_key=?", (corpus.spec.watch_id,)
    ).fetchone()[0]


def test_newer_incomplete_capture_keeps_old_support_then_older_complete_fallback_promotes(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        initial = capture(c, "retained", "20170101000000")
        first, _ = c.stage(initial)
        c.review(first)
        assert c.admit(first) == "accepted"
        previous = list(c.conn.execute("SELECT snapshot_id,payload_json FROM observations"))
        # A parseable but header-only newer capture lacks participant evidence.
        incomplete = b"<table><tr><td>Novice Jack and Jill Finals</td></tr><tr><th>Place</th><th>Leader</th><th>Follower</th></tr></table>"
        latest = capture(c, "newer-incomplete", "20200101000000", body=incomplete)
        failed, report = c.stage(latest, body=incomplete)
        assert report.failures
        assert c.admit(failed) in {"waiting_for_inputs", "needs_review"}
        assert accepted(c) == first
        assert list(c.conn.execute("SELECT snapshot_id,payload_json FROM observations")) == previous
        changed = BODY.replace(b"Alice Example", b"Restored Example")
        fallback = capture(c, "older-complete", "20180101000000", body=changed)
        replacement, report = c.stage(fallback, body=changed)
        assert not report.failures
        assert c.admit(replacement) == "accepted"
        assert accepted(c) == replacement
        assert (
            c.conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (c.spec.watch_id,),
            ).fetchone()[0]
            == "older-complete"
        )
        assert (
            c.conn.execute(
                "SELECT COUNT(*) FROM observations WHERE snapshot_id='older-complete'"
            ).fetchone()[0]
            == 1
        )
        assert c.conn.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0] == 3


@pytest.mark.parametrize(
    "change", ["different_capture", "same_final_alias", "clear_archive", "source_ref"]
)
def test_capture_watch_change_after_staging_vetoes_completion(tmp_path, change):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        final = (
            "https://web.archive.org/web/20180102000000id_/https://eepro.com/results/test/final.htm"
        )
        context = capture(c, "selected", "20180101000000", final=final)
        generation, _ = c.stage(context)
        c.review(generation)
        if change == "source_ref":
            c.conn.execute(
                "UPDATE watches SET source_ref='different-event' WHERE watch_id=?",
                (c.spec.watch_id,),
            )
        else:
            next_url = {
                "different_capture": "https://web.archive.org/web/20190101000000id_/https://eepro.com/results/test/final.htm",
                "same_final_alias": final,
                "clear_archive": None,
            }[change]
            c.conn.execute(
                "UPDATE watches SET archive_url=? WHERE watch_id=?", (next_url, c.spec.watch_id)
            )
        assert c.admit(generation) == "superseded"
        assert accepted(c) is None
        assert c.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert (
            c.conn.execute(
                "SELECT reason FROM admission_decisions ORDER BY decision_id DESC LIMIT 1"
            ).fetchone()[0]
            == "desired_inputs_changed"
        )


def test_active_capture_matches_requested_or_final_url_and_retries_by_fetch_time(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        final = (
            "https://web.archive.org/web/20180102000000id_/https://eepro.com/results/test/final.htm"
        )
        older = capture(c, "first-fetch", "20180101000000", final=final)
        assert selected_snapshot(c.conn, c.spec.watch_id, older.snapshot_id)
        c.conn.execute(
            "UPDATE watches SET archive_url=? WHERE watch_id=?", (final, c.spec.watch_id)
        )
        assert selected_snapshot(c.conn, c.spec.watch_id, older.snapshot_id)
        newer = capture(c, "retry", "20180101000000", requested=final)
        assert selected_snapshot(c.conn, c.spec.watch_id, newer.snapshot_id)
        assert not selected_snapshot(c.conn, c.spec.watch_id, older.snapshot_id)


def test_origin_candidate_ordering_stays_observation_time_then_identifier(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        old = c.snapshot("old")
        first, _ = c.stage(old)
        c.review(first)
        latest = c.snapshot("latest")
        assert not selected_snapshot(c.conn, c.spec.watch_id, old.snapshot_id)
        assert selected_snapshot(c.conn, c.spec.watch_id, latest.snapshot_id)
        assert c.admit(first) == "superseded"
        second, _ = c.stage(latest)
        assert c.admit(second) == "accepted"


def test_phase_two_index_label_does_not_make_captures_independent(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        context = capture(c, "older", "20180101000000")
        c.conn.execute("UPDATE watches SET kind='index' WHERE watch_id=?", (c.spec.watch_id,))
        attempt = begin_attempt(c.conn, context, c.page.EXTRACT_VERSION, c.page.PARSER_VERSION)
        assert attempt.unit_key == c.spec.watch_id
        newer = capture(c, "newer", "20190101000000")
        assert not selected_snapshot(c.conn, c.spec.watch_id, context.snapshot_id)
        assert selected_snapshot(c.conn, c.spec.watch_id, newer.snapshot_id)


def test_old_phase_two_independent_unit_cannot_bypass_active_capture_token(tmp_path):
    from swingset.admission.generations import current_snapshot

    with open_database(tmp_path) as db:
        c = Corpus(db)
        context = capture(c, "selected", "20180101000000")
        attempt = begin_attempt(c.conn, context, c.page.EXTRACT_VERSION, c.page.PARSER_VERSION)
        legacy_shape = replace(
            attempt, unit_key=f"{context.watch_id}/{context.snapshot_id}", archive_url=None
        )
        assert current_snapshot(c.conn, attempt)
        assert not current_snapshot(c.conn, legacy_shape)


def test_explicit_older_capture_replaces_already_admitted_newer_observations(tmp_path):
    with open_database(tmp_path) as db:
        c = Corpus(db)
        newer = capture(c, "accepted-newer", "20200101000000")
        first, _ = c.stage(newer)
        c.review(first)
        assert c.admit(first) == "accepted"
        older_body = BODY.replace(b"Alice Example", b"Earlier Example")
        older = capture(c, "selected-older", "20180101000000", body=older_body)
        replacement, _ = c.stage(older, body=older_body)
        assert c.admit(replacement) == "accepted"
        assert accepted(c) == replacement
        assert (
            c.conn.execute(
                "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?",
                (c.spec.watch_id,),
            ).fetchone()[0]
            == older.snapshot_id
        )
        assert {row[0] for row in c.conn.execute("SELECT snapshot_id FROM observations")} == {
            older.snapshot_id
        }
        assert (
            "Earlier Example"
            in c.conn.execute("SELECT payload_json FROM observations").fetchone()[0]
        )
