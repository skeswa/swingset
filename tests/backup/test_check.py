"""A loop asks a separate owner process to restore-check immutable history."""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from swingset.backup.check import check
from swingset.backup.snapshot import capture
from swingset.backup.transport import TransportError
from swingset.dev.services import ensure_service
from swingset.state.db import open_database


@pytest.fixture
def private_checker_files(tmp_path, request):
    """Keep owner ledgers on disk when test payloads use the bounded tmpfs."""
    if os.environ.get("SWINGSET_TEST_TMP_RETENTION") != "failed":
        yield tmp_path / "checks"
        return
    directory = Path(tempfile.mkdtemp(prefix="swingset-checker-case-", dir="/tmp"))
    root = directory / "checks"
    failures = request.session.testsfailed
    yield root
    # Failed calls and unfinished ownership retain their reconciliation files.
    if request.session.testsfailed != failures:
        return
    owners = root / ".owners"
    if any(owners.glob("*.json")):
        return
    lock = owners / "checker.lock"
    if lock.exists():
        descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            shutil.rmtree(directory)
        finally:
            os.close(descriptor)
    else:
        shutil.rmtree(directory)


def test_private_checker_restores_and_rejects_changed_history_without_client_owner_credentials(
    tmp_path, monkeypatch, private_checker_files
):
    image = "example.test/swingset@sha256:" + "a" * 64
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", image)
    monkeypatch.delenv("SWINGSET_SCRATCH_ADMIN_DSN", raising=False)
    root = private_checker_files
    root.mkdir()
    history = root / "history"
    with open_database(tmp_path / "state") as database:
        capture(database.state_dir, database.connection, history)
    digest = hashlib.sha256((history / "history.json").read_bytes()).hexdigest()
    endpoint = tmp_path / "checker.sock"
    environment = {**os.environ, "SWINGSET_SCRATCH_ADMIN_DSN": ensure_service().dsn()}
    environment.pop("SWINGSET_TEST_RUN", None)
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
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        until = time.monotonic() + 10
        while not endpoint.exists() and child.poll() is None and time.monotonic() < until:
            time.sleep(0.02)
        assert endpoint.exists()
        assert endpoint.stat().st_mode & 0o777 == 0o600
        # Different ephemeral socket paths still share the durable singleton
        # lease, so a replacement cannot reclaim the running owner's jobs.
        contender = subprocess.run(
            [
                sys.executable,
                "-m",
                "swingset.cli",
                "backup-check-serve",
                "--socket",
                str(tmp_path / "other-run" / "checker.sock"),
                "--root",
                str(root),
            ],
            env=environment,
            capture_output=True,
            timeout=10,
        )
        assert contender.returncode != 0
        assert child.poll() is None
        assert ensure_service().password.encode() not in contender.stdout + contender.stderr
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_SOCKET", str(endpoint))
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_ROOT", str(root))
        assert "SWINGSET_SCRATCH_ADMIN_DSN" not in os.environ
        assert check(history, manifest_sha256=digest)["complete"]
        assert check(history, manifest_sha256=digest)["audit"]["equal"]
        with pytest.raises(TransportError, match="check failed"):
            check(history, manifest_sha256="0" * 64)
        with pytest.raises(TransportError, match="outside"):
            check(tmp_path, manifest_sha256=digest)
    finally:
        child.terminate()
        child.communicate(timeout=10)


@pytest.mark.parametrize("corrupt", [False, True])
def test_drill_uses_private_owner_job_without_client_credentials_and_cleans_every_restore(
    tmp_path, monkeypatch, corrupt, private_checker_files
):
    from test_drill import NOW, ROOMY, _proof, backup, build_state

    from swingset.backup import drill

    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", "example.test/swingset@sha256:" + "a" * 64)
    state = tmp_path / "state"
    if corrupt:
        public = _proof(state)
        public["digest"] = "0" * 64
        build_state(state, {"mode": "closure", "closure": public})
    else:
        build_state(state)
    backup(state)
    root = private_checker_files
    root.mkdir()
    endpoint = tmp_path / "checker.sock"
    service = ensure_service()
    environment = {**os.environ, "SWINGSET_SCRATCH_ADMIN_DSN": service.dsn()}
    environment.pop("SWINGSET_TEST_RUN", None)
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
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        until = time.monotonic() + 10
        while not endpoint.exists() and child.poll() is None and time.monotonic() < until:
            time.sleep(0.02)
        assert endpoint.exists()
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_SOCKET", str(endpoint))
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_ROOT", str(root))
        denied_variables = (
            "SWINGSET_TEST_RUN",
            "SWINGSET_SCRATCH_ADMIN_DSN",
            "SWINGSET_OWNER_DATABASE_URL",
            "SWINGSET_TEST_ADMIN_DSN",
            "SWINGSET_TEST_SERVICE_DSN",
            "SWINGSET_TEST_LIFECYCLE_SERVICE_DSN",
        )
        owned_names: set[str] = set()
        observed_owners: set[tuple[int, int]] = set()
        stopped = threading.Event()

        def observe_owner() -> None:
            while not stopped.is_set():
                for path in (root / ".owners").glob("*.json"):
                    try:
                        record = json.loads(path.read_text())
                    except FileNotFoundError:
                        continue
                    observed_owners.add((record["pid"], record["process_group"]))
                    owned_names.update(record["databases"])
                stopped.wait(0.001)

        observer = threading.Thread(target=observe_owner)
        observer.start()
        try:
            # The loop has no administration credential. Restore the harness's
            # configured CI service before its own fixture cleanup runs.
            with monkeypatch.context() as client_environment:
                for variable in denied_variables:
                    client_environment.delenv(variable, raising=False)
                with open_database(state) as database:
                    result = drill.run(
                        database, now=NOW, drill_dir=tmp_path / "drill", free=lambda _: ROOMY
                    )
        finally:
            stopped.set()
            observer.join(timeout=5)
        assert not observer.is_alive()
        assert observed_owners == {(child.pid, child.pid)}
        assert owned_names, "the actual checker restore must create an observed private database"
        assert result["passed"] is (not corrupt), result
        if corrupt:
            assert "public_closure_digest_mismatch" in result["error"]
        else:
            assert result["release"]["mode"] == "partitioned"
            assert result["release"]["artifacts"] == 1
        assert [path.name for path in root.iterdir()] == [".owners"]
        assert [path.name for path in (root / ".owners").iterdir()] == ["checker.lock"]
        assert (root / ".owners").stat().st_mode & 0o777 == 0o700
        assert (root / ".owners" / "checker.lock").stat().st_mode & 0o777 == 0o600
        assert not list((tmp_path / "drill").rglob("database.json"))
        assert not list((tmp_path / "drill").rglob("restore-history"))
        with service.connect() as admin:
            assert (
                admin.execute(
                    "SELECT datname FROM pg_database WHERE datname=ANY(%s)",
                    (sorted(owned_names),),
                ).fetchall()
                == []
            )
    finally:
        child.terminate()
        stdout, stderr = child.communicate(timeout=10)
        assert service.password.encode() not in stdout + stderr


