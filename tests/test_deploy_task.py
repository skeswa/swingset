import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from swingset.deploy import health


def _ready(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            pytest.fail("child did not reach its ready point")
        time.sleep(0.01)


def test_exec_preserves_owner_pid_and_private_tmpdir_until_the_child_really_exits(tmp_path):
    tasks = tmp_path / "tasks"
    ready = tmp_path / "ready.json"
    program = """import os,json,tempfile,sys
from pathlib import Path
directory=Path(tempfile.gettempdir())
owner=json.loads((directory/'owner.json').read_text())
with tempfile.NamedTemporaryFile(delete=False) as handle:
 handle.write(b'disposable')
 temporary=handle.name
target=Path(sys.argv[1]);pending=target.with_suffix('.tmp')
pending.write_text(json.dumps({'pid':os.getpid(),'pgrp':os.getpgrp(),'tmpdir':str(directory),'temporary':temporary,'owner':owner}));pending.replace(target)
sys.stdin.read(1)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swingset.deploy.task",
            "--workspace",
            str(tasks),
            "--",
            "-c",
            program,
            str(ready),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _ready(ready, process)
        record = json.loads(ready.read_text())
        directory = Path(record["tmpdir"])
        assert record["pid"] == record["pgrp"] == record["owner"]["pid"] == process.pid
        assert directory.parent == tasks
        assert directory.stat().st_mode & 0o777 == 0o700
        assert Path(record["temporary"]).parent == directory
        assert record["owner"]["root"] == str(tasks.resolve())
        assert health.cleanup_task_directories(tasks, older_than=0) == []
        process.communicate("x", timeout=5)
        assert process.returncode == 0
        assert health.cleanup_task_directories(tasks, older_than=0) == [directory.name]
        assert not directory.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_exec_propagates_the_real_command_exit_status_without_adopting_other_files(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    unrelated = tasks / "operator-note"
    unrelated.write_text("keep")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "swingset.deploy.task",
            "--workspace",
            str(tasks),
            "--",
            "-c",
            "import os;os._exit(23)",
        ],
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    assert result.returncode == 23
    registered = [path for path in tasks.iterdir() if path.is_dir()]
    assert len(registered) == 1
    owner = json.loads((registered[0] / "owner.json").read_text())
    assert owner["pid"] != os.getpid()
    assert health.cleanup_task_directories(tasks, older_than=0) == [registered[0].name]
    assert unrelated.read_text() == "keep"


def test_invalid_wrapper_invocation_creates_no_workspace(tmp_path):
    tasks = tmp_path / "tasks"
    result = subprocess.run(
        [sys.executable, "-m", "swingset.deploy.task", "--workspace", str(tasks), "--"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not tasks.exists()


def test_dead_workspace_owner_with_live_group_peer_is_preserved_until_peer_is_reaped(tmp_path):
    tasks = tmp_path / "tasks"
    ready = tmp_path / "owner-ready.json"
    peer_ready = tmp_path / "peer-ready"
    program = """import json,sys,tempfile
from pathlib import Path
target=Path(sys.argv[1]);pending=target.with_suffix('.tmp')
pending.write_text(json.dumps({'directory':tempfile.gettempdir()}));pending.replace(target)
sys.stdin.read(1)
"""
    owner = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swingset.deploy.task",
            "--workspace",
            str(tasks),
            "--",
            "-c",
            program,
            str(ready),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        process_group=0,
    )
    peer = None
    try:
        _ready(ready, owner)
        directory = Path(json.loads(ready.read_text())["directory"])
        peer = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys;from pathlib import Path;Path(sys.argv[1]).touch();sys.stdin.read(1)",
                str(peer_ready),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            process_group=owner.pid,
        )
        _ready(peer_ready, peer)
        assert os.getpgid(peer.pid) == owner.pid
        owner.communicate("x", timeout=5)
        assert owner.returncode == 0 and peer.poll() is None
        assert health.cleanup_task_directories(tasks, older_than=0) == []
        assert directory.exists()
        os.killpg(owner.pid, signal.SIGTERM)
        peer.communicate(timeout=5)
        assert peer.returncode == -signal.SIGTERM
        assert health.cleanup_task_directories(tasks, older_than=0) == [directory.name]
    finally:
        if owner.poll() is None or peer is not None and peer.poll() is None:
            try:
                os.killpg(owner.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        owner.communicate(timeout=5)
        if peer is not None:
            peer.communicate(timeout=5)


def test_held_scheduler_never_launches_a_wrapper_or_touches_fresh_state(tmp_path):
    state, runtime, holds = (tmp_path / name for name in ("fresh-state", "runtime", "holds"))
    holds.mkdir()
    marker = tmp_path / "must-not-exist"
    program = """import sys
from pathlib import Path
from swingset.deploy import scheduler
probe=scheduler.Task('held-probe',('-c',"from pathlib import Path;Path("+repr(sys.argv[4])+").touch()"),60,0,1,1)
scheduler.tasks=lambda *_args:(probe,)
scheduler.run('derive',Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(state), str(runtime), str(holds), str(marker)],
        env={**os.environ, "SWINGSET_OOB_HOLD": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _ready(runtime / "tick-health.json", process)
        assert json.loads((runtime / "tick.json").read_text())["held"] is True
        assert not state.exists() and not marker.exists() and not (runtime / "tasks").exists()
        process.terminate()
        process.communicate(timeout=5)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
