"""The code identity a captured input bundle records (D-0188).

This module sits outside the scheduling loop so that the build stage and the
publication checks can read it without importing `schedule.cycle`, whose
imports would otherwise enter every project, link and build recipe scope.
"""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from swingset import __version__
from swingset.sources import sources


def repository_identity(package_root: Path | None = None) -> str:
    """Return the deployed revision, or a deterministic digest for local source.

    A path-flake deployment exports an ``uncommitted:<store name>`` marker that
    changes on every switch, including config-only ones, and every parse and
    projection unit is fingerprinted with this identity. Such a marker is
    ignored in favour of the digest of the Python sources, so only a change
    to the code itself re-queues work (D-0188). The digest covers the
    `swingset` package directory, the same tree it covered from
    `schedule/cycle.py`.
    """
    revision = os.environ.get("SWINGSET_REVISION", "").strip()
    if revision and not revision.startswith("uncommitted:"):
        return revision

    root = package_root or Path(__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        body = path.read_bytes()
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return f"source-sha256:{digest.hexdigest()}"


def versions() -> dict[str, str]:
    from swingset.link.service import LINKER_VERSION
    from swingset.project import PROJECTOR_VERSION

    result = {
        "package": __version__,
        "projector": str(PROJECTOR_VERSION),
        "linker": str(LINKER_VERSION),
        "repository": repository_identity(),
        "schema": "1",
    }
    for source in sources():
        for name, page in source.page_kinds.items():
            result[f"extract/{name}"] = str(page.EXTRACT_VERSION)
            result[f"parser/{name}"] = str(page.PARSER_VERSION)
    return result
