"""Independent H10 boundary checks with exact durable-reference fixtures."""

import copy
import json
from dataclasses import asdict, replace
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from swingset.build.builder import BuildInput
from swingset.build.files import sha256_file
from swingset.build.identity_policy import apply_identity_policy, repair_placement_identities
from swingset.state.db import open_database
from swingset.state.identity_journal import Decision, accept_journal, encode_journal
from swingset.state.identity_references import (
    ReferenceBinding,
    SourceReference,
    record_migration,
    retain_binding,
)

NOW = "2026-09-13T06:00:00+00:00"
REFERENCE = SourceReference(
    "scoringdance", "source-event", "round-sheet", "0", "entry:leader:bib:7"
)
BINDING = ReferenceBinding(
    REFERENCE,
    "entry",
    "entry",
    "event",
    "snapshot",
    "a" * 64,
    {"row": 0, "column": 1, "role": "leader", "bib": "7", "source_wsdc_id": 100},
)


def inputs(*, number=100, method="source_id", status="confirmed"):
    return BuildInput(
        {
            "entries": [
                {
                    "entry_id": "entry",
                    "contest_id": "contest",
                    "event_id": "event",
                    "role": "leader",
                    "bib": "7",
                    "name_raw": "Alex Lee",
                    "name_norm": "alex lee",
                    "wsdc_id": number,
                    "link_status": status,
                    "snapshot_id": "snapshot",
                    "parser_version": "1",
                    "source": "scoringdance",
                }
            ],
            "contests": [{"contest_id": "contest", "name_raw": "Novice Jack & Jill"}],
            "identity_links": [
                {
                    "subject_kind": "entry",
                    "subject_id": "entry",
                    "wsdc_id": 100,
                    "status": status,
                    "method": method,
                    "confidence": 1.0,
                }
            ],
            "link_candidates": [
                {
                    "subject_kind": "entry",
                    "subject_id": "entry",
                    "wsdc_id": 100,
                    "score": 0.97,
                    "name_similarity": 1.0,
                    "chosen": True,
                }
            ],
            "placements": [
                {
                    "placement_id": "place",
                    "leader_entry_id": "entry",
                    "follower_entry_id": None,
                    "leader_wsdc_id": number,
                    "follower_wsdc_id": None,
                    "registry_points_leader": 3 if number is not None else None,
                    "registry_points_follower": None,
                    "registry_confirmed": False,
                    "points_matches_expected": True,
                }
            ],
        },
        {},
        {},
        {},
        {},
        "bundle",
    )


def baseline(path, data):
    path.mkdir()
    files = {}
    for name in ("entries", "judges"):
        rows = data.tables.get(name, [])
        if not rows:
            continue
        target = path / "data" / name / "part.parquet"
        target.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows), target)
        files[target.relative_to(path).as_posix()] = sha256_file(target)
    (path / "_meta").mkdir()
    manifest = path / "_meta" / "manifest.json"
    manifest.write_text(json.dumps({"files": files}))
    (path / "BUILT").write_text(json.dumps({"manifest_hash": sha256_file(manifest)}))
    (path / "PUBLISHED").write_text(json.dumps({"commit": "acknowledged-baseline"}))
    return path


