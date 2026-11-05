"""The server unpack supervisor preserves frozen inputs and stops only its child."""

import copy
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from swingset.deploy import unpack


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    checkpoint = root / "transport/sets/set_20261105T040000Z"
    checkpoint.mkdir(parents=True)
    for path in [root, root / "transport", root / "transport/sets", checkpoint]:
        path.chmod(0o700)
    manifest = checkpoint / "backup.json"
    manifest.write_text('{"frozen":true}\n')
    policy = {
        "format": "swingset-unpack-job-v1",
        "nonce": "123456789abc",
        "root": unpack._identity(root),
        "transport": [
            unpack._identity(path)
            for path in [root / "transport", root / "transport/sets", checkpoint]
        ],
        "image": {
            "id": "sha256:" + "a" * 64,
            "source": "source-sha256:" + "b" * 64,
            "dependency": "sha256:" + "c" * 64,
        },
        "checkpoint": {
            "set_id": checkpoint.name,
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        },
        "expected": {
            "complete": True,
            "format": "swingset-sqlite37-unpack-v1",
            "set_id": checkpoint.name,
            "source_bytes": 8,
            "source_sha256": "d" * 64,
            "schema_version": 37,
            "indexed_files": 1,
            "indexed_bytes": 4,
            "segments": 1,
            "baseline_candidate": "cand_123abc",
            "state_sqlite": "/work/sqlite37/state.sqlite",
        },
        "baseline": {"candidate": "cand_123abc", "commit": "e" * 40, "manifest_digest": "f" * 64},
        "services": {"swarm:existing": [{"id": "1" * 64, "running": True, "health": "healthy"}]},
    }
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy))
    health = {
        "swarm:existing": [
            {**policy["services"]["swarm:existing"][0], "restart_count": 3, "oom_killed": False}
        ]
    }
    clock = [0.0]
    monkeypatch.setattr(
        unpack,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    monkeypatch.setattr(
        unpack.shutil, "disk_usage", lambda path: SimpleNamespace(free=300_000_000_000)
    )
    monkeypatch.setattr(unpack, "_health", lambda: copy.deepcopy(health))
    monkeypatch.setattr(
        unpack,
        "_process_owner",
        lambda: {"pid": 123, "process_start": "456", "process_group": 123, "boot_id": "fixture"},
    )
    return SimpleNamespace(
        root=root,
        manifest=manifest,
        policy=policy,
        path=policy_path,
        health=health,
        clock=clock,
        job=unpack.UnpackJob(policy),
    )


def _docker(fixture, monkeypatch):
    job = fixture.job
    state = {"Status": "created", "Running": False, "ExitCode": 0, "OOMKilled": False}
    limits = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges=true"],
        "Init": True,
        "RestartPolicy": {"Name": "no"},
        "Memory": unpack.MEMORY,
        "MemorySwap": unpack.MEMORY,
        "NanoCpus": 2_000_000_000,
        "PidsLimit": 128,
        "IpcMode": "private",
        "LogConfig": {
            "Type": "local",
            "Config": {"max-size": "10m", "max-file": "1", "compress": "false"},
        },
    }
    argv = job.argv()
    container = {
        "Id": "2" * 64,
        "Name": "/" + job.name,
        "Image": job.image,
        "State": state,
        "HostConfig": limits,
        "Config": {
            "Labels": {unpack.LABEL: fixture.policy["nonce"]},
            "User": f"{os.getuid()}:{os.getgid()}",
            "WorkingDir": "/app",
            "Entrypoint": ["/app/.venv/bin/swingset"],
            "Cmd": argv[argv.index(job.image) + 1 :],
            "Env": ["PATH=/app/.venv/bin:/usr/bin"],
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(source),
                "Destination": target,
                "RW": writable,
                "Propagation": "rprivate",
            }
            for source, target, writable in [
                (fixture.root / "transport", "/transport", False),
                (job.job / "work", "/work", True),
                (job.job / "spill", "/spill", True),
            ]
        ],
    }
    supplements = fixture.policy.get("supplemental_inputs")
    if supplements is not None:
        container["Mounts"].append(
            {
                "Type": "bind",
                "Source": supplements["directory"]["path"],
                "Destination": "/supplemental-inputs",
                "RW": False,
                "Propagation": "rprivate",
            }
        )
    calls = []
    behavior = {
        "start": "finish",
        "existing": False,
        "image_env": [],
        "supplements": copy.deepcopy(supplements["bundles"] if supplements else []),
    }

    def call(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            image = {
                "Id": job.image,
                "Architecture": "amd64",
                "Os": "linux",
                "Config": {
                    "Env": behavior["image_env"],
                    "Labels": {
                        "org.opencontainers.image.revision": fixture.policy["image"]["source"],
                        "io.swingset.dependency-image": fixture.policy["image"]["dependency"],
                    },
                },
            }
            output = json.dumps([image])
        elif args[:2] == ("docker", "create"):
            Path(args[args.index("--cidfile") + 1]).write_text(container["Id"])
            output = container["Id"] + "\n"
        elif args[:2] == ("docker", "inspect"):
            output = json.dumps([container])
        elif args[:2] == ("docker", "start"):
            if behavior["start"] == "error":
                state.update(Error="runtime setup failed", Pid=0, StartedAt="0001-01-01T00:00:00Z")
                raise RuntimeError("Docker start rejected the reviewed runtime")
            state.update(Status="running", Running=True)
            if behavior["start"] == "signal":
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            if behavior["start"] == "finish":
                state.update(Status="exited", Running=False)
                target = job.job / "work/sqlite37"
                target.mkdir(mode=0o700)
                (target / "state.sqlite").write_bytes(b"database")
                candidate = target / "candidates/cand_123abc"
                candidate.mkdir(parents=True)
                published = {
                    "candidate_id": "cand_123abc",
                    "commit": "e" * 40,
                    "manifest_digest": "f" * 64,
                }
                (candidate / "PUBLISHED").write_text(json.dumps(published))
                (target / "baseline").symlink_to("candidates/cand_123abc")
            output = container["Id"]
        elif args[:2] == ("docker", "logs"):
            output = json.dumps(
                {
                    **fixture.policy["expected"],
                    "closure": {"complete": True, "candidates": 1, "input_bundles": 1},
                    "supplemental_input_bundles": behavior["supplements"],
                }
            )
        elif args[:2] == ("docker", "kill"):
            state.update(Status="exited", Running=False, ExitCode=137)
            output = ""
        else:
            pytest.fail(f"Unexpected Docker operation: {args}")
        return subprocess.CompletedProcess(args, 0, output, "")

    def run(args, **kwargs):
        calls.append(tuple(args))
        if args[:2] == ["docker", "inspect"] and args[-1] == job.name:
            return subprocess.CompletedProcess(
                args,
                0 if behavior["existing"] else 1,
                json.dumps([container]) if behavior["existing"] else "",
                "No such object" if not behavior["existing"] else "",
            )
        if args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 0, json.dumps([container]), "")
        assert args == ["docker", "stop", "--time", "30", container["Id"]]
        state.update(Status="exited", Running=False, ExitCode=143)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(unpack, "_call", call)
    monkeypatch.setattr(unpack.subprocess, "run", run)
    return SimpleNamespace(container=container, calls=calls, behavior=behavior)


