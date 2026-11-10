"""Exact ownership fences private check databases and expanded install files."""

import json
import os
import select
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from swingset.dev.services import ensure_service
from swingset.state.postgres_copy import Copies


def staging(root):
    root.mkdir()
    path = root / ".install-owned_123"
    (path / "fetch/history").mkdir(parents=True)
    (path / "state").mkdir()
    (path / "fetch/history/payload").write_bytes(b"expanded history" * 1024)
    return path


@pytest.mark.parametrize("failure", [False, True])
def test_owned_database_and_expanded_files_cleanup_on_success_or_failure(tmp_path, failure):
    service = ensure_service()
    root = tmp_path / "check"
    work = staging(root)
    copies = Copies.for_admin(service.dsn())
    try:
        with copies:
            copies.own_directory(work, root=root)
            copies.own_directory(work, root=root)
            assert len(copies.record["directories"]) == 1
            with copies.empty() as connection:
                name = connection.info.dbname
                connection.execute("CREATE TABLE public.check_proof(value TEXT)")
                connection.execute("INSERT INTO public.check_proof VALUES('checked')")
            assert work.is_dir()  # empty() closes the DB before outer file ownership.
            if failure:
                raise RuntimeError("injected verification failure")
    except RuntimeError as error:
        assert failure and str(error) == "injected verification failure"
    assert not work.exists() and root.is_dir() and not copies.path.exists()
    with service.connect() as admin:
        assert (
            admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        )


