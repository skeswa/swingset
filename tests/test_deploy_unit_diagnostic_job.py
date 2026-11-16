"""The composed diagnostic keeps the qualified producer and one audited operator separate."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_deploy_import_job import APP, PLATFORM
from test_deploy_import_job import job as job
from test_deploy_verification_job import graph as graph

from swingset.deploy import unit_diagnostic as operator
from swingset.deploy import unit_diagnostic_job as supervisor


@pytest.fixture
def diagnostic(graph, monkeypatch, request):
    original, backend, files = graph
    target = getattr(request, "param", operator.UNIT)
    policy = copy.deepcopy(original.policy)
    body = Path(operator.__file__).read_bytes()
    checksum = hashlib.sha256(body).hexdigest()
    producer = {
        "image": operator.IMAGE,
        "runtime_artifact": operator.RUNTIME_ARTIFACT,
        "source_revision": operator.SOURCE,
        "installed_proof_sha256": operator.INSTALLED_PROOF,
        "installed_file_count": 570,
        "linker_version": "10",
    }
    policy.update(
        format=supervisor.UnitDiagnosticJob.FORMAT,
        runtime_image=operator.IMAGE,
        source_sha256="ae3bbcd17ae1d4a8af8650bd2fa50afcc36aa622c2280dcfc03825141dbafd17",
        producer=producer,
        operator_sha256=checksum,
        unit=target,
        business_clock=operator.BUSINESS_CLOCK,
        source_checkpoint=operator.SOURCE_CHECKPOINT,
        expected_checkpoint=operator.ACCEPTED_CHECKPOINT,
        input_acceptance_binding=None,
        freeze_seconds=2700,
        write_seconds=operator.WRITE_SECONDS,
        report_seconds=90,
    )
    policy["verifier_argv"] = [
        operator.IMAGE if part == original.policy["runtime_image"] else part
        for part in policy["verifier_argv"]
    ]
    image_index = policy["verifier_argv"].index(operator.IMAGE)
    policy["verifier_argv"][image_index:image_index] = [
        "--env",
        "SWINGSET_COPY_ROOT=/var/lib/swingset-runtime/unit-copy-jobs",
        "--env",
        "TMPDIR=/var/lib/swingset-spill",
        "--env",
        "SWINGSET_RUNTIME_IMAGE=" + operator.RUNTIME_ARTIFACT,
    ]
    image_index = policy["verifier_argv"].index(operator.IMAGE)
    policy["verifier_argv"][image_index + 1 :] = supervisor.diagnostic_command(target)
    directory = original.root / "operator"
    directory.mkdir(mode=0o555)
    # Root ownership is tested separately; this fixture runs as the local user.
    monkeypatch.setattr(supervisor, "operator_directory", lambda path: None)
    mount = {
        "type": "bind",
        "source": str(directory),
        "target": "/run/swingset-checker",
        "readonly": True,
    }
    files[str(directory / "unit_diagnostic.py")] = body
    index = next(i for i, item in enumerate(policy["mounts"]) if item["target"] == mount["target"])
    policy["mounts"][index] = mount
    network_path = str(original.app / "config/network.json")
    network = json.loads(files[network_path])
    network.update(
        image_id=operator.IMAGE,
        network=policy["deployment"] + "-private",
        workers={"verifier": {"address": "172.30.240.18"}},
    )
    files[network_path] = json.dumps(network, sort_keys=True).encode()
    policy["network_policy_sha256"] = hashlib.sha256(files[network_path]).hexdigest()
    proof_path = str(original.app / "authorization" / (APP + ".json"))
    proof = json.loads(files[proof_path])
    proof["policy_sha256"] = hashlib.sha256(
        json.dumps(network, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    files[proof_path] = json.dumps(proof).encode()
    backend.application["Config"]["Env"] += [
        "SWINGSET_COPY_ROOT=/var/lib/swingset-runtime/unit-copy-jobs",
        "TMPDIR=/var/lib/swingset-spill",
        "SWINGSET_RUNTIME_IMAGE=" + operator.RUNTIME_ARTIFACT,
    ]
    backend.application["Image"] = operator.IMAGE
    backend.application["Config"]["Cmd"] = supervisor.diagnostic_command(target)
    backend.application["Mounts"][index] = {
        "Type": "bind",
        "Source": mount["source"],
        "Destination": mount["target"],
        "RW": False,
    }
    backend.application["HostConfig"]["Mounts"][index] = {
        "Type": "bind",
        "Source": mount["source"],
        "Target": mount["target"],
        "ReadOnly": True,
    }
    backend.policy = policy

    def create(**updates):
        policy.update(updates)
        path = original.root / "diagnostic-policy.json"
        path.write_text(json.dumps(policy))
        path.chmod(0o600)
        plan_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        verdict = {
            "verdict": "ACCEPT_PROFILE_AND_OWNED_CLEANUP",
            "plan_sha256": plan_sha,
            "container_id": APP,
            "owned_database_limit": 1,
            "base_cleanup_authorized": False,
            "producer": producer,
            "operator_sha256": checksum,
            "unit": target,
        }
        review = original.root / "diagnostic-review.json"
        review.write_text(json.dumps(verdict))
        review.chmod(0o600)
        return supervisor.UnitDiagnosticJob(
            path,
            plan_sha,
            APP,
            PLATFORM,
            review=review,
            review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
            runner=backend,
            clock=lambda: backend.now,
            sleep=backend.sleep,
            free=lambda path: 500000000000,
        )

    return create, backend, files


@pytest.mark.parametrize("graph", [True], indirect=True)
def test_composed_job_checks_mount_then_preserves_resources_and_one_copy_contract(diagnostic):
    create, backend, _ = diagnostic
    instance = create()
    instance.bound()
    instance.sample()
    result = instance.run()
    assert result["complete"] and result["graph_proof"] is False, result
    assert result["producer"]["linker_version"] == "10"
    assert result["write_budget_seconds"] == instance.policy["write_seconds"] == 75
    assert result["unit"] == "project/inventory/all"
    assert result["protected12_unchanged"]
    assert sum(call[1] == "start" for call in backend.calls) == 1
    assert len(instance.bound()["Mounts"]) == 11
    assert instance.DEADLINES == {3600} and instance.policy["owned_database_limit"] == 1


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize("diagnostic", [operator.LINK_UNIT], indirect=True)
def test_fixed_link_target_binds_review_policy_and_actual_command(diagnostic):
    create, backend, _ = diagnostic
    instance = create()
    command = instance.utility_command()
    assert command == supervisor.diagnostic_command() + ["--unit", operator.LINK_UNIT]
    assert "--unit" not in supervisor.diagnostic_command()
    assert instance.result["unit"] == operator.LINK_UNIT
    instance.bound()
    backend.application["Config"]["Cmd"] = supervisor.diagnostic_command()
    with pytest.raises(ValueError):
        instance.bound()
    assert not any(call[1] == "start" for call in backend.calls)


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize("target", [operator.LINK_UNIT, "link/event/arbitrary-event"])
def test_policy_target_cannot_disagree_with_review_or_expand_fixed_scope(diagnostic, target):
    create, backend, _ = diagnostic
    with pytest.raises(ValueError):
        create(unit=target)
    assert not any(call[1] == "start" for call in backend.calls)
    with pytest.raises(ValueError):
        supervisor.diagnostic_command("link/event/arbitrary-event")


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize(
    "fault", ["writable", "destination", "source", "command", "memory", "old_owner"]
)
def test_invalid_composition_never_starts_or_stops_an_actor(diagnostic, fault):
    create, backend, _ = diagnostic
    instance = create()
    actual = backend.application
    mount = next(
        item for item in actual["Mounts"] if item["Destination"] == "/run/swingset-checker"
    )
    configured = next(
        item for item in actual["HostConfig"]["Mounts"] if item["Target"] == "/run/swingset-checker"
    )
    if fault == "writable":
        mount["RW"] = True
    elif fault == "destination":
        mount["Destination"] = "/app/src/swingset/project/process.py"
    elif fault == "source":
        configured["Source"] = "/var/run/docker.sock"
    elif fault == "command":
        actual["Config"]["Cmd"] = ["-m", "swingset.deploy.utility"]
    elif fault == "memory":
        actual["HostConfig"]["Memory"] += 1
    else:
        (instance.root / "unit-diagnostic-watch.started").write_text("old once fence")
    result = instance.run()
    assert not result["complete"] and not result["start_attempted"]
    assert not any(call[1] in {"start", "stop"} for call in backend.calls)


@pytest.mark.parametrize("graph", [True], indirect=True)
@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_image", "sha256:" + "f" * 64),
        (
            "runtime_image",
            "sha256:346f30a492e7263c77e7c4346595e5a28e01bb1957ce6ee7e16be4f5c0f74f89",
        ),
        ("input_acceptance_binding", {"checkpoint": "unsupported prediction"}),
        ("owned_database_limit", 2),
        ("unit", "project/year/2026"),
        ("freeze_seconds", 2701),
        ("write_seconds", 60),
        ("write_seconds", 74),
        ("write_seconds", 76),
        ("source_checkpoint", "a" * 64),
        ("operator_sha256", "a" * 64),
    ],
)
def test_review_refuses_other_producer_context_or_operator_before_docker(diagnostic, field, value):
    create, backend, _ = diagnostic
    with pytest.raises(ValueError):
        create(**{field: value})
    assert not backend.calls


@pytest.mark.parametrize("graph", [True], indirect=True)
def test_modified_operator_bytes_are_refused_each_time_the_actor_is_checked(diagnostic):
    create, backend, files = diagnostic
    instance = create()
    files[str(instance.root / "operator/unit_diagnostic.py")] += b"\n# changed after review\n"
    result = instance.run()
    assert not result["start_attempted"]
    assert not any(call[1] in {"start", "stop"} for call in backend.calls)


@pytest.mark.parametrize("graph", [True], indirect=True)
def test_existing_profile_still_refuses_the_extra_operator_mount(graph):
    instance, backend, _ = graph
    backend.application["Mounts"].append(
        {
            "Type": "bind",
            "Source": "/safe/operator.py",
            "Destination": "/probe/unit_diagnostic.py",
            "RW": False,
        }
    )
    assert not instance.run()["start_attempted"]
    assert not any(call[1] == "start" for call in backend.calls)


@pytest.mark.parametrize("graph", [True], indirect=True)
def test_real_guardian_accepts_composed_checker_root_and_refuses_old_probe(diagnostic):
    from swingset.deploy import security

    create, backend, files = diagnostic
    instance = create()
    actual = instance.bound()
    actual["State"]["Running"] = True
    network = json.loads(files[str(instance.app / "config/network.json")])
    assert security.validate_container(actual, network) == "verifier"
    mounts = actual["Mounts"]
    checker = next(item for item in mounts if item["Destination"] == "/run/swingset-checker")
    assert checker["Type"] == "bind" and checker["RW"] is False
    assert actual["Config"]["Cmd"][0] == "/run/swingset-checker/unit_diagnostic.py"
    environment = dict(item.split("=", 1) for item in actual["Config"]["Env"])
    assert environment["SWINGSET_COPY_ROOT"] == "/var/lib/swingset-runtime/unit-copy-jobs"
    assert environment["TMPDIR"] == "/var/lib/swingset-spill"
    assert environment["SWINGSET_RUNTIME_IMAGE"] == operator.RUNTIME_ARTIFACT
    for argument in ("--capture-state", "--receipt"):
        command = actual["Config"]["Cmd"]
        assert command[command.index(argument) + 1].startswith("/var/lib/swingset-runtime/")
    assert not any((item.get("Name") or "").endswith("-verifier-checker") for item in mounts)
    assert {item["Destination"] for item in mounts if item["RW"]} == {
        "/var/lib/swingset-runtime",
        "/var/lib/swingset-spill",
    }
    # A complete spec that passed our supervisor must also pass the deployed
    # guardian. Its original refusal remains enforced, not bypassed.
    refused = copy.deepcopy(actual)
    next(item for item in refused["Mounts"] if item["Destination"] == "/run/swingset-checker")[
        "Destination"
    ] = "/probe/unit_diagnostic.py"
    with pytest.raises(ValueError, match="unexpected host mount"):
        security.validate_container(refused, network)
    assert not any(call[1] == "start" for call in backend.calls)


@pytest.mark.parametrize("fault", [None, "owner", "writable", "symlink", "extra"])
def test_operator_directory_exposes_only_one_readonly_public_module(tmp_path, monkeypatch, fault):
    import os
    from types import SimpleNamespace

    directory = tmp_path / "operator"
    directory.mkdir(mode=0o755)
    (directory / "unit_diagnostic.py").write_text("pass\n")
    if fault == "extra":
        (directory / "another.py").write_text("unreviewed module\n")
    directory.chmod(0o755 if fault == "writable" else 0o555)
    path = directory
    if fault == "symlink":
        path = tmp_path / "alias"
        path.symlink_to(directory, target_is_directory=True)
    original = os.fstat

    def info(descriptor):
        actual = original(descriptor)
        return SimpleNamespace(st_mode=actual.st_mode, st_uid=10001 if fault == "owner" else 0)

    try:
        with monkeypatch.context() as metadata:
            metadata.setattr(supervisor.os, "fstat", info)
            if fault is None:
                supervisor.operator_directory(path)
            else:
                with pytest.raises((ValueError, OSError)):
                    supervisor.operator_directory(path)
    finally:
        directory.chmod(0o755)
    assert os.fstat is original
