"""Capture validated files once; atomically accept their invalidation work."""

import csv
import io
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

from swingset.clock import Clock
from swingset.config import (
    Config,
    parse_derivation,
    parse_history_start,
    parse_hosts,
    parse_release,
    parse_retention,
    parse_sources,
    parse_stages,
    parse_storage,
)
from swingset.fetch.archive import canonical, digest, durable_write
from swingset.schedule.fair_policy import parse_scheduler
from swingset.state.db import Database
from swingset.state.override_validation import validate_override
from swingset.state.recipes import capture_runtime, captured_audit_inputs, captured_recipe_inputs
from swingset.state.work import WorkUnit, accept_inputs

#: Approved decreases for the publication gate (`publish/gate.py`).
RELEASE_DECREASES = "release_decreases.csv"


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
        if path.name != RELEASE_DECREASES:
            validate_override(path, body)
        # Only builds read `release_decreases.csv`, for the publication gate
        # (D-0225); no derivation depends on it, so accepting a change queues
        # nothing. Every loop captures the bundle, so a typo in it must not
        # stop them: the gate parses it and refuses the release with the line.
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
        parse_stages(files["config/sources.toml"]),
        parse_derivation(files["config/sources.toml"]),
        parse_release(files["config/sources.toml"]),
        parse_storage(files["config/sources.toml"]),
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
    missing: list[tuple[Path, bytes]] = []
    for name, body in files.items():
        path = target / name
        if not path.exists():
            missing.append((path, body))
        elif digest(path.read_bytes()) != hashes[name]:
            raise ValueError(f"corrupt captured input: {path}")
    if missing:
        # Each member keeps its real file and directory durability barriers.
        # The context waits for every writer even if one future raises; a failed
        # member must never leave background writes or publish a new manifest.
        with ThreadPoolExecutor(max_workers=8) as writers:
            futures = [writers.submit(durable_write, path, body) for path, body in missing]
            for future in futures:
                future.result()
    durable_write(target / "manifest.json", canonical(hashes))
    return InputBundle(
        bundle_digest,
        target,
        MappingProxyType(files),
        config,
        config_dir.resolve(),
        overrides_dir.resolve(),
    )


