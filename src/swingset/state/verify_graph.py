"""Complete, copy-only differential verification of a frozen derivation graph.

Receipts authorize nothing unless every frozen unit returned exactly once.
The weekly live sample remains in ``verify`` and cannot issue these receipts.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import multiprocessing
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive, canonical, digest, durable_write
from swingset.state import engine

from .checkpoint_binding import (
    UnsupportedCheckpointTransition,
    input_acceptance_binding,
)
from .checkpoint_binding import (
    checkpoint_identity as checkpoint_identity,
)
from .db import Database, open_database
from .work import WorkUnit

FORMAT = "swingset-graph-verification-v1"


def context(conn: engine.Connection) -> dict[str, Any]:
    """The exact accepted code, runtime, declared transforms and policy values."""
    consumers: dict[str, dict[str, str]] = {"audit": {}, "pipeline": {}}
    for consumer, name, value in conn.execute(
        "SELECT consumer, input_name, digest FROM accepted_inputs ORDER BY consumer NULLS FIRST, "
        "input_name NULLS FIRST"
    ):
        if consumer in consumers:
            consumers[consumer][str(name)] = str(value)
    inputs = consumers["pipeline"]
    return {
        "audit": consumers["audit"],
        "versions": {
            name: value
            for name, value in inputs.items()
            if name.startswith(("recipe/code/", "version/"))
        },
        "inputs": inputs,
    }


def _source_units(conn: engine.Connection) -> dict[str, engine.Row]:
    return {
        str(row["unit_key"]): row
        for row in conn.execute(
            "SELECT g.generation_id, g.unit_key, g.page_kind, g.contract_version, g.input_fingerprint, "
            "g.manifest_json, g.recipe_json, g.result_json, g.report_json, g.previous_generation_id, "
            "g.work_token, g.created_at, g.run_id, g.state, g.removal_authority FROM source_units AS u "
            "JOIN source_generations AS g ON g.generation_id = u.accepted_generation_id ORDER BY "
            "u.unit_key NULLS FIRST"
        )
    }


def enumerate_units(conn: engine.Connection, scopes: Sequence[str] | None = None) -> list[str]:
    """Freeze the complete expected set before allocating any shards."""
    from .derivation_dependencies import retired

    names = [
        f"{s}/{k}/{u}"
        for s, k, u in conn.execute(
            "SELECT stage, unit_kind, unit_id FROM derivation_scopes WHERE stage IN ('project', 'link') "
            "AND NOT materialized_generation_id IS NULL ORDER BY stage NULLS FIRST, unit_kind NULLS "
            "FIRST, unit_id NULLS FIRST"
        )
        if not retired(WorkUnit(str(s), str(k), str(u)))
    ]
    for key, row in _source_units(conn).items():
        kind = json.loads(row["recipe_json"])["context"]["kind"]
        names.extend((f"extract/{kind}/{key}", f"parse/{kind}/{key}"))
    selected = None if scopes is None else {s.removeprefix("recipe/code/") for s in scopes}
    return sorted(
        name
        for name in names
        if selected is None
        or "/".join(name.split("/")[:2]) in selected
        or name.split("/")[0] in selected
    )


def validate_receipt(
    receipt: Mapping[str, Any],
    *,
    audit: Mapping[str, str] | None = None,
    checkpoint: str | None = None,
) -> bool:
    """Recompute coverage and truth flags, never trust a caller's ``complete``."""
    try:
        expected, results = receipt["expected_units"], receipt["results"]
        if (
            receipt.get("format") != FORMAT
            or not isinstance(expected, list)
            or not isinstance(results, list)
        ):
            return False
        if any(not isinstance(u, str) for u in expected) or len(set(expected)) != len(expected):
            return False
        if receipt.get("unit_set_digest") != digest(canonical(expected)):
            return False
        if Counter(r["unit"] for r in results) != Counter(expected):
            return False
        if any(
            r["state"] not in {"match", "mismatch"} or not r.get("generation_id") for r in results
        ):
            return False
        clean = all(r["state"] == "match" for r in results)
        if (
            receipt.get("complete") is not True
            or receipt.get("clean") is not clean
            or receipt.get("errors")
        ):
            return False
        if not all(isinstance(receipt[k], dict) for k in ("audit", "versions", "inputs")):
            return False
        binding = receipt.get("input_acceptance_binding")
        if binding is not None and (
            not isinstance(binding, dict)
            or binding.get("format") != "swingset-input-checkpoint-binding-v1"
            or binding.get("source_checkpoint") != receipt.get("source_checkpoint")
            or binding.get("checkpoint") != receipt["checkpoint"]
            or binding.get("inputs") != receipt["inputs"]
            or receipt.get("input_acceptance_binding_unavailable") is not None
        ):
            return False
        if audit is not None and receipt["audit"] != dict(audit):
            return False
        if checkpoint is not None and receipt["checkpoint"] != checkpoint:
            return False
        return isinstance(receipt.get("checkpoint"), str) and isinstance(
            receipt.get("bumped_transforms", []), list
        )
    except (KeyError, TypeError, ValueError):
        return False


