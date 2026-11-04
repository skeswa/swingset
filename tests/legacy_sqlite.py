"""Run obsolete SQLite upgrade assertions against their frozen original source.

Contemporary pipeline behavior runs against Postgres in ordinary tests. This
helper cannot be imported by the shipped package or select its live backend.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

CASES = {
    "test_fixture_exception.py": [
        "test_dry_run_does_not_open_state_or_request",
        "test_authorization_and_exact_manifest_fail_closed",
        "test_success_uses_shared_gate_and_preserves_all_production_facts",
        "test_redirect_escape_stops_after_one_charged_request",
        "test_failure_never_retries_and_retains_host_pause",
        "test_shared_pause_and_exhausted_daily_budget_make_no_requests",
        "test_operator_pause_is_not_bypassed",
        "test_byte_ceiling_keeps_prefix_and_never_exceeds_shared_byte_capacity",
        "test_request_ceiling_counts_redirects",
        "test_deadline_prevents_next_read_or_request",
        "test_fresh_robots_is_quarantined_without_dangling_production_blob_reference",
        "test_redirect_may_change_capture_but_not_original_resource",
        "test_source_pause_applies_to_fixture_purpose",
        "test_existing_writer_lock_prevents_accounting_access",
        "test_real_process_crash_leaves_conservative_budget_and_no_facts",
        "test_response_ceiling_and_total_ceiling_are_independent",
        "test_aggregate_byte_ceiling_stops_before_cdx_or_extra_body",
        "test_execute_without_authorization_never_opens_state",
        "test_unlisted_initial_archive_resource_is_refused_before_io",
        "test_owner_authorization_persists_when_unstarted_execution_window_moves",
        "test_fixture_bytes_and_receipt_keep_grant_day_across_dispatch_midnight",
    ],
    "test_replay_derivations.py": [
        "test_bounded_cohorts_resume_real_generations_without_parse_or_fairness",
        "test_crash_receipt_and_resume_keep_previous_atomic_completion",
        "test_refuses_unbound_or_linked_scratch_and_unsafe_receipts",
        "test_worker_crash_rolls_back_then_uses_standard_interruption_cooldown",
        "test_wallclock_reserves_atomic_worker_bound_without_starting_next_unit",
        "test_new_scope_discovered_after_earlier_cohort_is_revisited",
    ],
    "test_event_progress.py": ["test_checkpoint_preserves_facts_baselines_and_progress"],
    "test_controls.py": ["test_pause_checkpoint_restore_preserves_controls_and_retry_evidence"],
    "test_requirement_performance.py": [
        "test_v6_index_migration_preserves_findings_and_restarts_old_cursor"
    ],
    "test_retention_plan.py": [
        "test_a_caller_that_declares_nothing_keeps_what_the_finding_declared",
        "test_checkpoint_tests_still_pass_with_the_moved_closure",
    ],
    "test_registry_g1.py": ["test_v1_migration_preserves_provenance_pause_and_unknown_freshness"],
    "test_event_completion_restore.py": [
        "test_legacy_checkpoint_inventory_stays_unavailable_without_migration"
    ],
    "test_event_readiness.py": [
        "test_legacy_schema_reports_unavailable_controls_without_migration"
    ],
    "test_work_queue.py": [
        "test_migration_from_33_seeds_failing_units",
        "test_a_checkpoint_at_schema_34_verifies_and_restores",
        "test_schema_33_code_refuses_a_schema_34_file",
    ],
    "test_history_origin_dispatch.py": [
        "test_schema15_adds_only_empty_intent_receipts_preserving_prior_evidence"
    ],
    "test_derivation_equivalences.py": [
        "test_migration_from_schema_32_adds_empty_tables_and_narrows_the_trigger",
        "test_page_evidence_reads_revocations_at_schema_32",
        "test_a_checkpoint_at_schema_33_verifies_and_restores",
        "test_schema_32_code_refuses_a_schema_33_file",
    ],
    "test_derivation_payloads.py": [
        "test_migration_stores_shared_rows_once_and_keeps_every_label",
        "test_migration_failure_after_the_fill_keeps_the_old_rows_and_schema",
        "test_dropping_the_old_table_frees_pages_without_shrinking_the_file",
        "test_migration_refuses_to_drop_the_old_rows_when_a_label_does_not_match",
        "test_a_grant_written_with_foreign_keys_off_is_reported_and_cleared",
        "test_a_real_release_rebuilds_the_same_way_after_the_migration",
    ],
    "backup/test_checkpoint.py": [
        "test_restore_activation_keeps_schema_11_compatible",
        "test_restore_activation_recovers_schema_12_admissions_before_removing_marker",
        "test_restore_activation_recovers_schema_19_admissions_and_advances_pressure_epoch",
    ],
    "test_v2_migrations.py": [
        "test_v1_upgrade_preserves_parent_child_rows_and_review_history",
        "test_failed_evidence_recovery_rolls_back_entire_migration",
    ],
    "test_phase5_migration.py": [
        "test_migration_from_36_closes_unit_findings_and_drops_history",
        "test_migration_without_open_unit_findings_keeps_the_revision",
        "test_schema_36_code_refuses_a_schema_37_file",
        "test_migration_from_36_deletes_unit_admissions_and_their_scope_rows",
        "test_the_migrating_connection_keeps_its_secure_delete_setting",
    ],
    "test_partitions_migration.py": [
        "test_migration_from_34_adds_tables_and_drops_retired_work",
        "test_a_checkpoint_at_schema_35_verifies_and_restores",
        "test_schema_34_code_refuses_a_schema_35_file",
    ],
    "test_uninterned_schema.py": [
        "test_plan_apply_and_reclaim_run_on_a_database_without_interning",
        "test_holds_and_doctor_run_on_a_database_without_interning",
        "test_a_checkpoint_of_a_database_without_interning_verifies_and_restores",
    ],
}


class HistoricalSQLite:
    def __init__(self, root: Path) -> None:
        if sqlite3.sqlite_version_info < (3, 43, 0):
            raise RuntimeError(
                "Historical SQLite assertions need SQLite >=3.43; use the pinned test image"
            )
        self.root = root
        fixture = Path(__file__).parent / "fixtures" / "legacy_sqlite37"
        manifest = json.loads((fixture / "manifest.json").read_text())
        assert manifest["format"] == "swingset-historical-sqlite-tests-v2"
        seen = set()
        for archive_name, checksum in manifest["archives"].items():
            assert Path(archive_name).name == archive_name
            archive = fixture / archive_name
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    assert name not in seen and name in manifest["files"]
                    seen.add(name)
                    relative = Path(name)
                    assert not relative.is_absolute() and ".." not in relative.parts
                    body = bundle.read(name)
                    assert hashlib.sha256(body).hexdigest() == manifest["files"][name], name
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
        assert seen == set(manifest["files"])
        self.runner = root / "runner"
        self.runner.mkdir()
        (self.runner / "legacy_sqlite_runner.py").write_bytes(
            Path(__file__).with_name("legacy_sqlite_runner.py").read_bytes()
        )
        self.completed = {}

    def assert_case(self, file: str, name: str) -> None:
        assert name.split("[", 1)[0] in CASES[file], "Unknown historical assertion"
        if file not in self.completed:
            retention = os.environ.get("SWINGSET_TEST_TMP_RETENTION")
            if retention not in (None, "failed"):
                raise ValueError("Unknown historical test temporary retention policy")
            # Keep the isolated archive free of ambient selectors and worker
            # flags. The disposable harness may request only this literal policy.
            retention_options = (
                ["-o", "tmp_path_retention_policy=failed"] if retention == "failed" else []
            )
            filekey = file.replace("/", "_")
            allocation = None
            basetemp = self.root.with_name(self.root.name + "-" + filekey + "-tmp")
            if retention == "failed":
                from swingset.dev.pytest_paths import allocate_persistent_directory

                allocation = allocate_persistent_directory()
                basetemp = allocation.payload
                (self.root / (filekey + ".temporary.json")).write_text(
                    json.dumps({"directory": allocation.path.name}) + "\n"
                )
            result = self.root / (filekey + ".json")
            environment = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT"}
            }
            environment.update(
                PYTHONPATH=os.pathsep.join(
                    map(str, (self.root / "src", self.root / "tests", self.runner))
                ),
                PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                PYTHONDONTWRITEBYTECODE="1",
                HF_HUB_OFFLINE="1",
                HF_DATASETS_OFFLINE="1",
                HF_HOME=str(self.root / "empty-hf-home"),
                SWINGSET_LEGACY_RESULT=str(result),
                TMPDIR="/tmp",
            )
            started = time.monotonic()
            selected = [str(self.root / "tests" / file) + "::" + case for case in CASES[file]]
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--noconftest",
                    "-c",
                    "/dev/null",
                    "-o",
                    "cache_dir=" + str(self.root / "pytest-cache"),
                    *retention_options,
                    "-p",
                    "legacy_sqlite_runner",
                    "--basetemp",
                    str(basetemp),
                    *selected,
                ],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            detail = process.stdout + "\n" + process.stderr
            (self.root / (filekey + ".log")).write_text(detail)
            assert result.is_file(), detail
            value = json.loads(result.read_text())
            value.update(seconds=time.monotonic() - started, log=detail)
            assert not value["collection_errors"], value
            self.completed[file] = value
            if (
                allocation is not None
                and process.returncode == 0
                and all(
                    item["outcome"] == "passed"
                    and all(phase in item["phases"] for phase in ("setup", "call", "teardown"))
                    for item in value["results"].values()
                )
            ):
                from swingset.dev.pytest_paths import release_persistent_directory

                release_persistent_directory(allocation)
        value = self.completed[file]
        matched = {
            key: outcome
            for key, outcome in value["results"].items()
            if key == name or key.startswith(name + "[")
        }
        assert matched, value["log"]
        assert all(
            item["outcome"] == "passed" and "call" in item["phases"] for item in matched.values()
        ), matched
