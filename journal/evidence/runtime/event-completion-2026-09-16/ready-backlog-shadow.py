"""Offline finite policy comparison; synthetic admission and no HTTP client."""

import argparse
import hashlib
import json
import tempfile
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import swingset
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant
from swingset.schedule import event_capacity, event_enumerations, event_pressure
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database

FOLDER = Path(__file__).parent
INPUT = FOLDER / "watch-demand.json"
PIN = "5504f1e2e813ff260b6d6eef2bdd695cb110791e60d078b2b7bbdef9d5ff1ff6"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    package = Path(swingset.__file__).parent
    return {p.relative_to(package).as_posix(): sha(p) for p in sorted(package.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def scenario(cohort, turn, share, maximum):
    clock = FakeClock()
    default = Config({"example.test": HostConfig(daily_request_budget=10000)},
                     {"scoringdance": SourceConfig(True)})
    config = replace(default, scheduler=replace(default.scheduler, event_turn_requests=turn,
                     listed_page_percent=share, event_pressure_high=10000, event_pressure_low=8000))
    ownership, specs = {}, {}
    def memberships(conn, keys):
        return {key: ownership[key] for key in keys if key in ownership}
    completed, lanes = Counter(), Counter()
    first, previous, gaps, finished = {}, {}, {}, {}
    selection_seconds = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="swingset-ready-shadow-") as temporary:
        with open_database(Path(temporary)) as db, patch.object(event_enumerations, "memberships", memberships), patch.object(event_capacity, "memberships", memberships):
            conn = db.connection
            run = db.start_run(clock.now())
            gate = Gate(conn, config, clock)
            def add(ref, page, listed):
                kind = "round" if listed else "event"
                spec = WatchSpec("", "scoringdance", kind, "GET", f"https://example.test/{ref}/{page}",
                                 "scoringdance." + kind, source_ref=ref)
                upsert_watch(conn, spec, clock.now())
                ownership[spec.watch_id] = [{"source": "scoringdance", "source_ref": ref, "enumeration_id": None}]
                specs[spec.watch_id] = spec
            # All modeled pages are outstanding from the beginning. This is
            # intentionally different from the earlier one-watch-per-event model.
            for ref, pages in cohort.items():
                for page in range(pages):
                    add(ref, page, True)
            add("discovery-0", 0, False)
            for ordinal in range(1, maximum + 1):
                while conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
                    event_pressure.bootstrap(db, config, now=clock.now())
                before = time.monotonic()
                with db.transaction():
                    prepare_event_turns(conn, config, now=clock.now(), run_id=run)
                choice = next_watch(conn, config, now=clock.now(), run_id=run)
                selection_seconds.append(time.monotonic() - before)
                assert choice is not None and choice.turn is not None and choice.capacity is not None
                assert choice.capacity.competing, "Both lanes must remain eligible in this prefix"
                spec = specs[choice.key]
                with servicing(choice, run_id=run):
                    grant, action = issue(db, gate, clock, host=choice.host, source=spec.source,
                        watch=SimpleNamespace(watch_id=spec.watch_id, kind=spec.kind),
                        page_kind=spec.parser, crawl_delay=0, sweep=False)
                assert isinstance(grant, Grant), grant
                release(db, gate, clock, action, choice.host, Classification(Outcome.OK), body_bytes=0,
                        request_day=clock.now().date().isoformat())
                ref = choice.turn.source_ref
                lanes[choice.capacity.lane] += 1
                with db.transaction():
                    conn.execute("UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?", (choice.key,))
                if ref in cohort:
                    first.setdefault(ref, ordinal)
                    gaps[ref] = max(gaps.get(ref, 0), ordinal - previous.get(ref, 0))
                    previous[ref] = ordinal
                    completed[ref] += 1
                    if completed[ref] == cohort[ref]:
                        finished[ref] = ordinal
                else:
                    add(f"discovery-{ordinal}", 0, False)
                clock.sleep(5)
                if ordinal % 128 == 0:
                    print(json.dumps({"turn": turn, "share": share, "issued": ordinal,
                                      "cohort_pages": sum(completed.values()), "events_served": len(first)}), flush=True)
            counts = [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in
                      ("scheduler_requests", "scheduler_event_requests", "scheduler_capacity_requests")]
            counts.append(conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0])
            assert counts == [maximum] * 4
            assert all(completed[ref] <= pages for ref, pages in cohort.items())
            assert sum(lanes.values()) == maximum
            timings = sorted(selection_seconds)
            return {"event_turn_requests": turn, "listed_page_percent": share,
                "issued_requests": maximum, "lane_requests": dict(lanes),
                "cohort_pages_served": sum(completed.values()), "cohort_events_served": len(first),
                "cohort_events_finished": len(finished), "cohort_events_unserved": len(cohort) - len(first),
                "all_cohort_first_service_request": max(first.values()) if len(first) == len(cohort) else None,
                "largest_observed_gap_including_initial_wait": max(gaps.values(), default=0),
                "maximum_open_wait_requests": max(maximum - previous.get(ref, 0) for ref in cohort if ref not in finished),
                "selection_seconds": {"p50": timings[len(timings) // 2], "p95": timings[int(len(timings) * .95)], "max": timings[-1]},
                "elapsed_seconds": time.monotonic() - started,
                "per_event": [{"source_ref": ref, "modeled_pages": pages, "served": completed[ref],
                    "first_service_request": first.get(ref), "finished_at_request": finished.get(ref)} for ref, pages in sorted(cohort.items())]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=512)
    args = parser.parse_args()
    assert 1 <= args.max_requests <= 1024
    assert not args.output.exists() and sha(INPUT) == PIN
    demand = json.loads(INPUT.read_bytes())
    cohort = {r["source_ref"]: r["unattempted_result_watches"] for r in demand["source_events"] if r["unattempted_result_watches"]}
    assert len(cohort) == 218 and sum(cohort.values()) == 3757
    before = source_hashes()
    report = {"format": "ready-backlog-policy-shadow-v1", "at": datetime.now(UTC).isoformat(),
              "source_package": str(Path(swingset.__file__).parent), "source_sha256": before,
              "script_sha256": sha(Path(__file__)), "input_sha256": PIN,
              "cohort_events": len(cohort), "cohort_watch_rows": sum(cohort.values()), "scenarios": [],
              "network_requests": 0, "production_mutated": False, "passed": False,
              "limits": ["Synthetic membership, one successful request per retained watch-row size, all modeled pages initially ready; no admitted evidence, artifacts, interpretation or publication.",
                         "Watch rows can duplicate source requests; these are model sizes, not verified page counts.",
                         "Only a finite issued-request prefix with one host, one new-work class and continuous discovery; no retry, outage, byte cap, robot or redirect chains.",
                         "Large synthetic host/pressure limits keep those independent gates open; operating defaults are unchanged.",
                         "Request ordinals and five-second fake-clock steps are not an operating ETA; unserved events have no measured first-service time."]}
    try:
        for share in (50, 75):
            for turn in (1, 4, 8):
                report["scenarios"].append(scenario(cohort, turn, share, args.max_requests))
        report["source_unchanged"] = before == source_hashes()
        assert report["source_unchanged"]
        report["passed"] = True
    finally:
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    print(json.dumps({"output": str(args.output), "passed": report["passed"], "sha256": sha(args.output)}), flush=True)


if __name__ == "__main__":
    main()