def _stored_dependency(conn: engine.Connection, unit: WorkUnit, generation: str) -> str:
    row = conn.execute(
        (
            "SELECT dependency_set_id FROM derivation_equivalences WHERE stage = %s AND unit_kind = %s "
            "AND unit_id = %s AND generation_id = %s ORDER BY created_at DESC NULLS LAST, "
            "input_fingerprint DESC NULLS LAST LIMIT 1"
        ),
        (unit.stage, unit.unit_kind, unit.unit_id, generation),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT dependency_set_id FROM derivation_generations WHERE generation_id = %s",
            (generation,),
        ).fetchone()
    return str(row[0])


def _derive(database: Database, name: str, bundle: Any, clock: FakeClock) -> dict[str, Any]:
    from . import derivations, verify

    stage, kind, identifier = name.split("/", 2)
    unit = WorkUnit(stage, kind, identifier)
    row = database.connection.execute(
        (
            "SELECT materialized_generation_id FROM derivation_scopes WHERE stage = %s AND unit_kind = "
            "%s AND unit_id = %s"
        ),
        (stage, kind, identifier),
    ).fetchone()
    generation = str(row[0])
    expected = _stored_dependency(database.connection, unit, generation)
    found: list[str] = []
    original = derivations.complete

    def capture(conn: engine.Connection, selection: Any, **kwargs: Any) -> Any:
        if selection.unit == unit:
            found.append(derivations.digest(selection.dependencies))
        return original(conn, selection, **kwargs)

    # The real completion receives the final read set, including newly read
    # inputs. Merely calling desired() before the write would miss those.
    with patch.object(derivations, "complete", capture):
        result = verify.verify_unit(database, unit, bundle, clock, "graph_verify")
    result["generation_id"] = generation
    result["expected_dependencies"] = expected
    result["found_dependencies"] = found
    if not found:
        result["state"] = "failed"
        result.setdefault("error", "transform returned without completing its unit")
    elif result["state"] in {"match", "mismatch"} and any(value != expected for value in found):
        result.update(state="mismatch", reason="dependencies_changed")
    return result


