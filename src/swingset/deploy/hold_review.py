"""Review the existing rehearsal hold through its frozen administrative protocol."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

HOST = "rhuidean"
PREFIX = "swingset-stage2-hold-review-"
LIVE_FORMAT = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"oom":{{json .State.OOMKilled}},"restart":{{json .RestartCount}},"health":{{if index .State "Health"}}{{json (index .State "Health" "Status")}}{{else}}null{{end}}}'


def require(condition: Any) -> None:
    if not condition:
        raise ValueError("Rehearsal hold protocol identity or freshness check failed")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path) -> bytes:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid())
    require(info.st_nlink == 1 and not info.st_mode & 0o022 and info.st_size <= 1024 * 1024)
    return path.read_bytes()


def write(path: Path, value: Any, *, replace: bool = False) -> None:
    target = path.with_name(path.name + ".new") if replace else path
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write((json.dumps(value, sort_keys=True) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    if replace:
        os.replace(target, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _TransportFailure(RuntimeError):
    def __init__(self, code: int) -> None:
        self.returncode = code
        super().__init__("Administrative transport failed (exit " + str(code) + ")")


def command(argv: list[str], *, remote: bool = True) -> str:
    actual = ["ssh", HOST, shlex.join(argv)] if remote else argv
    result = subprocess.run(actual, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise _TransportFailure(result.returncode)
    require(len(result.stdout.encode()) <= 1024 * 1024)
    return result.stdout


def templates(tag: str, packet: Path, holdpath: str, image: str) -> list[list[str]]:
    """The six frozen commands; only the reviewed packet timestamp varies."""
    remote = "/var/tmp/" + PREFIX + tag
    temporary = "/holds/.stage2-import-review-" + tag + ".tmp"
    common = [
        "docker",
        "run",
        "--pull=never",
        "--network",
        "none",
        "--read-only",
        "--user",
        "0:0",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "64m",
        "--memory-swap",
        "64m",
        "--cpus",
        "1",
        "--pids-limit",
        "32",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=1m",
        "--log-opt",
        "max-file=1",
        "--log-opt",
        "compress=false",
        "--label",
        "io.swingset.owned-hold=" + tag,
        "--mount",
        "type=bind,src=" + str(Path(holdpath).parent) + ",dst=/holds",
    ]
    return [
        ["mkdir", "-m", "700", remote],
        ["scp", "-q", str(packet / "hold.json"), HOST + ":" + remote + "/hold.json"],
        common
        + [
            "--name",
            "swingset-hold-install-" + tag,
            "--mount",
            "type=bind,src=" + remote + "/hold.json,dst=/hold.json,readonly",
            "--entrypoint",
            "/usr/bin/install",
            image,
            "--mode=0444",
            "--",
            "/hold.json",
            temporary,
        ],
        [
            "sha256sum",
            str(Path(holdpath).parent) + "/.stage2-import-review-" + tag + ".tmp",
            holdpath,
        ],
        [
            "stat",
            "-c",
            "%u %a %h",
            str(Path(holdpath).parent) + "/.stage2-import-review-" + tag + ".tmp",
        ],
        common
        + [
            "--name",
            "swingset-hold-review-" + tag,
            "--entrypoint",
            "/usr/bin/mv",
            image,
            "-T",
            "--",
            temporary,
            "/holds/stage2-import.json",
        ],
    ]


def _prior(current: Path) -> dict[str, Any]:
    pointer_raw = read(current)
    pointer = json.loads(pointer_raw)
    old = Path(pointer["packet"])
    require(old.parent == current.parent and re.fullmatch(PREFIX + r"\d{8}-\d{6}", old.name))
    info = old.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )
    result = json.loads(read(old / "result.json"))
    admin_raw = read(old / "admin-commands.json")
    admin = json.loads(admin_raw)
    plan = json.loads(read(old / "admin-plan.json"))
    baseline = json.loads(read(old / "live-apps.json"))
    require(result["complete"] is True and result["admin_commands_sha256"] == digest(admin_raw))
    require(
        pointer["sha256"]
        == result["after_sha256"]
        == admin["temp_and_after_sha256"]
        == plan["new_sha256"]
    )
    require(re.fullmatch(r"[0-9a-f]{64}", pointer["sha256"]))
    require(result["hold"] == plan["hold"] and pointer["review_at"] == result["hold"]["review_at"])
    require(digest(read(old / "hold.json")) == pointer["sha256"])
    require(json.loads(read(old / "hold.json")) == result["hold"])
    require(len(baseline) == 12 and len({item["id"] for item in baseline}) == 12)
    require(all(re.fullmatch(r"[0-9a-f]{64}", item["id"]) for item in baseline))
    inspect = ["docker", "inspect", "--format", LIVE_FORMAT, *[item["id"] for item in baseline]]
    checks = result["commands"]
    require(len(checks) == 3 and len(checks[0]) == 2)
    holdpath = checks[0][-1]
    require(
        re.fullmatch(
            r"/var/tmp/swingset-stage2-app-[0-9a-f]{12}/holds/stage2-import.json", holdpath
        )
    )
    require(checks == [["sha256sum", holdpath], ["stat", "-c", "%u %a %h", holdpath], inspect])
    observed = admin["commands"]
    require(len(observed) == 6 and all(item["observed_exit_code"] == 0 for item in observed))
    install = observed[2]["argv"]
    image = install[install.index("--entrypoint") + 2]
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image))
    old_commands = templates(old.name.removeprefix(PREFIX), old, holdpath, image)
    require([item["argv"] for item in observed] == old_commands == plan["commands"])
    require(admin["final_metadata"] == "0 444 1")
    hold = result["hold"].copy()
    require(set(hold) == {"owner", "reason", "created_at", "reviewed_at", "review_at"})
    return dict(
        pointer_raw=pointer_raw,
        pointer=pointer,
        baseline=baseline,
        inspect=inspect,
        checks=checks,
        holdpath=holdpath,
        image=image,
        hold=hold,
        result=result,
        admin=admin,
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _live(prior: dict[str, Any], sha: str) -> None:
    require(
        [json.loads(line) for line in command(prior["inspect"]).splitlines()] == prior["baseline"]
    )
    require(command(prior["checks"][0]).split()[0] == sha)
    require(command(prior["checks"][1]).strip() == "0 444 1")


def _new_packet(
    current: Path, prior: dict[str, Any], now: dt.datetime, *, reopen: bool = False
) -> Path:
    hold, pointer = prior["hold"].copy(), prior["pointer"]
    tag = now.strftime("%Y%m%d-%H%M%S")
    packet = current.parent / (PREFIX + tag)
    packet.mkdir(mode=0o700)
    hold.update(reviewed_at=now.isoformat(), review_at=(now + dt.timedelta(hours=1)).isoformat())
    write(packet / "hold.json", hold)
    # The frozen cap-drop=ALL root helper reads this public, bind-mounted file.
    os.chmod(packet / "hold.json", 0o644)
    newsha = digest(read(packet / "hold.json"))
    commands = templates(tag, packet, prior["holdpath"], prior["image"])
    plan: dict[str, Any] = {
        "old_sha256": pointer["sha256"],
        "new_sha256": newsha,
        "commands": commands,
        "hold": hold,
        "journal_format": "swingset-hold-steps-v1",
        "before_pointer_sha256": digest(prior["pointer_raw"]),
    }
    if reopen:
        plan["operation"] = "reopen_expired"
    write(packet / "admin-plan.json", plan)
    return packet


def _run(current: Path, now: dt.datetime) -> dict[str, Any]:
    prior = _prior(current)
    pointer_raw, pointer, hold = prior["pointer_raw"], prior["pointer"], prior["hold"]
    operation = _operation(current, digest(pointer_raw))
    require(not operation.exists())
    require(now.tzinfo is not None and now > dt.datetime.fromisoformat(hold["reviewed_at"]))
    require(dt.datetime.fromisoformat(hold["review_at"]) > now)
    require(now + dt.timedelta(hours=1) > dt.datetime.fromisoformat(hold["review_at"]))
    _live(prior, pointer["sha256"])
    require(read(current) == pointer_raw)
    packet = _new_packet(current, prior, now)
    write(
        operation,
        {
            "packet": str(packet),
            "pointer_sha256": digest(pointer_raw),
            "plan_sha256": digest(read(packet / "admin-plan.json")),
        },
    )
    for index, argv in enumerate(json.loads(read(packet / "admin-plan.json"))["commands"]):
        if index == 5:
            _before_activation(current, packet)
        _step(packet, index, argv)
    return _finalize(current, packet, prior)


JOURNAL = "swingset-hold-steps-v1"
HELPER_FORMAT = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"created":{{json .Created}},"running":{{json .State.Running}},"status":{{json .State.Status}},"pid":{{json .State.Pid}},"exit":{{json .State.ExitCode}},"oom":{{json .State.OOMKilled}},"error_empty":{{json (eq .State.Error "")}},"started":{{json .State.StartedAt}},"finished":{{json .State.FinishedAt}},"restart":{{json .RestartCount}},"user":{{json .Config.User}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"label":{{json (index .Config.Labels "io.swingset.owned-hold")}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"network":{{json .HostConfig.NetworkMode}},"privileged":{{json .HostConfig.Privileged}},"drop":{{json .HostConfig.CapDrop}},"add":{{json .HostConfig.CapAdd}},"security":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},"swap":{{json .HostConfig.MemorySwap}},"cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"log":{{json .HostConfig.LogConfig}},"mounts":[{{range $i,$v := .Mounts}}{{if $i}},{{end}}{"type":{{json $v.Type}},"source":{{json $v.Source}},"destination":{{json $v.Destination}},"rw":{{json $v.RW}}}{{end}}]}'


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def _same(path: Path, value: Any) -> None:
    if path.exists():
        require(read(path) == _encoded(value))
    else:
        write(path, value)


def _receipt_command(packet: Path) -> list[str]:
    return [
        "scp",
        "-q",
        *[str(packet / name) for name in ("result.json", "admin-commands.json", "live-apps.json")],
        HOST + ":/var/tmp/" + packet.name + "/",
    ]


def _argv(packet: Path, index: int) -> list[str]:
    plan = json.loads(read(packet / "admin-plan.json"))
    require(plan.get("journal_format") == JOURNAL and 0 <= index <= 6)
    return plan["commands"][index] if index < 6 else _receipt_command(packet)


def _step(packet: Path, index: int, argv: list[str]) -> str:
    require(argv == _argv(packet, index))
    intent = packet / f"step-{index:02d}.intent.json"
    require(not intent.exists())  # An intent is never permission to redispatch.
    require(
        not any(
            (packet / f"step-{index:02d}.{kind}.json").exists()
            for kind in ("transport", "terminal", "receipts")
        )
    )
    if index in (2, 5):
        name = argv[argv.index("--name") + 1]
        require(
            not command(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--no-trunc",
                    "--quiet",
                    "--filter",
                    "name=^/" + name + "$",
                ]
            ).strip()
        )
    write(
        intent,
        {
            "index": index,
            "argv_sha256": digest(_encoded(argv)),
            "plan_sha256": digest(read(packet / "admin-plan.json")),
            "at": _now().isoformat(),
            "helper_absent": index in (2, 5),
        },
    )
    try:
        output = command(argv, remote=index not in (1, 6))
    except BaseException as error:
        write(
            packet / f"step-{index:02d}.transport.json",
            {
                "transport_exit_code": getattr(error, "returncode", None),
                "transport_error_type": type(error).__name__,
            },
        )
        vars(error).update(hold_packet=str(packet), hold_step=index)
        raise
    write(
        packet / f"step-{index:02d}.transport.json",
        {"transport_exit_code": 0, "transport_error_type": None, "stdout": output},
    )
    plan = json.loads(read(packet / "admin-plan.json"))
    if index == 3:
        require(
            [line.split()[0] for line in output.splitlines()]
            == [plan["new_sha256"], plan["old_sha256"]]
        )
    if index == 4:
        require(output.strip() == "0 444 1")
    return output


def _operation(current: Path, pointer_sha256: str) -> Path:
    require(re.fullmatch(r"[0-9a-f]{64}", pointer_sha256))
    return current.with_name(current.name + "." + pointer_sha256 + ".operation.json")


def _packet(
    current: Path, packet: Path, *, started: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    prior = _prior(current)
    require(packet.parent == current.parent and re.fullmatch(PREFIX + r"\d{8}-\d{6}", packet.name))
    info = packet.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )
    plan = json.loads(read(packet / "admin-plan.json"))
    require(plan.get("journal_format") == JOURNAL)
    require(plan["before_pointer_sha256"] == digest(read(current)))
    operation = _operation(current, plan["before_pointer_sha256"])
    expected_operation = {
        "packet": str(packet),
        "pointer_sha256": plan["before_pointer_sha256"],
        "plan_sha256": digest(read(packet / "admin-plan.json")),
    }
    require(plan.get("operation") in (None, "reopen_expired"))
    if plan.get("operation") == "reopen_expired":
        admission = _check_reopen_admission(prior, packet, plan)
        if started:
            copied = json.loads(read(packet / "reopen-review.json"))
            require(set(copied) == {"review_json", "review_sha256"})
            require(isinstance(copied["review_json"], str))
            raw = copied["review_json"].encode()
            require(len(raw) <= 65536 and digest(raw) == copied["review_sha256"])
            _check_reopen_review(packet, json.loads(raw), admission)
            expected_operation.update(
                reopen_review_sha256=digest(raw),
                reopen_admission_sha256=digest(read(packet / "reopen-admission.json")),
            )
    if started:
        require(json.loads(read(operation)) == expected_operation)
    else:
        require(plan.get("operation") == "reopen_expired" and not operation.exists())
        require(
            not any(packet.glob("step-*.json")) and not (packet / "reopen-review.json").exists()
        )
    require(plan["old_sha256"] == prior["pointer"]["sha256"])
    require(
        plan["commands"]
        == templates(packet.name.removeprefix(PREFIX), packet, prior["holdpath"], prior["image"])
    )
    require(plan["new_sha256"] == digest(read(packet / "hold.json")))
    require(plan["hold"] == json.loads(read(packet / "hold.json")))
    require(set(plan["hold"]) == set(prior["hold"]))
    require(
        all(plan["hold"][key] == prior["hold"][key] for key in ("owner", "reason", "created_at"))
    )
    reviewed = dt.datetime.fromisoformat(plan["hold"]["reviewed_at"])
    require(reviewed > dt.datetime.fromisoformat(prior["hold"]["reviewed_at"]))
    require(
        dt.datetime.fromisoformat(plan["hold"]["review_at"]) == reviewed + dt.timedelta(hours=1)
    )
    return prior, plan


def _reopen_admission(prior: dict[str, Any], packet: Path, plan: dict[str, Any]) -> dict[str, Any]:
    old = Path(prior["pointer"]["packet"])
    return {
        "format": "swingset-expired-hold-reopen-admission-v1",
        "pointer_sha256": digest(prior["pointer_raw"]),
        "prior_packet": str(old),
        "prior_hashes": {
            name: digest(read(old / name))
            for name in (
                "result.json",
                "admin-plan.json",
                "admin-commands.json",
                "hold.json",
                "live-apps.json",
            )
        },
        "protected12_sha256": digest(_encoded(prior["baseline"])),
        "old_sha256": prior["pointer"]["sha256"],
        "expired_review_at": prior["hold"]["review_at"],
        "active_fresh": False,
        "closed_prior": True,
        "plan_sha256": digest(read(packet / "admin-plan.json")),
        "new_sha256": plan["new_sha256"],
        "source_sha256": digest(Path(__file__).read_bytes()),
    }


def _check_reopen_admission(
    prior: dict[str, Any], packet: Path, plan: dict[str, Any]
) -> dict[str, Any]:
    admission: dict[str, Any] = json.loads(read(packet / "reopen-admission.json"))
    require(admission == _reopen_admission(prior, packet, plan))
    reviewed = dt.datetime.fromisoformat(plan["hold"]["reviewed_at"])
    require(
        reviewed.tzinfo is not None
        and dt.datetime.fromisoformat(prior["hold"]["review_at"]) <= reviewed
    )
    require(packet.name == PREFIX + reviewed.strftime("%Y%m%d-%H%M%S"))
    return admission


def _check_reopen_review(
    packet: Path, authorization: dict[str, Any], admission: dict[str, Any]
) -> None:
    require(authorization["verdict"] == "ACCEPT_EXPIRED_HOLD_REOPEN")
    require(
        authorization["packet"] == str(packet) and authorization["acknowledged_expired"] is True
    )
    require(authorization["admission_sha256"] == digest(read(packet / "reopen-admission.json")))
    require(
        all(
            authorization[key] == admission[key]
            for key in (
                "pointer_sha256",
                "plan_sha256",
                "old_sha256",
                "new_sha256",
                "expired_review_at",
            )
        )
    )
    require(authorization["authorized_indices"] == list(range(7)))


def prepare_reopen(current: Path) -> dict[str, Any]:
    """Prepare only local bytes after read-only validation of an expired closed hold."""
    with _locked(current):
        prior, now = _prior(current), _now()
        require(not _operation(current, digest(prior["pointer_raw"])).exists())
        require(
            now.tzinfo is not None and dt.datetime.fromisoformat(prior["hold"]["review_at"]) <= now
        )
        require(now > dt.datetime.fromisoformat(prior["hold"]["reviewed_at"]))
        _live(prior, prior["pointer"]["sha256"])
        require(read(current) == prior["pointer_raw"])
        packet = _new_packet(current, prior, now, reopen=True)
        plan = json.loads(read(packet / "admin-plan.json"))
        admission = _reopen_admission(prior, packet, plan)
        write(packet / "reopen-admission.json", admission)
        return {
            "packet": str(packet),
            **admission,
            "admission_sha256": digest(read(packet / "reopen-admission.json")),
            "operation_started": False,
            "remote_mutations": False,
        }


def _before_activation(current: Path, packet: Path) -> None:
    prior, plan = _packet(current, packet)
    deadline = plan["hold"] if plan.get("operation") == "reopen_expired" else prior["hold"]
    require(_now() < dt.datetime.fromisoformat(deadline["review_at"]))
    _live(prior, plan["old_sha256"])


def reopen(current: Path, packet: Path, review: Path, review_sha256: str) -> dict[str, Any]:
    """A reviewed new operation; never a replay or freshness waiver for the old hold."""
    with _locked(current):
        prior, plan = _packet(current, packet, started=False)
        raw = read(review)
        require(len(raw) <= 65536 and digest(raw) == review_sha256)
        _check_reopen_review(packet, json.loads(raw), _check_reopen_admission(prior, packet, plan))
        now = _now()
        require(
            dt.datetime.fromisoformat(plan["hold"]["reviewed_at"])
            <= now
            < dt.datetime.fromisoformat(plan["hold"]["review_at"])
        )
        _live(prior, plan["old_sha256"])
        require(read(current) == prior["pointer_raw"])
        # Losslessly seal the exact reviewed bytes using the existing durable JSON writer.
        write(
            packet / "reopen-review.json",
            {"review_json": raw.decode(), "review_sha256": review_sha256},
        )
        write(
            _operation(current, plan["before_pointer_sha256"]),
            {
                "packet": str(packet),
                "pointer_sha256": plan["before_pointer_sha256"],
                "plan_sha256": digest(read(packet / "admin-plan.json")),
                "reopen_review_sha256": review_sha256,
                "reopen_admission_sha256": digest(read(packet / "reopen-admission.json")),
            },
        )
        _packet(current, packet)  # Both durable fences precede the first remote effect.
        for index, argv in enumerate(plan["commands"]):
            if index == 5:
                _before_activation(current, packet)
            _step(packet, index, argv)
        return _finalize(current, packet, prior)


def _terminal(packet: Path, index: int, prior: dict[str, Any]) -> dict[str, Any] | None:
    argv = _argv(packet, index)
    name = argv[argv.index("--name") + 1]
    try:
        value = json.loads(command(["docker", "inspect", "--format", HELPER_FORMAT, name]))
        require(
            isinstance(value, dict)
            and set(value)
            == set(
                "id name image created running status pid exit oom error_empty started finished restart user entrypoint cmd label readonly network privileged drop add security memory swap cpus pids log mounts".split()
            )
        )
        require(
            all(
                type(value[key]) is int
                for key in ("pid", "exit", "restart", "memory", "swap", "cpus", "pids")
            )
        )
        image_index = argv.index("--entrypoint") + 2
        tag = packet.name.removeprefix(PREFIX)
        mounts: list[dict[str, Any]] = [
            {
                "type": "bind",
                "source": str(Path(prior["holdpath"]).parent),
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
        require(re.fullmatch(r"[0-9a-f]{64}", value["id"]))
        require(value["name"] == "/" + name and value["image"] == prior["image"])
        require(
            value["entrypoint"] == [argv[image_index - 1]]
            and value["cmd"] == argv[image_index + 1 :]
        )
        require(value["label"] == tag and value["user"] == "0:0")
        require(
            value["readonly"] is True
            and value["network"] == "none"
            and value["privileged"] is False
        )
        require(value["drop"] == ["ALL"] and not value["add"])
        require(value["security"] in (["no-new-privileges"], ["no-new-privileges:true"]))
        require(
            value["memory"] == value["swap"] == 64 << 20
            and value["cpus"] == 10**9
            and value["pids"] == 32
        )
        require(
            value["log"]
            == {"Type": "local", "Config": {"max-size": "1m", "max-file": "1", "compress": "false"}}
        )
        require(
            sorted(value["mounts"], key=lambda m: m["destination"])
            == sorted(mounts, key=lambda m: m["destination"])
        )
        require(
            value["running"] is False
            and value["status"] == "exited"
            and value["pid"] == 0
            and value["exit"] == 0
            and value["oom"] is False
            and value["restart"] == 0
            and value["error_empty"] is True
        )
        created, started, finished = [
            dt.datetime.fromisoformat(value[key].replace("Z", "+00:00"))
            for key in ("created", "started", "finished")
        ]
        require(created.tzinfo is not None and created <= started <= finished)
        # Docker may reorder mounts between inspections; every mount field was
        # checked above, and the terminal witness must survive that reordering.
        value["mounts"] = sorted(value["mounts"], key=lambda mount: mount["destination"])
        proof = {key: value[key] for key in ("id", "created", "started", "finished")}
        proof.update(profile_sha256=digest(_encoded(value)), exit_code=0)
        _same(packet / f"step-{index:02d}.terminal.json", proof)
        return proof
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def _receipt_proof(packet: Path) -> dict[str, Any] | None:
    """Observe public receipt bytes; never retry their uncertain transfer."""
    names = ("result.json", "admin-commands.json", "live-apps.json")
    paths = ["/var/tmp/" + packet.name + "/" + name for name in names]
    try:
        uid = command(["id", "-u"]).strip()
        require(re.fullmatch(r"[0-9]+", uid))
        metadata_command = ["stat", "-c", "%F|%u|%a|%h|%s", "/var/tmp/" + packet.name, *paths]
        before = command(metadata_command).splitlines()
        require(len(before) == 4)
        rows = [line.split("|") for line in before]
        require(all(len(row) == 5 for row in rows))
        require(rows[0][:3] == ["directory", uid, "700"] and int(rows[0][3]) >= 2)
        files = []
        for name, row in zip(names, rows[1:], strict=True):
            raw = read(packet / name)
            require(row == ["regular file", uid, "600", "1", str(len(raw))])
            files.append({"name": name, "sha256": digest(raw), "bytes": len(raw)})
        output = [line.split() for line in command(["sha256sum", *paths]).splitlines()]
        require(
            output == [[entry["sha256"], path] for entry, path in zip(files, paths, strict=True)]
        )
        require(command(metadata_command).splitlines() == before)
        proof = {
            "remote_owner_uid": int(uid),
            "directory_mode": "700",
            "regular_mode": "600",
            "links": 1,
            "files": files,
        }
        _same(packet / "step-06.receipts.json", proof)
        return proof
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return None


def _status(current: Path, packet: Path) -> dict[str, Any]:
    prior, plan = _packet(current, packet)
    states, records, blocked, missing = [], [], False, False
    for index in range(7):
        path = packet / f"step-{index:02d}.intent.json"
        if not path.exists():
            require(
                not any(
                    (packet / f"step-{index:02d}.{kind}.json").exists()
                    for kind in ("transport", "terminal", "receipts")
                )
            )
            missing = True
            states.append("unattempted")
            continue
        require(not missing)
        intent = json.loads(read(path))
        argv = _argv(packet, index)
        require(
            intent["index"] == index
            and intent["argv_sha256"] == digest(_encoded(argv))
            and intent["plan_sha256"] == digest(read(packet / "admin-plan.json"))
            and intent["helper_absent"] is (index in (2, 5))
        )
        outcome_path = packet / f"step-{index:02d}.transport.json"
        outcome = json.loads(read(outcome_path)) if outcome_path.exists() else {}
        if outcome:
            require(
                set(outcome)
                in (
                    {"transport_exit_code", "transport_error_type"},
                    {"transport_exit_code", "transport_error_type", "stdout"},
                )
            )
            require(
                outcome["transport_exit_code"] is None
                or type(outcome["transport_exit_code"]) is int
            )
            if outcome["transport_exit_code"] == 0:
                require(
                    outcome["transport_error_type"] is None
                    and isinstance(outcome.get("stdout"), str)
                )
            else:
                require(
                    isinstance(outcome["transport_error_type"], str)
                    and re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]{0,127}", outcome["transport_error_type"]
                    )
                )
        code = outcome.get("transport_exit_code")
        record = {
            "argv": argv,
            "transport_exit_code": code,
            "transport_error_type": outcome.get("transport_error_type", "MissingOutcome"),
        }
        proof = _terminal(packet, index, prior) if index in (2, 5) else None
        receipts = _receipt_proof(packet) if index == 6 else None
        complete = (
            proof is not None
            if index in (2, 5)
            else receipts is not None
            if index == 6
            else code == 0
        )
        if complete and index == 3:
            require(
                [line.split()[0] for line in outcome["stdout"].splitlines()]
                == [plan["new_sha256"], plan["old_sha256"]]
            )
        if complete and index == 4:
            require(outcome["stdout"].strip() == "0 444 1")
        if complete:
            record["completion_basis"] = (
                "exact owned helper terminal exit"
                if proof
                else "exact remote public receipt bytes and metadata"
                if receipts
                else "transport exit"
            )
            if receipts:
                record["receipt_proof_sha256"] = digest(read(packet / "step-06.receipts.json"))
                # Matching bytes prove transfer effect, never an unknown SCP exit.
                record["observed_exit_code"] = code
                record["observed_exit_code_basis"] = (
                    "transport exit" if code == 0 else "transport remains unknown"
                )
            else:
                record.update(
                    observed_exit_code=0, observed_exit_code_basis=record["completion_basis"]
                )
            if proof:
                record["helper_id"] = proof["id"]
            states.append("complete")
        else:
            blocked = True
            states.append("unknown")
        records.append(record)
    active_sha = plan["new_sha256"] if states[5] == "complete" else plan["old_sha256"]
    _live(prior, active_sha)
    active_hold = plan["hold"] if states[5] == "complete" else prior["hold"]
    now = _now()
    fresh = now < dt.datetime.fromisoformat(active_hold["review_at"])
    reopening = plan.get("operation") == "reopen_expired"
    administrative_fresh = (
        reopening
        and states[5] != "complete"
        and (
            dt.datetime.fromisoformat(plan["hold"]["reviewed_at"])
            <= now
            < dt.datetime.fromisoformat(plan["hold"]["review_at"])
        )
    )
    if states[5] == "complete":
        require(command(["test", "!", "-e", plan["commands"][3][1]]) == "")
    if states[2] == "complete" and states[5] != "complete":
        require(command(plan["commands"][3]).splitlines()[0].split()[0] == plan["new_sha256"])
        require(command(plan["commands"][4]).strip() == "0 444 1")
    journal = {p.name: digest(read(p)) for p in sorted(packet.glob("step-*.json"))}
    journal["operation_intent"] = digest(read(_operation(current, plan["before_pointer_sha256"])))
    result = {
        "format": "swingset-hold-status-v1",
        "packet": str(packet),
        "plan_sha256": digest(read(packet / "admin-plan.json")),
        "pointer_sha256": digest(read(current)),
        "journal_sha256": digest(_encoded(journal)),
        "states": states,
        "records": records,
        "active_sha256": active_sha,
        "active_fresh": fresh,
        "continuation_allowed": not blocked and (fresh or administrative_fresh),
        "next_index": next((i for i, state in enumerate(states) if state == "unattempted"), 7),
    }
    if reopening:
        copied = json.loads(read(packet / "reopen-review.json"))
        result.update(
            operation="reopen_expired",
            reopen_review_sha256=copied["review_sha256"],
            admission_sha256=digest(read(packet / "reopen-admission.json")),
            administrative_window_fresh=administrative_fresh,
        )
    return result


def _finalize(current: Path, packet: Path, prior: dict[str, Any]) -> dict[str, Any]:
    status = _status(current, packet)
    require(status["continuation_allowed"] and status["states"][:6] == ["complete"] * 6)
    plan = json.loads(read(packet / "admin-plan.json"))
    admin = {
        "before_sha256": plan["old_sha256"],
        "commands": status["records"][:6],
        "final_metadata": "0 444 1",
        "temp_and_after_sha256": plan["new_sha256"],
        "transport": prior["admin"]["transport"],
    }
    _same(packet / "live-apps.json", prior["baseline"])
    _same(packet / "admin-commands.json", admin)
    result = {
        **prior["result"],
        "at": plan["hold"]["reviewed_at"],
        "before_sha256": plan["old_sha256"],
        "after_sha256": plan["new_sha256"],
        "hold": plan["hold"],
        "admin_commands_sha256": digest(read(packet / "admin-commands.json")),
    }
    proof = json.loads(read(packet / "step-05.terminal.json"))
    result["review_expiry_gap"] = {
        "old_review_at": prior["hold"]["review_at"],
        "activation_started_at": proof["started"],
        "activation_finished_at": proof["finished"],
        "continuous_validity_proven": dt.datetime.fromisoformat(
            proof["finished"].replace("Z", "+00:00")
        )
        < dt.datetime.fromisoformat(prior["hold"]["review_at"]),
    }
    result.pop("expired_reopen", None)  # A subsequent routine renewal is not a new reopen.
    if plan.get("operation") == "reopen_expired":
        expired = dt.datetime.fromisoformat(prior["hold"]["review_at"])
        activated = dt.datetime.fromisoformat(proof["finished"].replace("Z", "+00:00"))
        require(activated >= expired)
        result["review_expiry_gap"].update(
            continuous_validity_proven=False, expired_seconds=(activated - expired).total_seconds()
        )
        result["expired_reopen"] = {
            "review_sha256": json.loads(read(packet / "reopen-review.json"))["review_sha256"],
            "admission_sha256": digest(read(packet / "reopen-admission.json")),
            "prior_packet": str(prior["pointer"]["packet"]),
        }
    _same(packet / "result.json", result)
    if status["states"][6] == "unattempted":
        _step(packet, 6, _receipt_command(packet))
    else:
        require(status["states"][6] == "complete")
    require(_receipt_proof(packet) is not None)
    require(_now() < dt.datetime.fromisoformat(plan["hold"]["review_at"]))
    _live(prior, plan["new_sha256"])
    require(read(current) == prior["pointer_raw"])
    write(
        current,
        {
            "packet": str(packet),
            "sha256": plan["new_sha256"],
            "review_at": plan["hold"]["review_at"],
        },
        replace=True,
    )
    return {
        "packet": str(packet),
        "sha256": plan["new_sha256"],
        "review_at": plan["hold"]["review_at"],
        "protected12_unchanged": True,
    }


@contextmanager
def _locked(current: Path) -> Iterator[None]:
    descriptor = os.open(
        current.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def status(current: Path, packet: Path) -> dict[str, Any]:
    with _locked(current):
        prior = _prior(current)
        if packet == Path(prior["pointer"]["packet"]):
            _live(prior, prior["pointer"]["sha256"])
            return {
                "complete": True,
                "legacy_or_closed": True,
                "continuation_allowed": False,
                "active_fresh": _now() < dt.datetime.fromisoformat(prior["hold"]["review_at"]),
            }
        _packet(current, packet)  # Establish private ownership before recording an error.
        try:
            value = _status(current, packet)
        except Exception as error:
            value = {
                "format": "swingset-hold-status-v1",
                "packet": str(packet),
                "continuation_allowed": False,
                "error_type": type(error).__name__,
            }
        path = packet / ("status-" + uuid.uuid4().hex + ".json")
        write(path, value)
        return {**value, "status_path": str(path), "status_sha256": digest(read(path))}


def resume(current: Path, packet: Path, review: Path, review_sha256: str) -> dict[str, Any]:
    with _locked(current):
        raw = read(review)
        require(digest(raw) == review_sha256)
        authorization = json.loads(raw)
        require(
            authorization["verdict"] == "ACCEPT_HOLD_REMAINING_ONLY"
            and authorization["packet"] == str(packet)
        )
        status_path = Path(authorization["status_path"])
        require(
            status_path.parent == packet
            and re.fullmatch(r"status-[0-9a-f]{32}\.json", status_path.name)
        )
        raw_status = read(status_path)
        require(digest(raw_status) == authorization["status_sha256"])
        old = json.loads(raw_status)
        value = _status(current, packet)
        require(
            value["continuation_allowed"]
            and all(
                value[key] == old[key] == authorization[key]
                for key in ("plan_sha256", "pointer_sha256", "journal_sha256")
            )
        )
        indices = authorization["authorized_remaining_indices"]
        require(indices == list(range(value["next_index"], 7)))
        if value.get("operation") == "reopen_expired":
            require(
                all(
                    authorization.get(key) == value[key]
                    for key in ("operation", "reopen_review_sha256", "admission_sha256")
                )
            )
        write(
            packet / ("continuation-" + review_sha256 + ".json"),
            {"review_sha256": review_sha256, "status_sha256": authorization["status_sha256"]},
        )
        prior, plan = _packet(current, packet)
        for index in indices:
            if index < 6:
                if index == 5:
                    _before_activation(current, packet)
                _step(packet, index, plan["commands"][index])
        return _finalize(current, packet, prior)


def run(current: Path) -> dict[str, Any]:
    """Review one existing hold; never create a new hold or resume a scheduler."""
    with _locked(current):
        return _run(current, _now())


def arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--current", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", type=Path)
    action.add_argument("--resume", type=Path)
    action.add_argument("--prepare-reopen", action="store_true")
    action.add_argument("--reopen", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--review-sha256")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    args = parser.parse_args(argv)
    try:
        if args.prepare_reopen:
            require(args.review is None and args.review_sha256 is None)
            result = prepare_reopen(args.current)
        elif args.reopen:
            require(
                args.review is not None and re.fullmatch(r"[0-9a-f]{64}", args.review_sha256 or "")
            )
            result = reopen(args.current, args.reopen, args.review, args.review_sha256)
        elif args.status:
            require(args.review is None and args.review_sha256 is None)
            result = status(args.current, args.status)
        elif args.resume:
            require(
                args.review is not None and re.fullmatch(r"[0-9a-f]{64}", args.review_sha256 or "")
            )
            result = resume(args.current, args.resume, args.review, args.review_sha256)
        else:
            require(args.review is None and args.review_sha256 is None)
            result = run(args.current)
    except Exception as error:
        failure: dict[str, Any] = {"complete": False, "error_type": type(error).__name__}
        context = vars(error)
        if "hold_packet" in context:
            failure.update(packet=context["hold_packet"], step=context["hold_step"])
        print(json.dumps(failure, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return (
        1
        if args.status
        and (
            not result.get("active_fresh", False)
            or not (result.get("complete") or result.get("continuation_allowed"))
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
