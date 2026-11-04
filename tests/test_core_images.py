"""Code-only edits reuse dependencies; changed tools and foreign images do not."""

import json
import stat
import subprocess
from pathlib import Path

import pytest

from swingset.dev import core_images as images
from swingset.dev import services

IMAGE = "sha256:" + "a" * 64
DEPENDENCY = "sha256:" + "b" * 64


def _project(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    for name in ("Dockerfile", "pyproject.toml", "uv.lock"):
        (root / name).write_text(name)
    (root / "src").mkdir()
    (root / "src" / "source.py").write_text("VALUE=1\n")
    return root


def _image(identifier):
    if identifier == DEPENDENCY:
        return {"Id": DEPENDENCY, "RootFS": {"Layers": ["base"]}}
    assert identifier == IMAGE
    return {
        "Id": IMAGE,
        "Os": "linux",
        "Architecture": "arm64",
        "RootFS": {"Layers": ["base", "test"]},
        "Config": {
            "Env": ["SWINGSET_DEPENDENCY_IMAGE=" + DEPENDENCY],
            "Labels": {"io.swingset.dependency-image": DEPENDENCY},
        },
    }


def test_code_edits_reuse_private_native_image_cache_without_a_build(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(images, "native_platform", lambda: "linux/arm64")
    monkeypatch.setattr(services, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(images, "_inspect", _image)
    key = images.dependency_key(root)
    images.register(root, IMAGE, DEPENDENCY, key=key)
    (root / "src" / "source.py").write_text("VALUE=2\n")
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("rebuilt for a source edit")
    )
    assert images.ensure(root) == IMAGE
    path = services.home() / "core-images" / (key + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["dependency_image"] == DEPENDENCY


@pytest.mark.parametrize("changed", ["Dockerfile", "pyproject.toml", "uv.lock", "platform"])
def test_dependency_and_tool_recipe_changes_invalidate_native_image_key(
    tmp_path, monkeypatch, changed
):
    root = _project(tmp_path)
    monkeypatch.setattr(images, "native_platform", lambda: "linux/arm64")
    before = images.dependency_key(root)
    if changed == "platform":
        monkeypatch.setattr(images, "native_platform", lambda: "linux/amd64")
    else:
        (root / changed).write_text("changed pinned input")
    assert images.dependency_key(root) != before


@pytest.mark.parametrize("damage", ["architecture", "label", "environment", "layers", "key"])
def test_native_image_adoption_refuses_foreign_dependencies(tmp_path, monkeypatch, damage):
    root = _project(tmp_path)
    monkeypatch.setattr(images, "native_platform", lambda: "linux/arm64")
    monkeypatch.setattr(services, "home", lambda: tmp_path / "home")

    def inspect(identifier):
        value = _image(identifier)
        if identifier == IMAGE:
            if damage == "architecture":
                value["Architecture"] = "amd64"
            elif damage == "label":
                value["Config"]["Labels"] = {}
            elif damage == "environment":
                value["Config"]["Env"] = []
            elif damage == "layers":
                value["RootFS"]["Layers"] = ["unrelated"]
        return value

    monkeypatch.setattr(images, "_inspect", inspect)
    with pytest.raises(RuntimeError):
        images.register(
            root, IMAGE, DEPENDENCY, key="wrong" if damage == "key" else images.dependency_key(root)
        )
    assert not (services.home() / "core-images").exists()


@pytest.mark.parametrize("bad_bounds", [False, True])
def test_cold_builder_is_owned_bounded_and_removed_even_on_guard_failure(
    tmp_path, monkeypatch, bad_bounds
):
    root = _project(tmp_path)
    monkeypatch.setattr(images, "native_platform", lambda: "linux/arm64")
    monkeypatch.setattr(services, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(images, "_inspect", _image)
    calls = []
    builder = None

    def run(command, **kwargs):
        nonlocal builder
        calls.append(command)
        if command[:3] == ["docker", "buildx", "create"]:
            builder = command[command.index("--name") + 1]
            assert builder.startswith("swingset-core-")
            assert (
                command[command.index("--driver-opt") + 1]
                == "memory=2g,memory-swap=2g,cpu-period=100000,cpu-quota=200000"
            )
        elif command[:2] == ["docker", "inspect"]:
            assert command[2] == "buildx_buildkit_" + builder + "0"
            value = {
                "HostConfig": {
                    "Memory": (4 if bad_bounds else 2) * 1024**3,
                    "MemorySwap": 2 * 1024**3,
                    "CpuPeriod": 100000,
                    "CpuQuota": 200000,
                }
            }
            return subprocess.CompletedProcess(command, 0, json.dumps([value]), "")
        elif command[:3] == ["docker", "buildx", "build"]:
            assert not bad_bounds
            assert command[command.index("--builder") + 1] == builder
            target = command[command.index("--target") + 1]
            Path(command[command.index("--iidfile") + 1]).write_text(
                DEPENDENCY if target == "dependencies" else IMAGE
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    if bad_bounds:
        with pytest.raises(RuntimeError, match="bounds"):
            images.ensure(root)
    else:
        assert images.ensure(root) == IMAGE
    assert calls[-1] == ["docker", "buildx", "rm", builder]
    assert sum(command[:3] == ["docker", "buildx", "build"] for command in calls) == (
        0 if bad_bounds else 2
    )
