"""Local source-event inventory uses admitted evidence without executing work."""

import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_admission import BODY, Corpus

from swingset.fetch.archive import Archive
from swingset.schedule.event_enumerations import bootstrap, memberships
from swingset.schedule.event_evidence import request, request_id
from swingset.schedule.event_inventory import inventory
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database


def directory(names):
    return (
        "<table>"
        + "".join(
            f'<tr><td><a href="{name}">{name}</a></td><td>2026-01-01</td><td>1K</td><td></td></tr>'
            for name in names
        )
        + "</table>"
    ).encode()


@pytest.fixture
def event(tmp_path):
    with open_database(tmp_path) as db:
        corpus = Corpus(db)
        parent = WatchSpec(
            "",
            "eepro",
            "index",
            "GET",
            "https://eepro.com/results/test/",
            "eepro.autoindex",
            source_ref="eepro:test",
        )
        upsert_watch(db.connection, parent, corpus.clock.now())
        yield SimpleNamespace(
            db=db, conn=db.connection, corpus=corpus, parent=parent, archive=Archive(tmp_path)
        )


def admit_parent(f, names, *, parent=None, authority=False, result_change=lambda result: result):
    parent = parent or f.parent
    body = directory(names)
    ctx = f.corpus.snapshot(
        "parent-" + str(f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0]),
        body,
        spec=parent,
    )
    generation, report = f.corpus.stage(
        ctx,
        body=body,
        result_change=result_change,
        report_change=lambda report: (
            replace(report, proposed_removal="watch") if authority else report
        ),
    )
    assert not report.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    return generation


def view(f, ref="eepro:test"):
    with f.db.transaction(immediate=False):
        return inventory(
            f.conn, f.archive, source="eepro", source_ref=ref, now=f.corpus.clock.now()
        )


def finish_bootstrap(f, limit=100):
    results = []
    while True:
        batch = bootstrap(f.db, now=f.corpus.clock.now(), limit=limit)
        results.append(batch)
        assert not batch["errors"]
        if not batch["may_have_more"]:
            return results


def child(f, name):
    spec = WatchSpec(
        "", "eepro", "round", "GET", f.parent.url + name, "eepro.round", source_ref="eepro:test"
    )
    context = f.corpus.snapshot("child-" + name, BODY, spec=spec)
    generation, report = f.corpus.stage(context)
    assert not report.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    return context, generation


def test_bootstrap_is_durable_alias_independent_and_does_not_schedule_requests(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    before = tuple(f.conn.execute("SELECT * FROM watches ORDER BY watch_id"))
    budgets = tuple(f.conn.execute("SELECT * FROM host_budget"))
    finish_bootstrap(f, limit=1)
    result = view(f)
    assert result["listed_pages"] == 2 and result["acquired_pages"] == 0
    assert result["interpreted_pages"] == 0 and result["pagination"] == "unknown"
    assert result["canonical_event_id"] is None and not result["known_pages_accounted_for"]
    assert all(page["first_known_at"] == result["first_known_at"] for page in result["members"])
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','new-alias','alias',1)"
    )
    f.conn.execute(
        "UPDATE watches SET state='archived',paused_until='2099-01-01T00:00:00+00:00' WHERE watch_id=?",
        (f.parent.watch_id,),
    )
    f.conn.execute("DELETE FROM pending_work")
    again = finish_bootstrap(f)
    assert sum(row["enumerations_created"] for row in again) == 0
    changed = view(f)
    assert changed["enumeration_id"] == result["enumeration_id"]
    assert changed["first_known_at"] == result["first_known_at"]
    assert changed["listed_pages"] == 2 and changed["canonical_event_id"] == "new-alias"
    assert tuple(f.conn.execute("SELECT * FROM host_budget")) == budgets
    assert not f.conn.execute("SELECT 1 FROM pending_work").fetchone()
    assert len(tuple(f.conn.execute("SELECT * FROM watches"))) == len(before)
    with sqlite3.connect(f.db.state_dir / "state.sqlite") as reader:
        reader.row_factory = sqlite3.Row
        reader.execute("PRAGMA query_only=1")
        reader.execute("BEGIN")
        assert (
            inventory(
                reader, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
            )["enumeration_id"]
            == result["enumeration_id"]
        )


