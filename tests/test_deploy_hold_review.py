"""Hold reviews repeat only the frozen administrative command templates."""

import datetime as dt
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.deploy import hold_review as review

NOW = dt.datetime(2026, 11, 11, 19, 30, tzinfo=dt.UTC)


@pytest.fixture
def prior(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "_now", lambda: NOW)
    packet = tmp_path / (review.PREFIX + "20261111-190000")
    packet.mkdir(mode=0o700)
    hold = {
        "owner": "codex-stage2-resume",
        "reason": "Pipeline schedulers remain unstarted",
        "created_at": "2026-11-06T20:46:24+00:00",
        "reviewed_at": "2026-11-11T19:00:00+00:00",
        "review_at": "2026-11-11T20:00:00+00:00",
    }
    review.write(packet / "hold.json", hold)
    oldsha = review.digest(review.read(packet / "hold.json"))
    baseline = [
        {
            "id": f"{i:064x}",
            "running": True,
            "image": "sha256:" + "a" * 64,
            "name": f"/protected-{i}",
            "oom": False,
            "restart": 0,
            "health": None,
        }
        for i in range(12)
    ]
    holdpath = "/var/tmp/swingset-stage2-app-cc89f18f5c31/holds/stage2-import.json"
    commands = review.templates("20261111-190000", packet, holdpath, "sha256:" + "b" * 64)
    admin = {
        "commands": [{"argv": argv, "observed_exit_code": 0} for argv in commands],
        "temp_and_after_sha256": oldsha,
        "final_metadata": "0 444 1",
        "transport": "SSH administrative commands and SCP; exact root helpers retained",
    }
    review.write(packet / "admin-commands.json", admin)
    review.write(
        packet / "admin-plan.json", {"new_sha256": oldsha, "hold": hold, "commands": commands}
    )
    review.write(packet / "live-apps.json", baseline)
    result = {
        "complete": True,
        "hold": hold,
        "after_sha256": oldsha,
        "admin_commands_sha256": review.digest(review.read(packet / "admin-commands.json")),
        "commands": [
            ["sha256sum", holdpath],
            ["stat", "-c", "%u %a %h", holdpath],
            [
                "docker",
                "inspect",
                "--format",
                review.LIVE_FORMAT,
                *[item["id"] for item in baseline],
            ],
        ],
    }
    review.write(packet / "result.json", result)
    current = tmp_path / "current.json"
    review.write(current, {"packet": str(packet), "sha256": oldsha, "review_at": hold["review_at"]})
    return SimpleNamespace(
        current=current,
        packet=packet,
        hold=hold,
        baseline=baseline,
        oldsha=oldsha,
        result=result,
        admin=admin,
        commands=commands,
    )


def helper_profile(prior, name):
    packet = prior.current.parent / (review.PREFIX + "20261111-193000")
    tag = packet.name.removeprefix(review.PREFIX)
    index = 2 if name.startswith("swingset-hold-install-") else 5
    argv = review.templates(tag, packet, prior.result["commands"][0][-1], "sha256:" + "b" * 64)[
        index
    ]
    image_index = argv.index("--entrypoint") + 2
    mounts = [
        {
            "type": "bind",
            "source": str(Path(prior.result["commands"][0][-1]).parent),
            "destination": "/holds",
            "rw": True,
        }
    ]
    if index == 2:
        mounts.append(
            {
                "type": "bind",
                "source": "/var/tmp/" + packet.name + "/hold.json",
                "destination": "/hold.json",
                "rw": False,
            }
        )
    return {
        "id": ("d" if index == 2 else "e") * 64,
        "name": "/" + name,
        "image": "sha256:" + "b" * 64,
        "created": "2026-11-11T19:30:00+00:00",
        "started": "2026-11-11T19:30:01+00:00",
        "finished": "2026-11-11T19:30:02+00:00",
        "running": False,
        "status": "exited",
        "pid": 0,
        "exit": 0,
        "oom": False,
        "error_empty": True,
        "restart": 0,
        "user": "0:0",
        "entrypoint": [argv[image_index - 1]],
        "cmd": argv[image_index + 1 :],
        "label": tag,
        "readonly": True,
        "network": "none",
        "privileged": False,
        "drop": ["ALL"],
        "add": None,
        "security": ["no-new-privileges"],
        "memory": 64 << 20,
        "swap": 64 << 20,
        "cpus": 10**9,
        "pids": 32,
        "log": {
            "Type": "local",
            "Config": {"max-size": "1m", "max-file": "1", "compress": "false"},
        },
        "mounts": mounts,
    }


