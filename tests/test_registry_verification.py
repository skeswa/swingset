"""H1/H2 acceptance through evidence and scheduler boundaries, entirely offline."""

import gzip
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.registry import advance_sweep, discover_registry
from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE, DancerPage
from swingset.state.db import open_database
from swingset.state.verification import (
    recover_registry_verifications,
    usable_verification,
    verification_summary,
)
from swingset.state.work import WorkUnit

FIXTURES = Path("src/swingset/sources/wsdc_registry/fixtures")
CONFIG = Config({"points.worldsdc.com": HostConfig()}, {"wsdc_registry": SourceConfig(True)})


class Lookup:
    def __init__(self, db, clock, *, found=False):
        self.db, self.clock = db, clock
        self.run = db.start_run(clock.now())
        self.spec = SOURCE.watch(1 if found else 1000000)
        upsert_watch(db.connection, self.spec, clock.now())
        self.body = (FIXTURES / f"lookup-{1 if found else 1000000}.body").read_bytes()
        self.status = 200 if found else 404
        self.archive = Archive(db.state_dir)
        self.client = FetchClient(
            db.connection, CONFIG, clock, self.archive, transport=httpx.MockTransport(self.respond)
        )

    def respond(self, request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            self.status,
            content=self.body if self.status != 304 else b"",
            headers={"etag": '"lookup"'},
        )

    def fetch(self, *, parse=True):
        result = self.client.fetch(self.spec.watch_id, DancerPage(), self.run)
        if parse and result.snapshot_id:
            parse_snapshot(
                self.db,
                self.archive,
                WorkUnit("parse", "snapshot", result.snapshot_id),
                self.clock,
                self.run,
            )
        return result


@pytest.mark.parametrize("found", [False, True])
@pytest.mark.parametrize("conditional", [False, True])
def test_successful_identical_check_renews_freshness_without_moving_provenance(
    tmp_path, found, conditional
):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        lookup = Lookup(db, clock, found=found)
        first = lookup.fetch()
        original = tuple(db.connection.execute("SELECT * FROM observations").fetchall())
        initial = usable_verification(db.connection, lookup.spec.watch_id)
        assert initial["outcome"] == ("found" if found else "not_found")
        clock.sleep(86400)
        if conditional:
            lookup.status = 304
        second = lookup.fetch()
        assert second.snapshot_id is None
        latest = usable_verification(db.connection, lookup.spec.watch_id)
        assert latest["checked_at"] > initial["checked_at"]
        assert latest["snapshot_id"] == first.snapshot_id
        assert tuple(db.connection.execute("SELECT * FROM observations").fetchall()) == original
        assert db.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        lookup.client.close()


@pytest.mark.parametrize(
    "damage", ["missing_body", "corrupt_body", "deflate_body", "missing_extract", "corrupt_extract"]
)
def test_damaged_cached_evidence_retains_recovery_digest_and_does_not_renew(tmp_path, damage):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        lookup = Lookup(db, clock)
        first = lookup.fetch()
        initial = usable_verification(db.connection, lookup.spec.watch_id)["checked_at"]
        snapshot = db.connection.execute(
            "SELECT * FROM snapshots WHERE snapshot_id=?", (first.snapshot_id,)
        ).fetchone()
        artifact = (
            lookup.archive.blob_path(snapshot["body_sha256"])
            if damage.endswith("body")
            else lookup.archive.extract_path(snapshot["extract_sha256"])
        )
        if damage.startswith("missing"):
            artifact.unlink()
        elif damage == "deflate_body":
            compressed = gzip.compress(b"content")
            artifact.write_bytes(compressed[:10] + b"\xff" * 20 + compressed[-8:])
        else:
            artifact.write_bytes(b"broken")
        clock.sleep(86400)
        lookup.status = 304
        lookup.fetch()
        failed = db.connection.execute(
            "SELECT * FROM registry_verifications ORDER BY verification_id DESC LIMIT 1"
        ).fetchone()
        assert failed["usable"] == 0
        assert failed["reason"] == (
            "missing_artifact" if damage.startswith("missing") else "corrupt_artifact"
        )
        assert failed["body_sha256"] == snapshot["body_sha256"]
        assert usable_verification(db.connection, lookup.spec.watch_id)["checked_at"] == initial
        lookup.client.close()


