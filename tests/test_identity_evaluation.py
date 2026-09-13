"""Synthetic H17 mechanics only; these labels are not reviewed production evidence."""

import json
from copy import deepcopy

import pytest

from swingset.link.evaluation import draw_sample, evaluate, main
from swingset.link.evaluation_input import read_population
from swingset.state.db import open_database

CUTOFF = "2026-09-12T18:00:00+00:00"


def population(count=80, *, stream="accepted", source="eepro"):
    return [
        {
            "subject_kind": "entry",
            "subject_id": f"entry-{i}",
            "event_id": f"event-{i}",
            "source": source,
            "name_raw": f"Person {i}",
            "year": 2025,
            "era": "historical",
            "role": "leader",
            "flags": ["unrestricted"],
            "stream": stream,
            "accepted_wsdc_id": i + 1 if stream == "accepted" else None,
            "candidate_ids": [i + 1] if stream == "accepted" else [],
            "link_method": "source_id" if stream == "accepted" else "none",
            "link_status": "confirmed" if stream == "accepted" else "unmatched",
            "source_reference": {"source_event": f"eepro:{i}", "contest": "jj", "bib": str(i)},
            "evidence": {"snapshot_id": f"snapshot-{i}", "body_sha256": f"{i:064x}"},
        }
        for i in range(count)
    ]


def sample(rows=None, **kwargs):
    return draw_sample(
        population() if rows is None else rows,
        seed="review-seed",
        cohort="synthetic-test",
        cutoff=CUTOFF,
        **kwargs,
    )


def review(row, decision="correct", **kwargs):
    return {
        "sample_id": row["sample_id"],
        "sample_fingerprint": row["sample_fingerprint"],
        "reviewer": "synthetic test reviewer",
        "reviewed_at": CUTOFF,
        "method": "synthetic independent fixture comparison",
        "evidence": ["fixture://synthetic-only"],
        "decision": decision,
        "reference_wsdc_id": row["accepted_wsdc_id"],
        **kwargs,
    }


def test_sampling_is_repeatable_order_independent_and_keeps_strata():
    rows = population() + population(20, source="scoringdance", stream="unresolved")
    first = sample(rows, per_stratum=5)
    assert first == sample(list(reversed(rows)), per_stratum=5)
    assert {row["stream"] for row in first["samples"]} == {"accepted", "unresolved"}
    assert {row["split"] for row in first["samples"]} == {"evaluation", "tuning"}
    assert sum(row["population_size"] for row in first["strata"]) == 100
    assert all(row["sample_size"] == min(5, row["population_size"]) for row in first["strata"])
    assert all(
        row["sample_fingerprint"] and row["source_reference"] and row["evidence"]
        for row in first["samples"]
    )


def test_split_keeps_events_people_and_candidate_aliases_together():
    rows = population(8)
    rows[1]["event_id"] = rows[0]["event_id"]
    rows[2]["candidate_ids"] = [rows[1]["accepted_wsdc_id"]]
    rows[3]["name_raw"] = rows[2]["name_raw"]
    rows[4]["accepted_wsdc_id"] = rows[3]["accepted_wsdc_id"]
    result = sample(rows, per_stratum=100)
    selected = {row["subject_id"]: row for row in result["samples"]}
    assert len({selected[f"entry-{i}"]["split_group"] for i in range(5)}) == 1
    assert len({selected[f"entry-{i}"]["split"] for i in range(5)}) == 1


def tuning_packet():
    rows = population(4)
    for row in rows:
        row["event_id"] = "prior-event"
    return sample(rows, per_stratum=1, evaluation_fraction=1e-20)


def prospective_population():
    rows = population(3)
    for i, row in enumerate(rows):
        row.update(
            subject_id=f"prospective-{i}",
            event_id=f"prospective-event-{i}",
            name_raw=f"Independent {i}",
            accepted_wsdc_id=1000 + i,
            candidate_ids=[1000 + i],
        )
    # A second subject connects only through the first prospective subject.
    rows[1]["candidate_ids"].append(1000)
    return rows