def transport(prior, monkeypatch, problem=None):
    calls = []
    state = {"installed": False, "newsha": None}

    def command(argv, *, remote=True):
        calls.append((argv, remote))
        if argv[:2] == ["docker", "inspect"]:
            if argv[3] == review.HELPER_FORMAT:
                return json.dumps(helper_profile(prior, argv[-1]))
            baseline = prior.baseline if problem != "live_drift" else prior.baseline[:-1]
            return "\n".join(json.dumps(item) for item in baseline)
        if argv == ["id", "-u"]:
            return "0\n"
        if argv[0] == "stat":
            if argv[2] == "%F|%u|%a|%h|%s":
                packet = prior.current.parent / (review.PREFIX + "20261111-193000")
                return "directory|0|700|2|4096\n" + "".join(
                    "regular file|0|600|1|" + str((packet / Path(name).name).stat().st_size) + "\n"
                    for name in argv[4:]
                )
            return "0 644 1" if problem == "metadata" else "0 444 1"
        if argv[0] == "sha256sum":
            if len(argv) == 4:
                packet = prior.current.parent / (review.PREFIX + "20261111-193000")
                return "".join(
                    review.digest(review.read(packet / Path(name).name)) + "  " + name + "\n"
                    for name in argv[1:]
                )
            if len(argv) == 3:
                return f"{state['newsha']} temp\n{prior.oldsha} old\n"
            sha = state["newsha"] if state["installed"] else prior.oldsha
            return ("f" * 64 if problem == "old_sha" else sha) + " hold\n"
        if argv[0] == "scp" and len(argv) == 4:
            assert remote is False
            state["newsha"] = review.digest(review.read(Path(argv[2])))
        if argv[0] == "docker" and "/usr/bin/mv" in argv:
            state["installed"] = True
        if problem == "receipt_copy" and argv[0] == "scp" and len(argv) > 4:
            raise RuntimeError("receipt copy failed")
        return ""

    monkeypatch.setattr(review, "command", command)
    return calls


def test_review_preserves_identity_and_exact_six_commands_with_only_new_times(prior, monkeypatch):
    calls = transport(prior, monkeypatch)
    result = review._run(prior.current, NOW)
    packet = Path(result["packet"])
    hold = json.loads(review.read(packet / "hold.json"))
    assert {
        key: value for key, value in hold.items() if key not in {"review_at", "reviewed_at"}
    } == {
        key: value for key, value in prior.hold.items() if key not in {"review_at", "reviewed_at"}
    }
    assert hold["reviewed_at"] == NOW.isoformat()
    assert hold["review_at"] == (NOW + dt.timedelta(hours=1)).isoformat()
    expected = review.templates(
        "20261111-193000", packet, prior.result["commands"][0][-1], "sha256:" + "b" * 64
    )
    assert [
        (argv, remote)
        for argv, remote in calls
        if argv in (expected[0], expected[1], expected[2], expected[5])
    ] == [(expected[index], index != 1) for index in (0, 1, 2, 5)]
    recorded = json.loads(review.read(packet / "admin-commands.json"))["commands"]
    assert [item["argv"] for item in recorded] == expected
    current = json.loads(review.read(prior.current))
    assert current["sha256"] == result["sha256"] == review.digest(review.read(packet / "hold.json"))
    assert json.loads(review.read(packet / "result.json"))["hold"] == hold
    assert stat.S_IMODE(packet.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == (0o644 if path.name == "hold.json" else 0o600)
        for path in packet.iterdir()
    )
    # Replaying the same instant refuses before any command or new packet.
    count = len(calls)
    with pytest.raises(ValueError):
        review._run(prior.current, NOW)
    assert len(calls) == count


@pytest.mark.parametrize(
    "problem",
    [
        "live_drift",
        "old_sha",
        "metadata",
        "expired",
        "stale_pointer",
        "admin_hash",
        "command_injection",
        "changed_identity",
    ],
)
def test_stale_or_changed_review_refuses_before_any_install(prior, monkeypatch, problem):
    calls = transport(prior, monkeypatch, problem)
    now = NOW
    if problem == "expired":
        now = NOW + dt.timedelta(hours=1)
    elif problem == "stale_pointer":
        pointer = json.loads(review.read(prior.current))
        pointer["sha256"] = "c" * 64
        prior.current.write_text(json.dumps(pointer))
    elif problem in {"admin_hash", "command_injection"}:
        prior.admin["commands"][2]["argv"].append("; touch /foreign")
        (prior.packet / "admin-commands.json").write_text(json.dumps(prior.admin))
        if problem == "command_injection":
            prior.result["admin_commands_sha256"] = review.digest(
                review.read(prior.packet / "admin-commands.json")
            )
            (prior.packet / "result.json").write_text(json.dumps(prior.result))
    elif problem == "changed_identity":
        prior.result["hold"]["owner"] = "foreign-owner"
        (prior.packet / "result.json").write_text(json.dumps(prior.result))
    before = review.read(prior.current)
    with pytest.raises(ValueError):
        review._run(prior.current, now)
    assert review.read(prior.current) == before
    assert not any(argv[:2] == ["docker", "run"] for argv, _ in calls)
    assert not (prior.current.parent / (review.PREFIX + "20261111-193000")).exists()


def test_failed_receipt_copy_retains_packet_and_never_advances_current(prior, monkeypatch):
    before = review.read(prior.current)
    transport(prior, monkeypatch, "receipt_copy")
    with pytest.raises(RuntimeError, match="receipt copy"):
        review._run(prior.current, NOW)
    assert review.read(prior.current) == before
    packet = prior.current.parent / (review.PREFIX + "20261111-193000")
    assert (packet / "result.json").exists() and (packet / "admin-plan.json").exists()


