import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from journal.tools.admission import rehearse_stepright_fixtures as rehearsal
from swingset.fetch.archive import canonical
from swingset.state import db as db_module
from swingset.state.db import open_database

PAST = datetime.now(UTC) - timedelta(minutes=1)


def external_review(request: dict[str, object], request_sha256: str) -> dict[str, object]:
    contracts = request["contracts"]
    assert isinstance(contracts, dict)
    return {
        "schema": rehearsal.REVIEW_SCHEMA,
        "scope": rehearsal.SCOPE,
        "review_request_sha256": request_sha256,
        "reviewer": "independent-test-reviewer",
        "reviewed_at": request["prepared_at"],
        "evidence": "Independent test review of exact fixture reports",
        "disposable_activation_authorized": True,
        "production_activation_authorized": False,
        "contracts": {kind: value["cohort_digest"] for kind, value in contracts.items()},
    }


def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical(value) + b"\n")


@pytest.mark.parametrize(
    ("prepared_at", "message"),
    (
        (datetime.now(UTC) + timedelta(days=1), "must not be in the future"),
        (datetime(2026, 9, 17), "timezone-aware UTC"),
    ),
)
def test_preparation_rejects_invalid_wall_clock(
    tmp_path: Path, prepared_at: datetime, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        rehearsal.prepare(tmp_path / "rehearsal", prepared_at=prepared_at)


def test_preparation_stages_exact_fixture_cohorts_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rehearsal tool is frozen at the schema it was reviewed against (D-0013).
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    monkeypatch.setattr(rehearsal, "SCHEMA_VERSION", 29)
    output = tmp_path / "rehearsal"
    request = rehearsal.prepare(output, prepared_at=PAST)

    assert request["review_status"] == "unreviewed"
    assert request["production_authority"] is False
    assert request["network_requests"] == request["production_mutations"] == 0
    assert {
        kind: len(contract["generations"]) for kind, contract in request["contracts"].items()
    } == {
        "steprightsolutions.index": 1,
        "steprightsolutions.event": 2,
        "steprightsolutions.round": 2,
    }
    interpretations = {
        generation["interpretation"]
        for contract in request["contracts"].values()
        for generation in contract["generations"]
    }
    assert interpretations == {
        "unreviewed_quarantine_body",
        "independently_reviewed_quarantine_body",
    }
    assert all(
        generation["removal_authority"] == "none"
        for contract in request["contracts"].values()
        for generation in contract["generations"]
    )
    with open_database(output / "state", read_only=True, lock=False) as database:
        assert database.schema_version == 29
        assert database.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
        assert (
            database.connection.execute("SELECT COUNT(*) FROM source_generations").fetchone()[0]
            == 5
        )
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM admission_policies WHERE mode='shadow'"
            ).fetchone()[0]
            == 3
        )


def test_execution_requires_exact_external_disposable_review(tmp_path: Path) -> None:
    output = tmp_path / "rehearsal"
    request = rehearsal.prepare(output, prepared_at=PAST)
    request_sha256 = rehearsal._sha(output / "review-request.json")
    review = external_review(request, request_sha256)
    review["production_activation_authorized"] = True
    review_path = tmp_path / "bad-review.json"
    write_json(review_path, review)

    with pytest.raises(ValueError, match="production_activation_authorized"):
        rehearsal.execute(output, review_path)

    with open_database(output / "state", read_only=True, lock=False) as database:
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM admission_policies WHERE mode='enforce'"
            ).fetchone()[0]
            == 0
        )
        assert database.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_reviewed_rehearsal_admits_five_without_network_or_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "rehearsal"
    request = rehearsal.prepare(output, prepared_at=PAST)
    review_path = tmp_path / "external-review.json"
    request_sha256 = rehearsal._sha(output / "review-request.json")
    write_json(review_path, external_review(request, request_sha256))

    def no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline rehearsal attempted a network socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    receipt = rehearsal.execute(output, review_path)

    assert receipt["scope"] == rehearsal.SCOPE
    assert receipt["network_requests"] == receipt["production_mutations"] == 0
    assert receipt["production_activation_authorized"] is False
    assert receipt["historical_year_acceptance"] is receipt["publication"] is False
    assert {
        kind: (values["accepted"], values["guarded"]) for kind, values in receipt["kinds"].items()
    } == {
        "steprightsolutions.index": (1, 0),
        "steprightsolutions.event": (2, 0),
        "steprightsolutions.round": (2, 0),
    }
    assert all(
        values["removal_authorities"] == {"none": values["prepared"]}
        for values in receipt["kinds"].values()
    )
    assert receipt["observations"]["before"] == receipt["observations"]["removed"] == 0
    assert receipt["observations"]["added"] > 0
    assert receipt["foreign_key_check"] == {"violations": 0, "rows": []}
    queued = {(unit["unit_kind"], unit["unit_id"]) for unit in receipt["queued_projection_units"]}
    assert ("source_index", "steprightsolutions") in queued
    assert any(kind == "source_event" for kind, _ in queued)
    assert ("map", "all") in queued
