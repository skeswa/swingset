"""Capture validated files once; atomically accept their invalidation work."""

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from swingset.clock import Clock
from swingset.config import (
    Config,
    parse_history_start,
    parse_hosts,
    parse_retention,
    parse_sources,
)
from swingset.fetch.archive import canonical, digest, durable_write
from swingset.schedule.fair_policy import parse_scheduler
from swingset.state.db import Database
from swingset.state.override_validation import validate_override
from swingset.state.recipes import capture_runtime, captured_recipe_inputs
from swingset.state.work import WorkUnit, accept_inputs


@dataclass(frozen=True)
class InputBundle:
    digest: str
    path: Path
    files: Mapping[str, bytes]
    config: Config
    config_dir: Path | None = None
    overrides_dir: Path | None = None

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
    files.update(capture_runtime())
    config = Config(
        parse_hosts(files["config/hosts.toml"]),
        parse_sources(files["config/sources.toml"]),
        parse_history_start(files["config/sources.toml"]),
        parse_scheduler(files["config/sources.toml"]),
        parse_retention(files["config/sources.toml"]),
    )
    # The retention limits as values, not as the bytes they were read from. An
    # absent `[retention]` table means the defaults, so a bundle that carried
    # only `config/sources.toml` could not tell a reader what the size cap was
    # when it was captured
    # ([D-0156](../../../journal/decisions/0156-capture-the-retention-limits-as-values.md)).
    files["policy/retention.json"] = canonical(asdict(config.retention))
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
    return InputBundle(
        bundle_digest,
        target,
        MappingProxyType(files),
        config,
        config_dir.resolve(),
        overrides_dir.resolve(),
    )


def accept(database: Database, bundle: InputBundle, clock: Clock) -> set[str]:
    from swingset.state.work import affected_work

    def affected(name: str, old: str | None, new: str, conn: object) -> list[WorkUnit]:
        del old, new, conn
        units = list(affected_work(database.connection, name))
        if name == "overrides/source_urls.csv" and bundle.csv("source_urls.csv"):
            units.append(WorkUnit("project", "map", "all"))
        return units

    # The runtime has one accepted identity. Its complete bytes remain in the
    # bundle; accepting hundreds of files must not invalidate every scope once
    # per file. Fetch scheduling is excluded from interpretation policy.
    digests = {
        name: value
        for name, value in bundle.file_hashes.items()
        if not name.startswith(("runtime/", "recipes/"))
    }
    digests.update(
        captured_recipe_inputs(bundle.files, history_start=bundle.config.history_start.isoformat())
    )
    digests["policy/inventory_year"] = digest(canonical(clock.now().year))
    # Separate version keys permit extractor-only invalidation.
    versions: dict[str, str] = json.loads(bundle.files["versions.json"])
    del digests["versions.json"]
    digests.update({f"version/{name}": value for name, value in versions.items()})
    with database.transaction() as conn:
        from swingset.state.identity_journal import accept_journal

        accept_journal(
            conn,
            bundle.files.get("overrides/identity_overrides.csv", b""),
            bundle_digest=bundle.digest,
            now=clock.now().isoformat(),
        )
        # Deleting an optional override is a correction too. Record its absence
        # and invalidate its consumers instead of retaining the previous policy.
        for row in conn.execute("SELECT input_name FROM accepted_inputs WHERE consumer='pipeline'"):
            name = str(row[0])
            if name not in digests and not name.startswith("version/"):
                digests[name] = digest(b"")
        changed = accept_inputs(
            database, "pipeline", digests, affected, accepted_at=clock.now().isoformat()
        )
        if changed & {"overrides/identity_overrides.csv", "overrides/suppressions.csv"}:
            from swingset.state.correction_age import record_pending

            record_pending(conn, database.state_dir, now=clock.now().isoformat())
            conn.execute(
                "INSERT INTO meta(key,value) VALUES ('correction_detected_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (clock.now().isoformat(),),
            )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES ('input_bundle_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (bundle.digest,),
        )
        from swingset.state.derivations import available, refresh

        if available(conn):
            refresh(conn, clock.now())
    return changed