def test_transport_quotes_frozen_remote_arguments_and_does_not_print_errors(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="sensitive error")

    monkeypatch.setattr(review.subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        review.command(["stat", "-c", "%u %a %h", "/reviewed/hold"])
    assert calls == [["ssh", "rhuidean", "stat -c '%u %a %h' /reviewed/hold"]]
    assert "sensitive" not in str(error.value)


def test_cli_dispatch_uses_maintained_hold_review(monkeypatch, tmp_path):
    from swingset.cli import main

    calls = []
    monkeypatch.setattr(review, "main", lambda argv: calls.append(argv) or 1)
    assert main(["review-rehearsal-hold", "--current", str(tmp_path / "current")]) == 1
    assert calls == [["--current", str(tmp_path / "current")]]


def interrupted(prior, monkeypatch, index):
    """A timed-out SSH command whose already-dispatched helper may finish later."""
    import subprocess

    calls = transport(prior, monkeypatch)
    normal = review.command
    packet = prior.current.parent / (review.PREFIX + "20261111-193000")
    expected = review.templates(
        "20261111-193000", packet, prior.result["commands"][0][-1], "sha256:" + "b" * 64
    )
    state = SimpleNamespace(calls=calls, packet=packet, helper="running", mutation=None, late=False)

    def command(argv, *, remote=True):
        if argv == expected[index]:
            calls.append((argv, remote))
            # The durable fence exists before dispatch, including when SSH
            # never supplies a result. Its absence must never be inferred later.
            assert (
                json.loads(review.read(packet / f"step-{index:02d}.intent.json"))["index"] == index
            )
            raise subprocess.TimeoutExpired(
                ["ssh", review.HOST], 30, output="private partial output"
            )
        if argv[:2] == ["docker", "inspect"] and argv[3] == review.HELPER_FORMAT:
            helper_index = 2 if argv[-1].startswith("swingset-hold-install-") else 5
            if helper_index == index:
                if state.helper == "absent":
                    calls.append((argv, remote))
                    raise RuntimeError("No owned helper terminal evidence")
                value = json.loads(normal(argv, remote=remote))
                if state.helper == "running":
                    value.update(running=True, status="running", pid=123)
                if state.late:
                    value["finished"] = "2026-11-11T20:01:00+00:00"
                if state.mutation:
                    state.mutation(value)
                return json.dumps(value)
        if index == 5 and state.helper == "terminal" and argv[0] == "sha256sum" and len(argv) == 2:
            calls.append((argv, remote))
            return review.digest(review.read(packet / "hold.json")) + " hold\n"
        return normal(argv, remote=remote)

    monkeypatch.setattr(review, "command", command)
    return state


def continuation(state, monkeypatch=None, *, name="review.json"):
    observed = review.status(state.current, state.packet)
    document = {
        key: observed[key]
        for key in (
            "status_path",
            "status_sha256",
            "plan_sha256",
            "pointer_sha256",
            "journal_sha256",
        )
    }
    document.update(
        verdict="ACCEPT_HOLD_REMAINING_ONLY",
        packet=str(state.packet),
        authorized_remaining_indices=list(range(observed["next_index"], 7)),
    )
    if observed.get("operation") == "reopen_expired":
        document.update(
            {
                key: observed[key]
                for key in ("operation", "reopen_review_sha256", "admission_sha256")
            }
        )
    path = state.packet / name
    review.write(path, document)
    return path, review.digest(review.read(path)), observed


def test_timeout_is_durable_unknown_then_terminal_proof_allows_remaining_only(prior, monkeypatch):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    state.current = prior.current
    before = review.read(prior.current)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    assert review.read(prior.current) == before
    outcome = json.loads(review.read(state.packet / "step-02.transport.json"))
    assert outcome == {"transport_error_type": "TimeoutExpired", "transport_exit_code": None}
    assert (
        "private partial output"
        not in review.read(state.packet / "step-02.transport.json").decode()
    )
    pending = review.status(prior.current, state.packet)
    assert not pending["continuation_allowed"] and pending["states"][2] == "unknown"
    state.helper = "terminal"
    path, sha, observed = continuation(state)
    assert observed["next_index"] == 3
    result = review.resume(prior.current, state.packet, path, sha)
    assert result["sha256"] != prior.oldsha
    attempts = [
        argv
        for argv, _ in state.calls
        if argv[:2] == ["docker", "run"] and "/usr/bin/install" in argv
    ]
    assert len(attempts) == 1
    recorded = json.loads(review.read(state.packet / "admin-commands.json"))["commands"][2]
    assert (
        recorded["transport_exit_code"] is None
        and recorded["transport_error_type"] == "TimeoutExpired"
    )
    assert (
        recorded["observed_exit_code"] == 0
        and recorded["observed_exit_code_basis"] == "exact owned helper terminal exit"
    )
    before = len(state.calls)
    with pytest.raises(ValueError):
        review.resume(prior.current, state.packet, path, sha)
    assert len(state.calls) == before


@pytest.mark.parametrize("helper", ["absent", "running"])
def test_absent_or_running_after_intent_is_never_unattempted(prior, monkeypatch, helper):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = helper
    observed = review.status(prior.current, state.packet)
    assert observed["states"][2] == "unknown" and not observed["continuation_allowed"]
    assert observed["states"][3:] == ["unattempted"] * 4
    assert sum(argv[:2] == ["docker", "run"] for argv, _ in state.calls) == 1


def test_late_rename_is_finalized_without_replay_and_records_expiry_gap(prior, monkeypatch):
    import subprocess

    state = interrupted(prior, monkeypatch, 5)
    state.current = prior.current
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper, state.late = "terminal", True
    monkeypatch.setattr(review, "_now", lambda: NOW + dt.timedelta(minutes=32))
    path, sha, observed = continuation(state)
    assert observed["next_index"] == 6 and observed["active_fresh"]
    review.resume(prior.current, state.packet, path, sha)
    result = json.loads(review.read(state.packet / "result.json"))
    assert not result["review_expiry_gap"]["continuous_validity_proven"]
    assert (
        sum(argv[:2] == ["docker", "run"] and "/usr/bin/mv" in argv for argv, _ in state.calls) == 1
    )
    assert result["hold"]["review_at"] == (NOW + dt.timedelta(hours=1)).isoformat()


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "foreign"),
        ("image", "sha256:" + "f" * 64),
        ("pid", 99),
        ("oom", True),
        ("network", "host"),
        ("memory", 128 << 20),
        ("mounts", []),
        ("cmd", ["foreign"]),
        ("label", "foreign"),
    ],
)
def test_only_exact_owned_terminal_profile_can_reconcile(prior, monkeypatch, field, value):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = "terminal"
    state.mutation = lambda profile: profile.update({field: value})
    observed = review.status(prior.current, state.packet)
    assert not observed["continuation_allowed"] and observed["states"][2] == "unknown"
    assert not (state.packet / "step-02.terminal.json").exists()
    assert "Config.Env" not in review.HELPER_FORMAT


