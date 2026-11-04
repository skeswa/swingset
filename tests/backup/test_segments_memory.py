"""A backup set's memory bound (architecture phase 4, WP6; invariant I10).

A synthetic state of 50,000 objects (25,000 bodies a host row names and
25,000 `derivation_output` objects) and a database of about 200 MB. The set
is written in a subprocess (this file run as a script), so `ru_maxrss` is the
writer's own: the database streams through zstd in chunks and the segments
stream file by file, so it stays under 300 MB.
"""

from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

OBJECTS = 50_000
DATABASE_BYTES = 200 * 1024 * 1024
RSS_LIMIT = 300 * 1024 * 1024
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def fixture(state: Path) -> None:
    import base64

    from swingset.state.db import open_database

    bodies = OBJECTS // 2
    stamp = NOW.isoformat()
    with open_database(state) as database:
        conn = database.connection
        run = database.start_run(NOW)
        hosts = []
        for index in range(bodies):
            body = f"body {index}\n".encode() * 8
            digest = hashlib.sha256(body).hexdigest()
            path = state / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(body, mtime=0))
            hosts.append((f"h{index}.example", digest))
        dependency = hashlib.sha256(b"[]").hexdigest()
        outputs, labels = [], []
        for index in range(OBJECTS - bodies):
            payload = json.dumps(
                {"dancer_id": f"memory-{index}", "run_id": run},
                sort_keys=True,
                separators=(",", ":"),
            )
            raw = (
                json.dumps(
                    ("dancers", f"memory-{index}", payload), sort_keys=True, separators=(",", ":")
                ).encode()
                + b"\n"
            )
            digest = hashlib.sha256(raw).hexdigest()
            path = state / "objects" / "derivation_output" / digest[:2] / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(raw, mtime=0))
            outputs.append((digest, len(raw), path.stat().st_size, stamp))
            labels.append((f"memory-output-{index}", digest, dependency, digest, stamp, run))
        with database.transaction():
            conn.execute(
                "UPDATE runs SET finished_at=%s,summary_json='{}' WHERE run_id=%s", (stamp, run)
            )
            conn.executemany(
                "INSERT INTO hosts (host, pause_streak, robots_sha256) VALUES (%s, 0, %s)", hosts
            )
            conn.executemany(
                "INSERT INTO objects (class, digest, raw_bytes, stored_bytes, stored_at) VALUES ('derivation_output', %s, %s, %s, %s)",
                outputs,
            )
            conn.execute(
                "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES(%s,'[]')",
                (dependency,),
            )
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES('project','history','memory-fixture',%s)",
                (stamp,),
            )
            conn.executemany(
                "INSERT INTO derivation_generations(generation_id,stage,unit_kind,unit_id,input_fingerprint,recipe_json,dependency_set_id,output_digest,row_count,created_at,run_id) VALUES(%s,'project','history','memory-fixture',%s,'{}',%s,%s,1,%s,%s)",
                labels,
            )
            conn.executemany(
                "INSERT INTO derivation_evicted(generation_id,evicted_at) VALUES(%s,%s)",
                ((row[0], stamp) for row in labels),
            )
        # Retained typed history, not an unknown table or rebuilt cache. Each
        # row is bounded; the original test exercises a 200 MiB database stream.
        expected = hashlib.sha256()
        raw_bytes = 0
        chunk = 4 * 1024 * 1024
        for index in range(DATABASE_BYTES // chunk):
            summary = '{"noise":"' + base64.b64encode(os.urandom(chunk)).decode("ascii") + '"}'
            raw = summary.encode()
            expected.update(raw)
            raw_bytes += len(raw)
            with database.transaction():
                conn.execute(
                    "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES(%s,%s,%s,0,%s)",
                    (f"memory-history-{index:04}", stamp, stamp, summary),
                )
        (state.parent / "memory-history.json").write_text(
            json.dumps(
                {
                    "rows": DATABASE_BYTES // chunk,
                    "bytes": raw_bytes,
                    "sha256": expected.hexdigest(),
                }
            )
        )


def verify(state: Path) -> dict[str, object]:
    from swingset.backup.restore_set import install
    from swingset.backup.segments import LOCAL_TRANSPORT
    from swingset.backup.transport import LocalDirectoryTransport
    from swingset.state import engine

    target = state.parent / "restored"
    from swingset.dev import services
    from swingset.state.postgres_copy import Copies

    service = services.ensure_service()
    with Copies.for_admin(service.dsn()) as copies, copies.empty() as owner:
        engine.write_settings(
            target,
            service.dsn(owner.info.dbname, role="worker"),
            control_dsn=service.dsn(owner.info.dbname, role="control"),
        )
        install(
            LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT),
            target,
            owner_dsn=service.dsn(owner.info.dbname),
        )
        return _verify_restored(state, target)


