"""Disposable local reproduction; no retained state, source edits, or network."""
import json
import tempfile
import time
from pathlib import Path

from test_event_enumerations import event
from test_event_gaps import response
from swingset.admission.page_evidence import Session
from swingset.schedule.event_evidence import request
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec

observations = []
with tempfile.TemporaryDirectory(prefix="swingset-request-domain-") as directory:
    fixture = event.__wrapped__(Path(directory))
    f = next(fixture)
    try:
        response(f)
        identity = request("eepro", "GET", f.parent.url + "one.htm")
        noise = WatchSpec("", "eepro", "round", "GET", f.parent.url + "unrelated.htm", "eepro.round")
        upsert_watch(f.conn, noise, f.corpus.clock.now())

        def check(label):
            began = time.perf_counter()
            with f.db.transaction(immediate=False):
                session = Session(f.conn, f.archive, cutoff=f.corpus.clock.now(), now=f.corpus.clock.now())
                result = session.verify_request(identity, classify_unavailability=True)
                observations.append(dict(
                    label=label, snapshot_count=f.conn.execute("SELECT count(*) FROM snapshots").fetchone()[0],
                    acquired=result["acquired"], interpreted=result["interpreted"], unavailable=result["unavailable"],
                    snapshots_exhausted=result["snapshots_exhausted"], reasons=result["reasons"],
                    loaded_rows=session.reader.rows, elapsed_seconds=round(time.perf_counter()-began,6),
                ))

        check("exact origin Gone404 alone")
        for i in range(63):
            f.corpus.snapshot("noise-" + str(i), b"unrelated valid body", spec=noise)
        check("same exact404 plus63 unrelated snapshots")
        f.corpus.snapshot("noise-63", b"unrelated valid body", spec=noise)
        check("same exact404 plus64 unrelated snapshots")
        alias = WatchSpec("", "eepro", "event", "GET", "https://EEPRO.COM:443/results/test/one.htm#alias", "eepro.round")
        upsert_watch(f.conn, alias, f.corpus.clock.now())
        f.corpus.snapshot("older-usable-alias", b"usable retained alias", spec=alias)
        f.conn.execute("UPDATE snapshots SET fetched_at='2026-01-01T00:00:01+00:00' WHERE snapshot_id='older-usable-alias'")
        check("usable normalized alias older than64 unrelated snapshots")
        f.conn.execute("DELETE FROM snapshots WHERE snapshot_id='noise-63'")
        f.conn.execute("DELETE FROM snapshots WHERE snapshot_id='noise-62'")
        check("same alias now inside complete63candidate source pass")
    finally:
        fixture.close()
print(json.dumps(observations, indent=2))
