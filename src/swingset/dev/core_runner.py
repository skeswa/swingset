"""Run the complete reviewed core in four bounded native Linux clients."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import core_images, physical, services
from .core_partition import PROLOGUE_NODE, assigned_nodes, load_weights, phase_nodes

ROOT = Path(__file__).resolve().parents[3]
LABEL = "io.swingset.core-job"
MEMORY = 2 * 1024**3
MINIMUM_FREE = 80 * 1024**3
BUDGET_SECONDS = 180
PARTITIONS = 4
CLIENT_CPUS = 6
AGGREGATE_WORKING_LIMIT = 7 * 1024**3


def uses_fast_core(arguments: Sequence[str]) -> bool:
    """Only the unfiltered local default owns Docker; every explicit route stays native."""
    return (
        not arguments
        and not os.environ.get("CI")
        and not os.environ.get("SWINGSET_TEST_SERVICE_DSN")
    )


def source_binding(root: Path) -> dict[str, str]:
    paths = [root / name for name in ("Dockerfile", "pyproject.toml", "uv.lock")]
    for name in ("src", "tests", "config", "overrides"):
        paths.extend(
            path
            for path in (root / name).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def source_revision(root: Path) -> str:
    package = root / "src" / "swingset"
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix().encode()
        body = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return "source-sha256:" + digest.hexdigest()


def _write_private(path: Path, value: dict[str, Any]) -> None:
    services.write_private(path, value)
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False, timeout=15
    )
    if check and result.returncode:
        # Docker diagnostics can include credentials; the private job log is separate.
        raise RuntimeError(f"Core Docker {' '.join(arguments[:2])} failed")
    return result


def _container(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(_docker("container", "inspect", name).stdout)[0]
    return value


def _private_environment(
    primary: services.Service, physical: services.Service, revision: str
) -> dict[str, str]:
    from psycopg.conninfo import make_conninfo

    from . import physical as physical_profile

    peer = _container(physical.name)
    physical_profile.validate_identity(physical, peer)
    labels = peer["Config"].get("Labels") or {}
    if (
        peer["HostConfig"]["Memory"] != MEMORY
        or peer["HostConfig"]["NanoCpus"] != 2_000_000_000
        or labels.get(services.LABEL) != "physical"
        or peer["Config"]["Image"] != services.POSTGRES_IMAGE
    ):
        raise RuntimeError("The physical test service belongs to another owner")
    address = peer["NetworkSettings"]["Networks"]["bridge"]["IPAddress"]
    if not ipaddress.ip_address(address).is_private:
        raise RuntimeError("Physical tests require their private Docker bridge endpoint")
    value = {**services.ci_environment(primary), **services.ci_environment(physical)}
    value["SWINGSET_TEST_SERVICE_DSN"] = make_conninfo(primary.dsn(), host="127.0.0.1", port="5432")
    value["SWINGSET_TEST_LIFECYCLE_SERVICE_DSN"] = make_conninfo(
        physical.dsn(), host=address, port="5432"
    )
    value.update(
        {
            "PYTHONPATH": "/workspace/src:/workspace/tests",
            "UV_PROJECT_ENVIRONMENT": "/app/.venv",
            "UV_NO_SYNC": "1",
            "SWINGSET_REVISION": revision,
            "SWINGSET_TEST_TIMINGS_PATH": "/tmp/core-phases.json",
            "SWINGSET_TEST_PARTITION_REPORT_DIR": "/tmp/partition-reports",
            "PYTEST_ADDOPTS": "-o cache_dir=/tmp/pytest-cache -o tmp_path_retention_policy=failed",
            "SWINGSET_TEST_TMP_RETENTION": "failed",
        }
    )
    return value


def _outcome_errors(client: dict[str, Any], assigned: list[str], label: str) -> list[str]:
    errors = []
    reports = client.get("reports") or {}
    outcome = reports.get("core-results.json") or {}
    counts = outcome.get("counts") or {}
    if counts.get("passed") != len(assigned) or any(
        counts.get(key, 0) for key in ("failed", "error", "skipped", "xfailed", "xpassed")
    ):
        errors.append(label + ": every assigned case must pass")
    executed = outcome.get("executed", [])
    if len(executed) != len(assigned) or set(executed) != set(assigned):
        errors.append(label + ": executed node membership differs")
    summary = reports.get("core-client-summary.json") or {}
    if (
        client.get("exit_code") != 0
        or outcome.get("exit_code") != 0
        or summary.get("pytest_exit_code") != 0
    ):
        errors.append(label + ": nonzero exit")
    if not (summary.get("cleanup") or {}).get("complete"):
        errors.append(label + ": cleanup incomplete")
    if not (summary.get("failure_artifact_export") or {}).get("complete"):
        errors.append(label + ": artifact export incomplete")
    if summary.get("uid") != 10001 or not summary.get("pid_namespace"):
        errors.append(label + ": unexpected client identity")
    if not re.fullmatch(r"[0-9a-f]{64}", client.get("container_id") or ""):
        errors.append(label + ": missing exact container identity")
    return errors


def _manifest_errors(part: dict[str, Any], selected: list[str], assigned: list[str]) -> list[str]:
    errors = []
    if not selected or len(set(selected)) != len(selected) or len(set(assigned)) != len(assigned):
        errors.append("invalid or duplicate node identities")
    for name, nodes in (("selected", selected), ("assigned", assigned)):
        if (
            part.get(name + "_count") != len(nodes)
            or part.get(name + "_sha256") != hashlib.sha256("\n".join(nodes).encode()).hexdigest()
        ):
            errors.append(name + " receipt checksum mismatch")
    return errors


def validate_prologue(client: dict[str, Any] | None, weights_sha256: str | None) -> dict[str, Any]:
    """A failed, missing or incompletely cleaned prologue cannot release concurrency."""
    if client is None:
        return {"errors": ["Expected the isolated core prologue"], "selected": []}
    reports = client.get("reports") or {}
    parts = list((reports.get("partitions") or {}).values())
    if len(parts) != 1:
        return {"errors": ["Prologue requires one controller manifest"], "selected": []}
    part = parts[0]
    selected = part.get("selected", [])
    assigned = [PROLOGUE_NODE]
    errors = _manifest_errors(part, selected, assigned)
    if (
        part.get("format") != 1
        or part.get("index") != 0
        or part.get("count") != 1
        or part.get("worker") != "controller"
        or part.get("phase") != "prologue"
        or part.get("partition_policy") != "one-case-prologue-v1"
        or part.get("weights_sha256") != weights_sha256
        or part.get("assigned") != assigned
        or selected.count(PROLOGUE_NODE) != 1
    ):
        errors.append("Prologue manifest differs from its one reviewed case")
    summary = reports.get("core-client-summary.json") or {}
    command = summary.get("pytest_command") or []
    if "--swingset-core-phase=prologue" not in command or any(
        value == "-n"
        or value.startswith(("-n", "--numprocesses", "--dist=", "--swingset-partition="))
        for value in command
    ):
        errors.append("Prologue must record its effective serial command")
    errors.extend(_outcome_errors(client, assigned, "prologue"))
    return {"errors": errors, "selected": selected}


def validate_reports(
    clients: list[dict[str, Any]],
    *,
    prologue: dict[str, Any] | None = None,
    count: int = PARTITIONS,
    weights: Mapping[str, float] | None = None,
    weights_sha256: str | None = None,
) -> dict[str, Any]:
    """Prove the serial case and concurrent remainder execute the complete core once."""
    serial = validate_prologue(prologue, weights_sha256)
    errors = list(serial["errors"])
    selections = []
    assignments = []
    namespaces = []
    container_ids = [prologue.get("container_id")] if prologue else []
    if len(clients) != count or {client.get("index") for client in clients} != set(range(count)):
        return {"errors": errors + ["Expected every core partition"]}
    for client in sorted(clients, key=lambda value: value["index"]):
        index = client["index"]
        reports = client.get("reports") or {}
        parts = list((reports.get("partitions") or {}).values())
        if len(parts) != 4 or {part.get("worker") for part in parts} != {
            f"gw{n}" for n in range(4)
        }:
            errors.append(f"partition {index}: four worker manifests required")
            continue
        selected = parts[0].get("selected", [])
        assigned = parts[0].get("assigned", [])
        try:
            expected = assigned_nodes(phase_nodes(selected, "parallel"), index, count, weights)
        except ValueError:
            errors.append(f"partition {index}: missing unique prologue case")
            expected = []
        for part in parts:
            errors.extend(
                f"partition {index}: " + error
                for error in _manifest_errors(part, selected, assigned)
            )
            if (
                part.get("index") != index
                or part.get("count") != count
                or part.get("format") != 1
                or part.get("phase") != "parallel"
                or part.get("partition_policy")
                != ("measured-lpt-v1" if weights is not None else "sha256-files-v1")
                or part.get("weights_sha256") != weights_sha256
                or part.get("selected") != selected
                or part.get("assigned") != assigned
                or assigned != expected
            ):
                errors.append(f"partition {index}: worker manifest mismatch")
                break
        errors.extend(_outcome_errors(client, assigned, f"partition {index}"))
        summary = reports.get("core-client-summary.json") or {}
        namespaces.append(summary.get("pid_namespace"))
        container_ids.append(client.get("container_id"))
        selections.append(selected)
        assignments.append(assigned)
    if len(selections) == count:
        if any(value != serial["selected"] for value in selections):
            errors.append("Execution phases collected different complete selections")
        all_assignments = [[PROLOGUE_NODE], *assignments]
        union = set().union(*map(set, all_assignments))
        if sum(map(len, all_assignments)) != len(union):
            errors.append("Execution phases overlap")
        if union != set(serial["selected"]):
            errors.append("Execution phase union omits selected cases")
        if not all(namespaces) or len(set(namespaces)) != count:
            errors.append("Concurrent clients must have separate private PID namespaces")
        if len(container_ids) != count + 1 or len(set(container_ids)) != count + 1:
            errors.append("Execution phases must have distinct exact container identities")
        return {
            "errors": errors,
            "selected_count": len(serial["selected"]),
            "prologue_count": 1,
            "assigned_counts": [len(value) for value in assignments],
            "membership_sha256": hashlib.sha256(
                "\n".join(sorted(serial["selected"])).encode()
            ).hexdigest(),
        }
    return {"errors": errors}


def _client_command(
    root: Path, image: str, environment: Path, primary_id: str, client: dict[str, Any], job: str
) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        client["name"],
        "--cidfile",
        str(environment.parent / f"client-{client['index']}.cid"),
        "--label",
        LABEL + "=" + job,
        "--platform",
        core_images.native_platform(),
        "--network",
        "container:" + primary_id,
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "--cpus",
        str(CLIENT_CPUS),
        "--volume",
        client["volume"] + ":/tmp",
        "--tmpfs",
        "/tmp/pytest-of-swingset:rw,size=768m,mode=1777,uid=10001,gid=10001",
        "--mount",
        f"type=bind,src={root},dst=/workspace,readonly",
        "--env-file",
        str(environment),
        *(
            [
                "--mount",
                f"type=bind,src={environment.parent / 'weights.json'},dst=/run/core-weights.json,readonly",
            ]
            if (environment.parent / "weights.json").is_file()
            else []
        ),
        "--workdir",
        "/workspace",
        "--entrypoint",
        "python",
        image,
        "-m",
        "swingset.dev.core_client",
        "run",
        "prologue" if client["index"] == "prologue" else f"{client['index']}/{PARTITIONS}",
    ]


def _signal_client(client: dict[str, Any], job: str, number: str) -> None:
    found = _docker("container", "inspect", client["name"], check=False)
    if found.returncode:
        return
    value = json.loads(found.stdout)[0]
    if (value["Config"].get("Labels") or {}).get(LABEL) != job or client.get(
        "container_id"
    ) not in (None, value["Id"]):
        raise RuntimeError("Core client ownership changed; refusing to signal it")
    _docker("kill", "--signal", number, value["Id"], check=False)


def _measure_client(client: dict[str, Any]) -> dict[str, Any] | None:
    found = _docker("container", "inspect", client["name"], check=False)
    if found.returncode:
        return None
    info = json.loads(found.stdout)[0]
    limits = info["HostConfig"]
    if (
        info["Config"].get("Labels", {}).get(LABEL) != client["job"]
        or limits["Memory"] != MEMORY
        or limits["MemorySwap"] != MEMORY
        or limits["NanoCpus"] != CLIENT_CPUS * 1_000_000_000
        or limits.get("PidMode")
    ):
        raise RuntimeError("Core client lost its ownership, private namespace or resource bounds")
    if client.get("container_id") not in (None, info["Id"]):
        raise RuntimeError("Core client container identity changed")
    client["container_id"] = info["Id"]
    result = _docker(
        "exec", info["Id"], "python", "-m", "swingset.dev.core_client", "measure", check=False
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def _measure_postgres(name: str) -> dict[str, Any] | None:
    result = _docker(
        "exec",
        name,
        "cat",
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory.peak",
        "/sys/fs/cgroup/memory.stat",
        "/sys/fs/cgroup/memory.swap.current",
        "/sys/fs/cgroup/memory.events",
        check=False,
    )
    if result.returncode:
        return None
    lines = result.stdout.splitlines()
    stats = {}
    events = {}
    swap = 0
    for line in lines[2:]:
        fields = line.split()
        if len(fields) == 1:
            swap = int(fields[0])
        elif fields[0] in {
            "low",
            "high",
            "max",
            "oom",
            "oom_kill",
            "oom_group_kill",
            "sock_throttled",
        }:
            events[fields[0]] = int(fields[1])
        else:
            stats[fields[0]] = int(fields[1])
    current = int(lines[0])
    return {
        "current": current,
        "peak": int(lines[1]),
        "working": max(0, current - stats.get("inactive_file", 0)),
        "anon": stats.get("anon"),
        "file": stats.get("file"),
        "swap": swap,
        "events": events,
    }


def _database_resources(primary: str) -> dict[str, Any]:
    ids = _docker("ps", "-q", "--no-trunc", "--filter", "label=" + services.LABEL).stdout.split()
    values = {identifier: _measure_postgres(identifier) for identifier in ids}
    if not values.get(primary):
        raise RuntimeError("Warm test database resources are unavailable")
    # The primary's inode/byte budget is separate from its memory cgroup.
    data = _docker(
        "exec", primary, "df", "-B1", "--output=used,size,iused,itotal", "/var/lib/postgresql/data"
    )
    used, total, inodes, inode_total = map(int, data.stdout.splitlines()[1].split())
    return {
        "services": values,
        "primary_id": primary,
        "test_data": {
            "bytes_used": used,
            "bytes_total": total,
            "inodes_used": inodes,
            "inodes_total": inode_total,
        },
    }


def resource_failure(
    clients: Mapping[str, Any], databases: dict[str, Any], baseline: Mapping[str, int], free: int
) -> str | None:
    if free < MINIMUM_FREE:
        return "host disk below 80 GiB"
    total = 0
    for name, value in clients.items():
        if not value:
            continue
        if value["events"].get("oom_kill", 0) or value["events"].get("oom", 0):
            return "client kernel OOM: " + name
        total += value.get("working", value["current"])
        payload = value.get("test_tmpfs")
        if payload and (
            payload["bytes_used"] > payload["bytes_total"] * 0.95
            or payload["inodes_total"]
            and payload["inodes_used"] > payload["inodes_total"] * 0.95
        ):
            return "pytest tmpfs capacity: " + name
    for name, value in databases["services"].items():
        if not value:
            continue
        if value["events"].get("oom_kill", 0) > baseline.get(name, 0):
            return "database kernel OOM: " + str(name)
        total += value["working"]
    if total > AGGREGATE_WORKING_LIMIT:
        return "aggregate development/test working memory exceeds 7 GiB"
    primary = databases["services"].get(databases.get("primary_id"))
    if primary and primary["working"] > 1.75 * 1024**3:
        return "test database working memory exceeds 1.75 GiB"
    payload = databases["test_data"]
    if (
        payload["bytes_used"] > 1.25 * 1024**3
        or payload["inodes_total"]
        and payload["inodes_used"] > payload["inodes_total"] * 0.95
    ):
        return "test database tmpfs capacity"
    return None


def _read_reports(root: Path, image: str, client: dict[str, Any], job: str) -> dict[str, Any]:
    name = client["name"] + "-report"
    try:
        result = _docker(
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            LABEL + "=" + job,
            "--platform",
            core_images.native_platform(),
            "--network",
            "none",
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--cpus",
            "1",
            "--mount",
            f"type=volume,source={client['volume']},target=/probe,readonly",
            "--mount",
            f"type=bind,src={root},dst=/workspace,readonly",
            "--env",
            "PYTHONPATH=/workspace/src",
            "--entrypoint",
            "python",
            image,
            "-m",
            "swingset.dev.core_client",
            "reports",
            "/probe",
        )
        value: dict[str, Any] = json.loads(result.stdout)
        return value
    finally:
        found = _docker("container", "inspect", name, check=False)
        if found.returncode == 0:
            info = json.loads(found.stdout)[0]
            if (info["Config"].get("Labels") or {}).get(LABEL) != job:
                raise RuntimeError("Report helper ownership changed; refusing cleanup")
            _docker("rm", "--force", info["Id"])


def _remove_passed_volume(client: dict[str, Any], job: str) -> None:
    if (
        not re.fullmatch(r"[0-9a-f]{24}", job)
        or client["index"] not in (*range(PARTITIONS), "prologue")
        or client["volume"] != f"swingset-core-{job}-{client['index']}-tmp"
    ):
        raise RuntimeError("Core volume is outside this job; retained")
    current = json.loads(_docker("volume", "inspect", client["volume"]).stdout)[0]
    if (
        current["Name"] != client["volume"]
        or current.get("Labels") != {LABEL: job, LABEL + ".partition": str(client["index"])}
        or current.get("CreatedAt") != client["volume_created"]
    ):
        raise RuntimeError("Core volume ownership changed; directory retained")
    if _docker("ps", "-aq", "--filter", "volume=" + client["volume"]).stdout.strip():
        raise RuntimeError("Core volume still has a container owner; retained")
    _docker("volume", "rm", client["volume"])


def measured_weights(clients: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for client in clients:
        phases = client["reports"]["core-results.json"].get("node_phases", {})
        for node in client["reports"]["core-results.json"]["executed"]:
            values = phases.get(node, {})
            if set(values) != {"setup", "call", "teardown"} or any(
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
                for value in values.values()
            ):
                raise RuntimeError("Complete core is missing valid per-node phase measurements")
            file = node.split("::", 1)[0]
            result[file] = result.get(file, 0.0) + sum(values.values())
    return {name: max(value, 0.000001) for name, value in result.items()}


def _stop_clients(clients: list[dict[str, Any]], job: str) -> None:
    for client in clients:
        process = client.get("process")
        try:
            if process is not None and process.poll() is None:
                _signal_client(client, job, "SIGINT")
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    _signal_client(client, job, "SIGKILL")
                    process.wait(timeout=10)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            client["stop_error"] = type(error).__name__
        finally:
            if client.get("stream") is not None:
                client["stream"].close()


def _start_client(
    clients: list[dict[str, Any]],
    index: int | str,
    *,
    root: Path,
    directory: Path,
    image: str,
    environment: Path,
    primary_id: str,
    job: str,
) -> dict[str, Any]:
    if index not in (*range(PARTITIONS), "prologue") or not re.fullmatch(r"[0-9a-f]{24}", job):
        raise RuntimeError("Core client ownership is outside this job")
    client: dict[str, Any] = {
        "index": index,
        "job": job,
        "name": f"swingset-core-{job}-{index}",
        "volume": f"swingset-core-{job}-{index}-tmp",
    }
    clients.append(client)
    _write_private(
        directory / f"client-{index}-owner.json",
        {
            "job": job,
            "index": index,
            "name": client["name"],
            "volume": client["volume"],
        },
    )
    _docker(
        "volume",
        "create",
        "--label",
        LABEL + "=" + job,
        "--label",
        LABEL + ".partition=" + str(index),
        client["volume"],
    )
    client["volume_created"] = json.loads(_docker("volume", "inspect", client["volume"]).stdout)[0][
        "CreatedAt"
    ]
    client["stream"] = (directory / f"client-{index}.log").open("w")
    client["process"] = subprocess.Popen(
        _client_command(root, image, environment, primary_id, client, job),
        stdout=client["stream"],
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return client


def _wait_clients(
    clients: list[dict[str, Any]],
    *,
    root: Path,
    primary_id: str,
    job: str,
    baseline: Mapping[str, int],
    samples: list[dict[str, Any]],
    requested: list[int],
    started: float,
) -> str | None:
    aborted = None
    signaled = None
    while any(client["process"].poll() is None for client in clients):
        measured = {
            client["name"]: _measure_client(client)
            for client in clients
            if client["process"].poll() is None
        }
        resources = _database_resources(primary_id)
        free = shutil.disk_usage(root).free
        samples.append(
            {
                "seconds": time.monotonic() - started,
                "clients": measured,
                "databases": resources,
                "host_free": free,
            }
        )
        if aborted is None:
            aborted = (
                "interrupted"
                if requested
                else resource_failure(measured, resources, baseline, free)
            )
        if aborted:
            if signaled is None:
                signaled = time.monotonic()
            for client in clients:
                _signal_client(client, job, "SIGINT")
        if signaled is not None and time.monotonic() - signaled > 60:
            for client in clients:
                if client["process"].poll() is None:
                    _signal_client(client, job, "SIGKILL")
        next_sample = time.monotonic() + (0.1 if len(clients) == 1 else 1)
        while time.monotonic() < next_sample:
            if not any(client["process"].poll() is None for client in clients):
                break
            if requested and aborted is None:
                aborted = "interrupted"
                signaled = time.monotonic()
                for client in clients:
                    _signal_client(client, job, "SIGINT")
            time.sleep(min(0.05, max(0, next_sample - time.monotonic())))
    return aborted


def _client_record(
    root: Path, image: str, client: dict[str, Any], job: str, directory: Path
) -> dict[str, Any]:
    try:
        received = (
            client.get("received") or _read_reports(root, image, client, job)
            if client.get("volume_created")
            else {"read_error": "Volume creation was not confirmed"}
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        received = {"read_error": type(error).__name__}
    cidfile = directory / f"client-{client['index']}.cid"
    identity = cidfile.read_text().strip() if cidfile.is_file() else None
    if client.get("container_id") not in (None, identity):
        client["stop_error"] = "Container identity changed"
    process = client.get("process")
    value = {
        "index": client["index"],
        "name": client["name"],
        "volume": client["volume"],
        "container_id": identity,
        "exit_code": process.returncode if process else None,
        "reports": received,
        "stop_error": client.get("stop_error"),
    }
    _write_private(directory / f"client-{client['index']}.json", value)
    return value


def run(root: Path | None = None) -> int:
    if not uses_fast_core(()):
        raise RuntimeError(
            "The fast core executor is host-only; CI and configured workers stay native"
        )
    with services.lock(services.home() / "core-executor.lock"):
        return _run_locked(root)


def _run_locked(root: Path | None = None) -> int:
    root = (root or ROOT).resolve()
    if shutil.disk_usage(root).free < MINIMUM_FREE:
        raise RuntimeError("The complete core needs at least 80 GiB of free host disk")
    job = uuid.uuid4().hex[:24]
    directory = services.home() / "core-runs" / job
    directory.mkdir(parents=True, mode=0o700)
    print(f"Complete core job and logs: {directory}", flush=True)
    started = time.monotonic()
    before = source_binding(root)
    revision = source_revision(root)
    image = core_images.ensure(root)
    primary = services.ensure_service()
    primary_info = _container(primary.name)
    if (
        primary_info["Config"]["Image"] != services.POSTGRES_IMAGE
        or primary_info["HostConfig"]["Memory"] != MEMORY
    ):
        raise RuntimeError("Core tests require the pinned 2 GiB test database")
    environment = directory / "private.env"
    cache = services.home() / "core-weights.json"
    weights = None
    weights_sha256 = None
    if cache.is_file():
        body = cache.read_bytes()
        (directory / "weights.json").write_bytes(body)
        os.chmod(directory / "weights.json", 0o600)
        weights, weights_sha256 = load_weights(directory / "weights.json")
    clients: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    requested: list[int] = []
    old_handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in old_handlers:
        signal.signal(number, lambda number, frame: requested.append(number))
    aborted = None
    baseline: dict[str, int] = {}
    physical_service: services.Service | None = None
    physical_cleanup: dict[str, Any] = {"complete": False}
    try:
        physical_service = services.ensure_service("physical")
        physical_server = physical_service
        try:
            resources = _database_resources(primary_info["Id"])
            baseline = {
                name: value["events"].get("oom_kill", 0)
                for name, value in resources["services"].items()
                if value
            }
            reason = resource_failure({}, resources, baseline, shutil.disk_usage(root).free)
            if reason:
                raise RuntimeError(reason)
            if requested:
                aborted = "interrupted"
                raise RuntimeError("Core interrupted before client startup")
            values = _private_environment(primary, physical_server, revision)
            if weights is not None:
                values["SWINGSET_CORE_PARTITION_WEIGHTS"] = "/run/core-weights.json"
            descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
            serial = _start_client(
                clients,
                "prologue",
                root=root,
                directory=directory,
                image=image,
                environment=environment,
                primary_id=primary_info["Id"],
                job=job,
            )
            aborted = _wait_clients(
                [serial],
                root=root,
                primary_id=primary_info["Id"],
                job=job,
                baseline=baseline,
                samples=samples,
                requested=requested,
                started=started,
            )
            _stop_clients([serial], job)
            serial_record = _client_record(root, image, serial, job, directory)
            serial["received"] = serial_record["reports"]
            prologue_errors = validate_prologue(serial_record, weights_sha256)["errors"]
            if aborted or prologue_errors or serial_record.get("stop_error"):
                raise RuntimeError("The isolated core prologue did not pass and clean up")
            if (
                _docker(
                    "container", "inspect", serial_record["container_id"], check=False
                ).returncode
                == 0
            ):
                raise RuntimeError("The isolated prologue container remains alive")
            if requested:
                aborted = "interrupted"
                raise RuntimeError("Core interrupted after its prologue")
            parallel = [
                _start_client(
                    clients,
                    index,
                    root=root,
                    directory=directory,
                    image=image,
                    environment=environment,
                    primary_id=primary_info["Id"],
                    job=job,
                )
                for index in range(PARTITIONS)
            ]
            aborted = _wait_clients(
                parallel,
                root=root,
                primary_id=primary_info["Id"],
                job=job,
                baseline=baseline,
                samples=samples,
                requested=requested,
                started=started,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            aborted = aborted or "executor failure: " + type(error).__name__
        finally:
            # The warm physical service remains; all owned children must stop first.
            _stop_clients(clients, job)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        aborted = aborted or "service lifecycle failure: " + type(error).__name__
        _stop_clients(clients, job)
    finally:
        environment.unlink(missing_ok=True)
        for number, previous in old_handlers.items():
            signal.signal(number, previous)
    if physical_service is not None:
        try:
            physical.assert_clean(physical_service)
            physical_cleanup = {"complete": True}
        except (OSError, RuntimeError, ValueError) as error:
            physical_cleanup = {"complete": False, "error_type": type(error).__name__}
    all_reports = [_client_record(root, image, client, job, directory) for client in clients]
    prologue_report = next((value for value in all_reports if value["index"] == "prologue"), None)
    reports = [value for value in all_reports if value["index"] != "prologue"]
    verified = validate_reports(
        reports,
        prologue=prologue_report,
        count=PARTITIONS,
        weights=weights,
        weights_sha256=weights_sha256,
    )
    if before != source_binding(root) or revision != source_revision(root):
        verified["errors"].append("Source or test inputs changed during the complete run")
    if aborted:
        verified["errors"].append(aborted)
    if any(client.get("stop_error") for client in all_reports):
        verified["errors"].append("A client could not be stopped safely")
    if not physical_cleanup["complete"]:
        verified["errors"].append(
            "Physical service inventory was not restored to its fresh baseline"
        )
    if not verified["errors"]:
        try:
            _write_private(cache, {"format": 1, "weights": measured_weights(all_reports)})
        except (OSError, RuntimeError) as error:
            verified["errors"].append(
                "Weight measurements were incomplete: " + type(error).__name__
            )
    wall = time.monotonic() - started
    summary = {
        "format": "swingset-core-run-v1",
        "job": job,
        "image": image,
        "source_revision": revision,
        "source_binding_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "weights_sha256": weights_sha256,
        "partition_policy": "measured-lpt-v1" if weights is not None else "sha256-files-v1",
        "partitions": PARTITIONS,
        "workers_per_client": 4,
        "kernel_limits": {
            "client_memory": MEMORY,
            "client_memory_plus_swap": MEMORY,
            "client_cpus": CLIENT_CPUS,
            "pytest_tmpfs": 768 * 1024**2,
            "aggregate_working": AGGREGATE_WORKING_LIMIT,
            "test_database_memory": primary_info["HostConfig"]["Memory"],
            "test_database_cpus": primary_info["HostConfig"].get("NanoCpus"),
        },
        "wall_seconds": wall,
        "within_180_seconds": wall <= BUDGET_SECONDS,
        "checks_passed": not verified["errors"],
        "clients": reports,
        "prologue": prologue_report,
        "physical_cleanup": physical_cleanup,
        "samples": samples,
        **verified,
    }
    _write_private(directory / "summary.json", summary)
    if not verified["errors"]:
        for client in clients:
            try:
                _remove_passed_volume(client, job)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                verified["errors"].append("Passing volume cleanup refused: " + type(error).__name__)
        summary.update(checks_passed=not verified["errors"], errors=verified["errors"])
    # End-to-end timing includes private receipts and mandatory volume cleanup.
    wall = time.monotonic() - started
    summary.update(wall_seconds=wall, within_180_seconds=wall <= BUDGET_SECONDS)
    _write_private(directory / "summary.json", summary)
    wall = time.monotonic() - started
    print(
        f"Core: {verified.get('selected_count', 0)} selected, {wall:.2f}s; report: {directory / 'summary.json'}",
        flush=True,
    )
    for issue in verified["errors"]:
        print("Core check failed: " + issue, flush=True)
    if wall > BUDGET_SECONDS:
        print("Core exceeded the 180-second development budget", flush=True)
    return (
        130
        if aborted == "interrupted"
        else (0 if not verified["errors"] and wall <= BUDGET_SECONDS else 1)
    )