@pytest.mark.parametrize(
    "mutation", ["status", "plan", "pointer", "journal", "expired", "replay_index"]
)
def test_continuation_is_bound_to_exact_reviewed_state(prior, monkeypatch, mutation):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    state.current = prior.current
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = "terminal"
    path, _, observed = continuation(state)
    document = json.loads(review.read(path))
    if mutation in {"status", "plan", "pointer", "journal"}:
        document[mutation + "_sha256"] = "f" * 64
    elif mutation == "replay_index":
        document["authorized_remaining_indices"] = list(range(2, 7))
    else:
        monkeypatch.setattr(review, "_now", lambda: NOW + dt.timedelta(hours=1))
    path.write_bytes(review._encoded(document))
    before = sum(argv[:2] == ["docker", "run"] for argv, _ in state.calls)
    with pytest.raises(ValueError):
        review.resume(prior.current, state.packet, path, review.digest(review.read(path)))
    assert sum(argv[:2] == ["docker", "run"] for argv, _ in state.calls) == before
    assert not list(state.packet.glob("continuation-*.json"))


def test_durable_intent_file_and_directory_sync_precede_effect(prior, monkeypatch):
    import os

    events = []
    original_sync = review.os.fsync
    calls = transport(prior, monkeypatch)
    normal = review.command

    def sync(descriptor):
        original_sync(descriptor)
        events.append("directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file")

    def command(argv, *, remote=True):
        if argv[0] in {"mkdir", "scp"} or argv[:2] == ["docker", "run"]:
            assert events[-2:] == ["file", "directory"]
        return normal(argv, remote=remote)

    monkeypatch.setattr(review.os, "fsync", sync)
    monkeypatch.setattr(review, "command", command)
    review._run(prior.current, NOW)
    assert calls


def test_missing_outcome_or_orphan_record_does_not_authorize_replay(prior, monkeypatch):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    (state.packet / "step-02.transport.json").unlink()
    state.helper = "absent"
    observed = review.status(prior.current, state.packet)
    assert not observed["continuation_allowed"] and observed["states"][2] == "unknown"
    # A surviving outcome without its durable intent is corruption, not an
    # unattempted slot. It cannot become a new dispatch opportunity.
    review.write(
        state.packet / "step-03.transport.json",
        {"transport_exit_code": 0, "transport_error_type": None, "stdout": ""},
    )
    observed = review.status(prior.current, state.packet)
    assert observed["error_type"] == "ValueError" and not observed["continuation_allowed"]


def test_status_reads_closed_legacy_but_never_resumes_missing_historical_intents(
    prior, monkeypatch
):
    calls = transport(prior, monkeypatch)
    observed = review.status(prior.current, prior.packet)
    assert (
        observed["complete"]
        and observed["legacy_or_closed"]
        and not observed["continuation_allowed"]
    )
    assert all(argv[:2] != ["docker", "run"] and argv[0] != "scp" for argv, _ in calls)
    incomplete = prior.current.parent / (review.PREFIX + "20261111-193000")
    incomplete.mkdir(mode=0o700)
    review.write(incomplete / "admin-plan.json", {"commands": prior.commands})
    with pytest.raises(ValueError):
        review.status(prior.current, incomplete)


