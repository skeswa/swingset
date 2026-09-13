"""Build pinned releases or coherent corrections from an acknowledged baseline."""

from __future__ import annotations

import json
from dataclasses import replace

from swingset.clock import Clock
from swingset.publish.card import render_card
from swingset.publish.service import Hub, expected_parent
from swingset.state.db import Database
from swingset.state.inputs import InputBundle

from .builder import BuildError, BuildInput, BuildMetadata, BuildResult, build_candidate
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
    if policy.get("mode") == "closure":
        from .closure import ClosureError, validate

        try:
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


def build_release(
    database: Database,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    remote: Hub | None = None,
    *,
    correction_only: bool = False,
) -> BuildResult:
    baseline_link = database.state_dir / "baseline"
    baseline = baseline_link.resolve() if baseline_link.is_symlink() else None
    with database.transaction(immediate=False) as conn:
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

            retained_policy = json.loads((reused.path / "_meta/manifest.json").read_bytes())[
                "release_policy"
            ]
            validate(conn, retained_policy["closure"])
            # An acknowledged baseline already has its durable build and remote
            # receipts. Reading it again cannot advance publication progress.
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
            data = enrich_coverage(
                conn,
                data,
                cutoff=cutoff,
                closure=coverage_closure,
                selected_mapping=selected_mapping,
            )
    if reused is not None:
        if selection is not None:
            generations.complete(database, selection, reused, now=clock.now(), run_id=run_id)
        return reused
    assert data is not None
    if pinned_manifest is not None:
        from .closure import public_manifest, retain

        # Retained input proof is not a completed build. Persist it before the
        # file phase so an interruption can reuse the exact cutoff afterward.
        with database.transaction() as conn:
            retain(conn, pinned_manifest)
        policy = dict(data.release_policy or {})
        policy["closure"] = public_manifest(pinned_manifest)
        policy["row_omissions"] = {
            "count": len(policy.get("row_omissions", ())),
            "basis": "unsupported event scopes",
        }
        data = replace(data, release_policy=policy)
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
    meta = BuildMetadata(
        run_id=run_id,
        repository_commit=captured_versions["repository"],
        expected_parent=expected_parent(database.state_dir, remote)
        if remote
        else json.loads((baseline / "PUBLISHED").read_bytes())["commit"]
        if baseline
        else None,
        schema_version=1,
        versions=captured_versions,
        card=render_card(data),
        built_at=clock.now(),
        input_paths=input_paths,
    )
    from .coverage import refresh_identity_counts

    result = build_candidate(
        database.state_dir,
        data,
        meta,
        suppressions=bundle.csv("suppressions.csv"),
        card_renderer=render_card,
        rows_finalizer=refresh_identity_counts,
    )
    if selection is not None:
        generations.complete(database, selection, result, now=clock.now(), run_id=run_id)
    return result
