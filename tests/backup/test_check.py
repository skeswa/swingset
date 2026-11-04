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