def test_public_cli_forwards_fixed_status_and_reviewed_resume_flags(monkeypatch, tmp_path):
    from swingset.cli import main

    calls = []
    monkeypatch.setattr(review, "main", lambda argv: calls.append(argv) or 1)
    current, packet, verdict = (tmp_path / name for name in ("current", "packet", "review"))
    assert main(["review-rehearsal-hold", "--current", str(current), "--status", str(packet)]) == 1
    assert calls[-1] == ["--current", str(current), "--status", str(packet)]
    assert (
        main(
            [
                "review-rehearsal-hold",
                "--current",
                str(current),
                "--resume",
                str(packet),
                "--review",
                str(verdict),
                "--review-sha256",
                "a" * 64,
            ]
        )
        == 1
    )
    assert calls[-1] == [
        "--current",
        str(current),
        "--resume",
        str(packet),
        "--review",
        str(verdict),
        "--review-sha256",
        "a" * 64,
    ]


def test_reconciled_helper_identity_cannot_be_replaced_under_the_same_name(prior, monkeypatch):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = "terminal"
    assert review.status(prior.current, state.packet)["continuation_allowed"]
    original = review.read(state.packet / "step-02.terminal.json")
    state.mutation = lambda profile: profile.update(id="f" * 64)
    observed = review.status(prior.current, state.packet)
    assert not observed["continuation_allowed"] and observed["states"][2] == "unknown"
    assert review.read(state.packet / "step-02.terminal.json") == original


@pytest.mark.parametrize("drift", [None, "rw", "source"])
def test_reviewed_resume_ignores_only_mount_order(prior, monkeypatch, drift):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    state.current = prior.current
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = "terminal"
    state.mutation = lambda profile: profile["mounts"].sort(key=lambda mount: mount["destination"])
    path, sha, observed = continuation(state)
    assert observed["next_index"] == 3
    terminal = review.read(state.packet / "step-02.terminal.json")

    def changed(profile):
        profile["mounts"].sort(key=lambda mount: mount["destination"], reverse=True)
        if drift is not None:
            mount = next(m for m in profile["mounts"] if m["destination"] == "/hold.json")
            mount[drift] = True if drift == "rw" else "/foreign/hold.json"

    state.mutation = changed
    if drift is None:
        assert review.resume(prior.current, state.packet, path, sha)["sha256"] != prior.oldsha
    else:
        before = review.read(prior.current)
        with pytest.raises(ValueError):
            review.resume(prior.current, state.packet, path, sha)
        assert review.read(prior.current) == before
        assert not (state.packet / "step-03.intent.json").exists()
    assert review.read(state.packet / "step-02.terminal.json") == terminal
    assert (
        sum(argv[:2] == ["docker", "run"] and "/usr/bin/install" in argv for argv, _ in state.calls)
        == 1
    )


def test_a_second_uncertain_step_consumes_review_without_replaying_either_helper(
    prior, monkeypatch
):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    state.current = prior.current
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    state.helper = "terminal"
    path, sha, observed = continuation(state)
    normal = review.command

    def command(argv, *, remote=True):
        result = normal(argv, remote=remote)
        if argv[:2] == ["docker", "run"] and "/usr/bin/mv" in argv:
            raise subprocess.TimeoutExpired(["ssh", review.HOST], 30)
        return result

    monkeypatch.setattr(review, "command", command)
    with pytest.raises(subprocess.TimeoutExpired):
        review.resume(prior.current, state.packet, path, sha)
    assert (state.packet / ("continuation-" + sha + ".json")).is_file()
    assert not (state.packet / "step-06.intent.json").exists()
    before = sum(argv[:2] == ["docker", "run"] for argv, _ in state.calls)
    with pytest.raises(ValueError):
        review.resume(prior.current, state.packet, path, sha)
    assert sum(argv[:2] == ["docker", "run"] for argv, _ in state.calls) == before == 2


def test_module_status_cli_reports_unknown_or_expired_as_nonzero(monkeypatch, tmp_path, capsys):
    for outcome in (
        {"continuation_allowed": False, "states": ["unknown"]},
        {"complete": True, "active_fresh": False, "continuation_allowed": False},
    ):
        monkeypatch.setattr(review, "status", lambda *args, outcome=outcome: outcome)
        assert (
            review.main(
                ["--current", str(tmp_path / "current"), "--status", str(tmp_path / "packet")]
            )
            == 1
        )
        assert json.loads(capsys.readouterr().out) == outcome