def _supplements(fixture):
    directory = fixture.root.parent / "separate-quarantine" / "inputs"
    directory.mkdir(parents=True, mode=0o700)
    bundles = []
    for payload, added in [(b"retained archive", True), (b"checkpoint archive", False)]:
        manifest = json.dumps({"code.py": hashlib.sha256(payload).hexdigest()}).encode()
        bundle = hashlib.sha256(manifest).hexdigest()
        path = directory / bundle
        path.mkdir()
        (path / "manifest.json").write_bytes(manifest)
        (path / "code.py").write_bytes(payload)
        bundles.append(
            {
                "bundle": bundle,
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "files": 2,
                "bytes": len(payload) + len(manifest),
                "added": added,
            }
        )
    fixture.policy["supplemental_inputs"] = {
        "directory": unpack._identity(directory),
        "bundles": sorted(bundles, key=lambda bundle: bundle["bundle"]),
    }
    fixture.path.write_text(json.dumps(fixture.policy))
    return directory


@pytest.mark.parametrize(
    "damage", ["argv", "nonce", "path", "unknown_image", "root_user", "unknown_field"]
)
def test_policy_refuses_command_injection_unpinned_images_and_directory_escape(fixture, damage):
    policy = fixture.policy
    if damage == "argv":
        policy["docker_argv"] = ["docker", "run", "--privileged"]
    elif damage == "nonce":
        policy["nonce"] = "../old-job"
    elif damage == "path":
        policy["transport"][0]["path"] = "/etc"
    elif damage == "unknown_image":
        policy["image"]["id"] = "latest"
    elif damage == "root_user":
        policy["root"]["uid"] = 0
    else:
        policy["checkpoint"]["extra"] = True
    fixture.path.write_text(json.dumps(policy))
    with pytest.raises(RuntimeError):
        unpack.load_policy(fixture.path)
    assert not fixture.job.job.exists()