def accept(
    database: Database, bundle: InputBundle, clock: Clock, *, rehydrate: bool = False
) -> set[str]:
    from swingset.state.work import affected_work

    if rehydrate:
        selected = database.connection.execute(
            "SELECT value FROM ops.meta WHERE key='input_bundle_hash'"
        ).fetchone()
        if (
            not database.holds_writer_lock
            or not (database.state_dir / "RESTORE_PENDING").is_file()
            or selected is None
            or str(selected[0]) != bundle.digest
        ):
            raise ValueError("retained input rehydration requires its exclusive pending restore")
        manifest = json.loads(
            (database.state_dir / "inputs" / bundle.digest / "manifest.json").read_text()
        )
        if (
            not isinstance(manifest, dict)
            or digest(canonical(manifest)) != bundle.digest
            or manifest != bundle.file_hashes
        ):
            raise ValueError("retained input rehydration bytes differ from the approved bundle")

    def affected(name: str, old: str | None, new: str, conn: object) -> list[WorkUnit]:
        del old, new, conn
        if rehydrate:
            return []
        units = list(affected_work(database.connection, name))
        if name in {"overrides/event_aliases.csv", "overrides/source_urls.csv"}:
            # A source only an override names has no listing yet: its mapping
            # unit starts from this hint (plan F19).
            from swingset.project.map import override_sources

            units.extend(
                WorkUnit("project", "mapping", source)
                for source in override_sources({name: bundle.files.get(name, b"")})
            )
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
        for row in conn.execute(
            "SELECT input_name FROM accepted_inputs WHERE consumer = 'pipeline'"
        ):
            name = str(row[0])
            if name not in digests and not name.startswith("version/"):
                digests[name] = digest(b"")
        # Environment leaves pipeline recipes completely, including migrated state.
        conn.execute(
            "DELETE FROM accepted_inputs WHERE consumer = 'pipeline' AND input_name = 'recipe/env'"
        )
        digests.pop("recipe/env", None)
        had_audit = (
            conn.execute(
                "SELECT 1 AS _value_0 FROM accepted_inputs WHERE consumer = 'audit' LIMIT 1"
            ).fetchone()
            is not None
        )
        audit = captured_audit_inputs(bundle.files)
        for (name,) in conn.execute(
            "SELECT input_name FROM accepted_inputs WHERE consumer = 'audit'"
        ):
            audit.setdefault(str(name), digest(b""))
        audit_changed = accept_inputs(
            database, "audit", audit, lambda *args: (), accepted_at=clock.now().isoformat()
        )
        if not had_audit:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT relname AS name FROM pg_catalog.pg_class WHERE relkind = 'r' AND (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
                )
            }
            selected = False
            if "derivation_scopes" in tables:
                selected = (
                    conn.execute(
                        "SELECT 1 AS _value_0 FROM derivation_scopes WHERE NOT materialized_generation_id IS NULL LIMIT 1"
                    ).fetchone()
                    is not None
                )
            if "source_units" in tables:
                selected = (
                    selected
                    or conn.execute(
                        "SELECT 1 AS _value_0 FROM source_units WHERE NOT accepted_generation_id IS NULL LIMIT 1"
                    ).fetchone()
                    is not None
                )
            if not selected:
                baseline = {
                    "audit": audit,
                    "versions": {
                        name: value
                        for name, value in digests.items()
                        if name.startswith("recipe/code/")
                    },
                }
                conn.execute(
                    (
                        "INSERT INTO meta (key, value) VALUES ('audit_bootstrap_context', %s) ON CONFLICT(key) DO NOTHING"
                    ),
                    (json.dumps(baseline, sort_keys=True),),
                )
        if audit_changed:
            from swingset.log import log

            log("audit_digest_changed", scopes=sorted(audit_changed))
            if had_audit:
                try:
                    with (database.state_dir / "ALERTS.log").open("a") as handle:
                        handle.write(
                            f"{clock.now().isoformat()} [audit-digest-changed] verify required: {','.join(sorted(audit_changed))}\n"
                        )
                except OSError as exc:
                    log("audit_alert_failed", error=str(exc))
            conn.execute(
                (
                    "INSERT INTO meta (key, value) VALUES ('audit_changed_scopes', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                ),
                (json.dumps(sorted(audit_changed)),),
            )
        changed = accept_inputs(
            database, "pipeline", digests, affected, accepted_at=clock.now().isoformat()
        )
        if not rehydrate and changed & {
            "overrides/identity_overrides.csv",
            "overrides/suppressions.csv",
        }:
            from swingset.state.correction_age import record_pending

            record_pending(conn, database.state_dir, now=clock.now().isoformat())
            conn.execute(
                (
                    "INSERT INTO meta (key, value) VALUES ('correction_detected_at', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                ),
                (clock.now().isoformat(),),
            )
        conn.execute(
            (
                "INSERT INTO meta (key, value) VALUES ('input_bundle_hash', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ),
            (bundle.digest,),
        )
        from swingset.state.derivations import available, refresh

        if available(conn):
            refresh(conn, clock.now())
    return changed


def accepted_override(database: Database, name: str) -> bytes:
    """The bytes of one file in the accepted input bundle, or empty when absent.

    Readers outside a recipe, such as the year-gap review, use this so they
    see the same override the projector was given (D-0187).
    """
    row = database.connection.execute(
        "SELECT value FROM meta WHERE key = 'input_bundle_hash'"
    ).fetchone()
    if row is None:
        return b""
    path = database.state_dir / "inputs" / str(row[0]) / name
    return path.read_bytes() if path.is_file() else b""


def load(state_dir: Path, bundle_digest: str) -> InputBundle:
    """Read one retained bundle exactly; never recapture today's runtime files."""
    from swingset.fetch.archive import Archive

    Archive._validate_hash(bundle_digest)
    root = Path(state_dir).resolve() / "inputs" / bundle_digest
    if not root.resolve().is_relative_to(Path(state_dir).resolve()):
        raise ValueError("retained input bundle escapes its state directory")
    manifest = json.loads((root / "manifest.json").read_text())
    if not isinstance(manifest, dict) or digest(canonical(manifest)) != bundle_digest:
        raise ValueError("retained input bundle manifest identity differs")
    files: dict[str, bytes] = {}
    for name, expected in manifest.items():
        relative = Path(name)
        path = root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
        ):
            raise ValueError("retained input bundle member is absent or unsafe")
        body = path.read_bytes()
        if digest(body) != expected:
            raise ValueError("retained input bundle member digest differs")
        files[name] = body
    if not {"config/hosts.toml", "config/sources.toml"}.issubset(files):
        raise ValueError("retained input bundle has no configuration")
    config = Config(
        parse_hosts(files["config/hosts.toml"]),
        parse_sources(files["config/sources.toml"]),
        parse_history_start(files["config/sources.toml"]),
        parse_scheduler(files["config/sources.toml"]),
        parse_retention(files["config/sources.toml"]),
        parse_stages(files["config/sources.toml"]),
        parse_derivation(files["config/sources.toml"]),
        parse_release(files["config/sources.toml"]),
        parse_storage(files["config/sources.toml"]),
    )
    return InputBundle(
        bundle_digest, root, MappingProxyType(files), config, root / "config", root / "overrides"
    )
