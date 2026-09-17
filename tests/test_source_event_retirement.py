"""Whole event withdrawal requires exhaustive independent declaration accounting."""

import json
import sqlite3
from dataclasses import replace

import pytest
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll
from test_event_retirement import edge, observe, retirement_view

from swingset.admission.event_retirement import verify_event
from swingset.admission.page_evidence import Limits, Session
from swingset.schedule.watches import upsert_watch


def withdrawn(f):
    admit_parent(f, ["old.htm"])
    finish_bootstrap(f)
    enroll(f, config())
    shifted = replace(f.parent, source_ref="eepro:other")
    f.conn.execute(
        "UPDATE watches SET source_ref=? WHERE watch_id=?", (shifted.source_ref, shifted.watch_id)
    )
    admit_parent(f, ["new.htm"], parent=shifted, authority=True)
    finish_bootstrap(f)


def check(f, *, limits=None):
    page = edge(f)
    with f.db.transaction(immediate=False):
        session = Session(
            f.conn,
            f.archive,
            cutoff=f.corpus.clock.now(),
            now=f.corpus.clock.now(),
            limits=limits or Limits(),
        )
        return verify_event(session, source="eepro", source_ref="eepro:test", page_edge=page)


def test_explicit_event_withdrawal_has_separate_immutable_receipt(event):
    from test_event_accounting import view

    f = event
    withdrawn(f)
    assert check(f)["retired"] is True
    enroll(f, config())
    for _ in range(5):
        observe(f)
    current = retirement_view(f)
    assert current["whole_event_retired"] is True
    accounting = view(f, source="eepro", source_ref="eepro:test")
    assert accounting["events"][0]["current_state"] == "explicitly_retired"
    assert accounting["state_counts"]["explicitly_retired"] == 1
    assert (
        f.conn.execute("SELECT count(*) FROM source_event_retirement_receipts").fetchone()[0] == 1
    )
    assert f.conn.execute("SELECT count(*) FROM event_retirement_receipts").fetchone()[0] == 1
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.conn.execute("DELETE FROM source_event_retirement_receipts")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.conn.execute("DELETE FROM source_event_retirement_proofs")


def test_exhausted_source_domain_never_proves_absence(event):
    withdrawn(event)
    value = check(event, limits=Limits(candidates=1))
    assert value["retired"] is None


def test_unprocessed_admission_and_lost_independent_evidence_remain_unknown(event):
    f = event
    withdrawn(f)
    unrelated = replace(f.parent, url=f.parent.url + "other/", source_ref="eepro:third")
    upsert_watch(f.conn, unrelated, f.corpus.clock.now())
    generation = admit_parent(f, ["third.htm"], parent=unrelated)
    assert check(f)["retired"] is None
    finish_bootstrap(f)
    assert check(f)["retired"] is True
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
    )
    f.archive.blob_path(manifest[0]["body_sha256"]).unlink()
    assert check(f)["retired"] is None


def test_new_independent_empty_event_declaration_invalidates_current_retirement(event):
    f = event
    withdrawn(f)
    enroll(f, config())
    for _ in range(5):
        observe(f)
    assert retirement_view(f)["whole_event_retired"] is True
    other = replace(f.parent, url=f.parent.url + "independent/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    admit_parent(
        f,
        ["other.htm"],
        parent=other,
        result_change=lambda result: replace(
            result,
            observations=tuple(
                replace(o, scope=replace(o.scope, ref="eepro:third")) for o in result.observations
            ),
            watches=tuple(replace(w, source_ref="eepro:third") for w in result.watches),
        ),
    )
    assert retirement_view(f)["whole_event_retired"] is not True
    finish_bootstrap(f)
    assert check(f)["retired"] is None  # Empty successor edge itself grants no new withdrawal.
    assert not f.conn.execute(
        "SELECT 1 FROM source_event_enumeration_members WHERE enumeration_id=(SELECT enumeration_id FROM source_event_inventory WHERE source_ref='eepro:test')"
    ).fetchone()


def empty_group(result):
    return replace(
        result,
        observations=tuple(
            replace(o, scope=replace(o.scope, ref="eepro:third")) for o in result.observations
        ),
        watches=tuple(replace(w, source_ref="eepro:third") for w in result.watches),
    )


def test_empty_declared_group_is_not_an_event_withdrawal(event):
    f = event
    admit_parent(f, ["old.htm"])
    finish_bootstrap(f)
    admit_parent(f, ["elsewhere.htm"], authority=True, result_change=empty_group)
    finish_bootstrap(f)
    assert edge(f)["all_predecessor_obligations_retired"] is True
    assert check(f)["retired"] is False
    assert check(f)["reason"] == "replacement_still_declares_event"