def reviewed(
    conn,
    *,
    number="100",
    kind="different_person",
    identifier="review",
    supersedes="",
    reference=REFERENCE,
    previous=(),
):
    row = Decision(
        identifier,
        **asdict(reference),
        wsdc_id=number,
        decision=kind,
        evidence="fixture:reviewed-source",
        reason="Independent review",
        author="Reviewer",
        date="2026-09-13",
        supersedes=supersedes,
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        accept_journal(conn, encode_journal((*previous, row)), bundle_digest="bundle", now=NOW)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return row


def apply(conn, data, published, *, bindings=(BINDING,), correction_only=True):
    with (
        patch("swingset.build.identity_policy.ReferenceReader.for_record", return_value=bindings),
        patch(
            "swingset.build.identity_policy.interpretation_support",
            return_value={"usable": True, "state": "accepted", "selected_observation": True},
        ),
    ):
        return apply_identity_policy(
            conn, data, baseline=published, correction_only=correction_only
        )


@pytest.mark.parametrize("method", ["source_id", "manual", "registry_placement"])
def test_every_confirmed_route_obeys_current_rejection_and_preserves_scores(tmp_path, method):
    data = inputs(method=method)
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        reviewed(db.connection)
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["placements"][0]["leader_wsdc_id"] is None
    assert result.tables["placements"][0]["registry_points_leader"] is None
    assert result.tables["placements"][0]["points_matches_expected"] is None
    assert result.tables["link_candidates"][0]["score"] == 0.97
    assert result.tables["link_candidates"][0]["chosen"] is False


@pytest.mark.parametrize("correction_only", [False, True])
def test_identity_policy_shares_scores_and_preserves_nested_input(tmp_path, correction_only):
    data = inputs()
    data.tables["identity_links"][0]["source_ref_ids"] = ["original-reference"]
    data.tables["link_candidates"][0]["evidence"] = {"cells": ["original-cell"]}
    marks = [{"round_id": "round", "rank": 1, "retained": {"cells": ["1"]}}]
    data = replace(data, tables={**data.tables, "final_marks": marks})
    before = copy.deepcopy(data.tables)
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        reviewed(db.connection)
        result = apply(db.connection, data, published, correction_only=correction_only)
    assert data.tables == before
    assert result.tables["final_marks"] is marks
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert data.tables["entries"][0]["wsdc_id"] == 100
    assert result.tables["identity_links"][0]["source_ref_ids"] != ["original-reference"]


def test_missing_source_reference_withholds_baseline_id_without_guessing(tmp_path):
    data = inputs()
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        result = apply(db.connection, data, published, bindings=())
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["entries"][0]["name_raw"] == "Alex Lee"
    assert result.tables["link_candidates"][0]["score"] == 0.97


def test_old_baseline_reference_obeys_supersession_at_migrated_reference(tmp_path):
    data = inputs(method="manual")
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        first = reviewed(db.connection, kind="same_person")
        target = replace(BINDING, reference=replace(REFERENCE, contest="renumbered"))
        with db.transaction() as conn:
            retain_binding(conn, target, now=NOW)
            record_migration(
                conn,
                migration_id="continuity",
                from_ref_id=REFERENCE.ref_id,
                to_ref_ids=(target.reference.ref_id,),
                status="approved",
                evidence="fixture:reviewed-continuity",
                reason="Source renumbering",
                author="Reviewer",
                date="2026-09-13",
                now=NOW,
            )
        reviewed(
            db.connection,
            number="101",
            kind="same_person",
            identifier="replacement",
            supersedes=first.decision_id,
            reference=target.reference,
            previous=(first,),
        )
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["identity_links"][0]["decision_ids"] == ["replacement"]


def test_same_snapshot_source_id_disagreement_cannot_keep_old_assertion(tmp_path):
    data = inputs()
    published = baseline(tmp_path / "baseline", data)
    changed = replace(BINDING, locator={**BINDING.locator, "source_wsdc_id": 101})
    with open_database(tmp_path / "state") as db:
        result = apply(db.connection, data, published, bindings=(changed,), correction_only=False)
    assert result.tables["entries"][0]["wsdc_id"] is None


def test_rejected_probable_candidate_keeps_evidence_but_is_no_longer_chosen(tmp_path):
    data = inputs(number=None, method="name_unique", status="probable")
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        reviewed(db.connection)
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["link_candidates"][0]["score"] == 0.97
    assert result.tables["link_candidates"][0]["chosen"] is False


def test_correction_cannot_promote_current_registry_evidence_absent_from_baseline(tmp_path):
    prior = inputs(number=None, method="name_unique", status="probable")
    published = baseline(tmp_path / "baseline", prior)
    current = inputs(method="registry_placement")
    with open_database(tmp_path / "state") as db:
        result = apply(db.connection, current, published, correction_only=True)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["placements"][0]["leader_wsdc_id"] is None
    assert result.tables["placements"][0]["registry_points_leader"] is None


def h17_inputs(
    entries=(("entry", "leader", "Alex Lee", 100, ("prelim", "final")),),
    *,
    method="source_id",
    contest="Novice Jack & Jill",
    dancers=((100, "Alex Lee"),),
    registry=(),
):
    """One contest whose entries all hold confirmed links; ``registry`` rows are
    (wsdc_id, role, result) at this event, division and style."""
    data = inputs(method=method)
    tables = data.tables
    tables["entries"] = [
        {
            **tables["entries"][0],
            "entry_id": entry_id,
            "role": role,
            "name_raw": name,
            "wsdc_id": number,
            "rounds_danced": list(rounds),
        }
        for entry_id, role, name, number, rounds in entries
    ]
    tables["identity_links"] = [
        {**tables["identity_links"][0], "subject_id": entry_id, "wsdc_id": number}
        for entry_id, _, _, number, _ in entries
    ]
    tables["link_candidates"] = []
    tables["contests"] = [
        {
            "contest_id": "contest",
            "name_raw": contest,
            "division": "novice",
            "dance_style": "wcs",
            "wsdc_points_eligible": True,
        }
    ]
    tables["placements"] = [
        {
            "placement_id": f"place-{place}",
            "contest_id": "contest",
            "event_id": "event",
            "place": place,
            f"{role}_entry_id": entry_id,
            f"{role}_wsdc_id": number,
        }
        for place, (entry_id, role, _, number, _) in enumerate(entries, start=1)
    ]
    tables["dancers"] = [
        {"wsdc_id": number, "first_name": name.split()[0], "last_name": name.split()[1]}
        for number, name in dancers
    ]
    tables["registry_placements"] = [
        {
            "wsdc_id": number,
            "event_id": "event",
            "role": role,
            "division": "novice",
            "dance_style": "wcs",
            "result": result,
        }
        for number, role, result in registry
    ]
    return data


def apply_h17(tmp_path, data, published=None, support=None):
    """Bind each record to its own printed id and apply the policy."""

    def printed(kind, row, **_):
        reference = replace(REFERENCE, participant=f"entry:{row['entry_id']}")
        return (
            replace(
                BINDING,
                reference=reference,
                subject_id=row["entry_id"],
                locator={**BINDING.locator, "source_wsdc_id": row["wsdc_id"]},
            ),
        )

    with (
        open_database(tmp_path / "state") as db,
        patch("swingset.build.identity_policy.ReferenceReader.for_record", side_effect=printed),
        patch(
            "swingset.build.identity_policy.interpretation_support",
            return_value=support
            or {"usable": True, "state": "accepted", "selected_observation": True},
        ),
    ):
        result = apply_identity_policy(
            db.connection, data, baseline=published, correction_only=False
        )
    ids = {row["entry_id"]: row["wsdc_id"] for row in result.tables["entries"]}
    return result, ids, result.release_policy["default_join_counts"]


@pytest.mark.parametrize("name", ["Alex Lee", "Lee Alex", "ALEX  LEE"])
def test_h17_accepts_new_printed_id_whose_registry_name_agrees(tmp_path, name):
    data = h17_inputs(entries=(("entry", "leader", name, 100, ("prelim", "final")),))
    result, ids, counts = apply_h17(tmp_path, data)
    assert ids == {"entry": 100}
    assert counts == {"accepted_h17": 1}
    assert result.tables["identity_links"][0]["acceptance_state"] == "accepted"
    assert result.tables["placements"][0]["leader_wsdc_id"] == 100
    assert result.release_policy["accuracy_expansions"] == "D-0248"


def test_h17_accepts_new_registry_placement_join(tmp_path):
    data = h17_inputs(method="registry_placement", registry=((100, "leader", "1"),))
    _, ids, counts = apply_h17(tmp_path, data)
    assert ids == {"entry": 100}
    assert counts == {"accepted_h17": 1}


@pytest.mark.parametrize(
    ("dancers", "registry", "reason"),
    [
        ((), (), "printed_identity_not_in_registry"),
        (((100, "Sam Lee"),), (), "printed_name_disagrees"),
        (((100, "Alex Lee"),), ((100, "follower", "1"),), "role_contradicted"),
        (((100, "Alex Lee"),), ((100, "follower", "F"),), "role_contradicted"),
    ],
)
def test_h17_withholds_a_printed_id_that_fails_a_rule(tmp_path, dancers, registry, reason):
    data = h17_inputs(dancers=dancers, registry=registry)
    result, ids, counts = apply_h17(tmp_path, data)
    assert ids == {"entry": None}
    assert counts == {reason: 1}
    assert result.tables["identity_links"][0]["acceptance_state"] == "revoked"
    assert result.tables["placements"][0]["leader_wsdc_id"] is None


@pytest.mark.parametrize(
    ("contest", "withheld"),
    [("Novice Jack & Jill", {"flipped", "finalist"}), ("Switch Jack & Jill", set())],
)
def test_h17_withholds_every_final_only_entry_of_a_role_contradicted_contest(
    tmp_path, contest, withheld
):
    entries = (
        ("bib", "leader", "Alex Lee", 100, ("prelim", "final")),
        ("flipped", "follower", "Alex Lee", 100, ("final",)),
        ("finalist", "leader", "Kim Park", 101, ("final",)),
        ("other", "follower", "Jo Diaz", 102, ("prelim", "final")),
    )
    dancers = ((100, "Alex Lee"), (101, "Kim Park"), (102, "Jo Diaz"))
    data = h17_inputs(entries, contest=contest, dancers=dancers)
    _, ids, counts = apply_h17(tmp_path, data)
    assert {key for key, value in ids.items() if value is None} == withheld
    assert counts.get("role_contradicted", 0) == len(withheld)


def test_h17_registry_role_contradiction_marks_the_contest(tmp_path):
    entries = (
        ("flipped", "leader", "Alex Lee", 100, ("final",)),
        ("finalist", "leader", "Kim Park", 101, ("final",)),
        ("bib", "follower", "Jo Diaz", 102, ("prelim", "final")),
    )
    dancers = ((100, "Alex Lee"), (101, "Kim Park"), (102, "Jo Diaz"))
    data = h17_inputs(entries, dancers=dancers, registry=((100, "follower", "1"),))
    _, ids, counts = apply_h17(tmp_path, data)
    assert ids == {"flipped": None, "finalist": None, "bib": 102}
    assert counts == {"accepted_h17": 1, "role_contradicted": 2}


@pytest.mark.parametrize("state", ["superseded", "legacy_unassessed"])
def test_h17_never_accepts_a_new_join_on_legacy_support(tmp_path, state):
    data = h17_inputs(entries=(("entry", "leader", "Alex Lee", 100, ("prelim", "final")),))
    nulled = copy.deepcopy(data)
    for row in nulled.tables["entries"]:
        row["wsdc_id"] = None
    published = baseline(tmp_path / "baseline", nulled)
    _, ids, counts = apply_h17(
        tmp_path,
        data,
        published=published,
        support={"usable": False, "state": state, "selected_observation": True},
    )
    assert ids == {"entry": None}
    assert counts == {"source_interpretation_unavailable": 1}


def test_h17_does_not_accept_new_manual_joins(tmp_path):
    data = inputs(method="manual")
    with open_database(tmp_path / "state") as db:
        reviewed(db.connection, kind="same_person")
        result = apply(db.connection, data, None, correction_only=False)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.release_policy["default_join_counts"] == {"accuracy_gate_pending": 1}


def test_baseline_id_keeps_the_baseline_path_without_h17_rules(tmp_path):
    data = inputs()
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        result = apply(db.connection, data, published, correction_only=False)
    assert result.tables["entries"][0]["wsdc_id"] == 100
    assert result.release_policy["default_join_counts"] == {"accepted": 1}


def test_no_identity_or_points_cannot_retain_expected_points_flag():
    tables = {
        "entries": [],
        "placements": [
            {
                "leader_entry_id": None,
                "follower_entry_id": None,
                "leader_wsdc_id": None,
                "follower_wsdc_id": None,
                "registry_points_leader": None,
                "registry_points_follower": None,
                "registry_confirmed": True,
                "points_matches_expected": True,
            }
        ],
    }
    repair_placement_identities(tables)
    assert tables["placements"][0]["registry_confirmed"] is False
    assert tables["placements"][0]["points_matches_expected"] is None


@pytest.mark.parametrize("method", ["manual", "name_unique", "assignment", "bib_reuse", "none"])
def test_confirmed_assertion_requires_a_current_supported_acceptance_path(tmp_path, method):
    data = inputs(method=method)
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["identity_links"][0]["acceptance_state"] == "revoked"
    assert result.tables["link_candidates"][0]["score"] == 0.97


def test_superseded_manual_support_cannot_survive_as_an_unrestricted_pair(tmp_path):
    data = inputs(method="manual")
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        first = reviewed(db.connection, kind="same_person")
        reviewed(
            db.connection,
            kind="different_person",
            number="101",
            identifier="replacement",
            supersedes=first.decision_id,
            previous=(first,),
        )
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] is None
    assert result.tables["identity_links"][0]["decision_ids"] == ["replacement"]


def test_active_reviewed_positive_remains_valid_manual_support(tmp_path):
    data = inputs(method="manual")
    published = baseline(tmp_path / "baseline", data)
    with open_database(tmp_path / "state") as db:
        reviewed(db.connection, kind="same_person")
        result = apply(db.connection, data, published)
    assert result.tables["entries"][0]["wsdc_id"] == 100
    assert result.tables["identity_links"][0]["acceptance_state"] == "accepted"
