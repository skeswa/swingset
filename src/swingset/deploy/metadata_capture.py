"""Capture reviewed retained graph metadata without starting an actor or transform.

This stdlib-only module may be staged unchanged on the reviewed server. SQL,
namespace signal script, policy and acceptance remain private operation inputs.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_bytes(path: Path, cap: int = 1048576, require_uid: int | None = None) -> bytes:
    s = path.lstat()
    assert (
        stat.S_ISREG(s.st_mode)
        and (require_uid is None or s.st_uid == require_uid)
        and (s.st_nlink == 1)
        and (s.st_size <= cap)
    )
    return path.read_bytes()


def collector_bytes(path: Path) -> bytes:
    """Bind installed or privately staged code without permitting writable peers."""
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022
    return file_bytes(path)


def decode_documents(
    raw: bytes, *, maximum_bytes: int, maximum_documents: int
) -> list[dict[str, Any]]:
    """Decode bounded psql JSON output, including formatted composite aggregates.

    Document decoding does not establish section identity or completeness. Those
    checks belong to the reviewed capture policy in verify_sections().
    """
    if maximum_bytes <= 0 or maximum_documents <= 0 or len(raw) > maximum_bytes:
        raise ValueError("JSON stream limits exceeded")

    def invalid_constant(value: str) -> None:
        raise ValueError("Invalid JSON constant")

    decoder = json.JSONDecoder(parse_constant=invalid_constant)
    text = raw.decode("utf-8")
    position = 0
    documents: list[dict[str, Any]] = []
    while position < len(text):
        if text[position] in " \t\r\n":
            position += 1
            continue
        if len(documents) >= maximum_documents:
            raise ValueError("JSON document limit exceeded")
        value, position = decoder.raw_decode(text, position)
        if not isinstance(value, dict):
            raise ValueError("JSON stream document must be an object")
        documents.append(value)
    return documents


FORMAT = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"pid":{{json .State.Pid}},"started_at":{{json .State.StartedAt}},"paused":{{json .State.Paused}},"oom":{{json .State.OOMKilled}},"restart":{{json .RestartCount}},"health":{{if index .State "Health"}}{{json .State.Health.Status}}{{else}}null{{end}}}'


class CommandFailed(RuntimeError):
    """Retain a static command kind and exit code without arguments or stderr."""

    def __init__(self, argv: list[str], exit_code: int) -> None:
        kind = argv[1] if len(argv) > 1 and argv[0] == "docker" else "unknown"
        self.kind = kind if kind in {"inspect", "image", "ps", "top", "exec"} else "unknown"
        self.exit_code = exit_code
        super().__init__("Read-only command failed")

    def safe_details(self) -> dict[str, Any]:
        return {"kind": self.kind, "exit_code": self.exit_code}


class _Capture:
    def __init__(self, directory: Path, review_path: Path) -> None:
        self.alarm_fired = False
        self.directory = directory
        assert os.geteuid() == os.getegid() == 1000 and self.directory == self.directory.resolve()
        assert (
            self.directory.stat().st_uid == self.directory.stat().st_gid == 1000
            and stat.S_IMODE(self.directory.stat().st_mode) == 0o700
        )
        for source in [
            self.directory / "policy.json",
            self.directory / "capture.sql",
            self.directory / "kill_client.sh",
            review_path,
        ]:
            item = source.lstat()
            assert item.st_uid == item.st_gid == 1000 and stat.S_IMODE(item.st_mode) == 0o600
        self.policy_raw = file_bytes(self.directory / "policy.json")
        self.policy = json.loads(self.policy_raw)
        assert (
            str(self.directory) == self.policy["server_root"]
            and self.policy["operator_uid"] == self.policy["operator_gid"] == 1000
        )
        self.kill_script = file_bytes(self.directory / "kill_client.sh")
        assert digest(self.kill_script) == self.policy["kill_client_sha256"]
        self.sql = file_bytes(self.directory / "capture.sql")
        assert digest(self.sql) == self.policy["capture_sql_sha256"]
        self.review_raw = file_bytes(review_path)
        self.review = json.loads(self.review_raw)
        assert (
            review_path.parent == self.directory
            and self.review["verdict"] == "ACCEPT"
            and (self.review["scope"] == "read-only-retained-metadata")
        )
        assert self.review["policy_sha256"] == digest(self.policy_raw) and self.review[
            "capture_sql_sha256"
        ] == digest(self.sql)
        assert (
            self.review["collector_sha256"] == digest(collector_bytes(Path(__file__)))
            and self.review["kill_client_sha256"] == digest(self.kill_script)
            and (self.review["execution_authorized_by_root"] is True)
        )
        assert dt.datetime.fromisoformat(self.review["expires_at"]) > dt.datetime.now(
            dt.UTC
        ) + dt.timedelta(seconds=600)
        self.output = self.directory / "capture"
        self.output.mkdir(mode=0o700)
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.started = time.monotonic()
        self.nonce = self.policy["nonce"]
        self.tracked: dict[int, dict[str, Any]] = {}
        self.process: subprocess.Popen[bytes] | None = None
        self.cleaning = False
        assert re.fullmatch("[a-z0-9-]+", self.nonce)
        self.result: dict[str, Any] = {
            "format": "swingset-failed-graph-metadata-capture-v2",
            "complete": False,
            "read_only": True,
            "nonce": self.nonce,
            "at": dt.datetime.now(dt.UTC).isoformat(),
            "policy_sha256": digest(self.policy_raw),
            "review_sha256": digest(self.review_raw),
            "capture_sql_sha256": digest(self.sql),
            "collector_sha256": digest(collector_bytes(Path(__file__))),
            "source_checkpoint": self.policy["source_checkpoint"],
            "graph_receipt_sha256": self.policy["graph_receipt_sha256"],
            "source_revision": self.policy["source_revision"],
            "runtime_image": self.policy["runtime_image"],
            "samples": 0,
            "guard_target_interval_seconds": 10,
            "guard_max_start_gap_seconds": 0,
            "guard_max_scan_seconds": 0,
            "minimum_free_bytes": None,
            "owned_processes": [],
        }

    def save(self, name: str, value: Any) -> None:
        raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
        with open(self.output / name, "xb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
        os.close(fd)

    def command(self, argv: list[str], timeout: float = 15) -> bytes:
        remaining = (600 if self.cleaning else 540) - (time.monotonic() - self.started)
        assert remaining > 0
        v = subprocess.run(argv, capture_output=True, timeout=min(timeout, remaining), check=False)
        assert len(v.stdout) <= 1048576 and len(v.stderr) <= 16384
        if v.returncode:
            raise CommandFailed(argv, v.returncode)
        return v.stdout

    def inspect(self, cid: str) -> dict[str, Any]:
        v = json.loads(self.command(["docker", "inspect", "--format", FORMAT, cid]))
        assert isinstance(v, dict) and v["id"] == cid
        return v

    def birth(self, pid: int) -> str:
        return Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()[19]

    def nspid(self, pid: int) -> list[int]:
        return [
            int(v)
            for line in Path("/proc", str(pid), "status").read_text().splitlines()
            if line.startswith("NSpid:")
            for v in line.split()[1:]
        ]

    def host_cgroup(self, pid: int) -> str:
        return Path("/proc", str(pid), "cgroup").read_text()

    def pg_mapping(self) -> dict[str, Any]:
        pin = self.policy["private_pg_incarnation"]
        assert isinstance(pin, dict)
        v = self.inspect(self.policy["private_pg"]["id"])
        assert (
            v["running"]
            and (not v["paused"])
            and (not v["oom"])
            and (v["pid"] == pin["host_pid"])
            and (v["started_at"] == pin["started_at"])
            and (v["restart"] == self.policy["private_pg"]["restart"])
        )
        assert (
            self.birth(pin["host_pid"]) == pin["start_ticks"]
            and self.nspid(pin["host_pid"]) == pin["nspid"]
            and (pin["namespace_pid"] == pin["nspid"][-1] == 1)
            and (self.host_cgroup(pin["host_pid"]) == pin["cgroup"])
            and (self.boot == pin["boot_id"])
        )
        return pin

    def checked_file(self, path: Path, expected: str) -> None:
        s = path.lstat()
        assert (
            stat.S_ISREG(s.st_mode)
            and s.st_uid == s.st_gid == 0
            and (s.st_nlink == 1)
            and (stat.S_IMODE(s.st_mode) == 0o444)
        )
        assert digest(file_bytes(path)) == expected

    def guards(self, initial: bool = False) -> None:
        scan_start = time.monotonic()
        if self.result.get("last_guard_started_monotonic") is not None:
            self.result["guard_max_start_gap_seconds"] = max(
                self.result["guard_max_start_gap_seconds"],
                scan_start - self.result["last_guard_started_monotonic"],
            )
        self.result["last_guard_started_monotonic"] = scan_start
        assert (
            time.monotonic() - self.started < (600 if self.cleaning else 540)
            and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == self.boot
        )
        for pin in self.policy["protected12"]:
            v = self.inspect(pin["id"])
            assert not v.pop("paused")
            public = {k: v[k] for k in pin}
            assert public == pin
        g = self.policy["guard"]
        v = self.inspect(g["container_id"])
        assert (
            v["running"]
            and (not v["paused"])
            and (not v["oom"])
            and (v["restart"] == 0)
            and (v["pid"] == g["pid"])
            and (v["started_at"] == g["started_at"])
            and (self.birth(g["pid"]) == g["start_ticks"])
            and (self.boot == g["boot_id"])
        )
        app = Path("/var/tmp") / self.policy["deployment"]
        self.checked_file(app / "security.py", self.policy["security_sha256"])
        self.checked_file(app / "config/network.json", self.policy["network_policy_sha256"])
        self.checked_file(app / "holds/stage2-import.json", self.policy["hold_sha256"])
        hold = json.loads(file_bytes(app / "holds/stage2-import.json"))
        assert (
            all(hold[k] == v for k, v in self.policy["hold_identity"].items())
            and hold["review_at"] == self.policy["hold_review_at"]
        )
        remaining = (
            dt.datetime.fromisoformat(hold["review_at"]) - dt.datetime.now(dt.UTC)
        ).total_seconds()
        assert remaining > (
            self.policy["limits"]["initial_hold_remaining_seconds"] if initial else 0
        )
        status = json.loads(file_bytes(app / "authorization/containers.json", require_uid=0))
        assert (
            0 <= time.time() - status["utc"] < 90
            and status["policy_sha256"] == self.policy["guard_policy_sha256"]
            and (status["resource_exit"] == 0)
            and (not status.get("observation_error"))
        )
        for name, sha in self.policy["preseal_files"].items():
            assert (
                digest(file_bytes(Path(self.policy["preseal_root"]) / name, require_uid=None))
                == sha
            )
        assert (
            self.command(
                ["docker", "ps", "-q", "--no-trunc", "--filter", "label=io.swingset.guarded=true"]
            ).split()
            == []
        )
        free = os.statvfs(app).f_bavail * os.statvfs(app).f_frsize
        assert free >= self.policy["limits"]["minimum_free_bytes"]
        self.pg_mapping()
        self.result["guard_max_scan_seconds"] = max(
            self.result["guard_max_scan_seconds"], time.monotonic() - scan_start
        )
        self.result["samples"] += 1
        self.result["minimum_free_bytes"] = (
            free
            if self.result["minimum_free_bytes"] is None
            else min(free, self.result["minimum_free_bytes"])
        )

    def discover(self) -> None:
        mapping = self.pg_mapping()
        for value in self.command(
            ["docker", "top", self.policy["private_pg"]["id"], "-eo", "pid"]
        ).split():
            if not value.isdigit():
                continue
            pid = int(value)
            try:
                raw = Path("/proc", str(pid), "cmdline").read_bytes()
                argv = [x.decode() for x in raw.split(b"\x00") if x]
                if "packet_nonce=" + self.nonce not in argv:
                    continue
                group = self.host_cgroup(pid)
                vector = self.nspid(pid)
                assert (
                    group == mapping["cgroup"]
                    and len(vector) == len(mapping["nspid"])
                    and (vector[0] == pid)
                    and (vector[-1] > 1)
                )
                assert Path(argv[0]).name in {"timeout", "psql"}
                record = {
                    "pid": pid,
                    "namespace_pid": vector[-1],
                    "nspid": vector,
                    "host_cgroup": group,
                    "start_ticks": self.birth(pid),
                    "argv": argv,
                    "argv_sha256": digest(raw),
                    "boot_id": self.boot,
                    "process_group": os.getpgid(pid),
                }
                if pid not in self.tracked:
                    self.tracked[pid] = record
            except FileNotFoundError:
                continue

    def incarnation_alive(self, record: dict[str, Any]) -> bool:
        try:
            fields = Path("/proc", str(record["pid"]), "stat").read_text().rsplit(")", 1)[1].split()
            return fields[19] == record["start_ticks"] and fields[0] != "Z"
        except FileNotFoundError:
            return False

    def still_owned(self, record: dict[str, Any]) -> bool:
        try:
            return (
                self.incarnation_alive(record)
                and self.birth(record["pid"]) == record["start_ticks"]
                and (
                    digest(Path("/proc", str(record["pid"]), "cmdline").read_bytes())
                    == record["argv_sha256"]
                )
                and (self.host_cgroup(record["pid"]) == record["host_cgroup"])
                and (self.nspid(record["pid"]) == record["nspid"])
                and (
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip() == record["boot_id"]
                )
            )
        except FileNotFoundError:
            return False

    def signal_namespace(self, record: dict[str, Any], sig: str) -> None:
        mapping = self.pg_mapping()
        assert self.still_owned(record) and record["host_cgroup"] == mapping["cgroup"]
        argv = [
            "docker",
            "exec",
            "-i",
            "--user",
            "postgres",
            self.policy["private_pg"]["id"],
            "/bin/sh",
            "-s",
            "--",
            str(record["namespace_pid"]),
            record["start_ticks"],
            record["argv_sha256"],
            self.nonce,
            sig,
            mapping["start_ticks"],
        ]
        remaining = 600 - (time.monotonic() - self.started)
        assert remaining > 0
        v = subprocess.run(
            argv,
            input=self.kill_script,
            capture_output=True,
            timeout=min(10, remaining),
            check=False,
        )
        assert len(v.stdout) <= 256 and len(v.stderr) <= 1024
        self.result.setdefault("namespace_signals", []).append(
            {
                "host_pid": record["pid"],
                "namespace_pid": record["namespace_pid"],
                "start_ticks": record["start_ticks"],
                "signal": sig,
                "exit": v.returncode,
                "status": v.stdout.decode().strip(),
            }
        )
        assert v.returncode == 0 and v.stdout.decode().strip() in {"absent", "signalled-" + sig}

    def stop_owned(self) -> None:
        try:
            self.discover()
        except BaseException:
            pass
        for sig in ("TERM", "KILL"):
            for record in self.tracked.values():
                if self.incarnation_alive(record):
                    assert self.still_owned(record)
                    self.signal_namespace(record, sig)
            until = min(self.started + 590, time.monotonic() + 5)
            while time.monotonic() < until and any(
                self.incarnation_alive(v) for v in self.tracked.values()
            ):
                time.sleep(0.1)
        if self.process is not None and self.process.poll() is None:
            record = self.result["docker_exec_client"]
            assert (
                self.birth(self.process.pid) == record["start_ticks"]
                and self.host_cgroup(self.process.pid) == record["host_cgroup"]
                and (
                    digest(Path("/proc", str(self.process.pid), "cmdline").read_bytes())
                    == record["argv_sha256"]
                )
            )
            os.kill(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=min(5, max(0.1, self.started + 595 - time.monotonic())))
            except subprocess.TimeoutExpired:
                assert (
                    self.birth(self.process.pid) == record["start_ticks"]
                    and self.host_cgroup(self.process.pid) == record["host_cgroup"]
                    and (
                        digest(Path("/proc", str(self.process.pid), "cmdline").read_bytes())
                        == record["argv_sha256"]
                    )
                )
                os.kill(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=min(3, max(0.1, self.started + 598 - time.monotonic())))
        self.discover()
        assert not any(self.incarnation_alive(v) for v in self.tracked.values())

    def verify_sections(self, path: Path) -> None:
        expected = self.policy["expected_sections"]
        limit = self.policy["limits"]["public_output_bytes"] - 65536
        if limit <= 0 or not expected or len(set(expected)) != len(expected):
            raise ValueError("Invalid reviewed section limits")
        with open(path, "rb") as stream:
            raw = stream.read(limit + 1)
        documents = decode_documents(raw, maximum_bytes=limit, maximum_documents=len(expected))
        sections = []
        for value in documents:
            sections.append(value["section"])
            assert value["complete"] is True
            if value["section"] in {"identity", "identity_final"}:
                assert (
                    value["schema"] == self.policy["source_schema"]
                    and value["identity"]
                    == {"environment": "dev", "database_id": self.policy["database_id"]}
                    and (value["database_oid"] == self.policy["database_oid"])
                )
            if value["section"].startswith("link/"):
                identifier = value["section"].removeprefix("link/")
                targets = [
                    target
                    for target in self.policy["targets"]
                    if target["unit"] == "link/event/" + identifier
                ]
                if not identifier or len(targets) != 1:
                    raise ValueError("Link section requires one exact reviewed event target")
                target = targets[0]
                label = hashlib.sha256()
                for row in value["retained"]:
                    item = [row["table_name"], row["record_key"], row["payload_json"]]
                    label.update(
                        json.dumps(
                            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        ).encode()
                        + b"\n"
                    )
                assert label.hexdigest() == target["expected"][0]
            if value["section"] == "units":
                for row in value["rows"]:
                    for eq in row["newest_equivalences"]:
                        assert eq["recipe_bytes"] is not None and eq["recipe_bytes"] <= 262144
            if value["section"] == "dependencies":
                for d in value["sets"]:
                    if d["present"]:
                        raw = d["manifest_json"].encode()
                        assert digest(raw) == d["id"]
        assert sections == expected
        self.result["sections"] = sections

    def execute(self) -> dict[str, Any]:
        try:
            self.guards(initial=True)
            image = json.loads(
                self.command(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        '{"id":{{json .Id}},"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}',
                        self.policy["runtime_image"],
                    ]
                )
            )
            assert image == {
                "id": self.policy["runtime_image"],
                "revision": self.policy["source_revision"],
            }
            self.result["image"] = image
            argv = [
                "docker",
                "exec",
                "-i",
                "--user",
                "postgres",
                "--env",
                "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=45000",
                "--env",
                "PGAPPNAME=" + self.nonce,
                self.policy["private_pg"]["id"],
                "timeout",
                "--signal=TERM",
                "--kill-after=5s",
                "510s",
                "psql",
                "-X",
                "-q",
                "-A",
                "-t",
                "-w",
                "-U",
                "postgres",
                "-d",
                self.policy["database"],
                "-v",
                "ON_ERROR_STOP=1",
                "-v",
                "packet_nonce=" + self.nonce,
                "-f",
                "-",
            ]
            self.process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            until = time.monotonic() + 2
            while True:
                client_raw = Path("/proc", str(self.process.pid), "cmdline").read_bytes()
                client_argv = [v.decode() for v in client_raw.split(b"\x00") if v]
                if (
                    client_argv
                    and Path(client_argv[0]).name == "docker"
                    and (client_argv[1:] == argv[1:])
                ):
                    break
                assert self.process.poll() is None and time.monotonic() < until
                time.sleep(0.01)
            self.result["docker_exec_client"] = {
                "pid": self.process.pid,
                "start_ticks": self.birth(self.process.pid),
                "process_group": os.getpgid(self.process.pid),
                "boot_id": self.boot,
                "argv": client_argv,
                "argv_sha256": digest(client_raw),
                "host_cgroup": self.host_cgroup(self.process.pid),
            }
            until = time.monotonic() + 5
            while time.monotonic() < until:
                self.discover()
                if any(Path(v["argv"][0]).name == "psql" for v in self.tracked.values()):
                    break
                if self.process.poll() is not None:
                    raise RuntimeError("Local socket psql refused before SQL")
                time.sleep(0.1)
            assert any(Path(v["argv"][0]).name == "psql" for v in self.tracked.values())
            self.save(
                "owner.json",
                {
                    "nonce": self.nonce,
                    "boot_id": self.boot,
                    "policy_sha256": digest(self.policy_raw),
                    "root": {
                        "path": str(self.directory),
                        "uid": self.directory.stat().st_uid,
                        "device": self.directory.stat().st_dev,
                        "inode": self.directory.stat().st_ino,
                    },
                    "client": self.result["docker_exec_client"],
                    "owned_processes": list(self.tracked.values()),
                    "private_pg_incarnation": self.policy["private_pg_incarnation"],
                    "kill_client_sha256": self.policy["kill_client_sha256"],
                },
            )
            print(
                json.dumps(
                    {
                        "phase": "query_capture_started",
                        "nonce": self.nonce,
                        "docker_exec_client_pid": self.process.pid,
                        "read_only": True,
                    }
                ),
                flush=True,
            )
            assert self.process.stdin is not None
            assert self.process.stdout is not None and self.process.stderr is not None
            self.process.stdin.write(self.sql)
            self.process.stdin.close()
            selector = selectors.DefaultSelector()
            selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            cap = self.policy["limits"]["public_output_bytes"] - 65536
            total = 0
            stderr = b""
            next_check = 0.0
            with open(self.output / "data.jsonl", "xb") as output:
                while selector.get_map():
                    if time.monotonic() >= next_check:
                        self.guards()
                        self.discover()
                        next_check = time.monotonic() + 10
                        print(
                            json.dumps(
                                {
                                    "phase": "query_capture",
                                    "nonce": self.nonce,
                                    "public_bytes": total,
                                    "samples": self.result["samples"],
                                }
                            ),
                            flush=True,
                        )
                    for key, _ in selector.select(timeout=0.5):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stdout":
                            if total + len(chunk) > cap:
                                raise RuntimeError("Aggregate public output limit reached")
                            output.write(chunk)
                            total += len(chunk)
                        else:
                            if len(stderr) + len(chunk) > 16384:
                                raise RuntimeError("psql diagnostic output limit reached")
                            stderr += chunk
                output.flush()
                os.fsync(output.fileno())
            self.result["psql_exit_code"] = self.process.wait(timeout=5)
            with open(self.output / "psql-stderr.txt", "xb") as f:
                f.write(stderr)
                f.flush()
                os.fsync(f.fileno())
            assert self.result["psql_exit_code"] == 0
            self.verify_sections(self.output / "data.jsonl")
            self.guards()
            self.discover()
            assert not any(self.incarnation_alive(v) for v in self.tracked.values())
            self.result["complete"] = True
        except BaseException as error:
            self.result["error_type"] = type(error).__name__
            if isinstance(error, CommandFailed):
                self.result["command_failure"] = error.safe_details()
            trace = error.__traceback__
            assert trace is not None
            while trace.tb_next is not None:
                trace = trace.tb_next
            self.result["error_line"] = trace.tb_lineno
        finally:
            self.cleaning = True
            try:
                self.stop_owned()
                self.result["owned_processes_absent"] = True
            except BaseException as error:
                self.result["cleanup_error_type"] = type(error).__name__
                self.result["complete"] = False
            self.result["owned_processes"] = list(self.tracked.values())
            if self.process is not None:
                self.result["docker_exec_client_exit"] = self.process.poll()
            try:
                check = (
                    "BEGIN READ ONLY; SET LOCAL ROLE swingset_worker; SELECT count(*) FROM pg_stat_activity WHERE datname='"
                    + self.policy["database"]
                    + "' AND application_name='"
                    + self.nonce
                    + "'; ROLLBACK;"
                )
                base = [
                    "docker",
                    "exec",
                    "--user",
                    "postgres",
                    "--env",
                    "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=5000",
                    self.policy["private_pg"]["id"],
                    "psql",
                    "-X",
                    "-q",
                    "-A",
                    "-t",
                    "-w",
                    "-U",
                    "postgres",
                    "-d",
                    self.policy["database"],
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    check,
                ]
                until = min(self.started + 595, time.monotonic() + 10)
                while True:
                    self.result["remaining_named_backends"] = int(self.command(base).strip())
                    if self.result["remaining_named_backends"] == 0:
                        break
                    assert time.monotonic() < until
                    time.sleep(0.2)
                self.guards()
                self.result["post_guards_passed"] = True
            except BaseException as error:
                self.result["postcheck_error_type"] = type(error).__name__
                self.result["complete"] = False
            self.result["files"] = {}
            try:
                for name in ["data.jsonl", "psql-stderr.txt", "owner.json"]:
                    path = self.output / name
                    if path.exists():
                        d = hashlib.sha256()
                        count = 0
                        with open(path, "rb") as stream:
                            os.fsync(stream.fileno())
                            while chunk := stream.read(1048576):
                                d.update(chunk)
                                count += len(chunk)
                        self.result["files"][name] = {"bytes": count, "sha256": d.hexdigest()}
            except BaseException as error:
                self.result["file_hash_error_type"] = type(error).__name__
                self.result["complete"] = False
            self.finish()
        return self.result

    def finish(self) -> None:
        self.result["overall_alarm_fired"] = self.alarm_fired
        self.result["complete"] = (
            self.result["complete"]
            and (not self.alarm_fired)
            and (time.monotonic() - self.started < 600)
        )
        self.result.pop("last_guard_started_monotonic", None)
        self.result["seconds"] = round(time.monotonic() - self.started, 3)
        self.result["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
        self.save("result.json", self.result)


def run(directory: Path, review: Path) -> dict[str, Any]:
    """Consume one reviewed private packet; always restore the caller's signals."""
    if sys.flags.optimize:
        raise RuntimeError("Graph forensics requires Python safety assertions")
    started = time.monotonic()
    previous_umask = os.umask(0o077)
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM)}
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    try:
        capture = _Capture(directory, review)

        def alarm_expired(signum: int, frame: Any) -> None:
            capture.alarm_fired = True
            raise TimeoutError("Overall600-second capture alarm")

        def interrupted(signum: int, frame: Any) -> None:
            raise InterruptedError("Operator signal")

        signal.signal(signal.SIGALRM, alarm_expired)
        signal.signal(signal.SIGTERM, interrupted)
        signal.setitimer(signal.ITIMER_REAL, 600)
        return capture.execute()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if previous_timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, previous_timer[0] - (time.monotonic() - started)),
                previous_timer[1],
            )
        os.umask(previous_umask)


