"""Fresh doctor evidence accounts for explicit gaps without claiming success."""

import gzip
import json

import pytest
from test_event_enumerations import admit_parent, child, finish_bootstrap
from test_event_enumerations import event as event
from test_event_inventory_parity import read

from swingset.admission.page_evidence import Limits
from swingset.schedule.event_report import report
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec


def gone(f, name="two.htm", *, alias=False, via="origin"):
    spec = WatchSpec(
        "",
        "eepro",
        "event" if alias else "round",
        "GET",
        ("https://EEPRO.COM:443/results/test/" + name + "#alias") if alias else f.parent.url + name,
        "eepro.round",
        source_ref="eepro:alias" if alias else "eepro:test",
    )
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    context = f.corpus.snapshot("gone-" + name, b"This page is gone", spec=spec, via=via)
    f.conn.execute(
        "UPDATE snapshots SET classification='Gone',http_status=404 WHERE snapshot_id=?",
        (context.snapshot_id,),
    )
    return context


def prepare(f):
    admit_parent(f, ["one.htm", "two.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)


def test_mixed_interpreted_and_unavailable_pages_are_accounted_without_success_inflation(event):
    f = event
    prepare(f)
    response = gone(f)
    before = tuple(f.conn.execute("SELECT * FROM event_stage_operations"))
    result = read(f)
    assert result["listed_pages"] == 2
    assert result["acquired_pages"] == result["interpreted_pages"] == 1
    assert result["unavailable_pages"] == 1
    assert result["unavailability_unknown_pages"] == 0
    assert result["known_pages_accounted_for"] is True
    assert result["pagination"] == "unknown" and result["published_pages"] is None
    assert result["unsupported_pages"] is None
    member = next(p for p in result["members"] if p["unavailable"])
    assert member["acquired"] is member["interpreted"] is False
    assert member["accounted_for"] is True
    assert member["next_action"] == "review_source_unavailability"
    assert member["unavailability_support"]["snapshot_id"] == response.snapshot_id
    assert tuple(f.conn.execute("SELECT * FROM event_stage_operations")) == before
    with f.db.transaction(immediate=False):
        detailed = report(
            f.conn, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
        )
    assert detailed["detail"]["unavailable_pages"] == 1
    assert detailed["detail"]["known_pages_accounted_for"] is True


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_unavailable_body_loss_makes_gap_unknown_and_restore_rechecks_it(event, damage):
    f = event
    prepare(f)
    gone(f)
    value = read(f)
    support = next(p["unavailability_support"] for p in value["members"] if p["unavailable"])
    path = f.archive.blob_path(support["body_sha256"])
    saved = path.read_bytes()
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(gzip.compress(b"different"))
    value = read(f)
    assert value["unavailable_pages"] is None
    assert value["unavailability_unknown_pages"] == 1
    assert value["known_pages_accounted_for"] is None
    path.write_bytes(saved)
    assert read(f)["known_pages_accounted_for"] is True


def test_same_request_negative_evidence_can_come_from_an_undeclared_alias_watch(event):
    f = event
    prepare(f)
    response = gone(f, alias=True)
    value = read(f)
    assert value["known_pages_accounted_for"] is True
    member = next(p for p in value["members"] if p["unavailable"])
    assert response.watch_id not in member["declared_watch_ids"]
    assert response.watch_id in member["evidence_watch_ids"]
    assert response.snapshot_id in member["snapshot_ids"]


def test_unavailable_gap_still_requires_usable_parent_support(event):
    f = event
    parent = admit_parent(f, ["two.htm"])
    finish_bootstrap(f)
    gone(f)
    assert read(f)["known_pages_accounted_for"] is True
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (parent,)
        ).fetchone()[0]
    )
    f.archive.blob_path(manifest[0]["body_sha256"]).unlink()
    value = read(f)
    assert value["unavailable_pages"] == 1
    assert value["known_pages_accounted_for"] is False


def test_archive_error_does_not_account_for_an_unacquired_origin_page(event):
    f = event
    prepare(f)
    gone(f, via="wayback")
    value = read(f)
    assert value["unavailable_pages"] == 0
    assert value["known_pages_accounted_for"] is False


def test_usable_older_interpretation_wins_over_latest_gone(event):
    f = event
    prepare(f)
    child(f, "two.htm")
    gone(f)
    value = read(f)
    assert value["unavailable_pages"] == 0
    assert value["interpreted_pages"] == 2
    assert value["known_pages_accounted_for"] is True


def test_unavailable_verification_shares_the_finite_inventory_budget(event):
    f = event
    prepare(f)
    gone(f)
    value = read(f, limits=Limits(decoded_bytes=4))
    assert value["unavailable_pages"] is None
    assert value["known_pages_accounted_for"] is None
    assert value["unavailability_unknown_pages"] == 2