def _source(database: Database, name: str, archive: Archive) -> dict[str, Any]:
    from dataclasses import replace

    from swingset.admission.contracts import declare, evaluate, inspect
    from swingset.admission.generations import serialize_result
    from swingset.admission.removal import wdr_preserves_rows
    from swingset.sources import get_page_kind
    from swingset.sources.base import ParseContext, ParseResult

    stage, kind, key = name.split("/", 2)
    row = database.connection.execute(
        (
            "SELECT g.generation_id, g.unit_key, g.page_kind, g.contract_version, g.input_fingerprint, "
            "g.manifest_json, g.recipe_json, g.result_json, g.report_json, g.previous_generation_id, "
            "g.work_token, g.created_at, g.run_id, g.state, g.removal_authority FROM source_units AS u "
            "JOIN source_generations AS g ON g.generation_id = u.accepted_generation_id WHERE u.unit_key "
            "= %s"
        ),
        (key,),
    ).fetchone()
    if row is None:
        raise ValueError("source unit lost its accepted pointer")
    recipe = json.loads(row["recipe_json"])
    anchor = ParseContext(**recipe["context"])
    manifest = json.loads(row["manifest_json"])
    if not manifest:
        raise ValueError("empty source manifest")
    page = get_page_kind(kind)
    parsed, reports, extract_digests = [], [], []
    for item in manifest:
        snapshot = database.connection.execute(
            (
                "SELECT s.snapshot_id, s.watch_id, s.method, s.url, s.form, s.fetched_at, s.http_status, "
                "s.etag, s.last_modified, s.content_type, s.body_sha256, s.body_bytes, s.content_changed, "
                "s.run_id, s.via, s.headers_json, s.classification, s.extract_status, s.extract_sha256, "
                "s.parse_status, s.parsed_at, s.extract_version, s.parser_version, s.captured_at, "
                "s.observed_at, s.archive_url, s.requested_archive_url, s.extract_recipe_sha256, w.source, "
                "w.parser, w.source_ref FROM snapshots AS s JOIN watches AS w USING (watch_id) WHERE "
                "snapshot_id = %s"
            ),
            (item["snapshot_id"],),
        ).fetchone()
        if snapshot is None or any(
            snapshot[field] != item[field] for field in ("watch_id", "body_sha256", "url")
        ):
            raise ValueError("source manifest no longer matches frozen snapshot")
        ctx = (
            anchor
            if item["snapshot_id"] == anchor.snapshot_id
            else ParseContext(
                str(snapshot["snapshot_id"]),
                str(snapshot["watch_id"]),
                str(snapshot["url"]),
                str(snapshot["source"]),
                str(snapshot["parser"]),
                snapshot["source_ref"],
                str(item["observed_at"]),
            )
        )
        body = archive.read_body(str(item["body_sha256"]))
        extract = page.extract(body)
        extract_digests.append(digest(canonical(extract)))
        if stage == "parse":
            result = declare(page, ctx, extract, page.parse(extract, ctx))
            parsed.append(result)
            reports.append(inspect(ctx, body, extract, result))
    found: list[Any]
    expected: list[Any]
    if stage == "extract":
        found = extract_digests
        expected = [item["extract_sha256"] for item in manifest]
    else:
        if len(parsed) == 1:
            result, report = parsed[0], reports[0]
        else:
            slots = tuple(str(item["slot"]) for item in manifest)
            coverage = replace(
                reports[0].coverage,
                expected_pages=slots,
                observed_pages=slots,
                source_count=(
                    sum(
                        int(r.coverage.source_count)
                        for r in reports
                        if r.coverage.source_count is not None
                    )
                    if all(r.coverage.source_count is not None for r in reports)
                    else None
                ),
                interpreted_count=sum(r.coverage.interpreted_count for r in reports),
            )
            report = evaluate(
                kind,
                reports[0].contract_version,
                tuple(field for r in reports for field in r.fields),
                coverage,
            )
            result = ParseResult(
                observations=tuple(o for r in parsed for o in r.observations),
                watches=tuple(w for r in parsed for w in r.watches),
                warnings=tuple(w for r in parsed for w in r.warnings),
            )
        # stage() adds this contract guard after inspect(). Compare exactly
        # what source cutoff compares, including the shared staging rules.
        if kind == "wdr.rounds":
            report = replace(
                report,
                guards=(
                    *report.guards,
                    wdr_preserves_rows(database.connection, anchor.watch_id, result),
                ),
            )
        found = [
            serialize_result(result),
            canonical(report.as_dict()).decode(),
            report.contract_version,
            extract_digests,
        ]
        expected = [
            row["result_json"],
            row["report_json"],
            row["contract_version"],
            [item["extract_sha256"] for item in manifest],
        ]
    return {
        "unit": name,
        "generation_id": str(row["generation_id"]),
        "state": "match" if expected == found else "mismatch",
        "expected_digest": digest(canonical(expected)),
        "found_digest": digest(canonical(found)),
    }