@pytest.mark.parametrize("bridge", ["event", "name", "accepted_id", "candidate_id"])
def test_prior_unsampled_tuning_people_exclude_entire_prospective_component(bridge):
    prior = tuning_packet()
    assert len(prior["samples"]) == 1
    omitted = next(i for i in range(4) if f"entry-{i}" != prior["samples"][0]["subject_id"])
    rows = prospective_population()
    if bridge == "event":
        rows[0]["event_id"] = "prior-event"
    elif bridge == "name":
        rows[0]["name_raw"] = f" PERSON   {omitted} "
    elif bridge == "accepted_id":
        rows[0]["accepted_wsdc_id"] = omitted + 1
    else:
        rows[0]["candidate_ids"].append(omitted + 1)
    result = sample(
        rows, per_stratum=10, evaluation_fraction=1 - 1e-12, prior_tuning_packets=[prior]
    )
    selected = {row["subject_id"]: row for row in result["samples"]}
    assert [selected[f"prospective-{i}"]["split"] for i in range(3)] == [
        "tuning",
        "tuning",
        "evaluation",
    ]
    eligibility = result["heldout_eligibility"]
    assert (eligibility["population_size"], eligibility["eligible"], eligibility["excluded"]) == (
        3,
        1,
        2,
    )
    assert sum(row["eligible"] for row in eligibility["strata"]) == 1
    assert sum(row["excluded"] for row in eligibility["strata"]) == 2
    assert eligibility["excluded_components"][0]["prior_packet_digests"] == [prior["sample_digest"]]
    assert result["prior_tuning_packets"][0]["tuning_population_size"] == 4
    assert evaluate(result, [])["heldout_eligibility"] == eligibility


def test_prior_tuning_packet_digest_is_verified_before_using_exposure():
    prior = tuning_packet()
    prior["tuning_exposure"]["keys"].clear()
    with pytest.raises(ValueError, match="digest mismatch"):
        sample(prospective_population(), prior_tuning_packets=[prior])


def test_prior_candidate_only_identity_is_exposure_without_becoming_a_label():
    rows = population(1)
    rows[0]["candidate_ids"] = [9000]
    prior = sample(rows, evaluation_fraction=1e-20)
    prospective = prospective_population()
    prospective[0]["accepted_wsdc_id"] = 9000
    result = sample(prospective, prior_tuning_packets=[prior])
    assert result["heldout_eligibility"]["excluded"] == 2
    assert prior["samples"][0]["accepted_wsdc_id"] == 1
    assert evaluate(result, [])["reviewed_precision_status"] == "unavailable"


def test_legacy_prior_packet_unknown_exposure_fails_closed_and_carries_forward():
    from swingset.link.evaluation import _digest

    prior = tuning_packet()
    del prior["tuning_exposure"]
    prior["sample_digest"] = _digest({k: v for k, v in prior.items() if k != "sample_digest"})
    result = sample(prospective_population(), prior_tuning_packets=[prior])
    assert result["heldout_eligibility"]["eligible"] == 0
    assert result["heldout_eligibility"]["status"] == "prior_exposure_unavailable"
    assert not result["tuning_exposure"]["complete"]
    assert all(row["split"] == "tuning" for row in result["samples"])
    assert evaluate(result, [])["reviewed_precision_status"] == "unavailable"
    descendant = sample(population(2), prior_tuning_packets=[result])
    assert descendant["heldout_eligibility"]["eligible"] == 0


def test_prior_exposure_inherits_ancestors_and_deduplicates_packet_order():
    first = tuning_packet()
    second = sample(prospective_population(), prior_tuning_packets=[first])
    assert set(first["tuning_exposure"]["keys"]) <= set(second["tuning_exposure"]["keys"])
    rows = population(2)
    descendant = sample(rows, prior_tuning_packets=[second], evaluation_fraction=1 - 1e-12)
    assert descendant["heldout_eligibility"]["excluded"] == 2
    assert sample(rows, prior_tuning_packets=[first, second]) == sample(
        rows, prior_tuning_packets=[second, first, first]
    )


def test_no_adjudications_explicitly_leave_precision_unavailable():
    result = evaluate(sample(), [])
    assert result["reviewed_precision_status"] == "unavailable"
    assert result["population_precision"] is None
    assert result["strata"][0]["precision"] is None
    assert result["strata"][0]["precision_interval_95"] is None
    assert result["strata"][0]["missing_reviews"] == 10


def test_precision_reports_correct_numerator_denominator_and_uncertainty():
    frozen = sample()
    selected = [row for row in frozen["samples"] if row["split"] == "evaluation"]
    reviews = [
        review(row, "correct" if index < 8 else "incorrect") for index, row in enumerate(selected)
    ]
    result = evaluate(frozen, reviews)
    stratum = result["strata"][0]
    assert result["reviewed_precision_status"] == "available"
    assert stratum["precision_numerator"] == 8
    assert stratum["precision_denominator"] == 10
    assert stratum["precision"] == 0.8
    assert stratum["false_accepted"] == 2
    assert stratum["precision_interval_95"] == pytest.approx([0.490162, 0.943318], abs=1e-6)
    assert result == evaluate(frozen, list(reversed(reviews)))
    assert result["population_precision"] is None