def test_independent_empty_group_survives_the_original_owners_withdrawal(event):
    f = event
    admit_parent(f, ["old.htm"])
    finish_bootstrap(f)
    other = replace(f.parent, url=f.parent.url + "independent/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    admit_parent(f, ["other.htm"], parent=other, result_change=empty_group)
    finish_bootstrap(f)
    shifted = replace(f.parent, source_ref="eepro:other")
    f.conn.execute(
        "UPDATE watches SET source_ref=? WHERE watch_id=?", (shifted.source_ref, shifted.watch_id)
    )
    admit_parent(f, ["new.htm"], parent=shifted, authority=True)
    finish_bootstrap(f)
    assert edge(f)["all_predecessor_obligations_retired"] is True
    assert check(f)["retired"] is False
    assert check(f)["reason"] == "independent_event_declaration_survives"


def test_watch_source_rewrite_cannot_hide_a_retained_independent_declaration(event):
    f = event
    admit_parent(f, ["old.htm"])
    finish_bootstrap(f)
    other = replace(f.parent, url=f.parent.url + "independent/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    admit_parent(f, ["other.htm"], parent=other, result_change=empty_group)
    finish_bootstrap(f)
    shifted = replace(f.parent, source_ref="eepro:other")
    f.conn.execute(
        "UPDATE watches SET source_ref=? WHERE watch_id=?", (shifted.source_ref, shifted.watch_id)
    )
    admit_parent(f, ["new.htm"], parent=shifted, authority=True)
    finish_bootstrap(f)
    f.conn.execute("UPDATE watches SET source='changed' WHERE watch_id=?", (other.watch_id,))
    assert check(f)["retired"] is None


def test_history_retains_enumerations_and_one_retirement_through_revalidation(event):
    from swingset.schedule.event_history import report

    f = event
    withdrawn(f)
    enroll(f, config())
    for _ in range(5):
        observe(f)
    history = report(
        f.conn, source="eepro", source_ref="eepro:test", stream="source_event_retirement"
    )
    assert len(history["rows"]) == 1 and history["reached_high_water"]
    enums = report(f.conn, source="eepro", source_ref="eepro:test", stream="enumerations", limit=1)
    assert len(enums["rows"]) == 1 and enums["next_cursor"]
    last = report(
        f.conn,
        source="eepro",
        source_ref="eepro:test",
        stream="enumerations",
        after=enums["next_cursor"],
        through=enums["through"],
    )
    assert len(last["rows"]) == 1 and last["reached_high_water"]
    assert last["rows"][0]["pagination"] == "unknown"
    unrelated = replace(f.parent, url=f.parent.url + "other/", source_ref="eepro:third")
    upsert_watch(f.conn, unrelated, f.corpus.clock.now())
    admit_parent(f, ["third.htm"], parent=unrelated)
    finish_bootstrap(f)
    enroll(f, config())
    f.corpus.clock.sleep(1000)
    for _ in range(5):
        observe(f)
    assert retirement_view(f)["whole_event_retired"] is True
    assert (
        len(
            report(
                f.conn, source="eepro", source_ref="eepro:test", stream="source_event_retirement"
            )["rows"]
        )
        == 1
    )
    assert f.conn.execute("SELECT count(*) FROM source_event_retirement_proofs").fetchone()[0] == 2
    assert f.conn.execute("SELECT count(*) FROM event_retirement_receipts").fetchone()[0] == 1


def test_admission_journal_insert_invalidates_a_source_domain_absence_proof(event):
    from test_event_enumerations import directory

    f = event
    other = replace(f.parent, url=f.parent.url + "independent/")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    body = directory(["independent.htm"])
    context = f.corpus.snapshot("unadmitted-parent", body, spec=other)
    generation, _ = f.corpus.stage(context, body=body, result_change=empty_group)
    withdrawn(f)
    for _ in range(5):
        observe(f)
    assert retirement_view(f)["whole_event_retired"] is True
    # This journal-only insert bypasses normal state-update invalidation. The
    # absence proof still cannot overlook an independently accepted declaration.
    f.conn.execute(
        "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) "
        "VALUES(?,'accepted','adversarial journal insert',?,'test')",
        (generation, f.corpus.clock.now().isoformat()),
    )
    assert retirement_view(f)["whole_event_retired"] is None