def _historical_versions(conn: engine.Connection, name: str) -> tuple[dict[str, str], bool]:
    stage, kind, key = name.split("/", 2)
    scope = "recipe/code/" + ("link" if stage == "link" else f"{stage}/{kind}")
    if stage in {"parse", "extract"}:
        row = conn.execute(
            (
                "SELECT g.recipe_json FROM source_units AS u JOIN source_generations AS g ON g.generation_id "
                "= u.accepted_generation_id WHERE u.unit_key = %s"
            ),
            (key,),
        ).fetchone()
        recipe = json.loads(row[0])
        inputs = {n: d for c, n, d in recipe["accepted_inputs"] if c == "pipeline"}
        result = {scope: inputs[scope]} if scope in inputs else {}
        label = "extract" if stage == "extract" else "parser"
        result[f"version/{label}/{kind}"] = str(recipe[label + "_version"])
    else:
        row = conn.execute(
            (
                "SELECT g.recipe_json FROM derivation_scopes AS s JOIN derivation_generations AS g ON "
                "g.generation_id = s.materialized_generation_id WHERE s.stage = %s AND s.unit_kind = %s AND "
                "s.unit_id = %s"
            ),
            (stage, kind, key),
        ).fetchone()
        latest = conn.execute(
            (
                "SELECT r.recipe_json FROM derivation_equivalences AS e JOIN derivation_recipes AS r USING "
                "(recipe_sha256) JOIN derivation_scopes AS s ON s.stage = e.stage AND s.unit_kind = "
                "e.unit_kind AND s.unit_id = e.unit_id AND s.materialized_generation_id = e.generation_id "
                "WHERE e.stage = %s AND e.unit_kind = %s AND e.unit_id = %s ORDER BY e.created_at DESC NULLS "
                "LAST, e.input_fingerprint DESC NULLS LAST LIMIT 1"
            ),
            (stage, kind, key),
        ).fetchone()
        inputs = json.loads((latest or row)[0]).get("inputs", {})
        result = {scope: inputs[scope]} if scope in inputs else {}
    return result, "recipe/env" in inputs


def _shard(
    clone: str, archive_root: str, units: list[str], bundle: Any, now: str
) -> dict[str, Any]:
    results = []
    with open_database(Path(clone), lock=False) as database:
        for name in units:
            try:
                result = (
                    _source(database, name, Archive(Path(archive_root)))
                    if name.startswith(("extract/", "parse/"))
                    else _derive(database, name, bundle, FakeClock(datetime.fromisoformat(now)))
                )
            except Exception as exc:
                result = {
                    "unit": name,
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            if result["state"] in {"match", "mismatch"}:
                result["stored_versions"], result["stored_legacy_recipe"] = _historical_versions(
                    database.connection, name
                )
                live_versions = context(database.connection)["versions"]
                result["declared_bump"] = any(
                    version in live_versions
                    and old != live_versions[version]
                    and (
                        not result["stored_legacy_recipe"] or not version.startswith("recipe/code/")
                    )
                    for version, old in result["stored_versions"].items()
                )
            results.append(result)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "results": results,
        "peak_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024),
    }