def test_partial_or_inconclusive_review_does_not_estimate_precision():
    frozen = sample()
    rows = [r for r in frozen["samples"] if r["split"] == "evaluation"]
    incomplete = evaluate(frozen, [review(rows[0])])["strata"][0]
    assert incomplete["precision_numerator"] == 1
    assert incomplete["precision_denominator"] == 1
    assert incomplete["precision"] is None
    reviews = [
        review(r, "insufficient_evidence" if i == 0 else "correct") for i, r in enumerate(rows)
    ]
    inconclusive = evaluate(frozen, reviews)["strata"][0]
    assert inconclusive["missing_reviews"] == 0
    assert inconclusive["insufficient_evidence"] == 1
    assert inconclusive["precision"] is None


def test_unresolved_review_finds_missing_candidates_outside_empty_pool():
    frozen = sample(population(stream="unresolved"))
    row = next(r for r in frozen["samples"] if r["split"] == "evaluation")
    adjudication = review(row, "matched", reference_wsdc_id=999999, candidate_search_complete=True)
    result = evaluate(frozen, [adjudication])["strata"][0]
    assert result["missing_candidates"] == 1
    assert result["resolvable_abstentions"] == 1
    assert result["precision"] is None
    adjudication["candidate_search_complete"] = False
    with pytest.raises(ValueError, match="beyond the candidate list"):
        evaluate(frozen, [adjudication])


def test_stale_duplicate_unattributed_or_inappropriate_reviews_are_rejected():
    frozen = sample()
    row = frozen["samples"][0]
    for updates, message in [
        ({"sample_fingerprint": "stale"}, "frozen subject"),
        ({"reviewer": ""}, "author"),
        ({"evidence": []}, "evidence"),
        ({"decision": "matched"}, "stream"),
        ({"reference_wsdc_id": 999999}, "contradicts"),
    ]:
        with pytest.raises(ValueError, match=message):
            evaluate(frozen, [{**review(row), **updates}])
    with pytest.raises(ValueError, match="duplicate"):
        evaluate(frozen, [review(row), review(row)])
    changed = deepcopy(frozen)
    changed["samples"][0]["candidate_ids"] = [99999]
    with pytest.raises(ValueError, match="digest"):
        evaluate(changed, [])


def test_enriched_regression_cases_cannot_masquerade_as_representative_sample():
    rows = population(1)
    rows[0]["stream"] = "known_failure"
    with pytest.raises(ValueError, match="regression corpus"):
        sample(rows)


def test_population_reader_includes_unresolved_entries_with_no_link_or_candidates(
    tmp_path, monkeypatch
):
    from test_link_service import entry, seed

    import swingset.state.db as state_db

    # This legacy fixture predates immutable derivation receipts.
    monkeypatch.setattr(state_db, "SCHEMA_VERSION", 13)

    with open_database(tmp_path) as db:
        seed(db.connection)
        entry(db.connection, "unresolved", "c1", "7", "New Person")
        db.connection.execute(
            "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('future','future','Future listing',2030,'2030-01-01','2030-01-04','registry','[]','test','snap','1','t','t','run')"
        )
        rows, context = read_population(db.connection)
        assert context["latest_event_year"] == 2026
        assert rows[0]["era"] == "latest_year"
        assert len(rows) == 1
        assert rows[0]["stream"] == "unresolved"
        assert rows[0]["candidate_ids"] == []
        assert rows[0]["source_reference"]["legacy_subject_id"] == "unresolved"
        assert "local settled" in context["population"]
        db.connection.execute("INSERT INTO pending_work VALUES ('link','event','event','now')")
        with pytest.raises(ValueError, match="settled"):
            read_population(db.connection)


def test_evaluation_cli_creates_report_without_mutating_sample_or_reviews(tmp_path):
    sample_path, reviews, output = (
        tmp_path / "sample.json",
        tmp_path / "reviews.json",
        tmp_path / "report.json",
    )
    sample_path.write_text(json.dumps(sample()))
    original = sample_path.read_bytes()
    reviews.write_text("[]")
    main(
        [
            "evaluate",
            "--sample",
            str(sample_path),
            "--reviews",
            str(reviews),
            "--output",
            str(output),
        ]
    )
    assert json.loads(output.read_text())["reviewed_precision_status"] == "unavailable"
    assert sample_path.read_bytes() == original
    assert reviews.read_text() == "[]"
    with pytest.raises(FileExistsError):
        main(
            [
                "evaluate",
                "--sample",
                str(sample_path),
                "--reviews",
                str(reviews),
                "--output",
                str(output),
            ]
        )