def test_timed_out_receipt_copy_reconciles_exact_public_bytes_then_pointer_only(prior, monkeypatch):
    import subprocess

    calls = transport(prior, monkeypatch)
    normal = review.command

    def command(argv, *, remote=True):
        output = normal(argv, remote=remote)
        if argv[0] == "scp" and len(argv) > 4:
            raise subprocess.TimeoutExpired(["scp"], 30)
        return output

    monkeypatch.setattr(review, "command", command)
    before = review.read(prior.current)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    assert review.read(prior.current) == before
    state = SimpleNamespace(
        current=prior.current, packet=prior.current.parent / (review.PREFIX + "20261111-193000")
    )
    path, sha, observed = continuation(state)
    assert observed["next_index"] == 7
    assert observed["records"][6]["transport_exit_code"] is None
    assert observed["records"][6]["observed_exit_code"] is None
    assert (
        observed["records"][6]["completion_basis"]
        == "exact remote public receipt bytes and metadata"
    )
    assert review.resume(prior.current, state.packet, path, sha)["sha256"] != prior.oldsha
    assert sum(argv[0] == "scp" and len(argv) > 4 for argv, _ in calls) == 1


@pytest.mark.parametrize("problem", ["partial", "hash", "mode", "symlink", "owner"])
def test_receipt_transport_success_cannot_advance_pointer_without_exact_readback(
    prior, monkeypatch, problem
):
    calls = transport(prior, monkeypatch)
    normal = review.command

    def command(argv, *, remote=True):
        output = normal(argv, remote=remote)
        if argv[0] == "sha256sum" and len(argv) == 4:
            if problem == "partial":
                return "\n".join(output.splitlines()[:-1])
            if problem == "hash":
                return "f" * 64 + output[64:]
        if argv[:3] == ["stat", "-c", "%F|%u|%a|%h|%s"]:
            if problem == "mode":
                return output.replace("regular file|0|600", "regular file|0|644", 1)
            if problem == "symlink":
                return output.replace("regular file", "symbolic link", 1)
            if problem == "owner":
                return output.replace("regular file|0|", "regular file|1000|", 1)
        return output

    monkeypatch.setattr(review, "command", command)
    before = review.read(prior.current)
    with pytest.raises(ValueError):
        review._run(prior.current, NOW)
    assert review.read(prior.current) == before
    packet = prior.current.parent / (review.PREFIX + "20261111-193000")
    observed = review.status(prior.current, packet)
    assert observed["states"][6] == "unknown" and not observed["continuation_allowed"]
    assert not (packet / "step-06.receipts.json").exists()
    assert sum(argv[0] == "scp" and len(argv) > 4 for argv, _ in calls) == 1


def test_bare_renewal_cannot_bypass_an_unresolved_operation_with_a_new_packet(prior, monkeypatch):
    import subprocess

    state = interrupted(prior, monkeypatch, 2)
    with pytest.raises(subprocess.TimeoutExpired):
        review._run(prior.current, NOW)
    before = len(state.calls)
    with pytest.raises(ValueError):
        review._run(prior.current, NOW + dt.timedelta(minutes=1))
    assert len(state.calls) == before
    assert not (prior.current.parent / (review.PREFIX + "20261111-193100")).exists()
    intent = review._operation(prior.current, review.digest(review.read(prior.current)))
    assert json.loads(review.read(intent))["packet"] == str(state.packet)


@pytest.fixture
def expired_prior(prior):
    """The same closed identity, whose last reviewed interval ended 6.5 hours ago."""
    reviewed = NOW - dt.timedelta(hours=7.5)
    old = prior.packet.with_name(review.PREFIX + reviewed.strftime("%Y%m%d-%H%M%S"))
    prior.packet.rename(old)
    prior.packet = old
    prior.hold.update(
        reviewed_at=reviewed.isoformat(), review_at=(reviewed + dt.timedelta(hours=1)).isoformat()
    )
    review.write(old / "hold.json", prior.hold, replace=True)
    prior.oldsha = review.digest(review.read(old / "hold.json"))
    prior.commands = review.templates(
        old.name.removeprefix(review.PREFIX),
        old,
        prior.result["commands"][0][-1],
        "sha256:" + "b" * 64,
    )
    prior.admin.update(
        commands=[{"argv": argv, "observed_exit_code": 0} for argv in prior.commands],
        temp_and_after_sha256=prior.oldsha,
    )
    review.write(old / "admin-commands.json", prior.admin, replace=True)
    review.write(
        old / "admin-plan.json",
        {"new_sha256": prior.oldsha, "hold": prior.hold, "commands": prior.commands},
        replace=True,
    )
    prior.result.update(
        hold=prior.hold,
        after_sha256=prior.oldsha,
        admin_commands_sha256=review.digest(review.read(old / "admin-commands.json")),
    )
    review.write(old / "result.json", prior.result, replace=True)
    review.write(
        prior.current,
        {"packet": str(old), "sha256": prior.oldsha, "review_at": prior.hold["review_at"]},
        replace=True,
    )
    return prior


def reopen_approval(prior):
    prepared = review.prepare_reopen(prior.current)
    packet = Path(prepared["packet"])
    authorization = {
        key: prepared[key]
        for key in (
            "pointer_sha256",
            "plan_sha256",
            "old_sha256",
            "new_sha256",
            "expired_review_at",
            "admission_sha256",
        )
    }
    authorization.update(
        verdict="ACCEPT_EXPIRED_HOLD_REOPEN",
        packet=str(packet),
        acknowledged_expired=True,
        authorized_indices=list(range(7)),
    )
    path = packet / "approval.json"
    review.write(path, authorization)
    return packet, path, review.digest(review.read(path))


