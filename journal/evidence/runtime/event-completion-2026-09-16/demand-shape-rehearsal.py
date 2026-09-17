"""Offline scheduler model using retained watch-count shape, not retained admission."""
import hashlib
import json
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant
from swingset.schedule import event_enumerations, event_capacity
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database

ROOT=Path(__file__).resolve().parents[4]
INPUT=Path(__file__).with_name("watch-demand.json")
OUTPUT=Path(__file__).with_name("demand-shape-rehearsal.json")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not OUTPUT.exists()
    assert sha(INPUT)=="5504f1e2e813ff260b6d6eef2bdd695cb110791e60d078b2b7bbdef9d5ff1ff6"
    demand=json.loads(INPUT.read_bytes())
    cohort={r["source_ref"]:r["unattempted_result_watches"] for r in demand["source_events"] if r["unattempted_result_watches"]}
    assert len(cohort)==218 and sum(cohort.values())==3757
    source_files=sorted((ROOT/"src").rglob("*.py"))+sorted((ROOT/"src").rglob("*.sql"))
    source_hashes={str(p.relative_to(ROOT)):sha(p) for p in source_files}
    started=time.monotonic()
    clock=FakeClock()
    config=Config({"example.test":HostConfig(daily_request_budget=10000)}, {"scoringdance":SourceConfig(True)})
    completed=Counter(); first={}; previous={}; gaps=Counter(); finished={}; lanes=Counter(); ownership={}
    # Membership is explicitly a model input. No captured admission or source
    # interpretation is asserted, and no HTTP client is ever instantiated.
    def memberships(conn, keys):
        return {key:ownership[key] for key in keys if key in ownership}
    with tempfile.TemporaryDirectory(prefix="swingset-demand-shape-") as temporary:
        with open_database(Path(temporary)) as db, patch.object(event_enumerations,"memberships",memberships), patch.object(event_capacity,"memberships",memberships):
            conn=db.connection;run=db.start_run(clock.now());gate=Gate(conn,config,clock)
            def add(ref,page,listed):
                spec=WatchSpec("","scoringdance","round" if listed else "event","GET",f"https://example.test/{ref}/{page}","scoringdance.round" if listed else "scoringdance.event",source_ref=ref)
                upsert_watch(conn,spec,clock.now())
                ownership[spec.watch_id]=[{"source":"scoringdance","source_ref":ref,"enumeration_id":None}]
            for ref in cohort:add(ref,0,True)
            add("discovery-0",0,False)
            for number in range(10000):
                with db.transaction():prepare_event_turns(conn,config,now=clock.now(),run_id=run)
                choice=next_watch(conn,config,now=clock.now(),run_id=run)
                assert choice is not None and choice.turn is not None and choice.capacity is not None
                with servicing(choice,run_id=run):
                    grant,action=issue(db,gate,clock,host=choice.host,source="scoringdance",watch=SimpleNamespace(watch_id=choice.key,kind="round"),page_kind="scoringdance.round",crawl_delay=0,sweep=False)
                assert isinstance(grant,Grant)
                release(db,gate,clock,action,choice.host,Classification(Outcome.OK),body_bytes=0,request_day=clock.now().date().isoformat())
                ordinal=number+1;ref=choice.turn.source_ref;lanes[choice.capacity.lane]+=1
                with db.transaction():conn.execute("UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",(choice.key,))
                if ref in cohort:
                    first.setdefault(ref,ordinal);gaps[ref]=max(gaps[ref],ordinal-previous.get(ref,0));previous[ref]=ordinal
                    completed[ref]+=1
                    if completed[ref]==cohort[ref]:finished[ref]=ordinal
                    else:add(ref,completed[ref],True)
                else:
                    add(f"discovery-{ordinal}",0,False)
                clock.sleep(5)
                if number%500==0:print(json.dumps({"requests":ordinal,"cohort_pages":sum(completed.values()),"events_finished":len(finished)}),flush=True)
                if len(finished)==len(cohort):break
            assert dict(completed)==cohort and len(finished)==218
            requests=conn.execute("SELECT count(*) FROM scheduler_requests").fetchone()[0]
            assert requests==conn.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0]==conn.execute("SELECT count(*) FROM scheduler_capacity_requests").fetchone()[0]==conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0]
            report={"format":"event-demand-shape-rehearsal-v1","at":datetime.now(UTC).isoformat(),"input_sha256":sha(INPUT),"script_sha256":sha(Path(__file__)),"source_sha256":source_hashes,"cohort_events":len(cohort),"cohort_watch_rows":sum(cohort.values()),"event_turn_requests":config.scheduler.event_turn_requests,"listed_page_percent":config.scheduler.listed_page_percent,"issued_requests":requests,"lane_requests":dict(lanes),"latest_first_service_request":max(first.values()),"largest_request_gap_including_initial_wait":max(gaps.values()),"cohort_drained_at_request":max(finished.values()),"per_event":[{"source_ref":ref,"modeled_pages":cohort[ref],"first_service_request":first[ref],"finished_at_request":finished[ref],"largest_request_gap":gaps[ref]} for ref in sorted(cohort)],"network_requests":0,"production_mutated":False,"passed":True,"elapsed_seconds":time.monotonic()-started,"limits":["Synthetic membership and one successful request per modeled watch; no artifact, admission, interpretation, or release acceptance.","Retained watch counts can duplicate requests; this is demand shape, not distinct pages.","One synthetic host and new-work class, with continuous eligible discovery; no competing classes, outages, retries, or blocked intervals.","Fake clock advances five seconds per issued request; modeled times are not operating forecasts.","This does not calibrate the unfinished-event watermark or establish ordinary-host service acceptance."]}
    with OUTPUT.open("x") as stream:stream.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"output":str(OUTPUT),"sha256":sha(OUTPUT),"requests":requests,"cohort":len(finished),"seconds":report["elapsed_seconds"]}),flush=True)


if __name__=="__main__":main()
