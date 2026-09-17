"""Eligible history offers retain event order when newer plans arrive."""

from collections import Counter
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant
from swingset.history import backfill
from swingset.history.platform import KnownEvent, Page, PlannedPage, PlatformPlan
from swingset.schedule.fairness import WatchChoice, next_watch, prepare_event_turns, servicing
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.control_scopes import for_watch


def test_newer_historical_offers_join_behind_admitted_waiting_events(event, monkeypatch):
    f = event
    clock = f.corpus.clock
    config = Config(
        {"web.archive.org": HostConfig(daily_request_budget=100)}, {"eepro": SourceConfig(True)}
    )
    config = replace(config, scheduler=replace(config.scheduler, event_turn_requests=2))
    run = f.db.start_run(clock.now())
    plan_rows, specifications = [], {}

    def add(name, count, year):
        parent = replace(
            f.parent,
            watch_id="",
            url=f"https://eepro.com/results/{name}/",
            source_ref=f"eepro:{name}",
        )
        upsert_watch(f.conn, parent, clock.now())
        names = [f"{number}.htm" for number in range(count)]
        admit_parent(f, names, parent=parent)
        finish_bootstrap(f)
        new_rows = []
        for filename in names:
            url = parent.url + filename
            specification = WatchSpec(
                "",
                "eepro",
                "round",
                "GET",
                url,
                "eepro.round",
                source_ref=parent.source_ref,
                archive_url=f"https://web.archive.org/web/20200101000000id_/{url}",
            )
            specifications[url] = specification
            new_rows.append(
                PlannedPage(
                    KnownEvent(
                        name, "eepro", parent.source_ref, year, f"{year}-01", date(year, 1, 1)
                    ),
                    Page("eepro", parent.source_ref, url, "eepro.round", "round", parent.url),
                    (),
                )
            )
        plan_rows[:0] = new_rows
        # This regression isolates already-eligible historical offers. Ordinary
        # watch polling and year/capture gate tests have their own fixtures.
        f.conn.execute("UPDATE watches SET next_check_at=NULL")
        f.conn.execute("DELETE FROM pending_work")

    def eligible(database, configuration, time, item, plan):
        return specifications[item.page.url], None, "eligible"

    monkeypatch.setattr(backfill, "_candidate", eligible)
    add("older", 2, 2018)
    add("large", 6, 2019)
    served, completed = [], set()
    for ordinal in range(10):
        if ordinal == 4:
            add("newest", 2, 2020)
        candidates = backfill.offers(f.db, config, clock, plan=PlatformPlan(tuple(plan_rows), ()))
        choices = [
            WatchChoice(
                spec.watch_id,
                None,
                "web.archive.org",
                "old",
                for_watch(
                    f.conn,
                    source=spec.source,
                    watch_id=spec.watch_id,
                    page_kind=spec.parser,
                    watch_kind=spec.kind,
                    host="web.archive.org",
                ),
                clock.now(),
                archive=True,
                history=True,
            )
            for spec in candidates
        ]
        with f.db.transaction() as conn:
            prepare_event_turns(
                conn, config, now=clock.now(), extra_choices=choices, exclude=completed, run_id=run
            )
        choice = next_watch(
            f.conn, config, now=clock.now(), extra_choices=choices, exclude=completed, run_id=run
        )
        assert choice is not None and choice.turn is not None
        assert choice.turn.enumeration_id is not None
        gate = Gate(f.conn, config, clock)
        with servicing(choice, run_id=run):
            grant, action = issue(
                f.db,
                gate,
                clock,
                host=choice.host,
                source="eepro",
                watch=SimpleNamespace(watch_id=choice.key, kind="round"),
                page_kind="eepro.round",
                crawl_delay=0,
                sweep=False,
            )
        assert isinstance(grant, Grant)
        release(
            f.db,
            gate,
            clock,
            action,
            choice.host,
            Classification(Outcome.OK),
            body_bytes=0,
            request_day=clock.now().date().isoformat(),
        )
        served.append(choice.turn.source_ref)
        completed.add(choice.key)
        clock.sleep(5)
    assert served[:6] == ["eepro:large"] * 2 + ["eepro:older"] * 2 + ["eepro:large"] * 2
    assert Counter(served) == {"eepro:large": 6, "eepro:older": 2, "eepro:newest": 2}
    assert f.conn.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == 10
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 10
