"""Own and supervise only the fresh reference-R sparse image/namespace."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast


def stage(payload: dict[str, Any]) -> None:
    """Place only the reviewed runner in its new root, then exec the supervisor."""
    policy = json.loads(payload["policy_text"])
    nonce = policy.get("nonce", "")
    if os.getuid() != 0 or not re.fullmatch("[a-f0-9]{12}", nonce):
        raise RuntimeError("reference staging requires root and a fresh nonce")
    root = Path("/var/tmp/swingset-stage2-reference-r-" + nonce)
    if policy.get("vm_root") != str(root):
        raise RuntimeError("reference staging path differs")
    sources = payload["runner_sources"]
    names = {"reference.py", "reference_policy.py", "reference_workspace.py", "reference_worker.py"}
    hashes = {name: hashlib.sha256(body.encode()).hexdigest() for name, body in sources.items()}
    review = json.loads(payload["review_text"])
    if (
        set(sources) != names
        or hashes != payload["script_sha256"]
        or review.get("script_sha256") != hashes
        or review.get("verdict") != "ACCEPT"
        or review.get("policy_sha256")
        != hashlib.sha256(payload["policy_text"].encode()).hexdigest()
        or review.get("nonce") != nonce
        or review.get("exclusive_large_job") is not True
    ):
        raise RuntimeError("reference staging review differs")
    if any(len(body.encode()) > 256 * 1024 for body in sources.values()):
        raise RuntimeError("reference runner exceeds its small staging bound")
    os.umask(0o077)
    root.mkdir(mode=0o700)
    metadata = root.lstat()
    start = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
    owner = {
        "pid": os.getpid(),
        "start_ticks": int(start),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "uid": metadata.st_uid,
        "invocation_id": payload["invocation_id"],
    }
    documents = {
        **sources,
        "payload.json": json.dumps(payload),
        "stage-owner.json": json.dumps(owner),
    }
    for name, body in documents.items():
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    os.execv(
        sys.executable,
        [
            sys.executable,
            str(root / "reference_workspace.py"),
            "supervise",
            str(root / "payload.json"),
        ],
    )


if __name__ == "__main__" and sys.argv[1:] == ["stage"]:
    stage(json.loads(sys.stdin.readline()))
    raise AssertionError("reference exec returned")

if __package__:
    from . import reference_policy as policy
else:
    # The checked standalone bundle runs outside the frozen application's package.
    import reference_policy as policy  # type: ignore[import-not-found,no-redef]

P: dict[str, Any]
ROOT: Path
IMAGE: Path
MOUNT: Path
MKFS: str
BOOT: str
START: float
CGROUP: Path


def configure(payload: dict[str, Any], *, operation: bool = False) -> None:
    global P, ROOT, IMAGE, MOUNT, MKFS, BOOT, START, CGROUP
    P = policy.decode_payload(payload) if operation else policy.validate_runtime(payload)
    ROOT = Path(P["vm_root"])
    IMAGE = ROOT / "filesystem.img"
    MOUNT = ROOT / "mounted"
    MKFS = P["mkfs"]
    BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    START = time.monotonic()
    CGROUP = Path("/sys/fs/cgroup/swingset-reference-r-" + P["nonce"])


def assert_stage_owner() -> None:
    found = identity(ROOT)
    owner = json.loads((ROOT / "stage-owner.json").read_text())
    if (
        ROOT.is_symlink()
        or found["mode"] != 0o700
        or any(found[name] != owner[name] for name in ("device", "inode", "uid"))
        or owner["uid"] != 0
        or owner["pid"] != os.getpid()
        or owner["start_ticks"] != process(os.getpid())["start_ticks"]
        or owner["boot_id"] != BOOT
        or owner["invocation_id"] != P["invocation_id"]
    ):
        raise RuntimeError("reference staging owner differs")


class ControlledStop(BaseException):
    pass


def stop_signal(signum: int, frame: Any) -> None:
    del frame
    raise ControlledStop("supervisor signal " + str(signum))


def stamp() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def identity(path: Path) -> dict[str, int]:
    s = path.lstat()
    return {
        "device": s.st_dev,
        "inode": s.st_ino,
        "uid": s.st_uid,
        "mode": stat.S_IMODE(s.st_mode),
        "logical_bytes": s.st_size,
        "allocated_bytes": s.st_blocks * 512,
    }


def process(pid: int) -> dict[str, Any]:
    raw = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "process_group": int(raw[2]), "start_ticks": int(raw[19]), "boot_id": BOOT}


def save(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".writing")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, sort_keys=True, default=str)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    if path.name in {"result.json", "owner.json", "reference-result.json"}:
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def emit(kind: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"kind": kind, "utc": stamp(), "nonce": P["nonce"], **fields},
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )


def log_tail(path: Path) -> str:
    with path.open("rb") as source:
        source.seek(max(0, source.seek(0, os.SEEK_END) - 16384))
        return source.read(16384).decode(errors="replace")


def command(argv: list[str], timeout: float = 30) -> str:
    return policy.checked_command(ROOT, argv, timeout)


def check_image() -> None:
    a = IMAGE.lstat()
    b = P["image_identity"]
    if not stat.S_ISREG(a.st_mode) or (a.st_dev, a.st_ino, a.st_uid) != (
        b["device"],
        b["inode"],
        b["uid"],
    ):
        raise RuntimeError("owned backend identity changed")
    if a.st_size != P["backend_cap_bytes"]:
        raise RuntimeError("owned backend capacity changed")
    if a.st_blocks * 512 > P["backend_cap_bytes"]:
        raise RuntimeError("backend physical allocation exceeds capacity")
    if a.st_blocks * 512 >= P["backend_cap_bytes"] - 128 * 1024**2:
        raise RuntimeError("backend physical allocation stop")


def check_loop_capacity(loop: str) -> int:
    capacity = int(command(["blockdev", "--getsize64", loop]).strip())
    check_image()
    if capacity != P["backend_cap_bytes"] or capacity != IMAGE.stat().st_size:
        raise RuntimeError("private loop capacity differs from backend cap")
    return capacity


def storage_guard() -> None:
    check_image()
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < P["stop_free_bytes"]:
        raise RuntimeError("VM free-space stop")
    lease = json.loads((ROOT / "lease.json").read_text())
    if (
        lease["nonce"] != P["nonce"]
        or lease["stop"]
        or time.monotonic() - lease["vm_monotonic_received"] > 10
    ):
        raise RuntimeError("host lease expired or stopped")
    if lease["host_free_bytes"] < P["stop_free_bytes"]:
        raise RuntimeError("Mac free-space stop")


def terminate_owned(child: subprocess.Popen[str], owner: dict[str, Any]) -> None:
    try:
        os.killpg(child.pid, 0)
    except ProcessLookupError:
        child.wait(timeout=30)
        return
    leader = Path(f"/proc/{child.pid}")
    if leader.exists():
        found = process(child.pid)
        if found != owner or found["process_group"] != child.pid:
            raise RuntimeError("refuse signalling replaced namespace owner")
    else:
        # A dead namespace leader can leave an application peer alive. Match
        # every remaining group member to the saved private namespace and
        # birth bound before signalling that exact group.
        metadata = json.loads((ROOT / "namespace.json").read_text())
        if metadata["owner"] != owner:
            raise RuntimeError("dead owner lacks exact namespace ledger")
        peers = []
        for directory in Path("/proc").iterdir():
            if not directory.name.isdigit():
                continue
            try:
                candidate = process(int(directory.name))
                if candidate["process_group"] != child.pid:
                    continue
                if (
                    candidate["start_ticks"] < owner["start_ticks"]
                    or os.readlink(directory / "ns/mnt") != metadata["namespaces"]["mount"]
                    or os.readlink(directory / "ns/net") != metadata["namespaces"]["network"]
                ):
                    raise RuntimeError("remaining group member ownership differs")
                peers.append(candidate)
            except (FileNotFoundError, ProcessLookupError):
                continue
        if not peers:
            raise RuntimeError("live group has no verifiable namespace peers")
    os.killpg(child.pid, signal.SIGSTOP)
    os.killpg(child.pid, signal.SIGKILL)
    child.wait(timeout=30)


def inside() -> None:
    ns = {"mount": os.readlink("/proc/self/ns/mnt"), "network": os.readlink("/proc/self/ns/net")}
    save(
        ROOT / "namespace.json",
        {
            "owner": process(os.getpid()),
            "namespaces": ns,
            "phase": "entered-before-external-commands",
            "complete": False,
        },
    )
    if (
        ns["mount"] == P["outer_namespaces"]["mount"]
        or ns["network"] == P["outer_namespaces"]["network"]
    ):
        raise RuntimeError("private mount/network namespace missing")
    os.nice(19)
    # Affinity exists on the Linux VM, while the host checker runs on macOS.
    cast(Any, os).sched_setaffinity(0, {max(cast(Any, os).sched_getaffinity(0))})
    storage_guard()
    command(
        [
            MKFS,
            "--nodiscard",
            "--quiet",
            "--uuid",
            P["filesystem_uuid"],
            "--label",
            P["filesystem_label"],
            "--data",
            "single",
            "--metadata",
            "single",
            str(IMAGE),
        ]
    )
    MOUNT.mkdir(mode=0o700)
    command(
        [
            "mount",
            "-t",
            "btrfs",
            "-o",
            "loop,compress=zstd:3,discard=sync,nodev,nosuid,noexec",
            str(IMAGE),
            str(MOUNT),
        ]
    )
    mounted = json.loads(
        command(
            [
                "findmnt",
                "--json",
                "--mountpoint",
                str(MOUNT),
                "-o",
                "TARGET,SOURCE,FSTYPE,OPTIONS,UUID",
            ]
        )
    )["filesystems"]
    save(
        ROOT / "mount-readback.json", {"mounts": mounted, "image": identity(IMAGE), "namespace": ns}
    )
    if len(mounted) != 1:
        raise RuntimeError("private mount is absent or ambiguous")
    policy.validate_mount(mounted[0], P["filesystem_uuid"])
    loop = mounted[0]["source"].split("[", 1)[0]
    if not re.fullmatch("/dev/loop[0-9]+", loop):
        raise RuntimeError("unexpected loop device")
    loops = json.loads(
        command(["losetup", "--list", "--json", "--output", "NAME,BACK-FILE,AUTOCLEAR,RO"])
    )["loopdevices"]
    owned = [
        x for x in loops if x["name"] == loop and Path(x["back-file"]).resolve() == IMAGE.resolve()
    ]
    if len(owned) != 1 or not owned[0]["autoclear"] or owned[0]["ro"]:
        raise RuntimeError("private loop backing/lifecycle differs")
    capacity = check_loop_capacity(loop)
    save(
        ROOT / "capacity-readback.json",
        {
            "loop_bytes": capacity,
            "loop": owned[0],
            "filesystem_uuid": P["filesystem_uuid"],
            "backend": identity(IMAGE),
        },
    )
    for name in ["state", "control", "tmp", "cache", "checkpoint-source"]:
        (MOUNT / name).mkdir(mode=0o700)
    (MOUNT / "control/unused-fetch").mkdir(mode=0o700)
    compressed = MOUNT / "control/compression-proof.bin"
    seed = b"".join(hashlib.sha256(f"fixture-{i}".encode()).digest() for i in range(256))
    pattern = seed + b"swingset-stage2-compression-fixture\n" * 2048
    expected = hashlib.sha256()
    logical_bytes = 8 * 1024**2
    remaining = logical_bytes
    command(["sync", "-f", str(MOUNT)])
    before = identity(IMAGE)
    with compressed.open("xb", buffering=0) as output:
        while remaining:
            storage_guard()
            chunk = pattern[: min(len(pattern), remaining)]
            output.write(chunk)
            expected.update(chunk)
            remaining -= len(chunk)
        os.fsync(output.fileno())
    command(["sync", "-f", str(MOUNT)])
    check_image()
    after = identity(IMAGE)
    allocated_growth = after["allocated_bytes"] - before["allocated_bytes"]
    compression = {
        "before": before,
        "after": after,
        "loop_bytes": capacity,
        "logical_fixture_bytes": logical_bytes,
        "fixture_sha256": expected.hexdigest(),
        "allocated_growth_bytes": allocated_growth,
    }
    save(ROOT / "compression-preflight.json", compression)
    if (
        hashlib.sha256(compressed.read_bytes()).hexdigest() != expected.hexdigest()
        or not 0 <= allocated_growth < logical_bytes
    ):
        raise RuntimeError("private backing allocation does not prove compression")
    readonly = MOUNT / "checkpoint-source"
    command(["mount", "--bind", P["checkpoint_root"], str(readonly)])
    command(["mount", "-o", "remount,bind,ro,nodev,nosuid,noexec", str(readonly)])
    options = (
        command(["findmnt", "-n", "-o", "OPTIONS", "--mountpoint", str(readonly)])
        .strip()
        .split(",")
    )
    if "ro" not in options:
        raise RuntimeError("frozen transport bind is not read-only")
    # Hide every live production state path in this namespace. Preserve only
    # read-only checkpoint/runtime binds; absolute old artifact paths fail
    # rather than reading a newer production file.
    runtime = MOUNT / "runtime-venv"
    runtime.mkdir(mode=0o700)
    command(["mount", "--bind", "/var/lib/swingset/venv", str(runtime)])
    command(["mount", "-o", "remount,bind,ro,nodev,nosuid", str(runtime)])
    masked = MOUNT / "masked-production"
    masked.mkdir(mode=0o700)
    (masked / "venv").mkdir(mode=0o700)
    command(["mount", "--bind", str(masked), "/var/lib/swingset"])
    command(["mount", "--bind", str(runtime), "/var/lib/swingset/venv"])
    command(["mount", "-o", "remount,bind,ro,nodev,nosuid", "/var/lib/swingset/venv"])
    command(["mount", "-o", "remount,bind,ro,nodev,nosuid,noexec", "/var/lib/swingset"])
    if (
        Path("/var/lib/swingset/state.sqlite").exists()
        or Path("/var/lib/swingset/checkpoints").exists()
    ):
        raise RuntimeError("live production paths remain visible")
    save(
        ROOT / "namespace.json",
        {
            "owner": process(os.getpid()),
            "namespaces": ns,
            "phase": "mounted-capacity-compression-proved-production-masked",
            "complete": True,
            "mount": mounted[0],
            "loop": owned[0],
            "loop_rdev": os.stat(loop).st_rdev,
            "image": identity(IMAGE),
            "compression": compression,
            "loop_bytes": capacity,
            "source_bind_options": options,
        },
    )
    private_env = runtime_environment()
    with (MOUNT / "control/worker.log").open("x") as log:
        worker = subprocess.Popen(
            [sys.executable, str(ROOT / "reference_worker.py"), "build"],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            env=private_env,
            cwd=MOUNT / "state",
            text=True,
        )
        save(ROOT / "worker-owner.json", process(worker.pid))
        assert worker.stdin is not None
        worker.stdin.write(json.dumps(P) + "\n")
        worker.stdin.close()
        while worker.poll() is None:
            storage_guard()
            if time.monotonic() - START > 5400:
                raise TimeoutError("reference job90minute bound")
            time.sleep(0.2)
    # Namespace owner remains alive until this result is durable. No explicit
    # unmount/detach/delete occurs; namespace exit releases its own AUTOCLEAR
    # loop. All application state stays in the image for later comparison.
    command(["sync", "-f", str(MOUNT)])
    check_image()
    result = json.loads((MOUNT / "control/worker-result.json").read_text())
    if worker.returncode == 0 and result.get("result") == "pass":
        export = json.loads((MOUNT / "control/export-manifest.json").read_text())
        candidate = MOUNT / export["candidate_relative_path"]
        if candidate.resolve().parent != (MOUNT / "state/candidates").resolve():
            raise RuntimeError("export candidate escaped private candidates")
        control = MOUNT / "control"
        operation = control / "operation-ledger"
        operation.mkdir(mode=0o700)
        for name in [
            "owner.json",
            "namespace-owner.json",
            "namespace.json",
            "worker-owner.json",
            "compression-preflight.json",
            "capacity-readback.json",
            "runtime-preflight.json",
            "preflight-owner.json",
        ]:
            (operation / name).write_bytes((ROOT / name).read_bytes())
        rows = [
            {
                "path": (candidate / row["path"]).relative_to(MOUNT).as_posix(),
                "bytes": row["bytes"],
                "sha256": row["sha256"],
            }
            for row in export["files"]
        ]
        for path in sorted(control.rglob("*")):
            if not path.is_file():
                continue
            h = hashlib.sha256()
            with path.open("rb") as member:
                while chunk := member.read(1024 * 1024):
                    h.update(chunk)
            rows.append(
                {
                    "path": path.relative_to(MOUNT).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": h.hexdigest(),
                }
            )
        capsule = {
            "format": "swingset-reference-R-export-v1",
            "nonce": P["nonce"],
            "script_sha256": P["script_sha256"],
            "policy_sha256": P["policy_sha256"],
            "review_sha256": P["review_sha256"],
            "source_identity": P["source_identity"],
            "raw_sqlite_sha256": P["raw_sha256"],
            "candidate_id": candidate.name,
            "files": rows,
            "remote_export_status": "NOT UPLOADED; a separate verified owned-SFTP operation is required before cleanup.",
        }
        encoded = (json.dumps(capsule, sort_keys=True) + "\n").encode()
        (control / "transport-manifest.json").write_bytes(encoded)
        result["transport_manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
        result["transport_manifest_path"] = "control/transport-manifest.json"
        result["export_files"] = len(rows)
        result["export_bytes"] = sum(x["bytes"] for x in rows)
    save(ROOT / "reference-result.json", result)
    save(
        ROOT / "final-capacity.json",
        {"loop_bytes": check_loop_capacity(loop), "backend": identity(IMAGE)},
    )
    if worker.returncode or result.get("result") != "pass":
        policy.command_failure(
            ROOT,
            cast(list[str], worker.args),
            worker.returncode,
            "",
            log_tail(MOUNT / "control/worker.log"),
        )
        raise RuntimeError("offline reference build failed; private artifacts retained")


def runtime_environment(preflight: bool = False) -> dict[str, str]:
    temporary = "/nonexistent-reference-r-" + P["nonce"] if preflight else str(MOUNT / "tmp")
    cache = "/nonexistent-reference-r-" + P["nonce"] if preflight else str(MOUNT / "cache")
    return {
        "PATH": P["runtime_path"],
        "LD_LIBRARY_PATH": P["runtime_ld_library_path"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "TMPDIR": temporary,
        "TMP": temporary,
        "TEMP": temporary,
        "XDG_CACHE_HOME": cache,
        "HF_HOME": cache + "/huggingface",
        "HF_HUB_OFFLINE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "ARROW_NUM_THREADS": "1",
        "TZ": "UTC",
    }


def cgroup_snapshot() -> dict[str, Any]:
    return {
        "path": str(CGROUP),
        "identity": identity(CGROUP),
        "values": {
            name: (CGROUP / name).read_text().strip()
            for name in [
                "cgroup.procs",
                "cgroup.events",
                "memory.max",
                "memory.swap.max",
                "memory.swap.current",
                "memory.oom.group",
                "memory.current",
                "memory.peak",
                "memory.events",
                "cpu.max",
                "cpu.stat",
            ]
        },
    }


def kill_owned_cgroup(expected: dict[str, Any], youngest_bound: int) -> None:
    found = identity(CGROUP)
    if any(found[k] != expected[k] for k in ["device", "inode", "uid"]):
        raise RuntimeError("refuse signalling replaced resource cgroup")
    for item in (CGROUP / "cgroup.procs").read_text().split():
        try:
            current = process(int(item))
        except FileNotFoundError:
            continue
        if current["start_ticks"] < youngest_bound:
            raise RuntimeError("owned cgroup contains an older unrelated process")
    (CGROUP / "cgroup.kill").write_text("1")


def supervise() -> int:
    created = False
    cg_created = False
    child: subprocess.Popen[str] | None = None
    owner: dict[str, Any] | None = None
    result: dict[str, Any] = {
        "utc": stamp(),
        "nonce": P["nonce"],
        "invocation_id": P["invocation_id"],
        "script_sha256": P["script_sha256"],
        "policy_sha256": P["policy_sha256"],
        "review_sha256": P["review_sha256"],
        "source_identity": P["source_identity"],
        "raw_sqlite_sha256": P["raw_sha256"],
        "input_bundle": P["input_bundle"],
        "operation": P["operation"],
        "no_deletion": True,
    }
    try:
        os.chdir("/")
        assert_stage_owner()
        if CGROUP.exists():
            raise RuntimeError("refuse existing reference root or resource cgroup")
        host_free = P["host_free_bytes"]
        vm_free = os.statvfs("/var/tmp").f_bavail * os.statvfs("/var/tmp").f_frsize
        cap = policy.backend_budget(P, host_free, vm_free)
        P["backend_cap_bytes"] = cap
        P["outer_namespaces"] = {
            "mount": os.readlink("/proc/self/ns/mnt"),
            "network": os.readlink("/proc/self/ns/net"),
        }
        result.update(
            {
                "owner": process(os.getpid()),
                "backend_cap_bytes": cap,
                "vm_available_before": vm_free,
                "host_available_before": host_free,
                "outer_namespaces": P["outer_namespaces"],
                "filesystem_uuid": P["filesystem_uuid"],
                "shared_mount_before": command(
                    ["findmnt", "-n", "-o", "SOURCE,FSTYPE,OPTIONS", "-T", P["checkpoint_root"]]
                ).strip(),
            }
        )
        created = True
        result["root"] = identity(ROOT)
        save(ROOT / "owner.json", result)
        CGROUP.mkdir()
        cg_created = True
        result["cgroup_identity"] = identity(CGROUP)
        for name, value in [
            ("memory.max", str(8 * 1024**3)),
            ("memory.swap.max", "0"),
            ("memory.oom.group", "1"),
            ("cpu.max", "100000 100000"),
        ]:
            (CGROUP / name).write_text(value)
        if not (CGROUP / "cgroup.kill").exists():
            raise RuntimeError("owned cgroup lacks fail-closed kill interface")
        result["cgroup_limits"] = cgroup_snapshot()
        limits = result["cgroup_limits"]["values"]
        if (
            limits["memory.max"] != str(8 * 1024**3)
            or limits["memory.swap.max"] != "0"
            or limits["memory.oom.group"] != "1"
            or limits["cpu.max"] != "100000 100000"
        ):
            raise RuntimeError("owned cgroup resource limits differ")
        save(ROOT / "owner.json", result)
        save(
            ROOT / "lease.json",
            {
                "nonce": P["nonce"],
                "host_free_bytes": host_free,
                "vm_monotonic_received": time.monotonic(),
                "stop": False,
            },
        )

        def leases() -> None:
            try:
                for line in sys.stdin:
                    lease = json.loads(line)
                    if lease.get("nonce") != P["nonce"]:
                        break
                    lease["vm_monotonic_received"] = time.monotonic()
                    save(ROOT / "lease.json", lease)
            finally:
                save(
                    ROOT / "lease.json",
                    {
                        "nonce": P["nonce"],
                        "host_free_bytes": 0,
                        "vm_monotonic_received": time.monotonic(),
                        "stop": True,
                    },
                )

        threading.Thread(target=leases, daemon=True).start()

        # The child blocks for its payload until it has entered the8GiB,
        # no-swap, one-CPU cgroup. Import/capture runs read-only BEFORE image
        # creation; no arbitrary virtual-address cap is imposed.
        child = subprocess.Popen(
            [
                "unshare",
                "--mount",
                "--net",
                "--propagation",
                "private",
                "--",
                sys.executable,
                str(ROOT / "reference_worker.py"),
                "preflight",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=runtime_environment(preflight=True),
            start_new_session=True,
            text=True,
        )
        owner = process(child.pid)
        save(ROOT / "preflight-owner.json", owner)
        (CGROUP / "cgroup.procs").write_text(str(child.pid))
        try:
            out, err = child.communicate(input=json.dumps(P) + "\n", timeout=60)
        except subprocess.TimeoutExpired as exc:
            policy.command_failure(
                ROOT, cast(list[str], child.args), None, exc.stdout, exc.stderr, str(exc)
            )
            raise
        (ROOT / "preflight.stderr").write_text(err)
        if child.returncode:
            policy.command_failure(ROOT, cast(list[str], child.args), child.returncode, out, err)
            raise RuntimeError("bounded read-only runtime preflight failed")
        preflight = json.loads(out)
        if preflight.get("result") != "pass":
            raise RuntimeError("read-only runtime preflight did not pass")
        result["runtime_preflight"] = preflight
        save(ROOT / "runtime-preflight.json", preflight)
        result["cgroup_after_preflight"] = cgroup_snapshot()
        if (CGROUP / "cgroup.procs").read_text().strip():
            raise RuntimeError("preflight left resource-cgroup peers")
        owner = None
        child = None
        lease = json.loads((ROOT / "lease.json").read_text())
        if lease["stop"] or time.monotonic() - lease["vm_monotonic_received"] > 10:
            raise RuntimeError("host lease stopped before allocation")
        fd = os.open(IMAGE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.truncate(P["backend_cap_bytes"])
            f.flush()
            os.fsync(f.fileno())
        P["image_identity"] = identity(IMAGE)
        result["image"] = P["image_identity"]
        save(ROOT / "owner.json", result)
        with (ROOT / "namespace.log").open("x") as log:
            child = subprocess.Popen(
                [
                    "unshare",
                    "--mount",
                    "--net",
                    "--propagation",
                    "private",
                    "--",
                    sys.executable,
                    str(ROOT / "reference_workspace.py"),
                    "inside",
                ],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                start_new_session=True,
                text=True,
            )
            owner = process(child.pid)
            save(ROOT / "namespace-owner.json", owner)
            (CGROUP / "cgroup.procs").write_text(str(child.pid))
            assert child.stdin is not None
            child.stdin.write(json.dumps(P) + "\n")
            child.stdin.close()
            last_emit = 0.0
            while child.poll() is None:
                storage_guard()
                if time.monotonic() - START > 5400:
                    raise TimeoutError("reference job90minute bound")
                if time.monotonic() - last_emit >= 30:
                    emit(
                        "progress",
                        backend=identity(IMAGE),
                        vm_free_bytes=os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize,
                        seconds=round(time.monotonic() - START, 1),
                    )
                    last_emit = time.monotonic()
                time.sleep(0.2)
        if child.returncode:
            policy.command_failure(
                ROOT,
                cast(list[str], child.args),
                child.returncode,
                "",
                log_tail(ROOT / "namespace.log"),
            )
            raise RuntimeError("private namespace failed; inspect retained owned logs")
        if (CGROUP / "cgroup.procs").read_text().strip():
            raise RuntimeError("namespace exited with live resource-cgroup peers")
        result["reference"] = json.loads((ROOT / "reference-result.json").read_text())
        result["result"] = "pass"
    except BaseException as e:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        result["result"] = "failed"
        result["error_class"] = type(e).__name__
        result["error"] = str(e)[:1000]
        if cg_created:
            try:
                kill_owned_cgroup(result["cgroup_identity"], result["owner"]["start_ticks"])
                if child is not None:
                    child.wait(timeout=30)
            except BaseException as stop_error:
                result["stop_error"] = type(stop_error).__name__ + ": " + str(stop_error)
                result["cleanup_review_required"] = True
        elif child is not None and owner is not None:
            try:
                terminate_owned(child, owner)
            except BaseException as stop_error:
                result["stop_error"] = type(stop_error).__name__ + ": " + str(stop_error)
                result["cleanup_review_required"] = True
    finally:
        if created:
            loops = json.loads(
                command(["losetup", "--list", "--json", "--output", "NAME,BACK-FILE,AUTOCLEAR,RO"])
            )["loopdevices"]
            remaining = [x for x in loops if Path(x["back-file"]).resolve() == IMAGE.resolve()]
            result["remaining_owned_loops"] = remaining
            result["namespace_owner_absent"] = (
                owner is None or not Path(f"/proc/{owner['pid']}").exists()
            )
            result["image_after_namespace_exit"] = identity(IMAGE) if IMAGE.exists() else None
            result["shared_mount_after"] = command(
                ["findmnt", "-n", "-o", "SOURCE,FSTYPE,OPTIONS", "-T", P["checkpoint_root"]]
            ).strip()
            if result["shared_mount_after"] != result["shared_mount_before"]:
                result["result"] = "failed"
                result["shared_mount_changed"] = True
            if cg_created:
                result["cgroup_final"] = cgroup_snapshot()
                events = dict(
                    line.split()
                    for line in result["cgroup_final"]["values"]["memory.events"].splitlines()
                )
                if any(int(events.get(key, 0)) for key in ["oom", "oom_kill", "oom_group_kill"]):
                    result["result"] = "failed"
                    result["resource_oom_observed"] = True
                result["owned_cgroup_empty"] = (
                    not result["cgroup_final"]["values"]["cgroup.procs"]
                    and "populated 0" in result["cgroup_final"]["values"]["cgroup.events"]
                )
            else:
                result["owned_cgroup_empty"] = True
            oversized = (
                result["image_after_namespace_exit"] is not None
                and result["image_after_namespace_exit"]["allocated_bytes"] > P["backend_cap_bytes"]
            )
            if (
                remaining
                or not result["namespace_owner_absent"]
                or not result["owned_cgroup_empty"]
                or oversized
            ):
                result["result"] = "failed"
                result["cleanup_review_required"] = True
            save(ROOT / "result.json", result)
        emit("result", **result)
    return 0 if result.get("result") == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("supervise", "inside"))
    parser.add_argument("payload", nargs="?", type=Path)
    args = parser.parse_args(argv)
    raw = json.loads(args.payload.read_text()) if args.payload else json.loads(sys.stdin.readline())
    configure(raw, operation=args.mode == "supervise")
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGHUP, stop_signal)
    if args.mode == "inside":
        inside()
        return 0
    return supervise()


if __name__ == "__main__":
    raise SystemExit(main())
