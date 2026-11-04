"""Run the CI test image with host-owned ordinary and physical test services."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from . import services


def run(image: str) -> int:
    """The worker receives private connections, never Docker control access."""
    primary = services.ensure_service()
    with (
        services.lifecycle_service() as physical,
        tempfile.TemporaryDirectory(prefix="swingset-ci-", dir="/tmp") as temporary,
    ):
        values = {**services.ci_environment(primary), **services.ci_environment(physical)}
        path = Path(temporary) / "test.env"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
        return subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--memory",
                "2g",
                "--memory-swap",
                "2g",
                "--network",
                "host",
                "--env-file",
                str(path),
                image,
            ],
            check=False,
        ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="The built CI test image")
    return run(parser.parse_args().image)


if __name__ == "__main__":
    raise SystemExit(main())