@pytest.mark.parametrize("status,body", [(200, b"{}"), (500, b"server error"), (403, b"blocked")])
def test_failed_checks_do_not_renew_and_do_not_strand_probe_for_a_year(tmp_path, status, body):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        lookup = Lookup(db, clock)
        lookup.fetch()
        old = usable_verification(db.connection, lookup.spec.watch_id)["checked_at"]
        clock.sleep(7 * 86400)
        db.connection.executemany(
            "INSERT OR REPLACE INTO cursors(name,value) VALUES (?,?)",
            (
                ("registry_probe_cursor", "1000000"),
                ("registry_probe_started_at", clock.now().isoformat()),
                ("registry_probe_misses", "0"),
            ),
        )
        clock.sleep(1)
        lookup.status, lookup.body = status, body
        result = lookup.fetch()
        refresh_policy(
            db.connection,
            CONFIG,
            lookup.spec.watch_id,
            clock.now(),
            outcome=result.classification.outcome,
            jitter=0,
        )
        advance_sweep(db, clock.now())
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()[0]
            == "1000000"
        )
        assert usable_verification(db.connection, lookup.spec.watch_id)["checked_at"] == old
        assert (
            db.connection.execute(
                "SELECT next_check_at FROM watches WHERE watch_id=?", (lookup.spec.watch_id,)
            ).fetchone()[0]
            == (clock.now() + timedelta(minutes=15)).isoformat()
        )
        lookup.client.close()


def test_identical_miss_finishes_finite_probe_using_new_check_time(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        lookup = Lookup(db, clock)
        first = lookup.fetch()
        clock.sleep(7 * 86400)
        started = clock.now()
        db.connection.executemany(
            "INSERT INTO cursors(name,value) VALUES (?,?)",
            (
                ("registry_probe_cursor", "1000000"),
                ("registry_probe_started_at", started.isoformat()),
                ("registry_probe_misses", "19"),
            ),
        )
        clock.sleep(1)
        lookup.fetch()
        advance_sweep(db, clock.now())
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            is None
        )
        assert (
            db.connection.execute("SELECT snapshot_id FROM observations").fetchone()[0]
            == first.snapshot_id
        )
        lookup.client.close()