DEPENDENCY_RECEIPT = "1368526ea8a0eb64540b8aa8994948059316a966d98a909e93c2970f6b1f7fe4"
DEPENDENCY_CONTEXT = "2dd0e1133c969450da13c7ae0ddcc40ec62f29d7cb2ffe76cecb9ccea846a336"
DEPENDENCY_CHECKPOINT = "00c831a474ecfa1de29ca0e8402366e33ed62cd5d409a0f294d9db9c4a0e2f37"
DEPENDENCY_SOURCE = {
    "database": "sst_copy_c696fead2daf4d3c83adfbbe_0",
    "oid": 16396,
    "owner": "postgres",
    "comment": "swingset-scratch:c696fead2daf4d3c83adfbbe",
    "environment": "dev",
    "database_id": "2f58b861-ebd6-4fd6-a9aa-44a020589439",
    "schema_version": "38",
    "source_schema_version": "37",
    "postgres_migration_checksum": "f3ff9118fc0c0ac721e24336208397318fbca72773fc9939099422a9749d3c68",
}
DEPENDENCY_REQUEST = "swingset-inventory-dependency-request-v1"
DEPENDENCY_STREAM = "swingset-inventory-dependency-stream-v1"
DEPENDENCY_ROOT_BYTES = 262144
DEPENDENCY_CHUNK_BYTES = 32768
DEPENDENCY_REPORT_BYTES = 262144


