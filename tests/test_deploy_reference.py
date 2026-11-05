"""Refuse stale identities and preserve the frozen reference operation's boundaries."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from swingset.deploy import reference
from swingset.deploy import reference_policy as rules
from swingset.deploy import reference_worker as worker
from swingset.deploy import reference_workspace as workspace


@pytest.fixture
def policy():
    nonce = "012345abcdef"
    return {
        "format": rules.FORMAT,
        "operation": "frozen offline reference R",
        "nonce": nonce,
        "vm_root": f"/var/tmp/swingset-stage2-reference-r-{nonce}",
        "filesystem_uuid": "e8c2b48f-f6ec-4a59-b0a0-a5e7d68f4b12",
        "filesystem_label": f"swingset-s2-R-{nonce}",
        "checkpoint_root": "/var/lib/swingset/checkpoints",
        "set_id": "set_20261105T040000Z",
        "manifest_sha256": "a" * 64,
        "raw_bytes": 38890938368,
        "raw_sha256": "b" * 64,
        "source_tree": "/nix/store/frozen-source",
        "source_identity": "source-sha256:" + "c" * 64,
        "runtime_ld_library_path": "/nix/store/frozen-gcc/lib",
        "runtime_path": "/run/current-system/sw/bin",
        "runtime_wrapper": "/nix/store/frozen-swingset/bin/swingset",
        "runtime_wrapper_sha256": "d" * 64,
        "runtime_python": "/var/lib/swingset/venv/bin/python",
        "input_bundle": "e" * 64,
        "baseline_candidate": "cand_" + "f" * 16,
        "baseline_commit": "1" * 40,
        "maximum_backend_bytes": 20 * rules.GIB,
        "stop_free_bytes": 88 * rules.GIB,
        "hard_floor_bytes": 80 * rules.GIB,
        "btrfs": "/nix/store/frozen-btrfs/bin/btrfs",
        "mkfs": "/nix/store/frozen-btrfs/bin/mkfs.btrfs",
    }


def reviewed(policy):
    raw = rules.canonical(policy)
    sources = {name: "# checked runner\n" for name in rules.FILES}
    hashes = {name: rules.sha256(body.encode()) for name, body in sources.items()}
    review = {
        "verdict": "ACCEPT",
        "operation": policy["operation"],
        "nonce": policy["nonce"],
        "policy_sha256": rules.sha256(raw),
        "script_sha256": hashes,
        "exclusive_large_job": True,
    }
    return {
        "policy_text": raw.decode(),
        "review_text": json.dumps(review),
        "runner_sources": sources,
        "script_sha256": hashes,
        "invocation_id": "2" * 32,
        "host_free_bytes": 120 * rules.GIB,
    }


def test_policy_and_review_bind_every_frozen_input(policy):
    payload = reviewed(policy)
    runtime = rules.decode_payload(payload)
    assert rules.validate_runtime(runtime)["raw_sha256"] == policy["raw_sha256"]
    runtime["input_bundle"] = "0" * 64
    with pytest.raises(ValueError, match="reviewed inputs"):
        rules.validate_runtime(runtime)


@pytest.mark.parametrize(
    "key,value",
    [
        ("vm_root", "/var/lib/swingset"),
        ("checkpoint_root", "/tmp/other"),
        ("maximum_backend_bytes", 21 * rules.GIB),
        ("logical_image_bytes", 65 * rules.GIB),
        ("hard_floor_bytes", 79 * rules.GIB),
        ("runtime_python", "/bin/sh"),
        ("runtime_path", ".:/bin"),
        ("raw_bytes", True),
        ("source_tree", "/nix/store/../etc"),
    ],
)
def test_policy_refuses_changed_scope_or_resource_limits(policy, key, value):
    with pytest.raises(ValueError):
        rules.validate_policy({**policy, key: value})


def test_review_refuses_changed_policy_bytes_and_runner_bytes(policy):
    payload = reviewed(policy)
    payload["policy_text"] += "\n"
    with pytest.raises(ValueError, match="independent review"):
        rules.decode_payload(payload)
    payload = reviewed(policy)
    payload["runner_sources"]["reference_worker.py"] += "# changed\n"
    with pytest.raises(ValueError, match="payload digest"):
        rules.decode_payload(payload)


def test_backend_budget_aligns_capacity_and_reserves_both_host_floors(policy):
    assert rules.backend_budget(policy, 110 * rules.GIB, 105 * rules.GIB + 4095) == 16 * rules.GIB
    assert rules.validate_policy(policy)["raw_bytes"] > policy["maximum_backend_bytes"]
    with pytest.raises(RuntimeError, match="12 GiB"):
        rules.backend_budget(policy, 100 * rules.GIB, 120 * rules.GIB)


@pytest.mark.parametrize("discard", ["discard", "discard=sync"])
def test_mount_accepts_only_known_synchronous_discard_readback(policy, discard):
    rules.validate_mount(
        {
            "uuid": policy["filesystem_uuid"],
            "fstype": "btrfs",
            "options": f"rw,compress=zstd:3,{discard},nodev,nosuid,noexec",
        },
        policy["filesystem_uuid"],
    )


@pytest.mark.parametrize(
    "options",
    [
        "discard=async",
        "nodiscard",
        "discard,nodatacow",
        "discard,nodatasum",
        "discard=sync,discard=async",
    ],
)
def test_mount_does_not_normalize_unsafe_options(policy, options):
    with pytest.raises(RuntimeError):
        rules.validate_mount(
            {
                "uuid": policy["filesystem_uuid"],
                "fstype": "btrfs",
                "options": "rw,compress=zstd:3,nodev,nosuid,noexec," + options,
            },
            policy["filesystem_uuid"],
        )


def test_mount_readback_is_retained_before_a_guard_refuses_it(tmp_path, monkeypatch, policy):
    image = tmp_path / "filesystem.img"
    image.write_bytes(b"tiny")
    monkeypatch.setattr(
        workspace,
        "P",
        {**policy, "outer_namespaces": {"mount": "old-mount", "network": "old-net"}},
        raising=False,
    )
    monkeypatch.setattr(workspace, "ROOT", tmp_path, raising=False)
    monkeypatch.setattr(workspace, "IMAGE", image, raising=False)
    monkeypatch.setattr(workspace, "MOUNT", tmp_path / "mounted", raising=False)
    monkeypatch.setattr(workspace, "BTRFS", policy["btrfs"], raising=False)
    monkeypatch.setattr(workspace, "MKFS", policy["mkfs"], raising=False)
    monkeypatch.setattr(workspace, "process", lambda pid: {"pid": pid})
    monkeypatch.setattr(workspace.os, "readlink", lambda path: "private-" + str(path))
    monkeypatch.setattr(workspace.os, "nice", lambda amount: None)
    monkeypatch.setattr(workspace.os, "sched_setaffinity", lambda *args: None, raising=False)
    monkeypatch.setattr(workspace.os, "sched_getaffinity", lambda *args: {0}, raising=False)
    monkeypatch.setattr(workspace, "storage_guard", lambda: None)
    records = []
    commands = []
    monkeypatch.setattr(workspace, "save", lambda path, value: records.append((path.name, value)))

    def command(argv, timeout=30):
        commands.append(argv)
        return (
            json.dumps(
                {
                    "filesystems": [
                        {
                            "uuid": policy["filesystem_uuid"],
                            "fstype": "btrfs",
                            "options": "discard=async",
                        }
                    ]
                }
            )
            if argv[0] == "findmnt"
            else ""
        )

    monkeypatch.setattr(workspace, "command", command)
    with pytest.raises(RuntimeError, match="compression/discard"):
        workspace.inside()
    assert records[-1][0] == "mount-readback.json"
    assert records[-1][1]["mounts"][0]["options"] == "discard=async"
    assert not any("quota" in argv for argv in commands)


def test_owned_cgroup_rejects_replacement_and_older_peers(tmp_path, monkeypatch):
    group = tmp_path / "group"
    group.mkdir()
    (group / "cgroup.procs").write_text("123\n")
    kill = group / "cgroup.kill"
    kill.write_text("0")
    monkeypatch.setattr(workspace, "CGROUP", group, raising=False)
    identity = workspace.identity(group)
    with pytest.raises(RuntimeError, match="replaced"):
        workspace.kill_owned_cgroup({**identity, "inode": identity["inode"] + 1}, 10)
    monkeypatch.setattr(workspace, "process", lambda pid: {"start_ticks": 9})
    with pytest.raises(RuntimeError, match="older unrelated"):
        workspace.kill_owned_cgroup(identity, 10)
    assert kill.read_text() == "0"
    monkeypatch.setattr(workspace, "process", lambda pid: {"start_ticks": 11})
    workspace.kill_owned_cgroup(identity, 10)
    assert kill.read_text() == "1"


@pytest.mark.parametrize("change", ["stale", "stop", "nonce", "space"])
def test_host_lease_cannot_be_replayed_or_weakened(policy, change):
    lease = {
        "nonce": policy["nonce"],
        "host_free_bytes": 100 * rules.GIB,
        "vm_monotonic_received": 100,
        "stop": False,
    }
    rules.validate_lease(lease, policy, 101)
    if change == "stale":
        lease["vm_monotonic_received"] = 80
    elif change == "stop":
        lease["stop"] = True
    elif change == "nonce":
        lease["nonce"] = "0" * 12
    else:
        lease["host_free_bytes"] = 87 * rules.GIB
    with pytest.raises(RuntimeError):
        rules.validate_lease(lease, policy, 101)


def test_fresh_reference_refuses_a_cached_baseline_or_publication_intent(tmp_path):
    baseline = tmp_path / "cand_old"
    baseline.mkdir()
    (baseline / "BUILT").write_text("{}")
    with pytest.raises(RuntimeError, match="fresh R"):
        worker.fresh_candidate(tmp_path, {baseline.name})
    fresh = tmp_path / "cand_new"
    fresh.mkdir()
    (fresh / "BUILT").write_text("{}")
    assert worker.fresh_candidate(tmp_path, {baseline.name}) == fresh
    (fresh / "PUBLISHING").write_text("{}")
    with pytest.raises(RuntimeError, match="publication intent"):
        worker.fresh_candidate(tmp_path, {baseline.name})


def test_reference_never_clears_an_activation_barrier(tmp_path):
    marker = tmp_path / "RESTORE_PENDING"
    marker.write_text("retained")
    with pytest.raises(RuntimeError, match="activation barrier"):
        worker.require_buildable_state(tmp_path)
    assert marker.read_text() == "retained"


@pytest.mark.parametrize("corruption", [None, "policy", "transfer"])
def test_authoritative_receipt_requires_stable_sftp_hash_and_policy(
    tmp_path, monkeypatch, policy, corruption
):
    payload = reviewed(policy)
    policy_hash = rules.sha256(payload["policy_text"].encode())
    review_hash = rules.sha256(payload["review_text"].encode())
    receipt = {
        "nonce": policy["nonce"],
        "operation": policy["operation"],
        "invocation_id": payload["invocation_id"],
        "script_sha256": payload["script_sha256"],
        "policy_sha256": policy_hash,
        "review_sha256": review_hash,
    }
    if corruption == "policy":
        receipt["policy_sha256"] = "0" * 64
    body = rules.canonical(receipt)
    fingerprint = {"sha256": rules.sha256(body), "bytes": len(body), "device": 1, "inode": 2}

    def run(argv, **kwargs):
        if argv[0].endswith("/ssh"):
            return SimpleNamespace(returncode=0, stdout=json.dumps(fingerprint), stderr="")
        assert argv[0].endswith("/sftp")
        (tmp_path / "authoritative-source-result.json").write_bytes(
            body + (b"corrupt" if corruption == "transfer" else b"")
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reference.subprocess, "run", run)
    args = (
        {},
        policy,
        tmp_path,
        payload["invocation_id"],
        payload["script_sha256"],
        policy_hash,
        review_hash,
    )
    if corruption:
        with pytest.raises(RuntimeError):
            reference.fetch_authoritative_receipt(*args)
    else:
        assert reference.fetch_authoritative_receipt(*args) == receipt


@pytest.mark.parametrize("raw_bytes", [0, 128 * rules.GIB + 1])
def test_policy_refuses_unbounded_logical_database(policy, raw_bytes):
    with pytest.raises(ValueError, match="database size"):
        rules.validate_policy({**policy, "raw_bytes": raw_bytes})


def test_backend_capacity_guard_refuses_resize_and_preserves_image(tmp_path, monkeypatch):
    image = tmp_path / "filesystem.img"
    cap = 12 * rules.GIB
    with image.open("xb") as output:
        output.truncate(cap)
    monkeypatch.setattr(workspace, "IMAGE", image, raising=False)
    monkeypatch.setattr(
        workspace,
        "P",
        {"image_identity": workspace.identity(image), "backend_cap_bytes": cap},
        raising=False,
    )
    workspace.check_image()
    with image.open("r+b") as output:
        output.truncate(cap + 4096)
    with pytest.raises(RuntimeError, match="capacity changed"):
        workspace.check_image()
    assert image.stat().st_size == cap + 4096


@pytest.mark.parametrize("failure", ["enospc", "timeout"])
def test_command_failure_retains_bounded_diagnostics_before_raise(tmp_path, monkeypatch, failure):
    argv = ["mkfs.btrfs", "owned-image"]

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30, output=b"partial", stderr=b"timeout")
        return subprocess.CompletedProcess(argv, 1, "x" * 20000, "ENOSPC: No space left on device")

    monkeypatch.setattr(rules.subprocess, "run", run)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        rules.checked_command(tmp_path, argv)
    row = json.loads(next(tmp_path.glob("command-failure-*.json")).read_text())
    assert row["argv"] == argv
    assert row["exit_code"] == (None if failure == "timeout" else 1)
    assert len(row["stdout"]) <= 16384
    assert row["stderr"] == (
        "timeout" if failure == "timeout" else "ENOSPC: No space left on device"
    )


def test_loop_capacity_mismatch_refuses_before_application_writes(tmp_path, monkeypatch):
    image = tmp_path / "filesystem.img"
    cap = 12 * rules.GIB
    with image.open("xb") as output:
        output.truncate(cap)
    monkeypatch.setattr(workspace, "IMAGE", image, raising=False)
    monkeypatch.setattr(
        workspace,
        "P",
        {"image_identity": workspace.identity(image), "backend_cap_bytes": cap},
        raising=False,
    )
    monkeypatch.setattr(workspace, "command", lambda argv: str(cap - 4096))
    with pytest.raises(RuntimeError, match="loop capacity differs"):
        workspace.check_loop_capacity("/dev/loop123")
    assert list(tmp_path.iterdir()) == [image]
    monkeypatch.setattr(workspace, "command", lambda argv: str(cap))
    assert workspace.check_loop_capacity("/dev/loop123") == cap