def test_recovery_never_uses_migration_time_or_attempt_clock(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        lookup = Lookup(db, clock)
        lookup.fetch()
        checked_at = usable_verification(db.connection, lookup.spec.watch_id)["checked_at"]
        db.connection.execute("DELETE FROM registry_verifications")
        db.connection.execute("DELETE FROM meta WHERE key='registry_verification_migrated'")
        clock.sleep(400 * 86400)
        db.connection.execute("UPDATE watches SET last_checked_at=?", (clock.now().isoformat(),))
        recover_registry_verifications(db)
        assert usable_verification(db.connection, lookup.spec.watch_id)["checked_at"] == checked_at
        recover_registry_verifications(db)
        assert (
            db.connection.execute("SELECT COUNT(*) FROM registry_verifications").fetchone()[0] == 1
        )
        summary = verification_summary(db.connection, clock.now())
        assert summary["oldest_usable_verification_age_seconds"] == 400 * 86400
        assert summary["alert"] is True
        lookup.fetch()
        assert verification_summary(db.connection, clock.now())["alert"] is False
        lookup.client.close()


def test_refreshed_stale_profile_rotates_despite_unchanged_claim_timestamp(tmp_path):
    from test_registry_confirmation import known_dancer

    clock = FakeClock()
    with open_database(tmp_path) as db:
        known_dancer(db.connection)
        lookup = Lookup(db, clock, found=True)
        lookup.fetch()
        clock.sleep(400 * 86400)
        discover_registry(db, clock.now())
        assert (
            db.connection.execute(
                "SELECT notes FROM watches WHERE watch_id=?", (lookup.spec.watch_id,)
            ).fetchone()[0]
            == "trickle"
        )
        original = db.connection.execute("SELECT registry_fetched_at FROM dancers").fetchone()[0]
        lookup.fetch()
        db.connection.execute(
            "UPDATE watches SET notes='' WHERE watch_id=?", (lookup.spec.watch_id,)
        )
        clock.sleep(86400)
        discover_registry(db, clock.now())
        assert (
            db.connection.execute(
                "SELECT notes FROM watches WHERE watch_id=?", (lookup.spec.watch_id,)
            ).fetchone()[0]
            == ""
        )
        assert (
            db.connection.execute("SELECT registry_fetched_at FROM dancers").fetchone()[0]
            == original
        )
        lookup.client.close()


def test_restored_probe_with_failed_attempt_and_annual_clock_gets_bounded_retry(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        discover_registry(db, clock.now())
        clock.sleep(1)
        db.connection.execute(
            "UPDATE watches SET last_checked_at=?,next_check_at=? WHERE source_ref='wsdc:1'",
            (clock.now().isoformat(), (clock.now() + timedelta(days=365)).isoformat()),
        )
        discover_registry(db, clock.now())
        assert (
            db.connection.execute(
                "SELECT next_check_at FROM watches WHERE source_ref='wsdc:1'"
            ).fetchone()[0]
            == (clock.now() + timedelta(minutes=15)).isoformat()
        )


def test_cycle_finishes_repeated_probe_without_any_parse_work(tmp_path):
    import shutil

    from swingset.schedule.cycle import run_cycle

    config_dir, overrides_dir = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config_dir)
    shutil.copytree("overrides", overrides_dir)
    (config_dir / "sources.toml").write_text("[sources.wsdc_registry]\nenabled = true\n")
    (overrides_dir / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides_dir / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    body = (FIXTURES / "lookup-1000000.body").read_bytes()
    clock = FakeClock()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            404, content=b"" if request.url.path == "/robots.txt" else body
        )
    )
    with open_database(tmp_path / "state") as db:
        for _ in range(2):
            run_cycle(
                db,
                config_dir=config_dir,
                overrides_dir=overrides_dir,
                clock=clock,
                transport=transport,
            )
        completed = db.connection.execute(
            "SELECT value FROM cursors WHERE name='registry_probe_last_completed_at'"
        ).fetchone()[0]
        observations = [tuple(row) for row in db.connection.execute("SELECT * FROM observations")]
        clock.sleep(7 * 86400)
        summary = run_cycle(
            db, config_dir=config_dir, overrides_dir=overrides_dir, clock=clock, transport=transport
        )
        assert summary["checked"] == 20
        assert summary["changed"] == 0
        assert "parse" not in summary["stages"]
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            is None
        )
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_last_completed_at'"
            ).fetchone()[0]
            > completed
        )
        assert [
            tuple(row) for row in db.connection.execute("SELECT * FROM observations")
        ] == observations


@pytest.mark.parametrize("snapshot_indexes", [False, True])
def test_recovery_and_parse_completion_use_bounded_indexes(tmp_path, snapshot_indexes):
    """Registry-scale recovery must never rescan every observation for each check."""
    from swingset.state.verification import finish_interpretation

    clock = FakeClock()
    with open_database(tmp_path) as db:
        if not snapshot_indexes:
            # Recovery also runs while upgrading v1, before the later FK indexes.
            db.connection.execute("DROP INDEX observations_snapshot_id_fk_idx")
            db.connection.execute("DROP INDEX registry_verifications_snapshot_id_fk_idx")
        lookup = Lookup(db, clock)
        result = lookup.fetch()
        db.connection.execute("DELETE FROM registry_verifications")
        db.connection.execute("DELETE FROM meta WHERE key='registry_verification_migrated'")
        statements = []
        db.connection.set_trace_callback(statements.append)
        try:
            recover_registry_verifications(db)
            finish_interpretation(db.connection, lookup.archive, result.snapshot_id)
        finally:
            db.connection.set_trace_callback(None)
        observation_queries = [
            query
            for query in statements
            if query.startswith("SELECT kind,payload_json FROM observations")
        ]
        verification_queries = [
            query
            for query in statements
            if query.startswith("SELECT verification_id,body_sha256 FROM registry_verifications")
        ]
        assert len(observation_queries) == 2
        assert len(verification_queries) == 1
        for query in observation_queries + verification_queries:
            table = "observations" if "FROM observations " in query else "registry_verifications"
            plan = [row[3] for row in db.connection.execute("EXPLAIN QUERY PLAN " + query)]
            assert any(
                f"SEARCH {table} USING INDEX" in step
                and ("watch_id=?" in step or "snapshot_id=?" in step)
                for step in plan
            ), plan
            assert not any(f"SCAN {table}" in step for step in plan)
        assert usable_verification(db.connection, lookup.spec.watch_id)["outcome"] == "not_found"
        lookup.client.close()