@pytest.mark.parametrize(
    "damage", ["manifest", "directory", "name", "image_env", "restart", "oom", "health"]
)
def test_preflight_refuses_changed_inputs_and_services_before_creating_a_job(
    fixture, monkeypatch, damage
):
    docker = _docker(fixture, monkeypatch)
    if damage == "manifest":
        fixture.manifest.write_text("changed")
    elif damage == "directory":
        fixture.manifest.parent.chmod(0o755)
    elif damage == "name":
        docker.behavior["existing"] = True
    elif damage == "image_env":
        docker.behavior["image_env"] = ["HF_TOKEN=never-print-this-value"]
    else:
        member = fixture.health["swarm:existing"][0]
        if damage == "restart":
            # A captured baseline records restart counts; a subsequent change fails.
            fixture.job.baseline = copy.deepcopy(fixture.health)
            member["restart_count"] += 1
            with pytest.raises(RuntimeError, match="restarted"):
                unpack._check_health(fixture.health, fixture.job.baseline)
            return
        member["oom_killed" if damage == "oom" else "health"] = (
            True if damage == "oom" else "unhealthy"
        )
    with pytest.raises(RuntimeError):
        fixture.job.prepare()
    assert not fixture.job.job.exists()
    assert not any(call[:2] == ("docker", "create") for call in docker.calls)


@pytest.mark.parametrize(
    "damage", ["network", "memory", "logs", "secret", "mount", "extra_mount", "failure_retention"]
)
def test_created_container_is_inspected_before_it_can_start(fixture, monkeypatch, damage):
    docker = _docker(fixture, monkeypatch)
    fixture.job.prepare()
    container = docker.container
    if damage == "network":
        container["HostConfig"]["NetworkMode"] = "host"
    elif damage == "memory":
        container["HostConfig"]["MemorySwap"] += 1
    elif damage == "logs":
        container["HostConfig"]["LogConfig"]["Config"]["compress"] = "true"
    elif damage == "secret":
        container["Config"]["Env"].append("HF_TOKEN=private")
    elif damage == "mount":
        container["Mounts"][0]["RW"] = True
    elif damage == "failure_retention":
        container["Config"]["Cmd"].remove("--keep-failed")
    else:
        container["Mounts"].append({"Destination": "/var/run/docker.sock"})
    with pytest.raises(RuntimeError):
        fixture.job.validate()
    assert not any(call[:2] == ("docker", "start") for call in docker.calls)


def test_success_retains_outputs_container_and_authoritative_result(fixture, monkeypatch):
    docker = _docker(fixture, monkeypatch)
    assert unpack.run(fixture.path) == 0
    result = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_text())
    assert result["complete"] and result["container_cleanup"]["not_running"]
    assert (fixture.job.job / "work/sqlite37/state.sqlite").read_bytes() == b"database"
    assert json.loads((fixture.job.job / "unpack-result.json").read_text())["closure"]["complete"]
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert "compress=false" in create and "max-file=1" in create and "max-size=10m" in create
    assert "--keep-failed" in create
    assert "--supplemental-inputs" not in create
    assert result["result"]["supplemental_input_bundles"] == []
    assert not any(call[1] in {"rm", "prune"} for call in docker.calls)


def test_separate_supplemental_archives_are_read_only_and_bound_to_native_receipt(
    fixture, monkeypatch
):
    directory = _supplements(fixture)
    policy = unpack.load_policy(fixture.path)
    assert Path(policy["supplemental_inputs"]["directory"]["path"]) == directory
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    docker = _docker(fixture, monkeypatch)
    assert unpack.run(fixture.path) == 0
    create = next(call for call in docker.calls if call[:2] == ("docker", "create"))
    assert (
        f"type=bind,src={directory},dst=/supplemental-inputs,bind-propagation=rprivate,readonly"
        in create
    )
    assert create[create.index("--supplemental-inputs") + 1] == "/supplemental-inputs"
    result = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_bytes())
    assert (
        result["result"]["supplemental_input_bundles"] == policy["supplemental_inputs"]["bundles"]
    )
    assert {bundle["added"] for bundle in result["result"]["supplemental_input_bundles"]} == {
        True,
        False,
    }
    assert all(path.read_bytes() == body for path, body in before.items())


