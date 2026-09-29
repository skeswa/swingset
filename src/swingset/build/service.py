"""Build pinned releases or coherent corrections from an acknowledged baseline."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.publish.card import render_card
from swingset.publish.service import Hub, expected_parent
from swingset.state.db import Database
from swingset.state.inputs import InputBundle

from .builder import BuildError, BuildInput, BuildMetadata, BuildResult, build_candidate
from .event_artifacts import artifact_source
from .identity_policy import apply_identity_policy, baseline_tables, correction_token
from .input import read_build_input, read_selected_input
from .schema import PRIMARY_KEYS, SCHEMAS


def correction_needed(database: Database, bundle: InputBundle) -> bool:
    baseline = database.state_dir / "baseline"
    if not baseline.is_symlink():
        return False
    policy = json.loads((baseline / "_meta/manifest.json").read_bytes()).get("release_policy")
    if not policy:
        return True
    if policy.get("mode") == "partitioned":
        # The artifacts are immutable and named by digest; what can move under
        # a published partitioned release is the identity policy, an
        # admission policy, a revocation, or the suppressions file (D-0224).
        from .manifest import ManifestError, read
        from .manifest import correction_token as partition_token

        try:
            value = read(database.state_dir, str(policy["manifest_digest"]))
        except (ManifestError, KeyError):
            return True
        return bool(
            policy["token"] != partition_token(database.connection, value["support_digest"])
            or policy["input_file_hashes"].get("overrides/suppressions.csv")
            != bundle.file_hashes.get("overrides/suppressions.csv")
        )
    if policy.get("mode") == "closure":
        from .closure import ClosureError, validate

        try:
            with artifact_source(database.connection, Archive(database.state_dir)):
                validate(database.connection, policy["closure"])
        except (ClosureError, KeyError):
            return True
    return bool(
        policy["token"]
        != correction_token(
            database.connection,
            closure=policy.get("closure") if policy.get("mode") == "closure" else None,
        )
        or policy["input_file_hashes"].get("overrides/suppressions.csv")
        != bundle.file_hashes.get("overrides/suppressions.csv")
    )


def _metadata(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None,
    data: BuildInput,
    baseline: Path | None,
    *,
    card: bytes | None = None,
    correction_only: bool = False,
) -> BuildMetadata:
    captured_versions = {
        str(k): str(v) for k, v in json.loads(bundle.files["versions.json"]).items()
    }
    captured_versions["identity_release"] = str(
        (data.release_policy or {})["token"]["release_version"]
    )
    input_paths = (
        {
            "config": str(bundle.config_dir),
            "overrides": str(bundle.overrides_dir),
        }
        if bundle.config_dir is not None and bundle.overrides_dir is not None
        else None
    )
    return BuildMetadata(
        run_id=run_id,
        repository_commit=captured_versions["repository"],
        expected_parent=expected_parent(database.state_dir, remote)
        if remote
        else json.loads((baseline / "PUBLISHED").read_bytes())["commit"]
        if baseline
        else None,
        schema_version=1,
        versions=captured_versions,
        card=render_card(data) if card is None else card,
        built_at=clock.now(),
        input_paths=input_paths,
        # The bytes: the gate parses them, so a malformed file refuses the
        # release with the line instead of failing the build (D-0225).
        decrease_overrides=bundle.files.get("overrides/release_decreases.csv", b""),
        correction_only=correction_only,
    )


def release_path(database: Database, bundle: InputBundle) -> str:
    """The path a normal release takes: `[release] path`, or `closure` before schema 35.

    `partitioned` and `whole` need current pointers and the release tables
    (migration 35); without them the closure path (or, before derivations,
    the settled read) runs, as before phase 3.
    """
    path = bundle.config.release.path
    if path == "closure":
        return path
    from swingset.state import derivations

    conn = database.connection
    if (
        not derivations.available(conn)
        or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='release_manifests'"
        ).fetchone()
    ):
        return "closure"
    return path


def build_release(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None = None,
    *,
    correction_only: bool = False,
    report: dict[str, Any] | None = None,
) -> BuildResult:
    """Build a release candidate, or reuse one; ``report`` receives stage 1's counts."""
    baseline_link = database.state_dir / "baseline"
    baseline = baseline_link.resolve() if baseline_link.is_symlink() else None
    if not correction_only and release_path(database, bundle) != "closure":
        return _build_from_partitions(
            database, bundle, clock, run_id, remote, baseline=baseline, report=report
        )
    with (
        database.transaction(immediate=False) as conn,
        artifact_source(conn, Archive(database.state_dir), clock=clock),
    ):
        accepted = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
        if accepted is None or accepted[0] != bundle.digest:
            raise BuildError("release inputs have not been accepted")
        from swingset.state import derivations

        from . import generations
        from .closure import select as select_closure
        from .closure_rows import reconstruct
        from .coverage import enrich_coverage, health_token
        from .reuse import reusable, trigger

        cutoff = clock.now()
        pinned = (
            select_closure(conn, cutoff=cutoff, baseline=baseline)
            if derivations.available(conn) and not correction_only
            else None
        )
        pinned_manifest = pinned.manifest() if pinned is not None else None
        build_trigger = (
            trigger(
                pinned,
                health=health_token(conn, now=cutoff),
                bundle_digest=bundle.digest,
                token=correction_token(conn, closure=pinned_manifest),
            )
            if pinned is not None
            else None
        )
        reused = (
            reusable(database.state_dir, build_trigger=build_trigger, baseline=baseline)
            if build_trigger is not None
            else None
        )
        if reused is not None:
            from .closure import validate
            from .closure_manifest import ClosureError

            retained_policy = json.loads((reused.path / "_meta/manifest.json").read_bytes())[
                "release_policy"
            ]
            try:
                validate(conn, retained_policy["closure"])
            except ClosureError as error:
                # The proof is retained only after the files are durable, so an
                # attempt stopped between the two leaves a candidate without it.
                # Such a candidate cannot be reused; build afresh (D-0199).
                if (
                    str(error) != "private_closure_proof_unavailable"
                    or (reused.path / "PUBLISHED").exists()
                ):
                    raise
                from swingset.publish.safety import reject_candidate

                reject_candidate(reused.path, str(error))
                reused = None
            else:
                # An acknowledged baseline already has its durable build and
                # remote receipts. Reading it again cannot advance publication.
                if (reused.path / "PUBLISHED").exists():
                    return reused
                pinned_manifest = retained_policy["closure"]
        selection = (
            generations.select(
                database,
                bundle_digest=bundle.digest,
                correction_only=correction_only,
                closure=pinned_manifest,
            )
            if derivations.available(conn)
            else None
        )
        selected_mapping = None
        row_omissions: list[dict[str, object]] = []
        if reused is not None:
            data = None
        elif correction_only:
            if baseline is None:
                raise BuildError("correction-only build requires a published baseline")
            data = BuildInput(
                baseline_tables(baseline),
                SCHEMAS,
                PRIMARY_KEYS,
                {
                    str(row[0]): int(row[1])
                    for row in conn.execute("SELECT name,value FROM revisions")
                },
                bundle.file_hashes,
                bundle.digest,
                bundle.config.history_start,
            )
        elif pinned is not None:
            with reconstruct(
                conn, pinned, directory=database.state_dir / "tmp", baseline=baseline
            ) as reconstructed:
                selected_snapshots = {str(item["snapshot_id"]) for item in pinned.source_support}
                data = read_selected_input(
                    conn, bundle, reconstructed, snapshot_ids=selected_snapshots
                )
                row_omissions = reconstructed.omissions
                selected_mapping = list(reconstructed.iter_table("source_event_map"))
        else:
            data = read_build_input(conn, bundle)
        from swingset.build.support_closure import close_revoked_support

        if data is not None:
            closure = close_revoked_support(conn, data.tables)
            data = replace(data, tables=closure.tables)
            data = apply_identity_policy(
                conn,
                data,
                baseline=baseline,
                correction_only=correction_only,
                closure=pinned_manifest,
            )
            policy = dict(data.release_policy or {})
            policy["support_withdrawals"] = {
                "removed_rows": closure.removed_rows,
                "withdrawn_claims": closure.withdrawn_claims,
                "reasons": closure.reasons,
            }
            # The policy owns only aggregate evidence. Do not keep the pre-policy
            # graph alive through changelog comparison and Parquet construction.
            del closure
            data = replace(data, release_policy=policy)
            if correction_only:
                policy = dict(data.release_policy or {})
                policy["pending_work"] = {
                    str(row[0]): int(row[1])
                    for row in conn.execute(
                        "SELECT stage,COUNT(*) FROM pending_work GROUP BY stage"
                    )
                }
                policy["pending_findings"] = {
                    str(row[0]): int(row[1])
                    for row in conn.execute(
                        "SELECT kind,COUNT(*) FROM findings WHERE closed_at IS NULL AND owner_kind IN ('parse','parse_failure') GROUP BY kind"
                    )
                }
                data = replace(data, release_policy=policy)
            if pinned is not None:
                policy = dict(data.release_policy or {})
                policy.update(
                    closure=pinned_manifest,
                    build_trigger=build_trigger,
                    row_omissions=row_omissions,
                )
                data = replace(data, release_policy=policy)
            from .suppression import apply_suppressions

            if bundle.csv("suppressions.csv"):
                restricted = {
                    name: [dict(row) for row in rows] for name, rows in data.tables.items()
                }
                apply_suppressions(restricted, bundle.csv("suppressions.csv"))
                data = replace(data, tables=restricted)
            coverage_closure = (
                {**pinned_manifest, "inventory": [*pinned_manifest["inventory"], *row_omissions]}
                if pinned_manifest is not None
                else None
            )
            previous_source_events = [
                dict(row)
                for row in data.tables.get("coverage", ())
                if row.get("scope_kind") == "source_event"
            ]
            data = enrich_coverage(
                conn,
                data,
                cutoff=cutoff,
                closure=coverage_closure,
                selected_mapping=selected_mapping,
            )
            if pinned_manifest is not None and pinned_manifest.get("event_coverage") is not None:
                from .event_coverage import rows as event_rows

                data = replace(
                    data,
                    tables={
                        **data.tables,
                        "coverage": [
                            *data.tables.get("coverage", ()),
                            *event_rows(
                                pinned_manifest["event_coverage"],
                                selected_mapping=selected_mapping or (),
                                schemas=SCHEMAS,
                            ),
                        ],
                    },
                )
            elif correction_only:
                for row in previous_source_events:
                    row["represented_pages"] = None
                    row["selected_interpreted_pages"] = None
                    row["scope_reasons"] = sorted(
                        set(row.get("scope_reasons") or ())
                        | {"correction_only_event_support_unassessed"}
                    )
                data = replace(
                    data,
                    tables={
                        **data.tables,
                        "coverage": [*data.tables.get("coverage", ()), *previous_source_events],
                    },
                )
    if reused is not None:
        if selection is not None:
            generations.complete(database, selection, reused, now=clock.now(), run_id=run_id)
        return reused
    assert data is not None
    if pinned_manifest is not None:
        from .closure import public_manifest

        policy = dict(data.release_policy or {})
        policy["closure"] = public_manifest(pinned_manifest)
        policy["row_omissions"] = {
            "count": len(policy.get("row_omissions", ())),
            "basis": "unsupported event scopes",
        }
        data = replace(data, release_policy=policy)
    meta = _metadata(
        database, bundle, clock, run_id, remote, data, baseline, correction_only=correction_only
    )
    from .coverage import refresh_identity_counts

    def finalize_rows(rows: dict[str, list[dict[str, Any]]]) -> None:
        refresh_identity_counts(rows)
        if pinned_manifest is not None and pinned_manifest.get("event_coverage") is not None:
            from .event_coverage import finalize

            finalize(rows, pinned_manifest["event_coverage"])

    result = build_candidate(
        database.state_dir,
        data,
        meta,
        suppressions=bundle.csv("suppressions.csv"),
        card_renderer=render_card,
        rows_finalizer=finalize_rows,
    )
    if pinned_manifest is not None:
        from .closure import retain

        # Retain the private proof only once the candidate that names it is
        # durable. Retaining it first stored a new copy on every failed
        # attempt, since its cutoff differs each time (D-0199).
        with database.transaction() as conn:
            retain(conn, pinned_manifest)
    if selection is not None:
        generations.complete(
            database,
            selection,
            result,
            now=clock.now(),
            run_id=run_id,
            closure=pinned_manifest,
        )
    return result


