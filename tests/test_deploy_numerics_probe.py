"""The numerical supervisor can affect only its newly allocated sandbox."""

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from swingset.deploy import numerics_probe as probe


@pytest.fixture
def job(tmp_path, monkeypatch):
    value = object.__new__(probe._Probe)
    value.directory = tmp_path
    value.started = time.monotonic()
    value.phase = "main"
    value.cid = None
    value.policy_raw = b"policy"
    value.review_raw = b"review"
    value.policy = {
        "nonce": "123456abcdef",
        "container_name": "swingset-pure-numerics-123456abcdef",
        "runtime_image": "sha256:image",
        "runtime_source_revision": "source",
        "labels": {"io.swingset.probe": "123456abcdef"},
        "old_graph_container": "old",
        "old_graph_started_at": "old-start",
        "command": ["/probe/numerics.py", "--input", "/probe/input.json"],
        "module_sha256": probe.sha(b"module"),
        "input_sha256": probe.sha(b"input"),
        "normalization_sha256": "norm",
        "subjects": {str(i): i for i in range(1, 6)},
        "create_argv": [
            "docker",
            "create",
            "--pull=never",
            "--log-driver=local",
            "--log-opt=max-size=1m",
            "--log-opt=max-file=2",
            "--log-opt=compress=false",
        ],
    }
    value.result = {"complete": False}
    monkeypatch.setattr(signal, "setitimer", lambda *args: (0, 0))
    original_read = probe.read

    def read(path, *args, **kwargs):
        if path == tmp_path / "numerics.py":
            return b"module"
        if path == tmp_path / "input.json":
            return b"input"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(probe, "read", read)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            "boot" if str(path) == "/proc/sys/kernel/random/boot_id" else original(path, *a, **kw)
        ),
    )
    return value


def cold_state(job):
    return {
        "Id": "a" * 64,
        "Image": job.policy["runtime_image"],
        "Name": "/" + job.policy["container_name"],
        "RestartCount": 0,
        "State": {
            "Status": "created",
            "Running": False,
            "Paused": False,
            "Pid": 0,
            "ExitCode": 0,
            "OOMKilled": False,
            "StartedAt": "zero",
            "FinishedAt": "zero",
        },
        "Config": {
            "User": "10001:10001",
            "Entrypoint": ["/app/.venv/bin/python"],
            "Cmd": job.policy["command"],
            "Healthcheck": {"Test": ["NONE"]},
            "Labels": job.policy["labels"],
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapAdd": None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 134217728,
            "MemorySwap": 134217728,
            "NanoCpus": 1000000000,
            "PidsLimit": 32,
            "PidMode": "",
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "AutoRemove": False,
            "RestartPolicy": {"Name": "no"},
            "LogConfig": copy.deepcopy(probe.LOG_CONFIG),
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(job.directory / name),
                "Destination": "/probe/" + name,
                "RW": False,
            }
            for name in ["numerics.py", "input.json"]
        ],
    }


