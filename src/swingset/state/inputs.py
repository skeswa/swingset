"""Capture validated files once; atomically accept their invalidation work."""

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from swingset.clock import Clock
from swingset.config import Config, parse_history_start, parse_hosts, parse_sources
from swingset.fetch.archive import canonical, digest, durable_write
from swingset.state.db import Database
from swingset.state.override_validation import validate_override
from swingset.state.work import WorkUnit, accept_inputs


@dataclass(frozen=True)
class InputBundle:
    digest: str
    path: Path
    files: Mapping[str, bytes]
    config: Config

    def csv(self, name: str) -> list[dict[str, str]]:
        body = self.files.get(f"overrides/{name}", b"")
        return list(csv.DictReader(io.StringIO(body.decode())))

    @property
    def file_hashes(self) -> dict[str, str]:
        return {name: digest(body) for name, body in self.files.items()}


def capture(
    config_dir: Path, overrides_dir: Path, state_dir: Path, versions: Mapping[str, str]
) -> InputBundle:
    files = {
        f"config/{name}": (config_dir / name).read_bytes()
        for name in ("hosts.toml", "sources.toml")
    }
    if not overrides_dir.is_dir():
        raise ValueError(f"overrides directory does not exist: {overrides_dir}")
    for path in sorted(overrides_dir.glob("*.csv")):
        body = path.read_bytes()
        validate_override(path, body)
        files[f"overrides/{path.name}"] = body
    weights = Path(__file__).parents[1] / "link" / "weights.toml"
    if weights.exists():
        import tomllib

        body = weights.read_bytes()
        tomllib.loads(body.decode())
        files["link/weights.toml"] = body
    files["versions.json"] = canonical(versions)
    config = Config(
        parse_hosts(files["config/hosts.toml"]),
        parse_sources(files["config/sources.toml"]),
        parse_history_start(files["config/sources.toml"]),
    )
    hashes = {name: digest(body) for name, body in files.items()}
    bundle_digest = digest(canonical(hashes))
    target = state_dir / "inputs" / bundle_digest
    for name, body in files.items():
        path = target / name
        if not path.exists():
            durable_write(path, body)
        elif digest(path.read_bytes()) != hashes[name]:
            raise ValueError(f"corrupt captured input: {path}")
    durable_write(target / "manifest.json", canonical(hashes))
    return InputBundle(bundle_digest, target, MappingProxyType(files), config)


def accept(database: Database, bundle: InputBundle, clock: Clock) -> set[str]:
    from swingset.state.work import affected_work

    def affected(name: str, old: str | None, new: str, conn: object) -> list[WorkUnit]:
        del old, new, conn
        units = list(affected_work(database.connection, name))
        if name == "overrides/source_urls.csv" and bundle.csv("source_urls.csv"):
            units.append(WorkUnit("project", "map", "all"))
        return units

    digests = bundle.file_hashes
    # Separate version keys permit extractor-only invalidation.
    versions: dict[str, str] = json.loads(bundle.files["versions.json"])
    del digests["versions.json"]
    digests.update({f"version/{name}": value for name, value in versions.items()})
    with database.transaction() as conn:
        # Deleting an optional override is a correction too. Record its absence
        # and invalidate its consumers instead of retaining the previous policy.
        for row in conn.execute("SELECT input_name FROM accepted_inputs WHERE consumer='pipeline'"):
            name = str(row[0])
            if name not in digests and not name.startswith("version/"):
                digests[name] = digest(b"")
        changed = accept_inputs(
            database, "pipeline", digests, affected, accepted_at=clock.now().isoformat()
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES ('input_bundle_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (bundle.digest,),
        )
    return changed