def test_expired_default_still_refuses_and_prepare_only_observes(expired_prior, monkeypatch):
    calls = transport(expired_prior, monkeypatch)
    before = review.read(expired_prior.current)
    with pytest.raises(ValueError):
        review.run(expired_prior.current)
    assert calls == []
    prepared = review.prepare_reopen(expired_prior.current)
    checks = expired_prior.result["commands"]
    assert [argv for argv, _ in calls] == [checks[2], checks[0], checks[1]]
    assert prepared["active_fresh"] is False and prepared["closed_prior"] is True
    assert prepared["operation_started"] is False and prepared["remote_mutations"] is False
    assert review.read(expired_prior.current) == before
    assert not review._operation(expired_prior.current, review.digest(before)).exists()
    assert {p.name for p in Path(prepared["packet"]).iterdir()} == {
        "hold.json",
        "admin-plan.json",
        "reopen-admission.json",
    }


def test_reviewed_reopen_preserves_old_gap_and_consumes_before_effects(expired_prior, monkeypatch):
    calls = transport(expired_prior, monkeypatch)
    packet, authorization, sha = reopen_approval(expired_prior)
    prior_bytes = {p.name: p.read_bytes() for p in expired_prior.packet.iterdir()}
    normal = review.command

    def command(argv, *, remote=True):
        if argv[0] in {"mkdir", "scp"} or argv[:2] == ["docker", "run"]:
            copied = json.loads(review.read(packet / "reopen-review.json"))
            assert copied["review_sha256"] == review.digest(copied["review_json"].encode()) == sha
            operation = review._operation(
                expired_prior.current,
                json.loads(review.read(packet / "admin-plan.json"))["before_pointer_sha256"],
            )
            assert json.loads(review.read(operation))["reopen_review_sha256"] == sha
        return normal(argv, remote=remote)

    monkeypatch.setattr(review, "command", command)
    result = review.reopen(expired_prior.current, packet, authorization, sha)
    receipt = json.loads(review.read(packet / "result.json"))
    assert result["sha256"] != expired_prior.oldsha
    assert receipt["review_expiry_gap"]["continuous_validity_proven"] is False
    assert receipt["review_expiry_gap"]["expired_seconds"] >= 6.5 * 3600
    assert receipt["expired_reopen"]["review_sha256"] == sha
    assert all(
        receipt["hold"][key] == expired_prior.hold[key] for key in ("owner", "reason", "created_at")
    )
    assert prior_bytes == {p.name: p.read_bytes() for p in expired_prior.packet.iterdir()}
    before = len(calls)
    with pytest.raises(ValueError):
        review.reopen(expired_prior.current, packet, authorization, sha)
    assert len(calls) == before


@pytest.mark.parametrize(
    "drift",
    [
        "pointer",
        "prior",
        "admission",
        "hold",
        "live",
        "unacknowledged",
        "review_hash",
        "pending",
        "expired_plan",
    ],
)
def test_expired_reopen_refuses_drift_before_consumption(expired_prior, monkeypatch, drift):
    calls = transport(expired_prior, monkeypatch)
    packet, authorization, sha = reopen_approval(expired_prior)
    if drift in {"pointer", "prior", "admission", "hold"}:
        target = {
            "pointer": expired_prior.current,
            "prior": expired_prior.packet / "result.json",
            "admission": packet / "reopen-admission.json",
            "hold": packet / "hold.json",
        }[drift]
        value = json.loads(review.read(target))
        value["unexpected"] = True
        if drift == "prior":
            value["complete"] = False
        review.write(target, value, replace=True)
    elif drift == "live":
        calls = transport(expired_prior, monkeypatch, problem="live_drift")
    elif drift == "unacknowledged":
        value = json.loads(review.read(authorization))
        value["acknowledged_expired"] = False
        review.write(authorization, value, replace=True)
        sha = review.digest(review.read(authorization))
    elif drift == "review_hash":
        sha = "f" * 64
    elif drift == "pending":
        pointer_sha = review.digest(review.read(expired_prior.current))
        review.write(
            review._operation(expired_prior.current, pointer_sha), {"foreign_pending": True}
        )
    else:
        monkeypatch.setattr(review, "_now", lambda: NOW + dt.timedelta(hours=2))
    before = len(calls)
    with pytest.raises((ValueError, KeyError)):
        review.reopen(expired_prior.current, packet, authorization, sha)
    assert not (packet / "reopen-review.json").exists()
    assert not any(packet.glob("step-*.intent.json"))
    assert all(
        argv[0] not in {"mkdir", "scp"} and argv[:2] != ["docker", "run"]
        for argv, _ in calls[before:]
    )