def mock_lifecycle(job, monkeypatch, fail_start=None, wrong_owner=False, cleanup_unknown=False):
    calls = []
    state = cold_state(job)

    def command(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return json.dumps({"id": job.policy["runtime_image"], "source": "source"}).encode()
        if argv[1] == "ps":
            return b""
        if argv[1] == "create":
            return ("a" * 64).encode()
        if argv[1] == "start":
            state["State"]["Running"] = True
            state["State"]["Pid"] = 345
            if fail_start:
                raise fail_start("interrupted start")
            state["State"]["Running"] = False
            state["State"]["Pid"] = 0
            return b""
        if argv[1] == "kill":
            state["State"]["Running"] = False
            state["State"]["Pid"] = 0
            return b""
        if argv[1] == "logs":
            return json.dumps(
                {
                    "format": "swingset-pure-link-numerics-v1",
                    "input_sha256": job.policy["input_sha256"],
                    "command_sha256": job.policy["module_sha256"],
                    "normalization_sha256": "norm",
                    "pairs": [
                        {
                            "subject_kind": row["subject_kind"],
                            "subject_id": row["subject_id"],
                            "dancer_id": row["candidate_wsdc_id"],
                        }
                        for row in job.policy["expected_pairs"]
                    ]
                    if "expected_pairs" in job.policy
                    else [
                        {"subject_id": k, "dancer_id": v} for k, v in job.policy["subjects"].items()
                    ],
                }
            ).encode()
        if argv[1] == "inspect":
            if argv[3] == probe.OWNED_FORMAT:
                if job.phase == "cleanup" and cleanup_unknown:
                    raise TimeoutError("daemon unavailable")
                response = copy.deepcopy(state)
                if wrong_owner:
                    response["Id"] = "b" * 64
                return json.dumps(response).encode()
            return b"ok"
        pytest.fail(str(argv))

    monkeypatch.setattr(job, "command", command)
    monkeypatch.setattr(job, "guards", lambda initial=False: None)
    monkeypatch.setattr(
        job,
        "public",
        lambda cid: {
            "id": "old",
            "image": job.policy["runtime_image"],
            "running": False,
            "pid": 0,
            "oom": False,
            "restart": 0,
            "started_at": "old-start",
        },
    )
    monkeypatch.setattr(job, "env_category", lambda cid: {"category": "absent", "duplicate": False})
    return calls


def test_success_retains_stopped_owned_container_and_durable_once_fence(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch)
    result = job.execute()
    assert result["complete"] and result["stop_confirmation"] == "confirmed_stopped"
    assert result["cold_inspection"]["host_config"]["LogConfig"] == probe.LOG_CONFIG
    assert json.loads((job.directory / "result.json").read_bytes()) == json.loads(
        json.dumps(result)
    )
    assert all(c[1] != "rm" for c in calls)
    before = list(calls)
    with pytest.raises(FileExistsError):
        job.execute()
    assert calls == before


def test_guard_failure_never_allocates_or_starts(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch)

    def refuse(initial=False):
        raise AssertionError("protected service changed")

    monkeypatch.setattr(job, "guards", refuse)
    result = job.execute()
    assert not result["complete"] and calls == []
    assert (job.directory / "owner.json").exists()


@pytest.mark.parametrize("failure", [InterruptedError, TimeoutError])
def test_signal_or_deadline_stops_only_exact_owned_container(job, monkeypatch, failure):
    calls = mock_lifecycle(job, monkeypatch, fail_start=failure)
    result = job.execute()
    assert not result["complete"] and result["error_type"] == failure.__name__
    assert result["stop_confirmation"] == "confirmed_stopped"
    assert [c for c in calls if c[1] == "kill"] == [
        ["docker", "kill", "--signal", "KILL", "a" * 64]
    ]


def test_wrong_inspected_cid_never_starts_or_signals(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch, wrong_owner=True)
    result = job.execute()
    assert not result["complete"] and result["stop_confirmation"] == "unknown"
    assert not any(c[1] in {"start", "kill"} for c in calls)


def test_unavailable_cleanup_cannot_report_success(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch, cleanup_unknown=True)
    result = job.execute()
    assert not result["complete"] and result["stop_confirmation"] == "unknown"
    assert result["cleanup_error_type"] == "TimeoutError"
    assert not any(c[1] == "kill" for c in calls)


@pytest.mark.parametrize("change", ["compress", "max-file", "driver", "missing", "duplicate"])
def test_logging_argv_drift_refuses_allocation(job, monkeypatch, change):
    calls = mock_lifecycle(job, monkeypatch)
    args = job.policy["create_argv"]
    if change == "missing":
        args.pop()
    elif change == "duplicate":
        args.append("--log-opt=compress=false")
    else:
        before, after = {
            "compress": ("compress=false", "compress=true"),
            "max-file": ("max-file=2", "max-file=1"),
            "driver": ("driver=local", "driver=json-file"),
        }[change]
        args[:] = [a.replace(before, after) for a in args]
    result = job.execute()
    assert not result["complete"] and not any(c[1] in {"create", "start"} for c in calls)


@pytest.mark.parametrize(
    "field,value", [("Type", "json-file"), ("compress", "true"), ("max-file", "1")]
)
def test_actual_logging_readback_drift_prevents_start(job, monkeypatch, field, value):
    mock_lifecycle(job, monkeypatch)
    state = cold_state(job)
    job.cid = state["Id"]
    if field == "Type":
        state["HostConfig"]["LogConfig"][field] = value
    else:
        state["HostConfig"]["LogConfig"]["Config"][field] = value
    with pytest.raises(AssertionError):
        job.cold(state)


def test_public_projection_uses_requested_protected_container(job, monkeypatch):
    calls = []
    monkeypatch.setattr(job, "command", lambda argv: calls.append(argv) or b"{}")
    job.public("protected")
    assert calls[0][-1] == "protected"


def test_optimized_standalone_refuses_without_access_and_import_is_inert(tmp_path):
    result = subprocess.run(
        [sys.executable, "-O", probe.__file__, "--directory", str(tmp_path), "--review", "missing"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"complete": False, "error_type": "RuntimeError"}
    assert list(tmp_path.iterdir()) == []
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from swingset.deploy import numerics_probe; assert 'psycopg' not in sys.modules; assert 'rapidfuzz' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("failure", [False, True])
def test_run_restores_caller_signals_timer_and_umask(monkeypatch, tmp_path, failure):
    handlers = {s: signal.getsignal(s) for s in [signal.SIGTERM, signal.SIGALRM]}
    old_umask = os.umask(0o027)
    old_timer = signal.setitimer(signal.ITIMER_REAL, 60)

    class Job:
        def __init__(self, directory, review):
            pass

        def execute(self):
            if failure:
                raise ValueError("validation failed")
            return {"complete": False}

    monkeypatch.setattr(probe, "_Probe", Job)
    try:
        if failure:
            with pytest.raises(ValueError):
                probe.run(tmp_path, tmp_path / "review.json")
        else:
            assert not probe.run(tmp_path, tmp_path / "review.json")["complete"]
        assert all(signal.getsignal(s) == h for s, h in handlers.items())
        assert 55 < signal.getitimer(signal.ITIMER_REAL)[0] <= 60
        assert os.umask(0o027) == 0o027
    finally:
        signal.setitimer(signal.ITIMER_REAL, *old_timer)
        os.umask(old_umask)


def test_overall_deadline_bounds_every_subprocess_and_refuses_after_expiry(job, monkeypatch):
    calls = []
    clock = [job.started + 89.5]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: (
            calls.append(kwargs) or subprocess.CompletedProcess(argv, 0, b"", b"")
        ),
    )
    job.command(["docker", "inspect", "owned"])
    assert calls[-1]["timeout"] == 0.5
    job.phase = "cleanup"
    clock[0] = job.started + 119.5
    job.command(["docker", "inspect", "owned"])
    assert calls[-1]["timeout"] == 0.5
    clock[0] = job.started + 120
    with pytest.raises(TimeoutError):
        job.command(["docker", "inspect", "owned"])
    assert len(calls) == 2


def test_exited_after_compute_deadline_cannot_pass(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch)
    command = job.command
    clock = [job.started]
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])

    def delayed(argv, **kwargs):
        output = command(argv, **kwargs)
        if argv[1] == "start":
            clock[0] += 10
        return output

    monkeypatch.setattr(job, "command", delayed)
    result = job.execute()
    assert not result["complete"] and result["error_type"] == "TimeoutError"
    assert result["stop_confirmation"] == "confirmed_stopped"
    assert not any(c[1] == "logs" for c in calls)


