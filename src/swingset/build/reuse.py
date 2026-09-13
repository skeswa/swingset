"""Reuse verified releases until selected evidence or public health changes."""

from __future__ import annotations

import json
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
