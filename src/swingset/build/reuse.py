"""Reuse verified releases until selected evidence or public health changes.

A closure release is triggered by its selected generations and support; a
partitioned one by its release manifest (D-0224).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .builder import BuildResult
from .closure_manifest import digest


def trigger(closure: Any, *, health: str, bundle_digest: str, token: dict[str, Any]) -> str:
    return digest(
        {
            "selected": [item["generation_id"] for item in closure.selected],
            "inventory": closure.inventory,
            "support": closure.support_token,
            "health": health,
            "bundle": bundle_digest,
            "correction": token,
        }
    )


def manifest_trigger(
    manifest: Mapping[str, Any], *, path: str, health: str, bundle_digest: str
) -> str:
    """A partitioned release's trigger: its manifest, assembly path, health and bundle.

    The manifest names every artifact, the correction token and the build
    inputs, so an unchanged manifest reuses the candidate (D-0224). Its
    baseline commit is left out: `reusable` already refuses an unpublished
    candidate built on another baseline, and an acknowledged baseline stays
    quiet after its own publication moves the baseline to it. A changed
    `[release] path` builds afresh: it is how an operator sets one assembly
    aside.
    """
    return digest(
        {
            "format": "release-manifest-trigger-v1",
            "manifest": {key: value for key, value in manifest.items() if key != "baseline_commit"},
            "path": path,
            "health": health,
            "bundle": bundle_digest,
        }
    )


def reusable(state_dir: Path, *, build_trigger: str, baseline: Path | None) -> BuildResult | None:
    from swingset.publish.safety import StaleCandidateError, verify_candidate_files

    baseline_commit = (
        json.loads((baseline / "PUBLISHED").read_bytes())["commit"] if baseline else None
    )
    directory = state_dir / "candidates"
    paths = ([baseline] if baseline else []) + (
        sorted(directory.iterdir()) if directory.exists() else []
    )
    for path in paths:
        if not (path / "BUILT").exists() or (path / "REJECTED").exists():
            continue
        try:
            built = json.loads((path / "BUILT").read_bytes())
            manifest = json.loads((path / "_meta/manifest.json").read_bytes())
            if (manifest.get("release_policy") or {}).get("build_trigger") != build_trigger:
                continue
            published = baseline is not None and path.resolve() == baseline
            if not published and (
                built.get("baseline_commit") != baseline_commit or (path / "PUBLISHED").exists()
            ):
                continue
            verify_candidate_files(path)
            return BuildResult(
                path.name,
                path,
                built["content_hash"],
                built["manifest_hash"],
                bool(built["changed"]),
                True,
            )
        except (OSError, ValueError, KeyError, StaleCandidateError):
            continue
    return None