class DependencyWitnessRefused(ValueError):
    """A fixed dependency witness failed without exposing its input."""


def _dependency_require(value: object) -> None:
    if not value:
        raise DependencyWitnessRefused()


def _dependency_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _dependency_require(key not in value)
        value[key] = item
    return value


def _dependency_constant(value: str) -> None:
    raise DependencyWitnessRefused()


def _dependency_canonical(value: object) -> str:
    # Match derivations.canonical, including float/int spelling and allow_nan.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dependency_digest(value: object) -> str:
    return digest(_dependency_canonical(value).encode())


def _dependency_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _dependency_generation(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"dg_[0-9a-f]{64}", value) is not None


def _dependency_fields(value: Any, fields: set[str]) -> None:
    _dependency_require(type(value) is dict and set(value) == fields)


def _dependency_label(value: Any) -> None:
    _dependency_require(
        type(value) is list
        and len(value) == 2
        and _dependency_hash(value[0])
        and type(value[1]) is int
        and 0 <= value[1] < 2**63
    )


def _dependency_request(raw: bytes, expected: str) -> dict[str, Any]:
    _dependency_require(_dependency_hash(expected) and len(raw) <= 1048576)
    _dependency_require(digest(raw) == expected)
    request: dict[str, Any] = json.loads(
        raw, object_pairs_hook=_dependency_unique, parse_constant=_dependency_constant
    )
    _dependency_fields(
        request,
        {
            "format",
            "graph_receipt_sha256",
            "graph_context_sha256",
            "source_checkpoint",
            "source_boundary_sha256",
            "inventory_generation_id",
            "inventory_group_sha256",
            "roots",
            "units",
        },
    )
    _dependency_require(
        request["format"] == DEPENDENCY_REQUEST
        and request["graph_receipt_sha256"] == DEPENDENCY_RECEIPT
        and request["graph_context_sha256"] == DEPENDENCY_CONTEXT
        and request["source_checkpoint"] == DEPENDENCY_CHECKPOINT
        and _dependency_hash(request["source_boundary_sha256"])
        and _dependency_generation(request["inventory_generation_id"])
    )
    selected = [
        {
            "kind": "derivation",
            "key": ["project", "inventory", "all"],
            "generation_id": request["inventory_generation_id"],
        }
    ]
    _dependency_require(request["inventory_group_sha256"] == _dependency_digest(selected))
    roots, units = request["roots"], request["units"]
    # The selected inventory group also consumes one top-level root slot.
    _dependency_require(type(roots) is list and 0 < len(roots) < 128)
    _dependency_require(all(_dependency_hash(root) for root in roots))
    _dependency_require(roots == sorted(set(roots)))
    _dependency_require(type(units) is list and 0 < len(units) <= 248)
    names = []
    for unit in units:
        _dependency_fields(
            unit,
            {
                "unit",
                "generation_id",
                "label",
                "graph_found_label",
                "expected_root",
                "found_root",
                "stored_versions",
            },
        )
        name = unit["unit"]
        _dependency_require(
            isinstance(name, str)
            and re.fullmatch(r"project/(?:resolution/[a-z0-9][a-z0-9-]{0,255}|year/202[35])", name)
            and _dependency_generation(unit["generation_id"])
            and _dependency_hash(unit["expected_root"])
            and _dependency_hash(unit["found_root"])
            and unit["expected_root"] != unit["found_root"]
        )
        _dependency_label(unit["label"])
        _dependency_label(unit["graph_found_label"])
        recipe = "recipe/code/" + "/".join(name.split("/")[:2])
        _dependency_require(
            unit["label"] == unit["graph_found_label"]
            and unit["stored_versions"]
            == {recipe: "5a79f84d820d759357fd719f286d7ba18341406645535e99982ad1d30f93f000"}
        )
        names.append(name)
    _dependency_require(names == sorted(set(names)))
    _dependency_require(set(roots) == {unit["expected_root"] for unit in units})
    return request