@pytest.mark.parametrize(
    "damage", ["duplicate", "unknown_field", "count", "relative", "owner", "mount_option"]
)
def test_supplemental_policy_refuses_ambiguous_or_unpinned_archives(fixture, damage):
    _supplements(fixture)
    supplements = fixture.policy["supplemental_inputs"]
    if damage == "duplicate":
        supplements["bundles"].append(supplements["bundles"][0])
    elif damage == "unknown_field":
        supplements["bundles"][0]["argv"] = ["--skip-verification"]
    elif damage == "count":
        supplements["bundles"][0]["files"] = True
    elif damage == "relative":
        supplements["directory"]["path"] = "../quarantine"
    elif damage == "owner":
        supplements["directory"]["uid"] = 0
    else:
        supplements["directory"]["path"] += ",dst=/unreviewed"
    fixture.path.write_text(json.dumps(fixture.policy))
    with pytest.raises(RuntimeError):
        unpack.load_policy(fixture.path)
    assert not fixture.job.job.exists()


@pytest.mark.parametrize(
    "damage", ["manifest", "extra", "missing", "bundle_link", "manifest_link", "ancestor_link"]
)
def test_supplemental_source_changes_refuse_create_without_modifying_archives(
    fixture, monkeypatch, damage
):
    directory = _supplements(fixture)
    bundle = fixture.policy["supplemental_inputs"]["bundles"][0]["bundle"]
    manifest = directory / bundle / "manifest.json"
    if damage == "manifest":
        manifest.write_bytes(b"changed")
    elif damage == "extra":
        (directory / "unreviewed").mkdir()
    elif damage == "missing":
        manifest.unlink()
    elif damage == "bundle_link":
        target = directory / bundle
        target.rename(directory.parent / bundle)
        target.symlink_to(directory.parent / bundle, target_is_directory=True)
    elif damage == "manifest_link":
        target = directory.parent / "other-manifest"
        manifest.rename(target)
        manifest.symlink_to(target)
    else:
        original = directory.parent
        moved = original.with_name("moved-quarantine")
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
    docker = _docker(fixture, monkeypatch)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        fixture.job.prepare()
    assert not fixture.job.job.exists()
    assert not any(call[:2] == ("docker", "create") for call in docker.calls)


@pytest.mark.parametrize(
    "damage", ["missing_mount", "duplicate_mount", "writable", "source", "argv"]
)
def test_supplemental_mount_and_cli_must_match_the_reviewed_source(fixture, monkeypatch, damage):
    _supplements(fixture)
    docker = _docker(fixture, monkeypatch)
    fixture.job.prepare()
    mount = docker.container["Mounts"][-1]
    if damage == "missing_mount":
        docker.container["Mounts"].pop()
    elif damage == "duplicate_mount":
        docker.container["Mounts"][0] = copy.deepcopy(docker.container["Mounts"][-1])
    elif damage == "writable":
        mount["RW"] = True
    elif damage == "source":
        mount["Source"] = str(fixture.root / "transport")
    else:
        docker.container["Config"]["Cmd"].remove("--supplemental-inputs")
    with pytest.raises(RuntimeError):
        fixture.job.validate()
    assert not any(call[:2] == ("docker", "start") for call in docker.calls)


@pytest.mark.parametrize("damage", ["missing", "extra", "manifest", "count", "added"])
def test_supplemental_success_requires_exact_native_provenance(fixture, monkeypatch, damage):
    _supplements(fixture)
    docker = _docker(fixture, monkeypatch)
    reported = docker.behavior["supplements"]
    if damage == "missing":
        reported.pop()
    elif damage == "extra":
        reported.append({**reported[0], "bundle": "9" * 64})
    elif damage == "manifest":
        reported[0]["manifest_sha256"] = "9" * 64
    elif damage == "count":
        reported[0]["files"] += 1
    else:
        reported[0]["added"] = not reported[0]["added"]
    assert unpack.run(fixture.path) == 1
    result = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_bytes())
    assert not result["complete"] and "provenance" in result["error"]
    assert not (fixture.job.job / "unpack-result.json").exists()
    assert (fixture.job.job / "work/sqlite37/state.sqlite").is_file()