def _build_from_partitions(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None,
    *,
    baseline: Path | None,
    report: dict[str, Any] | None = None,
) -> BuildResult:
    """Build a release from partition artifacts selected by current pointers (D-0224).

    Stage 1 builds every partition whose inputs changed and writes the
    release manifest. `partitioned` then streams the assembly one partition
    at a time (`assembly.py`). `whole` loads every artifact into `BuildInput`
    and runs the pipeline after reconstruction unchanged: revocation closure,
    identity policy, suppressions, coverage (with the artifacts' support),
    event coverage and `build_candidate`. Both publish the same rows.
    """
    from . import generations, partitioned
    from . import manifest as manifests
    from .assembly import Assembly, close_absent_dancers, withhold_invalid
    from .closure_manifest import digest
    from .coverage import enrich_coverage, health_token
    from .input import _read_snapshots, _review_queue
    from .reuse import manifest_trigger, reusable
    from .support_closure import close_revoked_support

    path = release_path(database, bundle)
    accepted = database.connection.execute(
        "SELECT value FROM meta WHERE key='input_bundle_hash'"
    ).fetchone()
    if accepted is None or accepted[0] != bundle.digest:
        raise BuildError("release inputs have not been accepted")
    release = partitioned.prepare(
        database,
        bundle.file_hashes,
        history_start=bundle.config.history_start,
        baseline=baseline,
        run_id=run_id,
        now=clock.now(),
    )
    if report is not None:
        stage = release.report
        report.update(
            path=path,
            manifest_digest=release.digest,
            manifest_bytes=release.size,
            built=len(release.built),
            withheld=len(release.withheld),
            withheld_by_reason=dict(
                sorted(Counter(reason for _, reason in release.withheld).items())
            ),
            **{
                key: stage.get(key, 0)
                for key in (
                    "partitions",
                    "multi_event",
                    "rebuilt",
                    "reused",
                    "bytes_written",
                    "stale_removed",
                    "seconds",
                    "peak_rss_bytes",
                )
            },
            artifacts_bytes=stage.get("state", {}).get("artifacts_bytes", 0),
        )
    witness: dict[str, Any] | None = None
    data: BuildInput | None = None
    streamed: Assembly | None = None
    try:
        with (
            database.transaction(immediate=False) as conn,
            artifact_source(conn, Archive(database.state_dir), clock=clock),
        ):
            accepted = conn.execute(
                "SELECT value FROM meta WHERE key='input_bundle_hash'"
            ).fetchone()
            if accepted is None or accepted[0] != bundle.digest:
                raise BuildError("release inputs have not been accepted")
            if (
                manifests.correction_token(conn, release.value["support_digest"])
                != (release.value["correction_token"])
            ):
                raise BuildError("correction policy changed while partitions were built; retry")
            cutoff = clock.now()
            build_trigger = manifest_trigger(
                release.value,
                path=path,
                health=health_token(conn, now=cutoff),
                bundle_digest=bundle.digest,
            )
            reused = reusable(database.state_dir, build_trigger=build_trigger, baseline=baseline)
            if reused is not None and (reused.path / "PUBLISHED").exists():
                # An acknowledged baseline already has its durable build and
                # remote receipts. Reading it again cannot advance publication.
                return reused
            selection = generations.select(
                database,
                bundle_digest=bundle.digest,
                correction_only=False,
                manifest=release,
                build_trigger=build_trigger,
            )
            revisions = {
                str(row[0]): int(row[1]) for row in conn.execute("SELECT name,value FROM revisions")
            }
            common = {
                "token": release.value["correction_token"],
                "mode": "partitioned",
                "path": path,
                "manifest_digest": release.digest,
                "evidence_cutoff": release.value["evidence_cutoff"],
                "withheld": {
                    "count": len(release.withheld),
                    "digest": digest(release.value["withheld"]),
                    "by_reason": dict(
                        sorted(Counter(reason for _, reason in release.withheld).items())
                    ),
                },
                "partitions": {"built": len(release.built)},
                "build_trigger": build_trigger,
            }
            support: Iterable[Mapping[str, Any]] = (
                partitioned.source_support(conn, release.support) if reused is None else ()
            )
            if reused is None and path == "partitioned":
                streamed = Assembly(
                    database.state_dir,
                    release,
                    baseline=baseline,
                    suppressions=bundle.csv("suppressions.csv"),
                    history_start=bundle.config.history_start,
                    cutoff=cutoff,
                )
                data = streamed.run(
                    conn,
                    support=support,
                    data=BuildInput(
                        {},
                        SCHEMAS,
                        PRIMARY_KEYS,
                        revisions,
                        bundle.file_hashes,
                        bundle.digest,
                        bundle.config.history_start,
                    ),
                    policy=common,
                )
            elif reused is None:
                fragments = partitioned.load(database.state_dir, release)
                close_absent_dancers(fragments.tables, fragments.counts)
                snapshot_ids = {str(item[0]) for item in release.support}
                tables = fragments.tables
                tables["snapshots"] = [
                    row for row in _read_snapshots(conn) if row["snapshot_id"] in snapshot_ids
                ]
                # Open findings describe omissions in this read snapshot, as in
                # the closure path.
                tables["review_queue"] = _review_queue(conn, {"findings"})
                data = BuildInput(
                    tables,
                    SCHEMAS,
                    PRIMARY_KEYS,
                    revisions,
                    bundle.file_hashes,
                    bundle.digest,
                    bundle.config.history_start,
                )
                del tables
                closure = close_revoked_support(conn, data.tables)
                data = replace(data, tables=closure.tables)
                data = apply_identity_policy(conn, data, baseline=baseline, correction_only=False)
                policy = dict(data.release_policy or {})
                policy.update(
                    common,
                    partitions={
                        **common["partitions"],
                        **{
                            name: count for name, count in sorted(fragments.counts.items()) if count
                        },
                    },
                    support_withdrawals={
                        "removed_rows": closure.removed_rows,
                        "withdrawn_claims": closure.withdrawn_claims,
                        "reasons": closure.reasons,
                    },
                )
                del closure
                data = replace(data, release_policy=policy)
                from .suppression import apply_suppressions

                if bundle.csv("suppressions.csv"):
                    restricted = {
                        name: [dict(row) for row in rows] for name, rows in data.tables.items()
                    }
                    apply_suppressions(restricted, bundle.csv("suppressions.csv"))
                    data = replace(data, tables=restricted)
                # The builder's checks per partition on the final rows: a
                # partition that fails is withheld, not the release (I1).
                checked: dict[str, list[Any]] = {
                    name: list(rows) for name, rows in data.tables.items()
                }
                late = Counter[str]()
                invalid = withhold_invalid(checked, bundle.config.history_start, late)
                fragments.omissions.extend(invalid)
                if report is not None and invalid:
                    # Stage 1's rows and the manifest still say `built`.
                    report["assembly_withheld"] = {
                        ("event/" if item["unit_kind"] == "event" else "dancers/")
                        + str(item["unit_id"]): item["reason"]
                        for item in invalid
                    }
                if late:
                    policy["partitions"] = {
                        **policy["partitions"],
                        **{name: count for name, count in sorted(late.items()) if count},
                    }
                    data = replace(data, tables=checked, release_policy=policy)
                data = enrich_coverage(
                    conn,
                    data,
                    cutoff=cutoff,
                    closure={"source_support": support, "inventory": fragments.omissions},
                    selected_mapping=fragments.mapping,
                )
                from .event_coverage import capture as capture_events
                from .event_coverage import rows as event_rows

                witness = capture_events(conn, cutoff=cutoff.isoformat(), selected_support=support)
                if witness is not None:
                    data = replace(
                        data,
                        tables={
                            **data.tables,
                            "coverage": [
                                *data.tables.get("coverage", ()),
                                *event_rows(
                                    witness, selected_mapping=fragments.mapping, schemas=SCHEMAS
                                ),
                            ],
                        },
                    )
        if reused is not None:
            generations.complete(database, selection, reused, now=clock.now(), run_id=run_id)
            return reused
        assert data is not None
        if streamed is not None:
            meta = _metadata(database, bundle, clock, run_id, remote, data, baseline, card=b"")
            result = streamed.write(meta, render_card)
            if report is not None:
                report.update(streamed.report())
        else:
            meta = _metadata(database, bundle, clock, run_id, remote, data, baseline)
            from .coverage import refresh_identity_counts

            def finalize_rows(rows: dict[str, list[dict[str, Any]]]) -> None:
                refresh_identity_counts(rows)
                if witness is not None:
                    from .event_coverage import finalize

                    finalize(rows, witness)

            result = build_candidate(
                database.state_dir,
                data,
                meta,
                suppressions=bundle.csv("suppressions.csv"),
                card_renderer=render_card,
                rows_finalizer=finalize_rows,
            )
    finally:
        if streamed is not None:
            streamed.close()
    generations.complete(database, selection, result, now=clock.now(), run_id=run_id)
    return result
