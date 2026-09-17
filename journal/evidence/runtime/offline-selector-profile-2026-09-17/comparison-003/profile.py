"""Compare one reviewed selector derivative on held schema-28 scratch, read-only.

No parser/projector/linker worker is executed. The baseline keeps query_only=0,
as the ordinary selector does, but SQLite mode=ro and an authorizer forbid writes.
A Python alarm and SQLite progress interrupt bound the measured call separately.
"""

from __future__ import annotations

import argparse
import cProfile
import fcntl
import functools
import hashlib
import importlib.util
import json
import os
import pstats
import signal
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PARENT_SOURCE = "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
PARENT_SOURCE_SHA = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
SOURCE_SHA = "238460ea2cd80f41233e16c98bbb1563659235fe05fed8749ae647b3415d57a0"
MARKER = "extension-input-scratch.json"


class SelectionTimeout(BaseException):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_only(action: int, first: str | None, second: str | None, *_: Any) -> int:
    if action in {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_ANALYZE,
    }:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and (second is not None or first not in {"data_version", "query_only", "user_version", "table_info"}):
        # table_info has an argument but only reads metadata.
        if first not in {"table_info", "query_only"}:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def measure(conn: sqlite3.Connection, selector: Any, *, seconds: float, derivations: Any, readiness: Any) -> tuple[dict[str, Any], cProfile.Profile]:
    require(0 < seconds <= 30, "selection profile must be at most30 seconds")
    profile = cProfile.Profile()
    calls: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(lambda: dict(calls=0, false=0, seconds=0.0))
    readiness_modes: Counter[str] = Counter()
    originals = {name:getattr(derivations,name) for name in ("current", "desired", "ready")}
    original_dancers = readiness.dancers_current

    def instrument(name: str, fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapped(connection: Any, unit: Any, *args: Any, **kwargs: Any) -> Any:
            item = calls[(name,unit.stage,unit.unit_kind)]
            item["calls"] += 1
            start = time.monotonic()
            try:
                result = fn(connection,unit,*args,**kwargs)
                if result is False:
                    item["false"] += 1
                return result
            finally:
                item["seconds"] += time.monotonic()-start
        return wrapped

    def dancers(connection: Any, *args: Any, **kwargs: Any) -> Any:
        mode = f"transaction={connection.in_transaction};query_only={connection.execute('PRAGMA query_only').fetchone()[0]}"
        readiness_modes[mode] += 1
        return original_dancers(connection,*args,**kwargs)

    def timeout(_signum: int, _frame: Any) -> None:
        raise SelectionTimeout()

    require(signal.getitimer(signal.ITIMER_REAL) == (0.0,0.0), "existing process timer must not be replaced")
    previous = signal.signal(signal.SIGALRM,timeout)
    started = time.monotonic()
    deadline = started+seconds
    report: dict[str, Any] = dict(status="started",limit_seconds=seconds,baseline_query_only=conn.execute("PRAGMA query_only").fetchone()[0],instrumentation="cProfile and current/desired/ready counters; no SQL trace callback; not uninstrumented throughput")
    for name, fn in originals.items():
        setattr(derivations,name,instrument(name,fn))
    readiness.dancers_current = dancers
    conn.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
    try:
        signal.setitimer(signal.ITIMER_REAL,seconds)
        profile.enable()
        unit = selector()
        report.update(status="selected" if unit else "no_runnable_unit",selected=None if unit is None else dict(stage=unit.stage,kind=unit.unit_kind,unit_id=unit.unit_id))
    except SelectionTimeout:
        report["status"] = "python_alarm_timeout"
    except sqlite3.OperationalError as error:
        if "interrupted" not in str(error) or time.monotonic()<deadline:
            raise
        report["status"] = "sqlite_progress_timeout"
    finally:
        profile.disable()
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)
        conn.set_progress_handler(None,0)
        if conn.in_transaction:
            conn.rollback()
        for name,fn in originals.items():
            setattr(derivations,name,fn)
        readiness.dancers_current = original_dancers
        report["elapsed_seconds"] = time.monotonic()-started
        report["derivation_calls"] = [dict(function=name,stage=stage,kind=kind,**values) for (name,stage,kind),values in sorted(calls.items())]
        report["dancer_readiness_modes"] = dict(readiness_modes)
        stats: Any = pstats.Stats(profile)
        report["profile_top_cumulative"] = [dict(file=key[0],line=key[1],function=key[2],primitive_calls=value[0],calls=value[1],own_seconds=value[2],cumulative_seconds=value[3]) for key,value in sorted(stats.stats.items(),key=lambda item:item[1][3],reverse=True)[:40]]
    return report,profile


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("source","scratch","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--helper-sha256",required=True)
    parser.add_argument("--marker-sha256",required=True)
    parser.add_argument("--seconds",type=float,default=30)
    args=parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode=True
    require(sha(Path(__file__))==args.helper_sha256,"reviewed profiling helper differs")
    source=args.source.resolve(strict=True)
    scratch=args.scratch.resolve(strict=True)
    output=args.output.resolve()
    require(not scratch.is_relative_to(Path('/var/lib/swingset').resolve()) and 'checkpoints' not in scratch.parts,"profiling is restricted to disposable scratch")
    require(not output.exists() and not any(output==root or output.is_relative_to(root) for root in (scratch,source,Path('/var/lib/swingset').resolve())),"new output must be outside scratch/source/production")
    require(sha(scratch/MARKER)==args.marker_sha256,"reviewed scratch marker differs")
    marker=json.loads((scratch/MARKER).read_bytes())
    require(marker['format']=='extension-input-scratch-v1' and marker['scratch']==str(scratch) and marker['source']==PARENT_SOURCE and marker['source_receipt_sha256']==PARENT_SOURCE_SHA and marker['schema']==28,"scratch source/schema identity differs")
    require(not (scratch/'RESTORE_PENDING').exists() and sha(scratch/'operator-hold')==marker['retained_files']['operator-hold']['sha256'],"scratch hold/restore interlock differs")
    receipt=source/'extension-source.json'
    require(sha(receipt)==SOURCE_SHA,"reviewed selector derivative receipt differs")
    require(json.loads(receipt.read_bytes())["predecessor_receipt_sha256"] == PARENT_SOURCE_SHA, "selector derivative parent differs")
    verifier=source/'journal/tools/runtime/rehearse_extension_migration.py'
    expected=json.loads(receipt.read_bytes())['files'][verifier.relative_to(source).as_posix()]
    require(sha(verifier)==(expected['sha256'] if isinstance(expected,dict) else expected),"frozen verifier differs")
    spec=importlib.util.spec_from_file_location('selector_source_verifier',verifier)
    assert spec and spec.loader
    util=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(util)
    util.verify_source(source,receipt,SOURCE_SHA)
    sys.path[:0]=[str(source/'src'),str(source)]
    from swingset.schedule.fairness import next_offline
    from swingset.state import derivation_readiness, derivations
    from swingset.state.control_scopes import unit_allowed
    for name,module in tuple(sys.modules.items()):
        if name=='swingset' or name.startswith('swingset.'):
            location = getattr(module, "__file__", None)
            require(isinstance(location,str) and Path(location).resolve().is_relative_to(source/'src'),"runtime import escaped reviewed selector derivative")
    def no_network(event: str, values: Any) -> None:
        if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen'}:
            raise RuntimeError('selector profiling forbids network and subprocesses')
    sys.addaudithook(no_network)
    with (scratch/'state.lock').open('r+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        require(not (scratch/'RESTORE_PENDING').exists() and sha(scratch/'operator-hold')==marker['retained_files']['operator-hold']['sha256'],"scratch hold changed at writer lock")
        conn=sqlite3.connect((scratch/'state.sqlite').as_uri()+'?mode=ro',uri=True,isolation_level=None)
        try:
            conn.row_factory=sqlite3.Row
            conn.set_authorizer(read_only)
            require(conn.execute('PRAGMA user_version').fetchone()[0]==28 and conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='28',"exact schema28 required")
            before=conn.execute("PRAGMA data_version").fetchone()[0]
            require(conn.total_changes==0,"unexpected pre-profile writes")
            now=datetime.now(UTC)
            report,profile=measure(conn,lambda:next_offline(conn,now=now,allowed=lambda unit:unit_allowed(conn,unit,now=now)),seconds=args.seconds,derivations=derivations,readiness=derivation_readiness)
            require(conn.total_changes==0 and conn.execute("PRAGMA data_version").fetchone()[0]==before,"scratch database changed during profile")
        finally:
            conn.close()
    util.verify_source(source,receipt,SOURCE_SHA)
    require(sha(scratch/'operator-hold')==marker['retained_files']['operator-hold']['sha256'],"scratch hold changed")
    report.update(format='reviewed-selector-comparison-profile-v1',parent_source_receipt_sha256=PARENT_SOURCE_SHA,recorded_at=datetime.now(UTC).isoformat(),source=str(source),source_receipt_sha256=SOURCE_SHA,scratch=str(scratch),marker_sha256=args.marker_sha256,helper_sha256=args.helper_sha256,sqlite_read_only=True,database_total_changes=0,data_version_unchanged=True,worker_executions=0,network_requests=0,production_applied=False,scope='one read-only instrumented selection; source and interlock checks occur outside selection timeout')
    output.mkdir(parents=True)
    profile.dump_stats(str(output/'selector.prof'))
    with (output/'report.json').open('x') as stream:
        json.dump(report,stream,indent=2)
        stream.write('\n')
    print(json.dumps(dict(status=report['status'],elapsed_seconds=report['elapsed_seconds'],output=str(output)),indent=2))


if __name__=='__main__':
    main()
