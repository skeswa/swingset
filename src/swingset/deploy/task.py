"""Give a child its own registered temporary workspace, then replace Python.

The scheduler starts this only after its hold and readiness checks. Creating
the owner record here keeps its PID equal to the eventual command's PID, so
the scheduler can prove that process dead before removing disposable files.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from .health import create_task_directory


def execute(workspace: Path, arguments: Sequence[str]) -> NoReturn:
    if not arguments:
        raise ValueError("a Python command is required")
    directory = create_task_directory(workspace)
    os.environ["TMPDIR"] = str(directory.resolve())
    os.execv(sys.executable, [sys.executable, *arguments])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if not arguments:
        parser.error("a Python command is required after --")
    execute(args.workspace, arguments)


if __name__ == "__main__":
    main()