def test_evidence_cutoff_cannot_predate_captured_input():
    rows = population(1)
    rows[0]["evidence"]["fetched_at"] = "2026-09-13T00:00:00+00:00"
    with pytest.raises(ValueError, match="after the cutoff"):
        sample(rows)


def test_sample_cli_reads_database_without_changing_state(tmp_path, monkeypatch):
    from test_link_service import entry, seed

    import swingset.state.db as state_db

    monkeypatch.setattr(state_db, "SCHEMA_VERSION", 13)

    state, output = tmp_path / "state", tmp_path / "sample.json"
    prior_path = tmp_path / "prior.json"
    prior_bytes = json.dumps(tuning_packet()).encode()
    prior_path.write_bytes(prior_bytes)
    with open_database(state) as db:
        seed(db.connection)
        entry(db.connection, "unresolved", "c1", "7", "New Person")
        before = list(db.connection.iterdump())
        main(
            [
                "sample",
                "--state",
                str(state),
                "--output",
                str(output),
                "--seed",
                "test",
                "--cohort",
                "synthetic",
                "--cutoff",
                CUTOFF,
                "--prior-tuning-packet",
                str(prior_path),
            ]
        )
        assert list(db.connection.iterdump()) == before
    artifact = json.loads(output.read_text())
    assert artifact["samples"][0]["candidate_ids"] == []
    assert artifact["context"]["schema_version"] >= 5
    assert (
        artifact["prior_tuning_packets"][0]["sample_digest"]
        == json.loads(prior_bytes)["sample_digest"]
    )
    assert prior_path.read_bytes() == prior_bytes


def test_review_packet_copies_verified_readable_evidence_and_starts_unreviewed(tmp_path):
    from swingset.fetch.archive import Archive
    from swingset.link.evaluation_packet import write_packet

    archive = Archive(tmp_path / "state")
    raw = b"<html><script>do_not_execute()</script>Person evidence</html>"
    body = archive.store_body(raw)
    extract = archive.store_extract({"rows": ["Retained person evidence"]})
    rows = population(1)
    rows[0]["name_raw"] = "<script>unsafe_name()</script>"
    rows[0]["evidence"].update(body_sha256=body, extract_sha256=extract)
    artifact = sample(rows)
    output = tmp_path / "packet"
    receipt = write_packet(artifact, archive, output)
    assert receipt["reviewed_precision"].startswith("unavailable")
    assert receipt["copied_artifacts"] == 2
    assert receipt["missing_artifacts"] == []
    page = (output / "index.html").read_text()
    assert "<script>unsafe_name()" not in page
    assert "&lt;script&gt;unsafe_name()" in page
    assert "do_not_execute()" not in page
    assert (output / "evidence" / f"body-{body}.txt").read_bytes() == raw
    assert (
        "Retained person evidence" in (output / "evidence" / f"extract-{extract}.txt").read_text()
    )
    template = json.loads((output / "adjudications-template.json").read_text())
    assert template[0]["decision"] == template[0]["reviewer"] == ""
    assert template[0]["sample_fingerprint"] == artifact["samples"][0]["sample_fingerprint"]
    assert json.loads((output / "sample.json").read_text()) == artifact
    with pytest.raises(FileExistsError):
        write_packet(artifact, archive, output)


def test_review_packet_marks_missing_or_corrupt_evidence_without_labels(tmp_path):
    from swingset.fetch.archive import Archive
    from swingset.link.evaluation_packet import write_packet

    archive = Archive(tmp_path / "state")
    body = archive.store_body(b"original")
    archive.blob_path(body).write_bytes(b"corrupted retained artifact")
    rows = population(1, stream="unresolved")
    rows[0]["evidence"].update(body_sha256=body, extract_sha256="b" * 64)
    output = tmp_path / "packet"
    receipt = write_packet(sample(rows), archive, output)
    assert receipt["copied_artifacts"] == 0
    assert len(receipt["missing_artifacts"]) == 2
    assert "Retained body unavailable" in (output / "index.html").read_text()
    assert (
        json.loads((output / "adjudications-template.json").read_text())[0][
            "candidate_search_complete"
        ]
        is False
    )