def _verify_restored(state: Path, target: Path) -> dict[str, object]:
    from swingset.state import engine

    expected = json.loads((state.parent / "memory-history.json").read_text())
    actual = hashlib.sha256()
    raw_bytes = 0
    with engine.connect(engine.resolve_dsn(target), read_only=True) as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE run_id LIKE 'memory-history-%%' ORDER BY run_id"
        ).fetchall()
        for (run_id,) in rows:
            text = conn.execute(
                "SELECT summary_json FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            raw = text.encode()
            actual.update(raw)
            raw_bytes += len(raw)
        assert {"rows": len(rows), "bytes": raw_bytes, "sha256": actual.hexdigest()} == expected
        assert raw_bytes >= DATABASE_BYTES
        assert conn.execute("SELECT count(*) FROM derivation_evicted").fetchone()[0] == OBJECTS // 2
        assert (
            conn.execute("SELECT count(*) FROM objects WHERE class='derivation_output'").fetchone()[
                0
            ]
            == OBJECTS // 2
        )
    restored_files = 0
    for directory in ("blobs/sha256", "objects/derivation_output"):
        paths = sorted(path for path in (state / directory).rglob("*") if path.is_file())
        assert len(paths) == OBJECTS // 2
        for original in paths:
            restored = target / original.relative_to(state)
            assert restored.is_file(), restored
            expected_stored = original.read_bytes()
            actual_stored = restored.read_bytes()
            assert (
                hashlib.sha256(actual_stored).digest() == hashlib.sha256(expected_stored).digest()
            )
            assert hashlib.sha256(gzip.decompress(actual_stored)).hexdigest() == restored.name
            restored_files += 1
    assert restored_files == OBJECTS
    return {**expected, "restored_files": restored_files}


def write(state: Path) -> dict[str, object]:
    from swingset.backup import check

    owner_checks = []
    request = check._request

    def observed_request(*args, **kwargs):
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result = request(*args, **kwargs)
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        scale = 1 if sys.platform == "darwin" else 1024
        owner_checks.append(
            {
                "before_rss_bytes": before * scale,
                "after_rss_bytes": after * scale,
                "complete": result.get("complete") is True,
                "audit_equal": result.get("audit", {}).get("equal") is True,
            }
        )
        return result

    check._request = observed_request
    from swingset.backup.segments import LOCAL_TRANSPORT, create_set
    from swingset.backup.transport import LocalDirectoryTransport
    from swingset.state.db import open_database

    with open_database(state) as database:
        result = create_set(
            database,
            LocalDirectoryTransport(state / "checkpoints", name=LOCAL_TRANSPORT),
            now=NOW,
            versions={},
            headroom=0,
            part_bytes=64 * 1024 * 1024,
            segment_bytes=32 * 1024 * 1024,
        )
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "owner_checks": owner_checks,
        "items": result["items"],
        "parts": result["parts"],
        "db_raw_bytes": result["db_raw_bytes"],
        # Linux reports kilobytes; macOS reports bytes.
        "max_rss_bytes": usage if sys.platform == "darwin" else usage * 1024,
    }


def main(step: str, directory: str) -> None:
    if step != "write":
        from swingset.dev import services

        physical = services.configured_ci_lifecycle_service()
        if physical is None:
            raise RuntimeError("memory fixture requires its owned physical test service")
        services._validate_service(physical)
        # Setup/audit jobs own explicit databases; the measured writer has
        # only its worker marker and the separate owner-check socket.
        services.ensure_service = lambda *_args, **_kwargs: physical
    else:
        assert os.environ.get("SWINGSET_BACKUP_CHECK_SOCKET")
        assert os.environ.get("SWINGSET_BACKUP_CHECK_ROOT")
        assert not any(name.startswith("SWINGSET_TEST_") for name in os.environ)
        assert "SWINGSET_SCRATCH_ADMIN_DSN" not in os.environ
        assert "SWINGSET_OWNER_DATABASE_URL" not in os.environ
    if step == "fixture":
        fixture(Path(directory))
        return
    if step == "verify":
        print(json.dumps(verify(Path(directory))))
        return
    print(json.dumps(write(Path(directory))))


