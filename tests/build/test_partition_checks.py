"""Per-partition checks: support without a clock, references, and the builder's checks."""

import re
from datetime import date

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.build.builder import BuildError, _validate
from swingset.build.closure_manifest import canonical
from swingset.build.partition_checks import SupportIndex, dangling, release_checks

START = date(2010, 1, 1)


def valid():
    return {
        "events": [
            {
                "event_id": "e",
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 1, 3),
                "year": 2026,
                "date_precision": "day",
                "coverage_tier": "sheets_complete",
            }
        ],
        "entries": [
            {"entry_id": "a", "contest_id": "c", "role": "leader", "bib": "1", "wsdc_id": None},
            {"entry_id": "b", "contest_id": "c", "role": "follower", "bib": "2", "wsdc_id": None},
        ],
        "rounds": [{"round_id": "r"}],
        "judges": [{"judge_id": "j", "wsdc_id": None}],
        "callback_marks": [
            {"round_id": "r", "entry_id": "a", "judge_id": "j", "mark": "yes", "mark_value": 10.0}
        ],
        "callbacks": [
            {
                "round_id": "r",
                "entry_id": "a",
                "yes_count": 1,
                "alt_count": 0,
                "no_count": 0,
                "score_sum": 10.0,
            }
        ],
        "placements": [
            {"placement_id": "p1", "round_id": "r", "place": 1, "leader_entry_id": "a"},
            {"placement_id": "p2", "round_id": "r", "place": 2, "follower_entry_id": "b"},
        ],
    }


def change(table, index, **values):
    rows = valid()
    rows[table][index] = {**rows[table][index], **values}
    return rows


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (change("entries", 0, role="lead"), "invalid_enum"),
        (change("events", 0, start_date=date(2026, 2, 1)), "invalid_event_dates"),
        (change("callback_marks", 0, entry_id="gone"), "missing_reference"),
        (change("callbacks", 0, yes_count=2), "callback_counts_disagree"),
        (change("placements", 1, place=3), "invalid_placements"),
        (change("entries", 0, wsdc_id=7, link_status="unmatched"), "unsupported_identity"),
    ],
)
def test_each_release_check_withholds_with_its_own_reason(rows, reason):
    assert release_checks(valid(), START) is None
    found = release_checks(rows, START)
    assert found is not None and found[0] == reason
    # The whole-release check still fails on the same rows, with the same message.
    with pytest.raises(BuildError, match="^" + re.escape(found[1])):
        _validate(rows, START)


def test_dangling_names_a_row_whose_parent_the_partition_lacks():
    rows = {
        ("entries", canonical(["a"])): {"entry_id": "a"},
        ("rounds", canonical(["r"])): {"round_id": "r"},
        ("judges", canonical(["j"])): {"judge_id": "j"},
        ("callback_marks", "m1"): {"entry_id": "a", "round_id": "r", "judge_id": "j"},
        ("callback_marks", "m2"): {"entry_id": "b", "round_id": "r", "judge_id": "j"},
        ("placements", "p"): {"leader_entry_id": None, "follower_entry_id": "a"},
    }
    assert dangling(rows) == [("callback_marks", "m2", "entry_id")]


def test_support_reads_accepted_source_generations_and_revocations(source_fixture):
    from swingset.admission.select import revoke_generation

    f = source_fixture
    parser = f.conn.execute(
        "SELECT json_extract(recipe_json,'$.parser_version') FROM source_generations WHERE generation_id=?",
        (f.generation,),
    ).fetchone()[0]
    support = SupportIndex(f.conn)
    assert support("round-one", str(parser)).state == "accepted"
    assert support("round-one", str(parser)).generations == (f.generation,)
    # Another parser of the same page, or a page nothing admitted, is legacy.
    assert support("round-one", "0").state == "legacy_unassessed"
    assert support("retained-calendar-baseline", "1").state == "legacy_unassessed"
    revoke_generation(
        f.db,
        f.generation,
        reason="Synthetic adjudication proved the sheet wrong",
        now=f.corpus.clock.now().isoformat(),
        run_id=f.corpus.run,
    )
    assert SupportIndex(f.conn)("round-one", str(parser)).state == "revoked"


def test_support_matches_each_observation_against_the_accepted_generations(source_fixture):
    from swingset.build.partition_checks import live_observations

    f = source_fixture
    parser = str(
        f.conn.execute(
            "SELECT json_extract(recipe_json,'$.parser_version') FROM source_generations "
            "WHERE generation_id=?",
            (f.generation,),
        ).fetchone()[0]
    )
    support = SupportIndex(f.conn)
    held = support.generation_observations(f.generation)
    assert held
    live = live_observations(f.conn, "round-one", parser)
    assert live and live <= held
    matched = support.matched("round-one", parser, live)
    assert (matched.state, matched.generations) == ("accepted", (f.generation,))
    one = next(iter(live))
    for other in (
        (*one[:4], "0" * 64, one[5]),
        (*one[:5], "999"),
        (one[0], one[1], one[2], one[3] + 100, one[4], one[5]),
    ):
        assert support.matched("round-one", parser, {*live, other}).state == "legacy_unassessed"
    assert support.matched("round-one", parser, ()).state == "legacy_unassessed"
    assert support.matched("round-one", "0", live).state == "legacy_unassessed"