def test_owned_template_clone_has_separate_bounded_time_and_cleans_canceled_creation(monkeypatch):
    import psycopg

    from swingset.state import postgres_copy

    service = ensure_service()
    # Exercise actual server cancellation without a large physical test fixture.
    monkeypatch.setattr(postgres_copy, "EMPTY_CREATE_SECONDS", 1)
    monkeypatch.setattr(postgres_copy, "TEMPLATE_CLONE_SECONDS", 3)
    execute = psycopg.Connection.execute
    delay = False
    lock_timeouts = []

    def slow_create(connection, statement, *args, **kwargs):
        text = statement.as_string(connection) if hasattr(statement, "as_string") else statement
        if delay and text.startswith("CREATE DATABASE "):
            lock_timeouts.append(execute(connection, "SHOW lock_timeout").fetchone()[0])
            execute(connection, "SELECT pg_sleep(1.1)")
        return execute(connection, statement, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", slow_create)
    with Copies.for_admin(service.dsn()) as copies:
        original = copies._create()
        with copies._admin(original) as source:
            source.execute("CREATE TABLE public.clone_proof(id INTEGER PRIMARY KEY, value TEXT)")
            source.execute("INSERT INTO public.clone_proof VALUES(1,'retained'),(2,'雪')")
        delay = True
        with pytest.raises(RuntimeError, match="SQLSTATE 57014"):
            copies._create()
        canceled = copies.record["databases"][-1]
        cloned = copies._create(template=original)
        with copies._admin(cloned) as connection:
            assert connection.execute("TABLE public.clone_proof ORDER BY id").fetchall() == [
                (1, "retained"),
                (2, "雪"),
            ]
            connection.execute("UPDATE public.clone_proof SET value='clone only' WHERE id=1")
        with copies._admin(original) as source:
            assert source.execute("TABLE public.clone_proof ORDER BY id").fetchall() == [
                (1, "retained"),
                (2, "雪"),
            ]
        assert lock_timeouts == ["10s", "10s"]
        claimed = list(copies.record["databases"])
        with pytest.raises(ValueError, match="not owned"):
            copies._create(template="template1")
        assert copies.record["databases"] == claimed
    with service.connect() as admin:
        assert not admin.execute(
            "SELECT datname FROM pg_database WHERE datname=ANY(%s)",
            ([original, canceled, cloned],),
        ).fetchall()
    assert not copies.path.exists()


def test_directory_ownership_refuses_root_escape_symlink_and_foreign_marker(tmp_path):
    root = tmp_path / "check"
    work = staging(root)
    outside = tmp_path / ".install-outside_1"
    outside.mkdir()
    link = root / ".install-link_123"
    link.symlink_to(work, target_is_directory=True)
    root_link = tmp_path / "root-link"
    root_link.symlink_to(root, target_is_directory=True)
    with Copies.for_admin(ensure_service().dsn()) as copies:
        for path, base in (
            (root, root),
            (outside, root),
            (link, root),
            (work, root_link),
            (work / "state", root),
        ):
            with pytest.raises(ValueError):
                copies.own_directory(path, root=base)
        marker = work / ".swingset-scratch-owner.json"
        marker.write_text("foreign")
        with pytest.raises(ValueError, match="already has"):
            copies.own_directory(work, root=root)
        assert marker.read_text() == "foreign"
        assert copies.record["directories"] == []
    assert (work / "fetch/history/payload").is_file() and outside.is_dir()


def test_cleanup_refuses_changed_nonce_and_preserves_the_directory(tmp_path):
    root = tmp_path / "check"
    work = staging(root)
    copies = Copies.for_admin(ensure_service().dsn())
    copies.__enter__()
    copies.own_directory(work, root=root)
    marker = work / ".swingset-scratch-owner.json"
    original = marker.read_bytes()
    changed = json.loads(original)
    changed["nonce"] = "0" * 32
    marker.write_text(json.dumps(changed))
    try:
        with pytest.raises(RuntimeError, match="ownership changed"):
            copies.__exit__()
        assert copies.path.is_file() and (work / "fetch/history/payload").is_file()
    finally:
        marker.write_bytes(original)
        copies.__exit__()
    assert not work.exists()


def test_cleanup_refuses_a_replacement_directory_at_the_owned_path(tmp_path):
    root = tmp_path / "check"
    work = staging(root)
    copies = Copies.for_admin(ensure_service().dsn())
    copies.__enter__()
    copies.own_directory(work, root=root)
    moved = root / ".install-original_1"
    work.rename(moved)
    work.mkdir()
    (work / ".swingset-scratch-owner.json").write_bytes(
        (moved / ".swingset-scratch-owner.json").read_bytes()
    )
    (work / "unowned").write_text("keep")
    try:
        with pytest.raises(RuntimeError, match="ownership changed"):
            copies.__exit__()
        assert (work / "unowned").read_text() == "keep"
    finally:
        (work / ".swingset-scratch-owner.json").unlink()
        (work / "unowned").unlink()
        work.rmdir()
        moved.rename(work)
        copies.__exit__()


_CHILD = """
import json,os,signal,sys
from pathlib import Path
from swingset.state.postgres_copy import Copies
with Copies.for_admin(os.environ['SWINGSET_COPY_TEST_ADMIN_DSN']) as copies:
    copies.own_directory(Path(sys.argv[1]),root=Path(sys.argv[2]))
    with copies.empty() as connection:
        connection.execute('CREATE TABLE public.check_proof(value TEXT)')
        connection.execute("INSERT INTO public.check_proof VALUES('checked')")
        print(json.dumps({'database':connection.info.dbname,'record':str(copies.path)}),flush=True)
        signal.pause()
"""


def test_sigkill_cleanup_waits_for_whole_owner_group_then_removes_database_and_files(tmp_path):
    service = ensure_service()
    root = tmp_path / "check"
    work = staging(root)
    environment = {**os.environ, "SWINGSET_COPY_TEST_ADMIN_DSN": service.dsn()}
    process = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(work), str(root)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        process_group=0,
    )
    sibling = None
    try:
        assert select.select([process.stdout], [], [], 30)[0], "scratch job did not become ready"
        line = process.stdout.readline()
        assert line, process.stderr.read()
        owned = json.loads(line)
        record = Path(owned["record"])
        assert record.is_file() and work.is_dir()
        # A sibling in the same process group models a still-running checker child.
        sibling = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"], process_group=process.pid
        )
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        with Copies.for_admin(service.dsn()):
            assert record.is_file() and work.is_dir()
            with service.connect() as admin:
                assert admin.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (owned["database"],)
                ).fetchone()
        os.killpg(process.pid, signal.SIGKILL)
        sibling.wait(timeout=10)
        with Copies.for_admin(service.dsn()):
            assert not record.exists() and not work.exists()
            with service.connect() as admin:
                assert (
                    admin.execute(
                        "SELECT 1 FROM pg_database WHERE datname=%s", (owned["database"],)
                    ).fetchone()
                    is None
                )
        assert root.is_dir()
    finally:
        for child in (process, sibling):
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)
        # Cleanup also runs on a failing assertion, using the same exact fences.
        with Copies.for_admin(service.dsn()):
            pass


