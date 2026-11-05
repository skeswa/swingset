"""One invocation after independent GO; no changes to production or repo code."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, TextIO

from .reference_policy import FILES, validate_policy, validate_review

ENDPOINT = "root@swingset@orb"


def stamp() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def free_bytes() -> int:
    a = os.statvfs("/private/tmp")
    return a.f_bavail * a.f_frsize


def reject_active_tests() -> None:
    rows = subprocess.run(
        ["ps", "-axo", "pid,ppid,pgid,command"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for row in rows[1:]:
        parts = row.strip().split(None, 3)
        if len(parts) != 4 or int(parts[0]) == os.getpid():
            continue
        command = parts[3]
        if re.search(
            r"/(?:python[0-9.]*(?: -m)? (?:pytest|swingset.dev.commands (?:test|check))|pytest(?: |$))",
            command,
        ):
            raise RuntimeError("native tests are active; serialize reference allocation")
    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    if any(x.startswith("swingset-core-") for x in names):
        raise RuntimeError("core clients are active; serialize reference allocation")


def fetch_authoritative_receipt(
    client_env: dict[str, str],
    policy: dict[str, Any],
    host_root: Path,
    invocation_id: str,
    hashes: dict[str, str],
    policy_sha256: str,
    review_sha256: str,
) -> dict[str, Any]:
    endpoint = "root@swingset@orb"
    remote = policy["vm_root"] + "/result.json"
    target = host_root / "authoritative-source-result.json"
    code = "import hashlib,json,os,stat,sys; p=sys.argv[1]; f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW); s=os.fstat(f); b=os.read(f,s.st_size+1); assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and len(b)==s.st_size; print(json.dumps({'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'device':s.st_dev,'inode':s.st_ino}))"
    remote_command = shlex.join(["/var/lib/swingset/venv/bin/python", "-c", code, remote])
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15"]

    def fingerprint() -> dict[str, Any]:
        r = subprocess.run(
            ["/usr/bin/ssh", *options, endpoint, remote_command],
            env=client_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode:
            raise RuntimeError("authoritative source receipt fingerprint failed")
        value: dict[str, Any] = json.loads(r.stdout)
        if not re.fullmatch("[0-9a-f]{64}", value["sha256"]):
            raise RuntimeError("invalid authoritative receipt fingerprint")
        return value

    before = fingerprint()
    batch = f"get {remote} {target}\n"
    (host_root / "receipt-sftp.batch").write_text(batch)
    r = subprocess.run(
        ["/usr/bin/sftp", "-q", "-B131072", "-R32", "-b", "-", *options, endpoint],
        input=batch,
        env=client_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (host_root / "receipt-sftp.stdout").write_text(r.stdout)
    (host_root / "receipt-sftp.stderr").write_text(r.stderr)
    if r.returncode:
        raise RuntimeError("authoritative source receipt SFTP failed")
    after = fingerprint()
    body = target.read_bytes()
    if (
        before != after
        or len(body) != before["bytes"]
        or hashlib.sha256(body).hexdigest() != before["sha256"]
    ):
        raise RuntimeError("authoritative SFTP receipt does not match stable source fingerprint")
    receipt: dict[str, Any] = json.loads(body)
    if (
        receipt.get("nonce") != policy["nonce"]
        or receipt.get("operation") != policy["operation"]
        or receipt.get("invocation_id") != invocation_id
        or receipt.get("script_sha256") != hashes
        or receipt.get("policy_sha256") != policy_sha256
        or receipt.get("review_sha256") != review_sha256
    ):
        raise RuntimeError("authoritative source receipt identity differs")
    (host_root / "receipt-transfer-verification.json").write_text(
        json.dumps(
            {
                "endpoint": endpoint,
                "remote_path": remote,
                "before": before,
                "after": after,
                "local_sha256": hashlib.sha256(body).hexdigest(),
                "verified": True,
            },
            indent=2,
        )
        + "\n"
    )
    return receipt


def run(policy_path: Path, review_path: Path) -> dict[str, object]:
    """Run one independently reviewed reference operation and verify its SFTP receipt."""
    policy_bytes = policy_path.read_bytes()
    review_bytes = review_path.read_bytes()
    policy = validate_policy(json.loads(policy_bytes))
    review = json.loads(review_bytes)
    sources = {name: Path(__file__).with_name(name).read_text() for name in FILES}
    hashes = {name: hashlib.sha256(body.encode()).hexdigest() for name, body in sources.items()}
    validate_review(review, policy, policy_bytes, hashes)
    reject_active_tests()
    initial_free = free_bytes()
    if initial_free < policy["stop_free_bytes"] + 13 * 1024**3:
        raise RuntimeError("insufficient safe Mac storage budget")
    host_root = Path("/tmp") / ("swingset-stage2-reference-r-run-" + policy["nonce"])
    if host_root.exists() or host_root.is_symlink():
        raise RuntimeError("refuse existing host operation ledger")
    host_root.mkdir(mode=0o700)
    awake = subprocess.Popen(
        ["/usr/bin/caffeinate", "-dimsu", "-w", str(os.getpid())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    payload: dict[str, Any] = {
        "policy_text": policy_bytes.decode(),
        "review_text": review_bytes.decode(),
        "runner_sources": sources,
        "invocation_id": uuid.uuid4().hex,
        "host_free_bytes": initial_free,
        "script_sha256": hashes,
    }
    command = [
        "orb",
        "run",
        "-m",
        "swingset",
        "-u",
        "root",
        "env",
        "-i",
        "PATH=/run/current-system/sw/bin:/run/wrappers/bin",
        "PYTHONDONTWRITEBYTECODE=1",
        policy["runtime_python"],
        "-c",
        sources["reference_workspace.py"],
        "stage",
    ]
    metadata = {
        **policy,
        "started_at": stamp(),
        "pid": os.getpid(),
        "host_free_before": initial_free,
        "script_sha256": hashes,
        "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "command_prefix": command[:12],
        "exclusive_large_job_preflight": True,
        "owned_caffeinate_pid": awake.pid,
        "invocation_id": payload["invocation_id"],
    }
    (host_root / "before.json").write_text(json.dumps(metadata, indent=2) + "\n")
    client_env = {
        k: os.environ[k]
        for k in ["PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "SSH_AUTH_SOCK"]
        if k in os.environ
    }
    child = subprocess.Popen(
        command,
        env=client_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        bufsize=1,
    )
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()

    def read(stream: TextIO, kind: str) -> None:
        for line in stream:
            events.put((kind, line))
        events.put((kind, None))

    threading.Thread(target=read, args=(child.stdout, "stdout"), daemon=True).start()
    threading.Thread(target=read, args=(child.stderr, "stderr"), daemon=True).start()
    result = None
    stream_parse_errors = 0
    stop = False
    last_lease = 0.0
    started = time.monotonic()
    minimum_free = initial_free
    ended: set[str] = set()
    stop_started = None
    with (host_root / "events.jsonl").open("x") as out, (host_root / "stderr.log").open("x") as err:
        child.stdin.write(json.dumps(payload) + "\n")
        child.stdin.flush()
        while child.poll() is None or len(ended) < 2:
            available = free_bytes()
            minimum_free = min(minimum_free, available)
            stop = (
                stop or available < policy["stop_free_bytes"] or time.monotonic() - started > 5500
            )
            if stop and stop_started is None:
                stop_started = time.monotonic()
            if (
                stop_started is not None
                and time.monotonic() - stop_started > 45
                and child.poll() is None
            ):
                child.stdin.close()
                if os.getpgid(child.pid) == child.pid:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=15)
            if child.poll() is None and time.monotonic() - last_lease >= 1:
                lease = {
                    "nonce": policy["nonce"],
                    "host_free_bytes": available,
                    "stop": stop,
                    "utc": stamp(),
                }
                try:
                    child.stdin.write(json.dumps(lease) + "\n")
                    child.stdin.flush()
                except BrokenPipeError:
                    pass
                last_lease = time.monotonic()
            try:
                kind, line = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                ended.add(kind)
                continue
            if kind == "stderr":
                err.write(line)
                err.flush()
                continue
            out.write(line)
            out.flush()
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                stream_parse_errors += 1
                continue
            if item.get("kind") == "result":
                result = item
            if item.get("kind") == "progress":
                print(json.dumps(item), flush=True)
        child.wait(timeout=30)
    if not child.stdin.closed:
        child.stdin.close()
    authoritative = fetch_authoritative_receipt(
        client_env,
        policy,
        host_root,
        payload["invocation_id"],
        hashes,
        hashlib.sha256(policy_bytes).hexdigest(),
        hashlib.sha256(review_bytes).hexdigest(),
    )
    final = {
        "finished_at": stamp(),
        "exit_code": child.returncode,
        "minimum_mac_free_bytes": minimum_free,
        "host_free_after": free_bytes(),
        "control_stream_result": result,
        "stream_parse_errors": stream_parse_errors,
        "authoritative_vm_result": authoritative,
        "script_sha256": hashes,
        "raw_logs": str(host_root),
        "no_deletion": True,
    }
    if minimum_free < policy["hard_floor_bytes"]:
        raise RuntimeError("hard disk floor violated; inspect retained resource ledger")
    print(
        json.dumps(
            {"result": authoritative.get("result"), "ledger": str(host_root / "result.json")}
        ),
        flush=True,
    )
    final["complete"] = (
        child.returncode == 0
        and authoritative.get("result") == "pass"
        and authoritative.get("owned_cgroup_empty") is True
        and authoritative.get("namespace_owner_absent") is True
        and authoritative.get("remaining_owned_loops") == []
    )
    (host_root / "result.json").write_text(json.dumps(final, indent=2) + "\n")
    if awake.poll() is None:
        awake.terminate()
        awake.wait(timeout=5)
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(argv)
    return 0 if run(args.policy, args.review)["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