@pytest.mark.parametrize("index", [2, 5])
def test_expired_reopen_unknown_helper_resumes_only_remaining(
    expired_prior, monkeypatch, capsys, index
):
    import subprocess

    from swingset.cli import main as public_main

    state = interrupted(expired_prior, monkeypatch, index)
    state.current = expired_prior.current
    packet, authorization, sha = reopen_approval(expired_prior)
    before = review.read(expired_prior.current)
    with pytest.raises(subprocess.TimeoutExpired):
        review.reopen(expired_prior.current, packet, authorization, sha)
    pending = review.status(expired_prior.current, packet)
    assert pending["states"][index] == "unknown" and not pending["continuation_allowed"]
    assert pending["active_fresh"] is False and review.read(expired_prior.current) == before
    with pytest.raises(ValueError):
        review.reopen(expired_prior.current, packet, authorization, sha)
    state.helper = "terminal"
    status_code = public_main(
        [
            "review-rehearsal-hold",
            "--current",
            str(expired_prior.current),
            "--status",
            str(packet),
        ]
    )
    public_status = json.loads(capsys.readouterr().out)
    assert status_code == (0 if index == 5 else 1)
    assert public_status["active_fresh"] is (index == 5)
    assert public_status["continuation_allowed"] is True
    assert public_status["administrative_window_fresh"] is (index == 2)
    path, review_sha, observed = continuation(state)
    assert observed["active_fresh"] is (index == 5)
    assert observed["operation"] == "reopen_expired" and observed["continuation_allowed"]
    assert json.loads(review.read(path))["authorized_remaining_indices"] == list(
        range(index + 1, 7)
    )
    review.resume(expired_prior.current, packet, path, review_sha)
    recorded = json.loads(review.read(packet / "admin-commands.json"))["commands"][index]
    assert (
        recorded["transport_exit_code"] is None
        and recorded["transport_error_type"] == "TimeoutExpired"
    )
    assert recorded["observed_exit_code"] == 0
    command = json.loads(review.read(packet / "admin-plan.json"))["commands"][index]
    assert sum(argv == command for argv, _ in state.calls) == 1
    assert (
        json.loads(review.read(packet / "result.json"))["review_expiry_gap"][
            "continuous_validity_proven"
        ]
        is False
    )


def test_reopen_unknown_receipt_transfer_uses_proof_for_pointer_only(expired_prior, monkeypatch):
    import subprocess

    calls = transport(expired_prior, monkeypatch)
    normal = review.command
    packet, authorization, sha = reopen_approval(expired_prior)

    def command(argv, *, remote=True):
        output = normal(argv, remote=remote)
        if argv[0] == "scp" and len(argv) > 4:
            raise subprocess.TimeoutExpired(["scp"], 30)
        return output

    monkeypatch.setattr(review, "command", command)
    before = review.read(expired_prior.current)
    with pytest.raises(subprocess.TimeoutExpired):
        review.reopen(expired_prior.current, packet, authorization, sha)
    assert review.read(expired_prior.current) == before
    state = SimpleNamespace(current=expired_prior.current, packet=packet)
    path, review_sha, observed = continuation(state)
    assert observed["states"] == ["complete"] * 7 and observed["next_index"] == 7
    assert observed["records"][6]["transport_exit_code"] is None
    assert json.loads(review.read(path))["authorized_remaining_indices"] == []
    review.resume(expired_prior.current, packet, path, review_sha)
    assert sum(argv[0] == "scp" and len(argv) > 4 for argv, _ in calls) == 1


def test_expired_prepared_window_never_redates_or_replays(expired_prior, monkeypatch):
    import subprocess

    state = interrupted(expired_prior, monkeypatch, 2)
    state.current = expired_prior.current
    packet, authorization, sha = reopen_approval(expired_prior)
    with pytest.raises(subprocess.TimeoutExpired):
        review.reopen(expired_prior.current, packet, authorization, sha)
    state.helper = "terminal"
    path, review_sha, _ = continuation(state)
    before = {p.name: p.read_bytes() for p in packet.glob("step-*.json")}
    monkeypatch.setattr(review, "_now", lambda: NOW + dt.timedelta(hours=2))
    with pytest.raises(ValueError):
        review.resume(expired_prior.current, packet, path, review_sha)
    assert before == {p.name: p.read_bytes() for p in packet.glob("step-*.json")}
    assert not (packet / ("continuation-" + review_sha + ".json")).exists()


def test_public_cli_forwards_explicit_prepare_and_reviewed_reopen(monkeypatch, tmp_path):
    from swingset.cli import main

    calls = []
    monkeypatch.setattr(review, "main", lambda argv: calls.append(argv) or 0)
    current, packet, authorization = (tmp_path / name for name in ("current", "packet", "approval"))
    prefix = ["review-rehearsal-hold", "--current", str(current)]
    assert main(prefix + ["--prepare-reopen"]) == 0
    assert calls[-1] == ["--current", str(current), "--prepare-reopen"]
    suffix = ["--reopen", str(packet), "--review", str(authorization), "--review-sha256", "a" * 64]
    assert main(prefix + suffix) == 0
    assert calls[-1] == ["--current", str(current), *suffix]