@pytest.mark.parametrize("trigger", ["signal", "deadline", "service_change"])
def test_interruption_deadline_and_health_failure_stop_only_recorded_child(
    fixture, monkeypatch, trigger
):
    docker = _docker(fixture, monkeypatch)
    docker.behavior["start"] = "signal" if trigger == "signal" else "running"
    if trigger == "deadline":
        monkeypatch.setattr(unpack, "DEADLINE", 1)
    elif trigger == "service_change":
        original = unpack._health

        def changed():
            current = original()
            if docker.container["State"]["Running"]:
                current["swarm:existing"][0]["restart_count"] += 1
            return current

        monkeypatch.setattr(unpack, "_health", changed)
    before = signal.getsignal(signal.SIGTERM)
    assert unpack.run(fixture.path) == 1
    assert signal.getsignal(signal.SIGTERM) is before
    stops = [call for call in docker.calls if call[:2] == ("docker", "stop")]
    assert stops == [("docker", "stop", "--time", "30", "2" * 64)]
    result = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_text())
    assert not result["complete"] and result["container_cleanup"]["not_running"]
    assert fixture.job.job.is_dir()


def test_cleanup_refuses_a_replaced_container_even_with_the_same_name(fixture, monkeypatch):
    docker = _docker(fixture, monkeypatch)
    fixture.job.prepare()
    docker.container["Id"] = "3" * 64
    docker.container["State"]["Running"] = True
    with pytest.raises(RuntimeError, match="ownership"):
        fixture.job.stop()
    assert not any(call[:2] == ("docker", "stop") for call in docker.calls)


def test_start_failure_receipt_records_bounded_error_and_safe_container_state(fixture, monkeypatch):
    docker = _docker(fixture, monkeypatch)
    docker.behavior["start"] = "error"
    assert unpack.run(fixture.path) == 1
    result = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_bytes())
    assert result["error_class"] == "RuntimeError"
    assert result["error"] == "Docker start rejected the reviewed runtime"
    assert result["container_cleanup"]["state"] == {
        "Error": "runtime setup failed",
        "Status": "created",
        "StartedAt": "0001-01-01T00:00:00Z",
        "Pid": 0,
    }
    assert not any(key in result["container_cleanup"] for key in ("Config", "Env"))


def test_reused_job_directory_preserves_old_files_without_adopting_or_stopping_a_container(
    fixture, monkeypatch
):
    docker = _docker(fixture, monkeypatch)
    fixture.job.job.mkdir(mode=0o700)
    marker = fixture.job.job / "retained-original-output"
    marker.write_text("preserve")
    assert unpack.run(fixture.path) == 1
    assert marker.read_text() == "preserve"
    assert not any(call[:2] in [("docker", "create"), ("docker", "stop")] for call in docker.calls)
    report = json.loads((fixture.root / "unpack-123456789abc-supervisor-result.json").read_bytes())
    assert report["container_cleanup"] == {"not_attempted": True, "not_running": True}


@pytest.mark.parametrize("damage", ["baseline", "closure", "exit"])
def test_success_requires_the_exact_restored_baseline_and_complete_closure(
    fixture, monkeypatch, damage
):
    docker = _docker(fixture, monkeypatch)
    fixture.job.prepare()
    fixture.job.validate()
    unpack._call("docker", "start", fixture.job.container_id)
    if damage == "baseline":
        published = fixture.job.job / "work/sqlite37/candidates/cand_123abc/PUBLISHED"
        published.write_text(
            json.dumps(
                {"candidate_id": "cand_123abc", "commit": "0" * 40, "manifest_digest": "f" * 64}
            )
        )
    elif damage == "closure":
        original = unpack._call

        def missing(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[:2] == ("docker", "logs"):
                value = json.loads(result.stdout)
                value["closure"]["complete"] = False
                result.stdout = json.dumps(value)
            return result

        monkeypatch.setattr(unpack, "_call", missing)
    else:
        docker.container["State"]["ExitCode"] = 2
    with pytest.raises(RuntimeError):
        fixture.job.postcheck()
    assert not (fixture.job.job / "unpack-result.json").exists()