def _checker(root):
    import fcntl
    from contextlib import contextmanager

    @contextmanager
    def locked():
        registry = root / ".owners"
        registry.mkdir(mode=0o700)
        descriptor = os.open(registry / "checker.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield registry, descriptor
        finally:
            os.close(descriptor)

    return locked()


def test_checker_recreation_reaps_old_namespace_pid_one_with_persistent_exact_guard(
    tmp_path, monkeypatch
):
    from swingset.state import postgres_copy

    root = tmp_path / "check"
    work = staging(root)
    service = ensure_service()
    old_identity = {
        "boot": "boot-A",
        "namespace": "pid:[old]",
        "pid_start": "old-init",
        "group_start": "old-init",
    }
    new_identity = {
        **old_identity,
        "namespace": "pid:[new]",
        "pid_start": "new-init",
        "group_start": "new-init",
    }
    with _checker(root) as (registry, guard):
        monkeypatch.setattr(postgres_copy, "_incarnation", lambda *_: old_identity)
        old = Copies.for_admin(service.dsn(), registry_root=registry, checker_lock_fd=guard)
        old.__enter__()
        old.record["pid"] = old.record["process_group"] = 1
        old.own_directory(work, root=root)
        saved = json.loads(old.path.read_text())
        assert saved["incarnation"] == old_identity and saved["pid"] == saved["process_group"] == 1
        # In the replacement container PID1 and PGID1 exist again. They are not
        # the previous namespace's init or its children.
        monkeypatch.setattr(postgres_copy, "_incarnation", lambda *_: new_identity)
        monkeypatch.setattr(postgres_copy, "_alive", lambda *_: True)
        monkeypatch.setattr(postgres_copy, "_group_alive", lambda *_: True)
        with Copies.for_admin(service.dsn(), registry_root=registry, checker_lock_fd=guard):
            assert not old.path.exists() and not work.exists()
        assert (registry / "checker.lock").is_file() and root.is_dir()


def test_checker_pid_reuse_does_not_reap_a_still_live_old_group(tmp_path, monkeypatch):
    from swingset.state import postgres_copy

    root = tmp_path / "check"
    root.mkdir()
    current = {
        "boot": "boot-A",
        "namespace": "pid:[same]",
        "pid_start": "current",
        "group_start": "current-group",
    }
    with _checker(root) as (registry, guard):
        monkeypatch.setattr(postgres_copy, "_incarnation", lambda *_: current)
        with Copies.for_admin(
            ensure_service().dsn(), registry_root=registry, checker_lock_fd=guard
        ) as copies:
            old = {
                **copies.record,
                "pid": 222222,
                "process_group": 333333,
                "incarnation": {**current, "pid_start": "old-owner", "group_start": "old-group"},
            }
            monkeypatch.setattr(postgres_copy, "_alive", lambda *_: True)
            monkeypatch.setattr(postgres_copy, "_group_alive", lambda *_: True)
            monkeypatch.setattr(
                postgres_copy, "_process_start", lambda pid: "new-owner" if pid == 222222 else None
            )
            assert not copies._owner_dead(old)
            # A new group leader at the same ID proves the old group finished;
            # Unix cannot reuse that group ID while the old group's children live.
            monkeypatch.setattr(
                postgres_copy,
                "_process_start",
                lambda pid: "new-owner" if pid == 222222 else "new-group",
            )
            assert copies._owner_dead(old)


@pytest.mark.parametrize(
    "unknown", ["boot", "namespace", "lock", "non_init", "missing_incarnation"]
)
def test_checker_recreation_refuses_unknown_or_unfenced_owners(tmp_path, monkeypatch, unknown):
    from swingset.state import postgres_copy

    root = tmp_path / "check"
    root.mkdir()
    current = {
        "boot": "boot-A",
        "namespace": "pid:[new]",
        "pid_start": "current",
        "group_start": "current",
    }
    with _checker(root) as (registry, guard):
        monkeypatch.setattr(postgres_copy, "_incarnation", lambda *_: current)
        with Copies.for_admin(
            ensure_service().dsn(), registry_root=registry, checker_lock_fd=guard
        ) as copies:
            old = {
                **copies.record,
                "pid": 1,
                "process_group": 1,
                "incarnation": {**current, "namespace": "pid:[old]"},
            }
            if unknown == "boot":
                old["incarnation"]["boot"] = "other-boot"
            if unknown == "namespace":
                old["incarnation"]["namespace"] = None
            if unknown == "lock":
                old["checker_lock"] = {
                    **old["checker_lock"],
                    "inode": old["checker_lock"]["inode"] + 1,
                }
            if unknown == "non_init":
                old["pid"] = old["process_group"] = 2
            if unknown == "missing_incarnation":
                del old["incarnation"]
            assert not copies._owner_dead(old)


def test_ownership_records_and_nonce_publish_are_fsynced_before_effects(tmp_path, monkeypatch):
    from swingset.state import postgres_copy

    root = tmp_path / "check"
    work = staging(root)
    sync = []
    original = os.fsync

    def fsync(descriptor):
        sync.append(os.fstat(descriptor).st_ino)
        return original(descriptor)

    monkeypatch.setattr(postgres_copy.os, "fsync", fsync)
    with Copies.for_admin(ensure_service().dsn()) as copies:
        before = len(sync)
        copies.own_directory(work, root=root)
        assert len(sync) >= before + 4  # record data+registry parent, marker data+work parent
        assert copies.path.stat().st_ino in sync
        assert copies.path.parent.stat().st_ino in sync
        assert (work / ".swingset-scratch-owner.json").stat().st_ino in sync
        assert work.stat().st_ino in sync


def test_explicit_durable_copy_registry_is_private_and_cannot_adopt_foreign_namespace(
    tmp_path, monkeypatch
):
    durable = tmp_path / "durable-owners"
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(durable))
    copies = Copies.for_admin(ensure_service().dsn())
    assert copies.path.parent == durable.resolve()
    assert copies.record["incarnation"] == copies._process_identity
    foreign = {
        **copies.record,
        "incarnation": {
            **copies.record["incarnation"],
            "namespace": "pid:[foreign]",
            "pid_start": "foreign",
        },
    }
    assert not copies._owner_dead(foreign)
    explicit = tmp_path / "checker-owners"
    assert Copies.for_admin(ensure_service().dsn(), registry_root=explicit).path.parent == explicit
    durable.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        Copies.for_admin(ensure_service().dsn())
    durable.chmod(0o700)
    monkeypatch.setenv("SWINGSET_COPY_ROOT", "relative")
    with pytest.raises(ValueError, match="absolute"):
        Copies.for_admin(ensure_service().dsn())
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(tmp_path / "owners-link"))
    (tmp_path / "owners-link").symlink_to(durable, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        Copies.for_admin(ensure_service().dsn())