def _transferable(value: Any) -> Any:
    """Copy immutable mapping wrappers for spawn's serialized task arguments."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.replace(
            value,
            **{
                f.name: _transferable(getattr(value, f.name))
                for f in dataclasses.fields(value)
                if f.init
            },
        )
    if isinstance(value, Mapping):
        return {key: _transferable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_transferable(item) for item in value)
    return value


def _clone_checkpoint(source: Path, destination: Path) -> str:
    """Use filesystem CoW when supported, otherwise make an independent copy.

    Reflink support depends on both filesystem and platform. An unsuccessful
    attempt can leave a partial destination; discard it before the full copy.
    Mutable artifact files must never share a hardlink.
    """
    if destination.exists():
        raise FileExistsError(destination)
    command = (
        ["/bin/cp", "-c", str(source), str(destination)]
        if sys.platform == "darwin"
        else ["cp", "--reflink=always", str(source), str(destination)]
        if sys.platform.startswith("linux")
        else None
    )
    if command is not None:
        try:
            result = subprocess.run(
                command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if result.returncode == 0:
                return "reflink"
        except OSError:
            pass
        destination.unlink(missing_ok=True)
    shutil.copy2(source, destination)
    return "full_copy"


def run(
    checkpoint: Path,
    bundle: Any,
    *,
    processes: int = 1,
    scopes: Sequence[str] | None = None,
    receipt_path: Path | None = None,
    bumped_transforms: Sequence[str] = (),
) -> dict[str, Any]:
    """Freeze one read-only snapshot, then write only on private process copies.

    Scratch uses TMPDIR (default /tmp) and is removed on exit. Archive content
    is read from the checkpoint, while projector filesystem writes use the clone.
    """
    from .postgres_copy import Copies

    if processes < 1:
        raise ValueError("processes must be positive")
    started = time.monotonic()
    now = datetime.now(UTC).isoformat()
    errors: list[str] = []
    results: list[dict[str, Any]] = []
    peak = 0
    with (
        tempfile.TemporaryDirectory(
            prefix="swingset-verify-", dir=os.environ.get("TMPDIR", "/tmp")
        ) as temporary,
        Copies(checkpoint) as copies,
    ):
        root = Path(temporary)
        frozen = root / "frozen"
        with open_database(
            checkpoint, read_only=True, lock=False, allow_checkpoint_schema=True
        ) as source:
            copies.freeze(source.connection, frozen)
            archive_root = source.state_dir
        with open_database(frozen, lock=False) as database:
            expected = enumerate_units(database.connection, scopes)
            binding = None
            binding_unavailable = None
            # Capture current candidate code/policies only on the scratch copy.
            if hasattr(bundle, "digest"):
                from .inputs import accept

                with database.connection.read_snapshot():
                    try:
                        binding = input_acceptance_binding(
                            database.connection, bundle, FakeClock(datetime.fromisoformat(now))
                        )
                    except UnsupportedCheckpointTransition as error:
                        # Ordinary verification also permits deliberate data changes.
                        # A migration caller must require the stronger binding.
                        binding_unavailable = str(error)
                    source_identity = (
                        binding["source_checkpoint"]
                        if binding is not None
                        else checkpoint_identity(database.connection)
                    )
                accept(database, bundle, FakeClock(datetime.fromisoformat(now)))
            else:
                source_identity = checkpoint_identity(database.connection)
            identity = checkpoint_identity(database.connection)
            accepted = context(database.connection)
            if binding is not None and (
                binding["checkpoint"] != identity or binding["inputs"] != accepted["inputs"]
            ):
                raise RuntimeError(
                    "input acceptance differs from its read-only checkpoint projection"
                )
        receipt: dict[str, Any] = {
            "format": FORMAT,
            "checkpoint": identity,
            "source_checkpoint": source_identity,
            "input_acceptance_binding": binding,
            "input_acceptance_binding_unavailable": binding_unavailable,
            **accepted,
            "expected_units": expected,
            "unit_set_digest": digest(canonical(expected)),
            "bumped_transforms": sorted(set(bumped_transforms)),
            "scopes": None if scopes is None else list(scopes),
            "clone_methods": [],
            "started_at": now,
            "results": [],
            "errors": errors,
            "complete": False,
            "clean": False,
        }
        if receipt_path is not None:
            durable_write(receipt_path, canonical(receipt))
        jobs = []
        for index in range(min(processes, max(1, len(expected)))):
            clone = root / f"shard-{index}"
            copies.clone(frozen, clone)
            receipt["clone_methods"].append({"shard": index, "method": "postgres_template"})
            jobs.append((str(clone), str(archive_root), expected[index::processes], bundle, now))
        if processes == 1:
            outputs = [_shard(*jobs[0])]
        else:
            outputs = []
            try:
                with concurrent.futures.ProcessPoolExecutor(
                    max_workers=processes, mp_context=multiprocessing.get_context("spawn")
                ) as pool:
                    futures = [
                        pool.submit(_shard, job[0], job[1], job[2], _transferable(job[3]), job[4])
                        for job in jobs
                    ]
                    for future in futures:
                        try:
                            outputs.append(future.result())
                        except Exception as exc:
                            errors.append(f"shard failed: {type(exc).__name__}: {exc}")
            except KeyboardInterrupt:
                errors.append("interrupted")
        for output in outputs:
            results.extend(output["results"])
            peak = max(peak, output["peak_rss_bytes"])
        receipt.update(
            results=sorted(results, key=lambda r: r["unit"]),
            seconds=round(time.monotonic() - started, 3),
            peak_rss_bytes=peak,
        )
        complete = (
            not errors
            and Counter(r["unit"] for r in results) == Counter(expected)
            and all(r["state"] in {"match", "mismatch"} for r in results)
        )
        receipt.update(
            complete=complete, clean=complete and all(r["state"] == "match" for r in results)
        )
        if receipt_path is not None:
            durable_write(receipt_path, canonical(receipt))
        return receipt
