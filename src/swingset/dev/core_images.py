"""Cache native test dependencies independently of the current source checkout."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import services


def native_platform() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "linux/arm64"
    if machine in {"x86_64", "amd64"}:
        return "linux/amd64"
    raise RuntimeError("The native core image requires ARM64 or AMD64")


def dependency_key(root: Path) -> str:
    parts = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("Dockerfile", "pyproject.toml", "uv.lock")
    }
    return hashlib.sha256(
        json.dumps({"platform": native_platform(), "files": parts}, sort_keys=True).encode()
    ).hexdigest()


def _inspect(identifier: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "image", "inspect", identifier], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("The cached native test image is unavailable")
    value: dict[str, Any] = json.loads(result.stdout)[0]
    return value


def register(root: Path, image: str, dependency_image: str, *, key: str) -> str:
    """Adopt a just-built image whose captured dependency key is still current."""
    if key != dependency_key(root) or not all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in (image, dependency_image)
    ):
        raise RuntimeError("Native test image does not match its dependency inputs")
    info = _inspect(image)
    dependency = _inspect(dependency_image)
    layers = dependency.get("RootFS", {}).get("Layers", [])
    if (
        not layers
        or info.get("RootFS", {}).get("Layers", [])[: len(layers)] != layers
        or "SWINGSET_DEPENDENCY_IMAGE=" + dependency_image not in info["Config"].get("Env", [])
    ):
        raise RuntimeError("Native test image does not derive from its dependency image")
    if (
        info["Id"] != image
        or info["Os"] + "/" + info["Architecture"] != native_platform()
        or (info["Config"].get("Labels") or {}).get("io.swingset.dependency-image")
        != dependency_image
    ):
        raise RuntimeError("Native test image metadata differs from its build")
    services.write_private(
        services.home() / "core-images" / (key + ".json"),
        {"key": key, "image": image, "dependency_image": dependency_image},
    )
    return image


def ensure(root: Path) -> str:
    key = dependency_key(root)
    directory = services.home() / "core-images"
    with services.lock(directory / "build.lock"):
        record = directory / (key + ".json")
        if record.is_file():
            value = services._json(record)
            if value.get("key") != key:
                raise RuntimeError("Native test image cache identity changed")
            try:
                return register(root, value["image"], value["dependency_image"], key=key)
            except RuntimeError:
                # A user may remove Docker's cached image while keeping local metadata.
                if (
                    subprocess.run(
                        ["docker", "image", "inspect", value["image"]],
                        capture_output=True,
                        check=False,
                    ).returncode
                    == 0
                ):
                    raise
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = directory / (key + ".build.log")
        print(f"Building native test dependencies; log: {log}", flush=True)
        builder = "swingset-core-" + uuid.uuid4().hex[:16]
        created = False
        try:
            result = subprocess.run(
                [
                    "docker",
                    "buildx",
                    "create",
                    "--name",
                    builder,
                    "--driver",
                    "docker-container",
                    "--driver-opt",
                    "memory=2g,memory-swap=2g,cpu-period=100000,cpu-quota=200000",
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Could not create the bounded native test builder")
            created = True
            subprocess.run(
                ["docker", "buildx", "inspect", "--bootstrap", builder],
                capture_output=True,
                check=True,
            )
            info = json.loads(
                subprocess.run(
                    ["docker", "inspect", "buildx_buildkit_" + builder + "0"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )[0]
            limits = info["HostConfig"]
            if (
                limits["Memory"] != 2 * 1024**3
                or limits["MemorySwap"] != 2 * 1024**3
                or limits["CpuPeriod"] != 100000
                or limits["CpuQuota"] != 200000
            ):
                raise RuntimeError("Native test builder lacks its 2 GiB / two CPU bounds")
            with (
                tempfile.TemporaryDirectory(prefix="swingset-core-build-", dir="/tmp") as temporary,
                log.open("w") as output,
            ):
                ids: list[str] = []
                for target in ("dependencies", "test"):
                    iid = Path(temporary) / (target + ".iid")
                    command = [
                        "docker",
                        "buildx",
                        "build",
                        "--builder",
                        builder,
                        "--load",
                        "--platform",
                        native_platform(),
                        "--target",
                        target,
                        "--iidfile",
                        str(iid),
                    ]
                    if ids:
                        command += [
                            "--build-arg",
                            "DEPENDENCY_IMAGE_DIGEST=" + ids[0],
                            "--build-arg",
                            "SOURCE_REVISION=unknown",
                        ]
                    result = subprocess.run(
                        [*command, str(root)], stdout=output, stderr=subprocess.STDOUT, check=False
                    )
                    if result.returncode:
                        raise RuntimeError(f"Native test dependency build failed; inspect {log}")
                    ids.append(iid.read_text().strip())
                return register(root, ids[1], ids[0], key=key)
        finally:
            if created:
                subprocess.run(
                    ["docker", "buildx", "rm", builder], capture_output=True, check=False
                )
