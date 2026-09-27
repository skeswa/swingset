"""Loops share the state; one exclusive writer waits for them and is not starved."""

import fcntl
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from swingset.state import rwlock
from swingset.state.db import (
    MIGRATION_PENDING,
    SCHEMA_VERSION,
    DatabaseLockedError,
    open_database,
    open_for_loop,
)

HOLDER = textwrap.dedent(
    """
    import sys
    from swingset.state.db import open_database

    state, mode, loop = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "shared":
        handle = open_database(state, mode="shared", loop=loop)
    else:
        handle = open_database(state, lock_timeout=None)
    print("OPEN", handle.lock_mode, flush=True)
    sys.stdin.readline()
    handle.close()
    print("CLOSED", flush=True)
    """
)


def _spawn(state: Path, mode: str, loop: str = "-") -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(state), mode, loop],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(child: subprocess.Popen[str]) -> str:
    assert child.stdin is not None
    child.stdin.write("\n")
    child.stdin.flush()
    output, error = child.communicate(timeout=20)
    assert child.returncode == 0, error
    return output


def _intent_held_exclusive(state: Path) -> bool:
    with (state / rwlock.STATE_INTENT).open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


@pytest.fixture
def state(tmp_path: Path) -> Path:
    open_database(tmp_path).close()
    return tmp_path


def test_two_loops_hold_the_state_shared_across_processes(state):
    first = _spawn(state, "shared", "derive")
    second = _spawn(state, "shared", "acquire")
    try:
        assert first.stdout is not None and second.stdout is not None
        assert first.stdout.readline().split() == ["OPEN", "shared"]
        assert second.stdout.readline().split() == ["OPEN", "shared"]
    finally:
        assert "CLOSED" in _finish(first)
        assert "CLOSED" in _finish(second)


def test_an_exclusive_open_waits_for_loops_and_blocks_new_ticks_meanwhile(state):
    loop = _spawn(state, "shared", "derive")
    assert loop.stdout is not None
    assert loop.stdout.readline().split() == ["OPEN", "shared"]
    writer = _spawn(state, "exclusive")
    try:
        deadline = time.monotonic() + 15
        while not _intent_held_exclusive(state):
            assert time.monotonic() < deadline, "the writer never took the intent"
            time.sleep(0.02)
        # The writer waits on the loop, and no new tick starts in front of it.
        with pytest.raises(DatabaseLockedError) as refused:
            open_database(state, mode="shared", loop="acquire")
        assert refused.value.reason == "writer pending"
        assert writer.poll() is None
    finally:
        _finish(loop)
    assert writer.stdout is not None
    assert writer.stdout.readline().split() == ["OPEN", "exclusive"]
    with pytest.raises(DatabaseLockedError):
        open_database(state, mode="shared", loop="acquire")
    _finish(writer)
    with open_database(state, mode="shared", loop="acquire") as database:
        assert database.lock_mode == "shared"


def test_a_timer_writer_fails_fast_and_leaves_the_intent_free(state):
    with open_database(state, mode="shared", loop="derive"):
        started = time.monotonic()
        with pytest.raises(DatabaseLockedError) as refused:
            open_database(state, lock_timeout=0)
        assert time.monotonic() - started < 1
        assert refused.value.reason == "in use"
        assert not _intent_held_exclusive(state)
        with open_database(state, mode="shared", loop="acquire"):
            pass


def test_a_second_open_of_one_loop_fails(state):
    with open_database(state, mode="shared", loop="derive") as database:
        assert database.loop == "derive"
        with pytest.raises(DatabaseLockedError) as refused:
            open_database(state, mode="shared", loop="derive")
        assert refused.value.reason == "loop running"
    with open_database(state, mode="shared", loop="derive"):
        pass


def test_a_loop_hands_an_old_schema_to_one_exclusive_migration(tmp_path):
    with pytest.raises(DatabaseLockedError) as refused:
        open_database(tmp_path, mode="shared", loop="derive")
    assert refused.value.reason == MIGRATION_PENDING
    with open_for_loop(tmp_path, "derive") as database:
        assert database.lock_mode == "shared"
        assert database.schema_version == SCHEMA_VERSION
        assert not database.holds_writer_lock


def test_a_loop_that_loses_the_migration_race_skips(tmp_path):
    # Another loop holds the state shared (as the lock pair alone, before any
    # schema), so this loop's one exclusive try fails and it skips its tick.
    other = rwlock.shared(tmp_path, loop="acquire")
    try:
        with pytest.raises(DatabaseLockedError) as refused:
            open_for_loop(tmp_path, "derive")
        assert refused.value.reason == "in use"
    finally:
        other.release()
    with open_for_loop(tmp_path, "derive") as database:
        assert database.schema_version == SCHEMA_VERSION


def test_a_shared_open_refuses_a_newer_schema(state):
    with open_database(state) as database:
        database.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer than supported"):
        open_database(state, mode="shared", loop="derive")


def test_only_the_exclusive_handle_is_the_writer(state):
    with open_database(state, mode="shared", loop="derive") as database:
        assert database.lock_mode == "shared" and not database.holds_writer_lock
    with open_database(state) as database:
        assert database.lock_mode == "exclusive" and database.holds_writer_lock
        assert database.loop is None
    with open_database(state, lock=False, read_only=True) as database:
        assert database.lock_mode is None and not database.holds_writer_lock


def test_shared_opens_need_a_loop_and_exclusive_ones_refuse_one(state):
    with pytest.raises(ValueError):
        open_database(state, mode="shared")
    with pytest.raises(ValueError):
        open_database(state, loop="derive")
    with pytest.raises(ValueError):
        open_database(state, mode="shared", loop="../x")


def test_heavy_work_excludes_heavy_ticks_and_waits_for_them(state):
    derive = rwlock.heavy_shared(state)
    with pytest.raises(rwlock.LockBusy) as refused:
        rwlock.heavy_exclusive(state, timeout=0)
    assert refused.value.reason == "heavy work running"
    derive.release()
    release = rwlock.heavy_exclusive(state, timeout=0)
    with pytest.raises(rwlock.LockBusy) as deferred:
        rwlock.heavy_shared(state)
    assert deferred.value.reason == "heavy work pending"
    release.release()
    rwlock.heavy_shared(state).release()


def test_lock_files_are_never_state():
    for name in (
        "state.lock",
        "state.intent",
        "heavy.lock",
        "heavy.intent",
        "control.lock",
        "venv.lock",
        "loop-derive.lock",
    ):
        assert rwlock.is_lock_file(name)
    for name in ("state.sqlite", "loop-.lock", "RESTORE_PENDING", "loop-derive.lock.tmp"):
        assert not rwlock.is_lock_file(name)


def test_a_checkpoint_copies_no_lock_file(state, tmp_path_factory):
    from swingset.backup.checkpoint import create_checkpoint

    rwlock.heavy_shared(state).release()
    with open_database(state, mode="shared", loop="derive"):
        pass
    with open_database(state) as database:
        checkpoint = create_checkpoint(
            state,
            database.connection,
            tmp_path_factory.mktemp("copy") / "checkpoint",
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
    assert (state / "loop-derive.lock").exists() and (state / "state.intent").exists()
    assert not [name for name in checkpoint.files if rwlock.is_lock_file(Path(name).name)]
