"""Run one reviewed pure numerical sandbox, retaining its owned container.

This stdlib-only supervisor can be staged unchanged beside its public operation
inputs. It never opens application data or imports the numerical implementation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read(path: Path, cap: int = 1048576, mode: int | None = None, uid: int | None = None) -> bytes:
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= cap
    assert mode is None or stat.S_IMODE(info.st_mode) == mode
    assert uid is None or info.st_uid == info.st_gid == uid
    return path.read_bytes()


def supervisor_bytes(path: Path) -> bytes:
    info = path.lstat()
    assert info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022
    return read(path)


def strict_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate numerical protocol JSON key")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=unique)


LOG_CONFIG = {"Type": "local", "Config": {"max-size": "1m", "max-file": "2", "compress": "false"}}


F = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"pid":{{json .State.Pid}},"started_at":{{json .State.StartedAt}},"paused":{{json .State.Paused}},"oom":{{json .State.OOMKilled}},"restart":{{json .RestartCount}},"health":{{if index .State "Health"}}{{json .State.Health.Status}}{{else}}null{{end}}}'

ENV_FORMAT = '{{$seen := false}}{{$duplicate := false}}{{$category := "absent"}}{{range .Config.Env}}{{$parts := split . "="}}{{if eq (index $parts 0) "RAPIDFUZZ_IMPLEMENTATION"}}{{if $seen}}{{$duplicate = true}}{{end}}{{$seen = true}}{{if eq . "RAPIDFUZZ_IMPLEMENTATION=cpp"}}{{$category = "cpp"}}{{else if eq . "RAPIDFUZZ_IMPLEMENTATION=python"}}{{$category = "python"}}{{else}}{{$category = "other"}}{{end}}{{end}}{{end}}{"category":{{json $category}},"duplicate":{{json $duplicate}}}'

OWNED_FORMAT = '{"Id":{{json .Id}},"Image":{{json .Image}},"Name":{{json .Name}},"Created":{{json .Created}},"State":{{json .State}},"RestartCount":{{json .RestartCount}},"Config":{"User":{{json .Config.User}},"Entrypoint":{{json .Config.Entrypoint}},"Cmd":{{json .Config.Cmd}},"Healthcheck":{{json .Config.Healthcheck}},"Labels":{{json .Config.Labels}}},"HostConfig":{{json .HostConfig}},"Mounts":{{json .Mounts}}}'


class _Probe:
    def __init__(self, directory: Path, review_path: Path) -> None:
        self.started = time.monotonic()
        self.phase = "main"
        self.directory = directory
        assert os.geteuid() == os.getegid() == 1000
        assert directory == directory.resolve()
        info = directory.lstat()
        assert (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == info.st_gid == 1000
            and stat.S_IMODE(info.st_mode) == 0o700
        )
        self.policy_raw = read(directory / "policy.json", mode=0o600, uid=1000)
        self.policy = strict_json(self.policy_raw)
        self.review_raw = read(review_path, mode=0o600, uid=1000)
        self.review = strict_json(self.review_raw)
        p, review = self.policy, self.review
        assert (
            str(directory) == p["server_root"]
            and not p["final_binding_pending"]
            and review_path.parent == directory
        )
        assert review["verdict"] == "ACCEPT" and review["scope"] == "pure-link-numerics"
        assert review["policy_sha256"] == sha(self.policy_raw) and review[
            "supervisor_sha256"
        ] == sha(supervisor_bytes(Path(__file__)))
        assert review["execution_authorized_by_root"] is True
        assert dt.datetime.fromisoformat(review["expires_at"]) > dt.datetime.now(
            dt.UTC
        ) + dt.timedelta(seconds=120)
        for name, key in [("numerics.py", "module_sha256"), ("input.json", "input_sha256")]:
            assert sha(read(directory / name, 67108864, 0o644, 1000)) == p[key] == review[key]
        assert (
            re.fullmatch(r"[a-f0-9]{12}", p["nonce"])
            and p["container_name"] == "swingset-pure-numerics-" + p["nonce"]
        )
        self.cid: str | None = None
        self.result: dict[str, Any] = {
            "format": "swingset-pure-numerics-operation-v1",
            "complete": False,
            "policy_sha256": sha(self.policy_raw),
            "review_sha256": sha(self.review_raw),
            "nonce": p["nonce"],
            "started_at": dt.datetime.now(dt.UTC).isoformat(),
        }

    def expected_pairs(self) -> set[tuple[str, str, int]]:
        if "expected_pairs" in self.policy:
            assert "subjects" not in self.policy
            rows = self.policy["expected_pairs"]
            assert 0 < len(rows) <= 256
            assert type(self.policy["expected_pair_count"]) is int
            triples = [
                (row["subject_kind"], row["subject_id"], row["candidate_wsdc_id"]) for row in rows
            ]
            assert len(triples) == self.policy["expected_pair_count"]
        else:
            assert len(self.policy["subjects"]) == 5
            triples = [
                ("entry", subject, dancer) for subject, dancer in self.policy["subjects"].items()
            ]
        assert len(triples) == len(set(triples))
        assert all(
            kind in {"entry", "judge"}
            and isinstance(subject, str)
            and type(dancer) is int
            and dancer > 0
            for kind, subject, dancer in triples
        )
        return set(triples)

    def validate_pairs(self, result: dict[str, Any]) -> None:
        expected = self.expected_pairs()
        explicit = "expected_pairs" in self.policy
        actual = [
            (
                row["subject_kind"] if explicit else row.get("subject_kind", "entry"),
                row["subject_id"],
                row["dancer_id"],
            )
            for row in result["pairs"]
        ]
        assert len(actual) == len(set(actual)) == len(expected) and set(actual) == expected

    def validate_logging_argv(self) -> None:
        argv = self.policy["create_argv"]
        assert argv[:3] == ["docker", "create", "--pull=never"]
        expected = {
            "--log-driver=local",
            "--log-opt=max-size=1m",
            "--log-opt=max-file=2",
            "--log-opt=compress=false",
        }
        actual = [arg for arg in argv if arg.startswith(("--log-driver", "--log-opt"))]
        assert len(actual) == len(expected) and set(actual) == expected

    def save(self, name: str, value: Any) -> None:
        raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
        fd = os.open(self.directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)

    def command(self, argv: list[str], timeout: float = 5, limit: int = 1048576) -> bytes:
        remaining = (120 if self.phase == "cleanup" else 90) - (time.monotonic() - self.started)
        if remaining <= 0:
            raise TimeoutError("Overall numerical operation deadline")
        v = subprocess.run(argv, capture_output=True, timeout=min(timeout, remaining), check=False)
        assert len(v.stdout) <= limit and len(v.stderr) <= 16384
        if v.returncode:
            raise RuntimeError("Owned numerical operation command failed")
        return v.stdout

    def public(self, cid: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.command(["docker", "inspect", "--format", F, cid]))
        return value

    def guards(self, initial: bool = False) -> None:
        assert time.monotonic() - self.started < (120 if self.phase == "cleanup" else 90)
        for pin in self.policy["protected12"]:
            v = self.public(pin["id"])
            assert not v["paused"] and {k: v[k] for k in pin} == pin
        g = self.policy["guard"]
        v = self.public(g["container_id"])
        birth = Path("/proc", str(g["pid"]), "stat").read_text().rsplit(")", 1)[1].split()[19]
        assert (
            v["running"]
            and (not v["paused"])
            and (not v["oom"])
            and (v["restart"] == 0)
            and (v["pid"] == g["pid"])
            and (v["started_at"] == g["started_at"])
            and (birth == g["start_ticks"])
            and (Path("/proc/sys/kernel/random/boot_id").read_text().strip() == g["boot_id"])
        )
        app = Path("/var/tmp") / self.policy["deployment"]
        for name, key in [
            ("security.py", "security_sha256"),
            ("config/network.json", "network_policy_sha256"),
            ("holds/stage2-import.json", "hold_sha256"),
        ]:
            assert sha(read(app / name, mode=0o444, uid=0)) == self.policy[key]
        hold = json.loads(read(app / "holds/stage2-import.json"))
        assert (
            all(hold[k] == v for (k, v) in self.policy["hold_identity"].items())
            and hold["review_at"] == self.policy["hold_review_at"]
        )
        assert (
            dt.datetime.fromisoformat(hold["review_at"]) - dt.datetime.now(dt.UTC)
        ).total_seconds() > (900 if initial else 0)
        status = json.loads(read(app / "authorization/containers.json", uid=0))
        assert (
            0 <= time.time() - status["utc"] < 90
            and status["policy_sha256"] == self.policy["guard_policy_sha256"]
            and (status["resource_exit"] == 0)
            and (not status.get("observation_error"))
        )
        for name, digest in self.policy["preseal_files"].items():
            assert sha(read(Path(self.policy["preseal_root"]) / name)) == digest
        assert (
            self.command(
                ["docker", "ps", "-q", "--no-trunc", "--filter", "label=io.swingset.guarded=true"]
            ).split()
            == []
        )
        free = os.statvfs(app).f_bavail * os.statvfs(app).f_frsize
        assert free >= 200000000000
        self.result.setdefault("guard_samples", []).append(
            {
                "at": dt.datetime.now(dt.UTC).isoformat(),
                "free_bytes": free,
                "protected12_exact": True,
                "guard_incarnation_exact": True,
                "root_hold_preseal_exact": True,
            }
        )

    def env_category(self, container: str) -> dict[str, Any]:
        v: dict[str, Any] = json.loads(
            self.command(["docker", "inspect", "--format", ENV_FORMAT, container])
        )
        assert v["category"] in {"cpp", "python", "other", "absent"} and v["duplicate"] is False
        return v

    def owned(self) -> dict[str, Any]:
        assert self.cid is not None
        v: dict[str, Any] = json.loads(
            self.command(["docker", "inspect", "--format", OWNED_FORMAT, self.cid])
        )
        assert (
            v["Id"] == self.cid
            and v["Image"] == self.policy["runtime_image"]
            and (v["Name"] == "/" + self.policy["container_name"])
        )
        assert all(
            v["Config"]["Labels"].get(k) == value for (k, value) in self.policy["labels"].items()
        )
        return v

    def cold(self, v: dict[str, Any]) -> None:
        assert self.cid is not None
        h = v["HostConfig"]
        assert h["LogConfig"] == LOG_CONFIG
        c = v["Config"]
        assert not v["State"]["Running"] and v["State"]["Status"] == "created"
        assert (
            c["User"] == "10001:10001"
            and c["Entrypoint"] == ["/app/.venv/bin/python"]
            and (c["Cmd"] == self.policy["command"])
            and (c.get("Healthcheck", {}).get("Test") == ["NONE"])
        )
        assert (
            h["NetworkMode"] == "none"
            and h["ReadonlyRootfs"]
            and (not h["Privileged"])
            and (not h.get("CapAdd"))
            and (h["CapDrop"] == ["ALL"])
            and (h["SecurityOpt"] == ["no-new-privileges"])
        )
        assert (
            h["Memory"] == h["MemorySwap"] == 134217728
            and h["NanoCpus"] == 1000000000
            and (h["PidsLimit"] == 32)
        )
        assert (
            h["PidMode"] == ""
            and h["IpcMode"] == "private"
            and (h["CgroupnsMode"] == "private")
            and (not h["AutoRemove"])
            and (h["RestartPolicy"]["Name"] == "no")
            and (not h.get("Tmpfs"))
            and (not h.get("Devices"))
        )
        mounts = sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in v["Mounts"])
        assert mounts == sorted(
            [
                ("bind", str(self.directory / "numerics.py"), "/probe/numerics.py", False),
                ("bind", str(self.directory / "input.json"), "/probe/input.json", False),
            ]
        )
        assert self.env_category(self.cid) == {"category": "absent", "duplicate": False}
        assert (
            self.command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{range .Config.Env}}{{if eq . "PYTHONDONTWRITEBYTECODE=1"}}ok{{end}}{{end}}',
                    self.cid,
                ]
            )
            .decode()
            .strip()
            == "ok"
        )
        self.result["cold_resources_verified"] = True
        self.result["cold_inspection"] = {
            "image": v["Image"],
            "user": c["User"],
            "entrypoint": c["Entrypoint"],
            "command": c["Cmd"],
            "mounts": mounts,
            "host_config": {
                k: h[k]
                for k in [
                    "NetworkMode",
                    "ReadonlyRootfs",
                    "Privileged",
                    "CapAdd",
                    "CapDrop",
                    "SecurityOpt",
                    "Memory",
                    "MemorySwap",
                    "NanoCpus",
                    "PidsLimit",
                    "PidMode",
                    "IpcMode",
                    "CgroupnsMode",
                    "AutoRemove",
                    "RestartPolicy",
                    "LogConfig",
                ]
            },
            "healthcheck": c.get("Healthcheck"),
        }

    def execute(self) -> dict[str, Any]:
        self.save(
            "owner.json",
            {
                "nonce": self.policy["nonce"],
                "policy_sha256": sha(self.policy_raw),
                "review_sha256": sha(self.review_raw),
                "pid": os.getpid(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "root_device": self.directory.stat().st_dev,
                "root_inode": self.directory.stat().st_ino,
                "root_uid": self.directory.stat().st_uid,
                "container_name": self.policy["container_name"],
            },
        )
        try:
            self.expected_pairs()
            self.guards(initial=True)
            old = self.policy["old_graph_container"]
            old_state = self.public(old)
            assert (
                old_state["id"] == old
                and old_state["image"] == self.policy["runtime_image"]
                and (not old_state["running"])
                and (old_state["pid"] == 0)
                and (not old_state["oom"])
                and (old_state["restart"] == 0)
                and (old_state["started_at"] == self.policy["old_graph_started_at"])
            )
            self.result["old_graph_environment"] = {
                "container_id": old,
                "safe_override_projection": self.env_category(old),
                "started_at": old_state["started_at"],
                "image": old_state["image"],
            }
            image = json.loads(
                self.command(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        '{"id":{{json .Id}},"source":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}',
                        self.policy["runtime_image"],
                    ]
                )
            )
            assert image == {
                "id": self.policy["runtime_image"],
                "source": self.policy["runtime_source_revision"],
            }
            assert not self.command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    "name=^/" + self.policy["container_name"] + "$",
                ]
            ).strip()
            self.validate_logging_argv()
            argv = self.policy["create_argv"]
            assert argv[:3] == ["docker", "create", "--pull=never"]
            created = self.command(argv).decode().strip()
            assert re.fullmatch("[a-f0-9]{64}", created)
            self.cid = created
            self.save(
                "allocation.json",
                {
                    "container_id": self.cid,
                    "nonce": self.policy["nonce"],
                    "image": self.policy["runtime_image"],
                    "labels": self.policy["labels"],
                    "policy_sha256": sha(self.policy_raw),
                },
            )
            self.result["container_id"] = self.cid
            v = self.owned()
            self.cold(v)
            began = time.monotonic()
            self.phase = "probe"
            probe_budget = min(10, 90 - (began - self.started))
            assert probe_budget > 0
            signal.setitimer(signal.ITIMER_REAL, probe_budget)
            self.command(["docker", "start", self.cid])
            deadline = began + probe_budget
            while True:
                v = self.owned()
                s = v["State"]
                assert not s["Paused"] and (not s["OOMKilled"]) and (v["RestartCount"] == 0)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Pure numerical probe exceeded10seconds")
                if not s["Running"]:
                    break
                time.sleep(0.1)
            self.phase = "main"
            remaining = 90 - (time.monotonic() - self.started)
            assert remaining > 0
            signal.setitimer(signal.ITIMER_REAL, remaining)
            self.result["probe_wall_seconds"] = round(time.monotonic() - began, 3)
            assert s["ExitCode"] == 0 and (not s["OOMKilled"]) and (s["Pid"] == 0)
            logs = self.command(["docker", "logs", self.cid], limit=1048576)
            probe = strict_json(logs)
            assert (
                probe["format"] == "swingset-pure-link-numerics-v1"
                and probe["input_sha256"] == self.policy["input_sha256"]
                and (probe["command_sha256"] == self.policy["module_sha256"])
                and (probe["normalization_sha256"] == self.policy["normalization_sha256"])
            )
            self.validate_pairs(probe)
            self.save("probe.json", probe)
            self.result["probe_sha256"] = sha((self.directory / "probe.json").read_bytes())
            self.result["complete"] = True
        except BaseException as error:
            self.result["error_type"] = type(error).__name__
            self.result["failure_phase"] = self.phase
        finally:
            self.phase = "cleanup"
            remaining = 120 - (time.monotonic() - self.started)
            signal.setitimer(signal.ITIMER_REAL, max(0.001, remaining))
            self.result["stop_confirmation"] = "unknown"
            if self.cid is not None:
                try:
                    v = self.owned()
                    if v["State"]["Running"]:
                        self.command(["docker", "kill", "--signal", "KILL", self.cid])
                        v = self.owned()
                    s = v["State"]
                    assert not s["Running"] and s["Pid"] == 0
                    self.result["terminal"] = {
                        k: s[k]
                        for k in [
                            "Status",
                            "Running",
                            "Pid",
                            "ExitCode",
                            "OOMKilled",
                            "StartedAt",
                            "FinishedAt",
                        ]
                    }
                    self.result["container_retained"] = True
                    self.result["owned_container_stopped"] = True
                    self.result["stop_confirmation"] = "confirmed_stopped"
                except BaseException as error:
                    self.result["cleanup_error_type"] = type(error).__name__
                    self.result["complete"] = False
            try:
                for name, key in [("numerics.py", "module_sha256"), ("input.json", "input_sha256")]:
                    assert (
                        sha(read(self.directory / name, 67108864, 0o644, 1000)) == self.policy[key]
                    )
                self.guards()
                self.result["post_guards_passed"] = True
            except BaseException as error:
                self.result["postcheck_error_type"] = type(error).__name__
                self.result["complete"] = False
            self.result["overall_deadline_exhausted"] = time.monotonic() - self.started >= 120
            self.result["complete"] = self.result["complete"] and (
                not self.result["overall_deadline_exhausted"]
            )
            self.result["seconds"] = round(time.monotonic() - self.started, 3)
            self.result["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
            self.save("result.json", self.result)
            signal.setitimer(signal.ITIMER_REAL, 0)
        return self.result


def run(directory: Path, review: Path) -> dict[str, Any]:
    """Consume one packet once, with a 90-second main and 120-second total bound.

    The compute alarm is ten seconds; owned-container cleanup can take additional
    time within the overall bound. An unconfirmed stop is always incomplete.
    """
    if sys.flags.optimize:
        raise RuntimeError("Numerics probe requires Python safety assertions")
    started = time.monotonic()
    previous_umask = os.umask(0o077)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGALRM)}
    timer = signal.getitimer(signal.ITIMER_REAL)

    def stopped(signum: int, frame: Any) -> None:
        raise InterruptedError("Numerical supervisor stopped")

    def expired(signum: int, frame: Any) -> None:
        raise TimeoutError("Numerical operation deadline")

    try:
        signal.signal(signal.SIGTERM, stopped)
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 90)
        probe = _Probe(directory, review)
        probe.started = started
        return probe.execute()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic() - started)), timer[1]
            )
        os.umask(previous_umask)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.directory, args.review)
    except Exception as error:
        print(json.dumps({"complete": False, "error_type": type(error).__name__}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "result_sha256": sha((args.directory / "result.json").read_bytes()),
                "container_id": result.get("container_id"),
            },
            sort_keys=True,
        )
    )
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