def test_additions_and_unauthorized_omissions_preserve_old_denominator_and_wait_age(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    first = view(f)
    ages = {page["request_id"]: page["first_known_at"] for page in first["members"]}
    admit_parent(f, ["one.htm", "three.htm"])
    finish_bootstrap(f)
    latest = view(f)
    assert latest["listed_pages"] == 3 and latest["predecessor_id"] == first["enumeration_id"]
    assert len(latest["added_request_ids"]) == 1 and latest["removed_request_ids"] == []
    assert all(
        page["first_known_at"] == ages[page["request_id"]]
        for page in latest["members"]
        if page["request_id"] in ages
    )
    assert (
        f.conn.execute(
            "SELECT count(*) FROM source_event_enumeration_members WHERE enumeration_id=?",
            (first["enumeration_id"],),
        ).fetchone()[0]
        == 2
    )


def test_authority_retires_only_its_own_contribution_and_preserves_shared_requests(event):
    f = event
    shared = "https://eepro.com/shared.htm"
    admit_parent(f, [shared, "one.htm"])
    second = replace(f.parent, url="https://eepro.com/results/other-parent/")
    upsert_watch(f.conn, second, f.corpus.clock.now())
    admit_parent(f, [shared], parent=second)
    finish_bootstrap(f)
    first = view(f)
    admit_parent(f, ["new.htm"], authority=True)
    finish_bootstrap(f)
    latest = view(f)
    assert latest["listed_pages"] == 2
    assert {row["request"]["url"] for row in latest["members"]} == {
        shared,
        f.parent.url + "new.htm",
    }
    assert (
        len(latest["removed_request_ids"]) == 1
        and latest["predecessor_id"] == first["enumeration_id"]
    )


def test_revoking_latest_parent_reopens_support_without_erasing_obligations(event):
    f = event
    first = admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    latest = admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    prior = view(f)
    f.conn.execute("UPDATE source_generations SET state='revoked' WHERE generation_id=?", (latest,))
    current = view(f)
    assert current["enumeration_id"] == prior["enumeration_id"] and current["listed_pages"] == 2
    assert "source_generation_revoked" in current["blockers"]
    assert not current["known_pages_accounted_for"]
    assert first not in {parent["generation_id"] for parent in current["parent_support"]}


@pytest.mark.parametrize("artifact", ["body", "extract"])
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_actual_interpretation_reopens_when_artifact_disappears(event, artifact, damage):
    f = event
    admit_parent(f, ["one.htm"])
    context, generation = child(f, "one.htm")
    finish_bootstrap(f)
    ready = view(f)
    assert ready["acquired_pages"] == ready["interpreted_pages"] == 1
    assert ready["known_pages_accounted_for"] and ready["pagination"] == "unknown"
    manifest = json.loads(
        f.conn.execute(
            "SELECT manifest_json FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
    )[0]
    path = (
        f.archive.blob_path(manifest["body_sha256"])
        if artifact == "body"
        else f.archive.extract_path(manifest["extract_sha256"])
    )
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt artifact")
    reopened = view(f)
    assert reopened["interpreted_pages"] == 0 and not reopened["known_pages_accounted_for"]
    assert reopened["acquired_pages"] == (0 if artifact == "body" else 1)
    assert reopened["enumeration_id"] == ready["enumeration_id"]
    assert reopened["members"][0]["first_known_at"] == ready["members"][0]["first_known_at"]


def test_unsupported_files_remain_visible_and_do_not_become_result_pages(event):
    f = event
    admit_parent(f, ["one.htm", "score.pdf"])
    finish_bootstrap(f)
    result = view(f)
    assert result["listed_pages"] == 2 and result["interpreted_pages"] == 0
    unsupported = next(
        page for page in result["members"] if page["request"]["url"].endswith(".pdf")
    )
    assert unsupported["watch_ids"] == [] and "unsupported_page_kind" in unsupported["blockers"]


def test_distinct_request_deduplicates_kinds_and_preserves_form_semantics(event):
    f = event

    def kinds(result):
        watches = result.watches
        return replace(result, watches=(watches[0], replace(watches[1], kind="event")))

    admit_parent(f, ["one.htm", "one.htm"], result_change=kinds)
    finish_bootstrap(f)
    result = view(f)
    assert result["listed_pages"] == 1 and len(result["members"][0]["watch_ids"]) == 2
    members = memberships(f.conn, result["members"][0]["watch_ids"])
    assert len(members) == 2 and all(len(rows) == 1 for rows in members.values())
    base = request("eepro", "post", "https://EEPRO.com:443/form#fragment", [("b", "2"), ("a", "1")])
    assert request_id(base) == request_id(
        request("eepro", "POST", "https://eepro.com/form", {"a": "1", "b": "2"})
    )
    assert request_id(base) != request_id(
        request("eepro", "POST", "https://eepro.com/form", {"a": "other", "b": "2"})
    )
    assert request_id(request("eepro", "GET", "https://eepro.com/a%2Fb")) != request_id(
        request("eepro", "GET", "https://eepro.com/a/b")
    )


def test_shared_watch_supports_two_source_events_without_canonical_map(event):
    f = event
    shared = "https://eepro.com/shared.htm"
    admit_parent(f, [shared])
    other = replace(f.parent, url="https://eepro.com/results/second/", source_ref="eepro:second")
    upsert_watch(f.conn, other, f.corpus.clock.now())
    admit_parent(f, [shared], parent=other)
    finish_bootstrap(f)
    one, two = view(f), view(f, "eepro:second")
    assert one["listed_pages"] == two["listed_pages"] == 1
    watch = one["members"][0]["watch_ids"][0]
    assert two["members"][0]["watch_ids"] == [watch]
    assert {row["source_ref"] for row in memberships(f.conn, [watch])[watch]} == {
        "eepro:test",
        "eepro:second",
    }


def test_unadmitted_legacy_does_not_gain_completion_and_old_schema_is_explicit(event):
    f = event
    f.corpus.snapshot("legacy", BODY)
    finish_bootstrap(f)
    result = view(f)
    assert result["enumeration_id"] is None and result["listed_pages"] is None
    assert result["interpreted_pages"] is None and result["pagination"] == "unknown"
    assert not result["known_pages_accounted_for"] and result["first_known_at"] is None
    with sqlite3.connect(":memory:") as old:
        assert (
            inventory(
                old, f.archive, source="eepro", source_ref="eepro:test", now=f.corpus.clock.now()
            )["supported"]
            is False
        )


def test_authoritative_parent_can_remove_a_whole_disappeared_event_group(event):
    f = event

    def group(ref):
        def relabel(result):
            return replace(
                result,
                observations=tuple(
                    replace(o, scope=replace(o.scope, ref=ref)) for o in result.observations
                ),
                watches=tuple(replace(w, source_ref=ref) for w in result.watches),
            )

        return relabel

    # A reviewed synthetic parent enumerates a different source event on each revision.
    # The parent URL/source unit stays the same; its own ctx ref is absent.
    parent = replace(f.parent, source_ref=None)
    f.conn.execute("UPDATE watches SET source_ref=NULL WHERE watch_id=?", (parent.watch_id,))
    admit_parent(
        f, ["one.htm"], parent=parent, authority=True, result_change=group("eepro:old-group")
    )
    finish_bootstrap(f)
    old = view(f, "eepro:old-group")
    assert old["listed_pages"] == 1
    admit_parent(
        f, ["two.htm"], parent=parent, authority=True, result_change=group("eepro:new-group")
    )
    finish_bootstrap(f)
    retired = view(f, "eepro:old-group")
    assert retired["listed_pages"] == 0 and len(retired["removed_request_ids"]) == 1
    assert retired["predecessor_id"] == old["enumeration_id"]
    assert view(f, "eepro:new-group")["listed_pages"] == 1


def test_redirected_and_archive_evidence_keep_original_source_request_identity(event):
    f = event
    admit_parent(f, ["one.htm"])
    context, _ = child(f, "one.htm")
    # FetchClient stores original watch.url in snapshots.url; final archive and
    # requested capture URLs live in separate columns. Redirect headers are metadata.
    f.conn.execute(
        "UPDATE snapshots SET via='wayback',archive_url='https://web.archive.org/web/20240101000000id_/https://eepro.com/final.htm',requested_archive_url='https://web.archive.org/web/20231201000000id_/https://eepro.com/one.htm',headers_json=? WHERE snapshot_id=?",
        (json.dumps({"location": "https://eepro.com/final.htm"}), context.snapshot_id),
    )
    finish_bootstrap(f)
    actual = view(f)
    assert actual["acquired_pages"] == actual["interpreted_pages"] == 1
    assert actual["members"][0]["request"]["url"] == f.parent.url + "one.htm"


def test_direct_result_document_needs_no_child_request(event):
    f = event
    # The admitted round itself supplies all local interpretation evidence.
    spec = f.corpus.spec
    context = f.corpus.snapshot("direct-results", BODY, spec=spec)
    generation, report = f.corpus.stage(context)
    assert not report.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    watches = f.conn.execute("SELECT count(*) FROM watches").fetchone()[0]
    finish_bootstrap(f)
    result = view(f)
    assert result["listed_pages"] == result["acquired_pages"] == result["interpreted_pages"] == 1
    assert result["known_pages_accounted_for"]
    assert f.conn.execute("SELECT count(*) FROM watches").fetchone()[0] == watches
    assert result["members"][0]["watch_ids"] == [spec.watch_id]


def test_bootstrap_crash_rolls_back_members_and_cursor_together(event, monkeypatch):
    from swingset.schedule import event_enumerations

    f = event
    admit_parent(f, ["one.htm"])
    original = event_enumerations._record

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after retained members")

    monkeypatch.setattr(event_enumerations, "_record", interrupted)
    with pytest.raises(RuntimeError, match="crash after"):
        bootstrap(f.db, now=f.corpus.clock.now())
    assert not f.conn.execute("SELECT 1 FROM source_event_enumerations").fetchone()
    assert not f.conn.execute("SELECT 1 FROM source_event_enumeration_members").fetchone()
    assert not f.conn.execute("SELECT 1 FROM event_enumeration_inputs").fetchone()
    assert not f.conn.execute(
        "SELECT 1 FROM cursors WHERE name=?", (event_enumerations.CURSOR,)
    ).fetchone()
    monkeypatch.setattr(event_enumerations, "_record", original)
    finish_bootstrap(f)
    assert view(f)["listed_pages"] == 1


def test_non_event_admissions_advance_without_decoding_registry_or_calendar(event, monkeypatch):
    from pathlib import Path

    from swingset.schedule import event_enumerations
    from swingset.sources.wsdc_calendar.adapter import SOURCE as calendar
    from swingset.sources.wsdc_registry.adapter import SOURCE as registry

    f = event
    examples = [
        (registry.watch(1), Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body")),
        (
            replace(calendar.seed_watches(None, None)[0], source_ref="calendar-index"),
            Path("src/swingset/sources/wsdc_calendar/fixtures/calendar-2026-09-09.html"),
        ),
    ]
    identifiers = []
    for spec, path in examples:
        upsert_watch(f.conn, spec, f.corpus.clock.now())
        body = path.read_bytes()
        ctx = f.corpus.snapshot(spec.source, body, spec=spec)
        # Calendar has no production admission contract yet. A synthetic reviewed
        # test contract exercises the future accepted-input exclusion, without
        # changing that production policy or claiming this fixture is assessed.
        identifier, report = f.corpus.stage(
            ctx,
            body=body,
            report_change=lambda r, source=spec.source: (
                replace(r, contract_version="offline-calendar-exclusion", guards=())
                if source == "wsdc_calendar"
                else r
            ),
        )
        assert not report.failures
        f.corpus.review(identifier)
        assert f.corpus.admit(identifier) == "accepted"
        identifiers.append(identifier)

    def forbidden_decode(*args):
        raise AssertionError("non-event bootstrap decoded source payload")

    monkeypatch.setattr(event_enumerations, "generation", forbidden_decode)
    results = finish_bootstrap(f, limit=1)
    assert sum(row["admissions_scanned"] for row in results) == 2
    assert not f.conn.execute("SELECT 1 FROM source_event_enumerations").fetchone()
    assert not f.conn.execute(
        "SELECT 1 FROM source_event_inventory WHERE source IN ('wsdc_registry','wsdc_calendar')"
    ).fetchone()
    assert {
        tuple(row)
        for row in f.conn.execute("SELECT generation_id,outcome FROM event_enumeration_inputs")
    } == {(i, "ignored_non_event_parent") for i in identifiers}


@pytest.mark.parametrize("relation", ["unit_and_fingerprint", "snapshot_and_parser"])
def test_related_revocation_reopens_accepted_copy_without_erasing_members(event, relation):
    from swingset.fetch.archive import canonical, digest

    f = event
    original = admit_parent(f, ["one.htm"])
    child(f, "one.htm")
    finish_bootstrap(f)
    before = view(f)
    assert before["known_pages_accounted_for"]
    # A retained second generation can name the same source evidence under a
    # different work token/unit. Revocation applies to the evidence, not its alias.
    copied = dict(
        f.conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (original,)
        ).fetchone()
    )
    if relation == "snapshot_and_parser":
        copied["unit_key"] += ":independent-retained-unit"
        f.conn.execute(
            "INSERT INTO source_units(unit_key,watch_id,page_kind) SELECT ?,watch_id,page_kind FROM source_units WHERE unit_key=?",
            (
                copied["unit_key"],
                f.conn.execute(
                    "SELECT unit_key FROM source_generations WHERE generation_id=?", (original,)
                ).fetchone()[0],
            ),
        )
    copied["work_token"] = "separate-retained-attempt"
    copied["state"] = "revoked"
    copied["generation_id"] = "gen_" + digest(
        canonical(
            {
                "unit": copied["unit_key"],
                "fingerprint": copied["input_fingerprint"],
                "report": json.loads(copied["report_json"]),
                "result": copied["result_json"],
                "previous": copied["previous_generation_id"],
                "work_token": copied["work_token"],
            }
        )
    )
    f.conn.execute(
        "INSERT INTO source_generations ("
        + ",".join(copied)
        + ") VALUES ("
        + ",".join("?" for _ in copied)
        + ")",
        tuple(copied.values()),
    )
    after = view(f)
    assert after["enumeration_id"] == before["enumeration_id"] and after["listed_pages"] == 1
    assert not after["known_pages_accounted_for"] and "source_evidence_revoked" in after["blockers"]
    assert (
        f.conn.execute(
            "SELECT state FROM source_generations WHERE generation_id=?", (original,)
        ).fetchone()[0]
        == "accepted"
    )


def test_tampered_generation_result_cannot_invent_listed_pages(event):
    f = event
    identifier = admit_parent(f, ["one.htm"])
    raw = f.conn.execute(
        "SELECT result_json FROM source_generations WHERE generation_id=?", (identifier,)
    ).fetchone()[0]
    # Model damaged imported SQLite bytes, bypassing its normal immutable trigger.
    f.conn.execute("DROP TRIGGER source_generation_immutable")
    f.conn.execute(
        "UPDATE source_generations SET result_json=? WHERE generation_id=?",
        (raw.replace("one.htm", "invented.htm"), identifier),
    )
    result = bootstrap(f.db, now=f.corpus.clock.now())
    assert result["enumerations_created"] == 0
    assert result["errors"] == [
        {"generation_id": identifier, "reason": "source_generation_content_changed"}
    ]
    assert not f.conn.execute("SELECT 1 FROM source_event_enumeration_members").fetchone()


@pytest.mark.parametrize("latest_failure", [True, False])
def test_latest_response_is_global_across_shared_request_watch_kinds(event, latest_failure):
    f = event

    def kinds(result):
        return replace(
            result, watches=(result.watches[0], replace(result.watches[1], kind="event"))
        )

    admit_parent(f, ["one.htm", "one.htm"], result_change=kinds)
    finish_bootstrap(f)
    member = view(f)["members"][0]
    specs = []
    for watch_id in member["watch_ids"]:
        row = f.conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
        specs.append(
            WatchSpec(
                "",
                row["source"],
                row["kind"],
                row["method"],
                row["url"],
                row["parser"],
                source_ref=row["source_ref"],
            )
        )
    old = f.corpus.snapshot("old-kind", BODY, spec=specs[0])
    new = f.corpus.snapshot("new-kind", BODY, spec=specs[1])
    failed = new if latest_failure else old
    f.conn.execute(
        "UPDATE snapshots SET classification='ServerError',http_status=503 WHERE snapshot_id=?",
        (failed.snapshot_id,),
    )
    blockers = view(f)["members"][0]["blockers"]
    assert ("latest_response_ServerError" in blockers) is latest_failure


def test_direct_result_uses_retained_request_when_watch_metadata_changes(event):
    f = event
    spec = f.corpus.spec
    ctx = f.corpus.snapshot("direct-original", BODY, spec=spec)
    identifier, _ = f.corpus.stage(ctx)
    f.corpus.review(identifier)
    assert f.corpus.admit(identifier) == "accepted"
    f.conn.execute(
        "UPDATE watches SET url='https://eepro.com/changed-metadata.htm' WHERE watch_id=?",
        (spec.watch_id,),
    )
    finish_bootstrap(f)
    actual = view(f)
    assert actual["members"][0]["request"]["url"] == spec.url
    assert actual["acquired_pages"] == actual["interpreted_pages"] == 1


def test_rejected_new_parent_does_not_replace_latest_admissible_obligations(event):
    f = event
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    prior = view(f)
    body = directory(["different.htm"])
    ctx = f.corpus.snapshot("unsupported-replacement", body, spec=f.parent)
    from swingset.admission.report import Guard

    identifier, report = f.corpus.stage(
        ctx,
        body=body,
        report_change=lambda r: replace(
            r,
            guards=(*r.guards, Guard("unknown-critical-column", False, "Synthetic changed input")),
        ),
    )
    assert report.failures
    assert f.corpus.admit(identifier) != "accepted"
    finish_bootstrap(f)
    actual = view(f)
    assert actual["enumeration_id"] == prior["enumeration_id"]
    assert actual["listed_pages"] == 2


def test_real_index_parser_autoindex_declaration_becomes_event_obligation(event):
    from swingset.sources.eepro.adapter import IndexPage

    f = event
    # Synthetic listing; the real parser emits the production autoindex watch kind.
    body = b'<a href="/results.php?event=example2026"><div class="event-title">Example 2026</div><div class="event-date">2026-01-01</div></a>'
    parent = WatchSpec(
        "", "eepro", "index", "GET", "https://eepro.com/", IndexPage.kind, source_ref="root-index"
    )
    upsert_watch(f.conn, parent, f.corpus.clock.now())
    context = f.corpus.snapshot("synthetic-root-listing", body, spec=parent)
    parsed = IndexPage().parse(IndexPage().extract(body), context)
    assert len(parsed.watches) == 1 and parsed.watches[0].kind == "autoindex"
    identifier, accounting = f.corpus.stage(context, body=body)
    assert not accounting.failures
    f.corpus.review(identifier)
    assert f.corpus.admit(identifier) == "accepted"
    finish_bootstrap(f)
    actual = view(f, "eepro:example2026")
    assert actual["listed_pages"] == 1 and actual["acquired_pages"] == 0
    assert actual["enumeration_id"] is not None and actual["pagination"] == "unknown"
    child_watch = parsed.watches[0]
    assert actual["members"][0]["watch_ids"] == [child_watch.watch_id]
    assert actual["members"][0]["request"]["url"] == child_watch.url
    assert (
        memberships(f.conn, [child_watch.watch_id])[child_watch.watch_id][0]["source_ref"]
        == "eepro:example2026"
    )
    assert view(f, "root-index")["reason"] == "source_event_not_inventoried"


def test_actual_awards_and_autoindex_legacy_purposes_remain_unassessed(event):
    from swingset.sources.eepro.adapter import IndexPage
    from swingset.sources.wdr.adapter import watches_from_overrides
    from swingset.sources.wsdc_registry.adapter import SOURCE as registry

    f = event
    index_body = b'<a href="?event=legacy2026">Legacy event</a>'
    ctx = f.corpus.snapshot("unadmitted-index", index_body, spec=f.parent)
    autoindex = IndexPage().parse(IndexPage().extract(index_body), ctx).watches[0]
    awards = next(
        w
        for w in watches_from_overrides(
            [
                {
                    "source": "wdr",
                    "url": "https://scores.worlddanceregistry.com/00000000-0000-0000-0000-000000000001/",
                }
            ]
        )
        if w.parser == "wdr.awards"
    )
    assert autoindex.kind == "autoindex" and awards.kind == "json"
    unsupported = WatchSpec(
        "",
        "eepro",
        "event",
        "GET",
        "https://eepro.com/unknown",
        "eepro.future_page",
        source_ref="eepro:unsupported",
    )
    mislabeled_registry = replace(registry.watch(1), kind="event")
    for spec in (autoindex, awards, unsupported, mislabeled_registry):
        upsert_watch(f.conn, spec, f.corpus.clock.now())
    finish_bootstrap(f, limit=1)
    for spec in (autoindex, awards, unsupported):
        with f.db.transaction(immediate=False):
            actual = inventory(
                f.conn,
                f.archive,
                source=spec.source,
                source_ref=spec.source_ref,
                now=f.corpus.clock.now(),
            )
        assert actual["enumeration_id"] is None and actual["listed_pages"] is None
        assert actual["blockers"] == ["legacy_unassessed"]
        assert not actual["known_pages_accounted_for"]
    assert not f.conn.execute(
        "SELECT 1 FROM source_event_inventory WHERE source='wsdc_registry'"
    ).fetchone()
    assert not f.conn.execute("SELECT 1 FROM source_event_enumerations").fetchone()


@pytest.mark.parametrize(
    "parser, expected",
    [
        ("eepro.autoindex", "event_index"),
        ("wdr.awards", "result"),
        ("wdr.rounds", "result"),
        ("eepro.index", "discovery"),
        ("wsdc_registry.dancer", "discovery"),
        ("eepro.unknown", None),
    ],
)
def test_request_purpose_uses_actual_parser_semantics(parser, expected):
    from swingset.schedule.event_request_kind import purpose

    assert purpose(parser) == expected
