"""H3 source-supported counterexamples from complete retained archived bodies."""

import hashlib
import json
from pathlib import Path

import pytest
from selectolax.parser import HTMLParser
from test_link_service import Bundle, run, seed

from swingset.link import DancerRecord, Subject, generate_candidates, score_candidate
from swingset.normalize.names import normalize_name
from swingset.sources.base import ParseContext
from swingset.sources.wsdc_registry.adapter import DancerPage
from swingset.state.db import open_database

FIXTURES = Path(__file__).parent / "fixtures/identity/judge-audit-2026-09-13"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def body(snapshot_id):
    metadata = MANIFEST["snapshots"][snapshot_id]
    raw = (FIXTURES / metadata["fixture"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata["body_sha256"]
    return raw


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["judge"]["name_raw"])
def test_raw_roster_and_independently_printed_id_support_the_comparison(case):
    judge = case["judge"]
    wanted = normalize_name(judge["name_raw"]).value
    roster = HTMLParser(body(judge["snapshot_id"]))
    assert any(
        normalize_name(node.attributes.get("title", "")).value == wanted
        for node in roster.css("th[title]")
    )
    results = HTMLParser(body(case["printed_entry"]["snapshot_id"]))
    assert any(
        normalize_name(node.text()).value == wanted
        and node.attributes["data-wsdc"] == str(case["source_supported_id"])
        for node in results.css("a[data-wsdc]")
    )
    for dancer in case["dancers"]:
        page = DancerPage()
        snapshot = MANIFEST["snapshots"][dancer["snapshot_id"]]
        context = ParseContext(
            snapshot["snapshot_id"],
            "retained",
            snapshot["url"],
            "wsdc_registry",
            page.kind,
            f"wsdc:{dancer['wsdc_id']}",
            snapshot["fetched_at"],
        )
        lookup = (
            page.parse(page.extract(body(dancer["snapshot_id"])), context).observations[0].payload
        )
        assert lookup.wsdc_id == dancer["wsdc_id"]
        assert (
            normalize_name(f"{lookup.first_name} {lookup.last_name}").value == dancer["name_norm"]
        )


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["judge"]["name_raw"])
def test_judge_counterexample_rescore_retains_true_evidence_and_caps_contrast(case):
    judge = case["judge"]
    subject = Subject("judge", judge["judge_id"], judge["name_raw"], event_year=judge["year"])
    dancers = [
        DancerRecord(
            dancer["wsdc_id"],
            f"{dancer['first_name']} {dancer['last_name']}",
            dancer["primary_role"],
            dancer["recent_year"],
            bool(dancer["is_pro"]),
            dancer["leader_required_level"],
            dancer["leader_allowed_level"],
            dancer["follower_required_level"],
            dancer["follower_allowed_level"],
        )
        for dancer in case["dancers"]
    ]
    candidates = {
        candidate.dancer.wsdc_id: candidate
        for candidate in generate_candidates(subject, dancers, {})
    }
    assert score_candidate(candidates[case["source_supported_id"]]) == pytest.approx(1.0)
    assert score_candidate(candidates[case["contrasting_id"]]) < 0.9
    # The underlying name evidence is preserved, including the mistaken perfect
    # nickname match caused by sharing only first and last tokens.
    for retained in case["retained_candidates"]:
        assert candidates[retained["wsdc_id"]].name_similarity == retained["name_similarity"]


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["judge"]["name_raw"])
def test_counterexample_linker_produces_no_new_default_judge_join(tmp_path, case):
    with open_database(tmp_path) as db:
        seed(db.connection)
        db.connection.execute("DELETE FROM dancers")
        for dancer in case["dancers"]:
            columns = ",".join(dancer)
            db.connection.execute(
                f"INSERT INTO dancers({columns}) VALUES ({','.join('?' for _ in dancer)})",
                tuple(dancer.values()),
            )
        judge = {key: value for key, value in case["judge"].items() if key != "year"}
        judge["event_id"] = "event"
        db.connection.execute(
            f"INSERT INTO judges({','.join(judge)}) VALUES ({','.join('?' for _ in judge)})",
            tuple(judge.values()),
        )
        run(db, Bundle())
        assert db.connection.execute("SELECT wsdc_id FROM judges").fetchone()[0] is None
        assert (
            db.connection.execute(
                "SELECT score FROM link_candidates WHERE wsdc_id=?", (case["contrasting_id"],)
            ).fetchone()[0]
            < 0.9
        )


def test_named_judge_without_registry_number_remains_a_valid_unmatched_judge(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        db.connection.execute(
            "INSERT INTO judges(judge_id,event_id,name_raw,initials,anonymous,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('judge','event','Taylor Unregistered','TU',0,'test','snap','1','t','t','run')"
        )
        run(db, Bundle())
        judge = db.connection.execute("SELECT name_raw,anonymous,wsdc_id FROM judges").fetchone()
        assert tuple(judge) == ("Taylor Unregistered", 0, None)
        link = db.connection.execute(
            "SELECT status,wsdc_id FROM identity_links WHERE subject_kind='judge'"
        ).fetchone()
        assert tuple(link) == ("unmatched", None)
        assert db.connection.execute("SELECT COUNT(*) FROM link_candidates").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0