class _DependencyStream:
    """The one fixed dependency protocol; root bodies never survive one root."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.started = time.monotonic()
        self.bytes = 0

    def frame(self, kind: str, fields: set[str]) -> dict[str, Any]:
        _dependency_require(time.monotonic() - self.started < 600)
        raw = self.stream.readline(65537)
        self.bytes += len(raw)
        _dependency_require(
            0 < len(raw) <= 65536
            and raw.endswith(b"\n")
            and self.bytes <= 64 * 1024 * 1024
            and time.monotonic() - self.started < 600
        )
        value: dict[str, Any] = json.loads(
            raw, object_pairs_hook=_dependency_unique, parse_constant=_dependency_constant
        )
        _dependency_fields(value, fields | {"kind"})
        _dependency_require(value["kind"] == kind)
        return value

    def root(self, expected: str, inventory: str) -> tuple[str | None, int]:
        begin = self.frame("root_begin", {"id", "raw_bytes"})
        size = begin["raw_bytes"]
        _dependency_require(
            begin["id"] == expected and type(size) is int and 2 <= size <= DEPENDENCY_ROOT_BYTES
        )
        raw = bytearray()
        while len(raw) < size:
            chunk = self.frame("root_chunk", {"offset", "base64"})
            _dependency_require(type(chunk["offset"]) is int and chunk["offset"] == len(raw))
            _dependency_require(isinstance(chunk["base64"], str))
            part = base64.b64decode(chunk["base64"], validate=True)
            _dependency_require(len(part) == min(DEPENDENCY_CHUNK_BYTES, size - len(raw)))
            raw.extend(part)
        end = self.frame("root_end", {"id"})
        _dependency_require(end["id"] == expected and digest(bytes(raw)) == expected)
        # Framing rejects nonfinite constants; retained canonical TEXT keeps the
        # pipeline's original JSON number semantics, without normalization.
        members = json.loads(raw, object_pairs_hook=_dependency_unique)
        _dependency_require(type(members) is list and all(type(x) is dict for x in members))
        ordered = sorted(members, key=_dependency_canonical)
        _dependency_require(_dependency_digest(members) == expected)
        _dependency_require(_dependency_digest(ordered) == expected)
        selected = [
            x
            for x in members
            if x.get("kind") == "dependency_set" and x.get("key") == "selected_project_inventory"
        ]
        if len(selected) != 1:
            return None, len(selected)
        _dependency_fields(selected[0], {"kind", "key", "fingerprint"})
        _dependency_require(_dependency_hash(selected[0]["fingerprint"]))
        changed = [dict(x, fingerprint=inventory) if x is selected[0] else x for x in members]
        return _dependency_digest(sorted(changed, key=_dependency_canonical)), 1


def _dependency_witness(raw: bytes, expected: str, stream: BinaryIO) -> dict[str, Any]:
    request = _dependency_request(raw, expected)
    reader = _DependencyStream(stream)
    header = reader.frame("header", {"format", "request_sha256", "source", "snapshot"})
    snapshot = header["snapshot"]
    _dependency_fields(
        snapshot,
        {
            "backend_pid",
            "pg_snapshot",
            "default_transaction_read_only",
            "transaction_read_only",
            "isolation",
            "statement_timeout",
            "lock_timeout",
            "transaction_timeout",
        },
    )
    _dependency_require(
        header["format"] == DEPENDENCY_STREAM
        and header["request_sha256"] == expected
        and header["source"] == DEPENDENCY_SOURCE
        and type(snapshot["backend_pid"]) is int
        and snapshot["backend_pid"] > 0
        and isinstance(snapshot["pg_snapshot"], str)
        and re.fullmatch(r"[0-9]+:[0-9]+:(?:[0-9]+(?:,[0-9]+)*)?", snapshot["pg_snapshot"])
        and snapshot["default_transaction_read_only"] == snapshot["transaction_read_only"] == "on"
        and snapshot["isolation"] == "repeatable read"
        and snapshot["statement_timeout"] == "15s"
        and snapshot["lock_timeout"] == "2s"
        and snapshot["transaction_timeout"] == "10min"
    )
    inventory = reader.frame("inventory", {"pointer_generation_id", "selected_generation_id"})
    _dependency_require(
        inventory["pointer_generation_id"]
        == inventory["selected_generation_id"]
        == request["inventory_generation_id"]
    )
    roots = {
        root: reader.root(root, request["inventory_group_sha256"]) for root in request["roots"]
    }
    results = []
    for unit in request["units"]:
        actual = reader.frame(
            "unit",
            {
                "unit",
                "pointer_generation_id",
                "generation_id",
                "label",
                "dependency_id",
                "stored_versions",
            },
        )
        _dependency_require(
            actual["unit"] == unit["unit"]
            and actual["pointer_generation_id"] == actual["generation_id"] == unit["generation_id"]
            and type(actual["label"]) is list
            and actual["label"] == unit["label"]
            and actual["dependency_id"] == unit["expected_root"]
            and actual["stored_versions"] == unit["stored_versions"]
        )
        _dependency_label(actual["label"])
        candidate, count = roots[unit["expected_root"]]
        results.append(
            {
                "unit": unit["unit"],
                "original_binding_sha256": _dependency_digest(actual),
                "original_root": unit["expected_root"],
                "counterfactual_root": candidate,
                "found_root": unit["found_root"],
                "inventory_entry_count": count,
                "explained": candidate == unit["found_root"],
            }
        )
    footer = reader.frame("committed", {"request_sha256", "roots", "units"})
    _dependency_require(
        footer["request_sha256"] == expected
        and type(footer["roots"]) is int
        and type(footer["units"]) is int
        and footer["roots"] == len(roots)
        and footer["units"] == len(results)
        and stream.read(1) == b""
        and time.monotonic() - reader.started < 600
    )
    report = {
        "format": "swingset-inventory-dependency-witness-v1",
        "complete": True,
        "request_sha256": expected,
        "graph_receipt_sha256": DEPENDENCY_RECEIPT,
        "graph_context_sha256": DEPENDENCY_CONTEXT,
        "source": DEPENDENCY_SOURCE,
        "source_checkpoint": {
            "sha256": DEPENDENCY_CHECKPOINT,
            "basis": "accepted_prior_boundary",
            "boundary_sha256": request["source_boundary_sha256"],
        },
        "inventory_group_sha256": request["inventory_group_sha256"],
        "inventory_basis": "source_scope_pointer_not_runtime_active_group",
        "original_label_basis": "stored_generation_metadata_and_reviewed_prior_source_boundary",
        "roots": len(roots),
        "top_level_roots_including_inventory": len(roots) + 1,
        "units": results,
        "remaining_units": [row["unit"] for row in results if not row["explained"]],
        "committed_footer_and_EOF": True,
        "source_process_exit_not_observed": True,
        "operation_authority": False,
    }
    _dependency_require(
        len(json.dumps(report, sort_keys=True).encode()) + 1 <= DEPENDENCY_REPORT_BYTES
    )
    return report


def dependency_witness(
    path: Path, expected: str, stream: BinaryIO, *, emit: bool = False
) -> dict[str, Any]:
    """Read only public request bytes and stdin; never open a database or output file.

    A reviewed operation must additionally prove source/reader exits zero and
    that the footer follows SQL COMMIT. This worker grants no operation authority.
    """
    if sys.flags.optimize or signal.getitimer(signal.ITIMER_REAL)[0]:
        raise DependencyWitnessRefused()
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum: int, frame: Any) -> None:
        raise TimeoutError()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 600)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as request:
            info = os.fstat(request.fileno())
            _dependency_require(
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and info.st_size <= 1048576
                and info.st_uid in {0, os.geteuid()}
                and not info.st_mode & 0o022
            )
            raw = request.read(1048577)
        report = _dependency_witness(raw, expected, stream)
        if emit:
            # Keep the absolute worker alarm through encoding and stdout flush,
            # including a blocked downstream report consumer.
            print(json.dumps(report, sort_keys=True), flush=True)
        return report
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--dependency-request", type=Path)
    parser.add_argument("--request-sha256")
    args = parser.parse_args(argv)
    if args.dependency_request is not None:
        if args.directory is not None or args.review is not None or args.request_sha256 is None:
            parser.error("Dependency stdin mode requires only its request and SHA256")
        try:
            dependency_witness(
                args.dependency_request, args.request_sha256, sys.stdin.buffer, emit=True
            )
        except Exception as error:
            print(
                json.dumps({"complete": False, "error_type": type(error).__name__}, sort_keys=True)
            )
            return 1
        return 0
    if args.directory is None or args.review is None or args.request_sha256 is not None:
        parser.error("Capture mode requires its directory and review")
    try:
        result = run(args.directory, args.review)
    except Exception as error:
        failure: dict[str, Any] = {"complete": False, "error_type": type(error).__name__}
        if isinstance(error, CommandFailed):
            failure["command_failure"] = error.safe_details()
        print(json.dumps(failure, sort_keys=True))
        return 1
    path = args.directory / "capture" / "result.json"
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "result": str(path),
                "sha256": digest(path.read_bytes()),
            },
            sort_keys=True,
        )
    )
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