def test_readonly_probe_stays_responsive_during_real_restore_and_rejects_stale_listener(
    tmp_path, monkeypatch, private_checker_files
):
    import importlib
    import socket

    checker = importlib.import_module("swingset.backup.check")
    monkeypatch.setenv("SWINGSET_RUNTIME_IMAGE", "example.test/swingset@sha256:" + "a" * 64)
    root = private_checker_files
    root.mkdir()
    history = root / "history"
    with open_database(tmp_path / "state") as database:
        capture(database.state_dir, database.connection, history)
    digest = hashlib.sha256((history / "history.json").read_bytes()).hexdigest()
    endpoint, busy, release = (tmp_path / name for name in ("checker.sock", "busy", "release"))
    # Only the unit's platform-identity provider is replaced on macOS. The
    # production provider always reads Linux procfs; no runtime override exists.
    code = """
import importlib,os,sys,time
from pathlib import Path
checker=importlib.import_module("swingset.backup.check")
original=checker.restore_check
endpoint,root,busy,release=map(Path,sys.argv[1:])
def delayed(*args,**kwargs):
    busy.write_text("restore entered")
    until=time.monotonic()+20
    while not release.exists():
        if time.monotonic()>until:raise RuntimeError("fixture release timed out")
        time.sleep(.01)
    return original(*args,**kwargs)
checker.restore_check=delayed
checker._listener_identity=lambda:{"boot_id":"fixture-boot","pid_namespace":"pid:[fixture]","pid":os.getpid()}
checker.serve(endpoint,root,os.environ["SWINGSET_SCRATCH_ADMIN_DSN"])
"""
    env = {**os.environ, "SWINGSET_SCRATCH_ADMIN_DSN": ensure_service().dsn()}
    env.pop("SWINGSET_TEST_RUN", None)
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(endpoint), str(root), str(busy), str(release)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = None
    try:
        until = time.monotonic() + 10
        while (
            not Path(str(endpoint) + ".probe").exists()
            and process.poll() is None
            and time.monotonic() < until
        ):
            time.sleep(0.02)
        assert Path(str(endpoint) + ".probe").exists()
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_SOCKET", str(endpoint))
        monkeypatch.setenv("SWINGSET_BACKUP_CHECK_ROOT", str(root))
        monkeypatch.delenv("SWINGSET_SCRATCH_ADMIN_DSN", raising=False)
        values = []
        client = threading.Thread(
            target=lambda: values.append(check(history, manifest_sha256=digest))
        )
        client.start()
        while not busy.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert busy.exists()
        monkeypatch.setattr(
            checker,
            "_listener_identity",
            lambda: {
                "boot_id": "fixture-boot",
                "pid_namespace": "pid:[fixture]",
                "pid": os.getpid(),
            },
        )
        started = time.monotonic()
        assert checker.probe(endpoint)["ready"] is True
        assert time.monotonic() - started < 3
        assert client.is_alive(), "the real restore remains queued in its main-thread handler"
        monkeypatch.setattr(
            checker,
            "_listener_identity",
            lambda: {
                "boot_id": "fixture-boot",
                "pid_namespace": "pid:[another-container]",
                "pid": os.getpid(),
            },
        )
        with pytest.raises(TransportError, match="listener identity"):
            checker.probe(endpoint)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as request:
            request.settimeout(5)
            request.connect(str(endpoint) + ".probe")
            request.sendall(b'{"history":"secret-marker","manifest_sha256":"not-a-job"}\n')
            body = request.recv(4096)
        assert json.loads(body)["complete"] is False
        assert b"secret-marker" not in body
        release.write_text("continue")
        client.join(timeout=15)
        assert not client.is_alive()
        assert values and values[0]["complete"] and values[0]["audit"]["equal"]
    finally:
        release.write_text("continue")
        if client is not None:
            client.join(timeout=15)
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert ensure_service().password.encode() not in stdout + stderr