def test_installed_term_handler_enters_owned_cleanup(job, monkeypatch):
    calls = mock_lifecycle(job, monkeypatch)
    command = job.command

    def interrupted(argv, **kwargs):
        output = command(argv, **kwargs)
        if argv[1] == "start":
            signal.raise_signal(signal.SIGTERM)
        return output

    monkeypatch.setattr(job, "command", interrupted)
    monkeypatch.setattr(probe, "_Probe", lambda directory, review: job)
    result = probe.run(job.directory, job.directory / "review.json")
    assert not result["complete"] and result["error_type"] == "InterruptedError"
    assert result["stop_confirmation"] == "confirmed_stopped"
    assert not any(c[1] == "logs" for c in calls)


def test_explicit_policy_validates_all_204_tuples_without_collapsing_subjects(job):
    job.policy.pop("subjects")
    rows = [
        {
            "subject_kind": "entry" if i < 203 else "judge",
            "subject_id": str(i // 2),
            "candidate_wsdc_id": i + 1,
        }
        for i in range(204)
    ]
    job.policy.update(expected_pairs=rows, expected_pair_count=204)
    output = {
        "pairs": [
            {
                "subject_kind": row["subject_kind"],
                "subject_id": row["subject_id"],
                "dancer_id": row["candidate_wsdc_id"],
            }
            for row in reversed(rows)
        ]
    }
    job.validate_pairs(output)
    assert len(job.expected_pairs()) == 204
    assert output["pairs"][-1]["subject_id"] == output["pairs"][-2]["subject_id"]
    output["pairs"].pop()
    with pytest.raises(AssertionError):
        job.validate_pairs(output)


@pytest.mark.parametrize(
    "damage", ["duplicate", "count", "extra", "wrong_kind", "missing_kind", "lossy_map"]
)
def test_explicit_tuple_result_rejects_ambiguous_or_different_pairs(job, damage):
    job.policy.pop("subjects")
    job.policy.update(
        expected_pairs=[
            {"subject_kind": "entry", "subject_id": "same", "candidate_wsdc_id": 1},
            {"subject_kind": "entry", "subject_id": "same", "candidate_wsdc_id": 2},
        ],
        expected_pair_count=2,
    )
    rows = [
        {"subject_kind": "entry", "subject_id": "same", "dancer_id": 1},
        {"subject_kind": "entry", "subject_id": "same", "dancer_id": 2},
    ]
    if damage == "duplicate":
        rows[1] = rows[0]
    elif damage == "count":
        job.policy["expected_pair_count"] = 3
    elif damage == "extra":
        rows.append(dict(rows[0], dancer_id=3))
    elif damage == "wrong_kind":
        rows[0]["subject_kind"] = "judge"
    elif damage == "missing_kind":
        rows[0].pop("subject_kind")
    else:
        rows = rows[:1]
    with pytest.raises((AssertionError, KeyError)):
        job.validate_pairs({"pairs": rows})


@pytest.mark.parametrize("damage", ["duplicate", "bound", "kind", "ambiguous_mode"])
def test_invalid_pair_policy_never_allocates_a_container(job, monkeypatch, damage):
    old = job.policy.pop("subjects")
    rows = [{"subject_kind": "entry", "subject_id": "one", "candidate_wsdc_id": 1}]
    if damage == "duplicate":
        rows *= 2
    elif damage == "bound":
        rows *= 257
    elif damage == "kind":
        rows[0]["subject_kind"] = "unsupported"
    else:
        job.policy["subjects"] = old
    job.policy.update(expected_pairs=rows, expected_pair_count=len(rows))
    calls = mock_lifecycle(job, monkeypatch)
    result = job.execute()
    assert not result["complete"] and not any(call[1] in {"create", "start"} for call in calls)


def test_supervisor_rejects_duplicate_protocol_json_keys():
    with pytest.raises(ValueError, match="Duplicate"):
        probe.strict_json(b'{"pairs":[],"pairs":[]}')


def test_explicit_pair_operation_accepts_repeated_subject_with_multiple_candidates(
    job, monkeypatch
):
    job.policy.pop("subjects")
    rows = [
        {"subject_kind": "entry", "subject_id": "same", "candidate_wsdc_id": 1},
        {"subject_kind": "entry", "subject_id": "same", "candidate_wsdc_id": 2},
        {"subject_kind": "judge", "subject_id": "another", "candidate_wsdc_id": 2},
    ]
    job.policy.update(expected_pairs=rows, expected_pair_count=3)
    calls = mock_lifecycle(job, monkeypatch)
    result = job.execute()
    assert result["complete"] and result["stop_confirmation"] == "confirmed_stopped"
    output = json.loads((job.directory / "probe.json").read_bytes())
    assert len(output["pairs"]) == 3
    assert output["pairs"][0]["subject_id"] == output["pairs"][1]["subject_id"]
    assert output["pairs"][2]["subject_kind"] == "judge"
    assert not any(call[1] == "rm" for call in calls)