def test_a_set_streams_under_its_memory_bound(tmp_path: Path) -> None:
    from swingset.dev import services
    from swingset.state import engine, postgres_schema
    from swingset.state.postgres_copy import Copies

    state = tmp_path / "state"
    with services.lifecycle_service() as physical, Copies.for_admin(physical.dsn()) as copies:
        with copies.empty() as owner:
            postgres_schema.migrate(owner, environment="dev")
            engine.write_settings(
                state,
                physical.dsn(owner.info.dbname, role="worker"),
                control_dsn=physical.dsn(owner.info.dbname, role="control"),
            )
            environment = dict(os.environ)
            environment.update(services.ci_environment(physical))
            with _owner_checker(tmp_path, physical, environment) as worker_environment:
                _assert_memory_bound(state, environment, worker_environment)


def _worker_environment(environment: dict[str, str], endpoint: Path, root: Path) -> dict[str, str]:
    denied = {
        "DATABASE_URL",
        "SWINGSET_DATABASE_URL",
        "SWINGSET_CONTROL_DATABASE_URL",
        "SWINGSET_OWNER_DATABASE_URL",
        "SWINGSET_SCRATCH_ADMIN_DSN",
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGOPTIONS",
    }
    result = {
        key: value
        for key, value in environment.items()
        if key not in denied and not key.startswith("SWINGSET_TEST_")
    }
    result.update(SWINGSET_BACKUP_CHECK_SOCKET=str(endpoint), SWINGSET_BACKUP_CHECK_ROOT=str(root))
    return result


@contextlib.contextmanager
def _owner_checker(root: Path, physical, environment: dict[str, str]):
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="swingset-memory-check-", dir="/tmp") as temporary:
        endpoint = Path(temporary) / "checker.sock"
        worker = _worker_environment(environment, endpoint, root)
        owner = {**worker, "SWINGSET_SCRATCH_ADMIN_DSN": physical.dsn()}
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "swingset.cli",
                "backup-check-serve",
                "--socket",
                str(endpoint),
                "--root",
                str(root),
            ],
            env=owner,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            until = time.monotonic() + 10
            while not endpoint.exists() and child.poll() is None and time.monotonic() < until:
                time.sleep(0.02)
            assert endpoint.is_socket(), "private owner checker did not become ready"
            assert endpoint.stat().st_mode & 0o777 == 0o600
            registry = root / ".owners"
            assert registry.is_dir() and not registry.is_symlink()
            assert registry.stat().st_mode & 0o777 == 0o700
            yield worker
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=10)
            from swingset.dev import services
            from swingset.state.postgres_copy import Copies

            assert not services._group_alive(child.pid), "private checker children remain alive"
            registry = root / ".owners"
            lock = registry / "checker.lock"
            if lock.exists():
                descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "w") as guard:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with Copies.for_admin(
                        physical.dsn(), registry_root=registry, checker_lock_fd=guard.fileno()
                    ):
                        pass
                assert not list(registry.glob("*.json")), "private checker ownership remains"


def _assert_memory_bound(
    state: Path, environment: dict[str, str], worker_environment: dict[str, str]
) -> None:
    for step in ("fixture", "write"):
        process = subprocess.run(
            [sys.executable, __file__, step, str(state)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            env=environment if step == "fixture" else worker_environment,
        )
        assert process.returncode == 0, process.stderr[-4000:]
    report = json.loads(process.stdout.strip().splitlines()[-1])
    assert report["items"] == OBJECTS, report
    assert report["db_raw_bytes"] >= DATABASE_BYTES, report
    assert report["parts"] >= 1, report
    assert report["owner_checks"] and all(
        check["complete"] and check["audit_equal"] for check in report["owner_checks"]
    ), report
    (state.parent / "memory-writer.json").write_text(json.dumps(report))
    assert report["max_rss_bytes"] < RSS_LIMIT, report
    checked = subprocess.run(
        [sys.executable, __file__, "verify", str(state)],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
        env=environment,
    )
    assert checked.returncode == 0, checked.stderr[-4000:]
    assert json.loads(checked.stdout.strip().splitlines()[-1])["bytes"] >= DATABASE_BYTES


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
