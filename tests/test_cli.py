import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_postgres_schema import fresh_database as fresh_database

import swingset.cli as cli
from swingset.backup.checkpoint import create_checkpoint
from swingset.cli import main
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.client import FetchResult
from swingset.publish.service import RemoteCommit
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.engine.locks import acquire_writer
from swingset.state.environment import FixtureIO


class EmptyHub(FixtureIO):
    def head(self) -> None:
        return None

    def is_initial_head(self, commit: str) -> bool:
        return False

    def inspect(self, commit: str) -> RemoteCommit:
        raise AssertionError(commit)

    def create_commit(self, **_kwargs: object) -> str:
        raise AssertionError("restore must not publish")


def test_import_supervisor_dispatch_does_not_open_a_database_target(tmp_path, monkeypatch):
    from swingset.deploy import import_job

    seen = []

    def execute(args):
        seen.append((args.plan, args.plan_sha256, args.container, args.platform))
        return 7

    monkeypatch.setattr(import_job, "execute", execute)
    monkeypatch.setattr(
        cli, "_operator_intent", lambda args: pytest.fail("supervisor opened database target")
    )
    policy = tmp_path / "policy.json"
    assert (
        main(
            [
                "import-job",
                "--plan",
                str(policy),
                "--plan-sha256",
                "a" * 64,
                "--container",
                "b" * 64,
                "--platform",
                "c" * 64,
            ]
        )
        == 7
    )
    assert seen == [(policy, "a" * 64, "b" * 64, "c" * 64)]


@pytest.mark.parametrize("command", ["unpack-job", "reference-build"])
def test_offline_job_commands_dispatch_without_a_database_target(tmp_path, monkeypatch, command):
    from swingset.deploy import reference, unpack

    policy = tmp_path / "policy.json"
    review = tmp_path / "review.json"
    arguments = ["--policy", str(policy)]
    if command == "reference-build":
        arguments += ["--review", str(review)]
    received = []

    def execute(argv):
        received.append(argv)
        return 7

    monkeypatch.setattr(reference if command == "reference-build" else unpack, "main", execute)
    monkeypatch.setattr(
        cli, "_operator_intent", lambda args: pytest.fail("offline job opened a database target")
    )
    assert main([command, *arguments]) == 7
    assert received == [arguments]


@pytest.mark.parametrize("keep_failed", [False, True])
@pytest.mark.parametrize("supplement", [False, True])
def test_sqlite_unpack_passes_explicit_failure_preservation(
    tmp_path, monkeypatch, keep_failed, supplement
):
    from swingset.state import legacy_restore

    received = []

    def unpack(source, target, **kwargs):
        received.append((target, kwargs))
        return {"complete": True}

    monkeypatch.setattr(legacy_restore, "unpack", unpack)
    target = tmp_path / "target"
    arguments = [
        "unpack-sqlite37",
        "--from",
        str(tmp_path / "transport"),
        "--set-id",
        "set_20261105T040000Z",
        "--manifest-sha256",
        "a" * 64,
        str(target),
    ]
    if keep_failed:
        arguments.append("--keep-failed")
    if supplement:
        arguments.extend(["--supplemental-inputs", str(tmp_path / "supplements")])
    assert main(arguments) == 0
    assert received == [
        (
            target,
            {
                "set_id": "set_20261105T040000Z",
                "expected_manifest_sha256": "a" * 64,
                "keep_failed": keep_failed,
                "supplemental_inputs": tmp_path / "supplements" if supplement else None,
            },
        )
    ]


def test_platform_bootstrap_dispatches_only_its_explicit_administrator_inputs(
    tmp_path, monkeypatch
):
    from swingset.deploy import bootstrap

    arguments = [
        "--mode",
        "roles",
        "--admin-dsn-file",
        str(tmp_path / "admin.dsn"),
        "--database",
        "postgres",
    ]
    received = []

    def execute(argv):
        received.append(argv)
        return 7

    monkeypatch.setattr(bootstrap, "main", execute)
    monkeypatch.setattr(
        cli,
        "_operator_intent",
        lambda args: pytest.fail("platform job opened an application target"),
    )
    assert main(["platform-bootstrap", *arguments]) == 7
    assert received == [arguments]


def checkpoint(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    with open_database(source) as database:
        result = create_checkpoint(
            source,
            database.connection,
            tmp_path / "checkpoint",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
    return result.path


def test_pause_timeout_applies_no_change(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        acquire_writer(db.connection)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "swingset.cli",
                "pause",
                "--all",
                "--state",
                str(tmp_path),
                "--lock-timeout",
                "0.1",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "no change was persisted" in result.stderr
        assert db.connection.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0] == 0


def test_pause_waits_and_commits_before_success(tmp_path):
    db = open_database(tmp_path)
    db.connection.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
    acquire_writer(db.connection)
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swingset.cli",
            "pause",
            "--all",
            "--state",
            str(tmp_path),
            "--lock-timeout",
            "5",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.15)
    assert child.poll() is None
    db.close()
    _, error = child.communicate(timeout=10)
    assert child.returncode == 0, error
    with open_database(tmp_path) as restored:
        assert (
            restored.connection.execute("SELECT scope_kind FROM operator_pauses").fetchone()[0]
            == "all"
        )


def test_doctor_does_not_wait_for_writer(tmp_path, capsys):
    with open_database(tmp_path):
        assert main(["doctor", "--state", str(tmp_path)]) == 0
    assert f'"schema_version": {SCHEMA_VERSION}' in capsys.readouterr().out


def test_doctor_and_summary_walk_the_retention_plan_only_when_asked(tmp_path, capsys, monkeypatch):
    """The walk needed 13 GB on the 35 GB copy, past the 8 GB worker, and the
    daily summary ran it. Both report the file's bytes unless `--retention`."""
    import swingset.state.retention as retention

    with open_database(tmp_path):
        pass
    walks = []
    real = retention.report
    monkeypatch.setattr(retention, "report", lambda *a, **k: walks.append(1) or real(*a, **k))
    for command in ("doctor", "summary"):
        assert main([command, "--state", str(tmp_path), "--json"]) == 0
        document = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]
        assert document["retention"]["usage"]["file_bytes"] > 0
        assert "walk" in document["retention"] and "plan_digest" not in document["retention"]
    assert walks == []
    assert main(["doctor", "--state", str(tmp_path), "--json", "--retention"]) == 0
    document = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]
    assert walks == [1] and "plan_digest" in document["retention"]


def test_doctor_reports_hold_age_fields_and_overdue(tmp_path, capsys):
    import json
    import os
    import time

    with open_database(tmp_path):
        pass
    marker = tmp_path / "operator-hold"
    marker.write_text("who: sandile\nwhy: checking the 2019 rounds\nreview-by: 2026-09-22\n")
    three_days_ago = time.time() - 3 * 24 * 3600
    os.utime(marker, (three_days_ago, three_days_ago))
    assert main(["doctor", "--state", str(tmp_path)]) == 0
    first_document = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]
    hold = first_document["operator_hold"]
    assert hold["present"] and hold["overdue"] and hold["age_hours"] >= 71
    assert hold["who"] == "sandile" and hold["review-by"] == "2026-09-22"
    marker.unlink()
    assert main(["doctor", "--state", str(tmp_path)]) == 0
    first_document = json.JSONDecoder().raw_decode(capsys.readouterr().out)[0]
    assert first_document["operator_hold"] == {"present": False}


def test_accept_year_refuses_without_event_evidence_and_records_when_it_can(tmp_path, capsys):
    with open_database(tmp_path):
        pass
    assert main(["accept-year", "--year", "2012", "--by", "claude", "--state", str(tmp_path)]) == 1
    assert "cannot accept a year without event evidence" in capsys.readouterr().err
    assert main(["accept-year", "--year", "2012", "--by", " ", "--state", str(tmp_path)]) == 1
    with open_database(tmp_path) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM history_acceptance").fetchone()[0] == 0


def test_restore_pending_blocks_mutations_but_allows_diagnosis(tmp_path):
    from postgres_statements import contents

    with open_database(tmp_path):
        pass
    (tmp_path / "RESTORE_PENDING").write_text("verify first")
    with open_database(tmp_path, allow_restore_pending=True) as db:
        before = contents(db.connection)
    assert main(["parse", "--state", str(tmp_path)]) == 1
    with open_database(tmp_path, allow_restore_pending=True) as db:
        assert contents(db.connection) == before
    assert (
        main(
            [
                "pause",
                "--all",
                "--actor",
                "restore-test",
                "--reason",
                "verify first",
                "--state",
                str(tmp_path),
            ]
        )
        == 0
    )
    with open_database(tmp_path, allow_restore_pending=True) as db:
        assert tuple(
            db.connection.execute("SELECT action,actor,reason FROM control_events").fetchone()
        ) == ("pause", "restore-test", "verify first")
    assert (tmp_path / "RESTORE_PENDING").is_file()
    assert main(["doctor", "--state", str(tmp_path)]) == 0


def test_restore_checkpoint_uses_remote_verification_and_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = checkpoint(tmp_path)
    target = tmp_path / "restored"
    monkeypatch.setattr(cli, "hub", lambda: EmptyHub())
    assert (
        main(["restore", "--checkpoint", str(saved), "--writer-stopped", "--state", str(target)])
        == 0
    )
    assert (target / "database.json").is_file()
    with open_database(target, read_only=True) as restored:
        assert restored.schema_version == SCHEMA_VERSION
    assert not (target / "RESTORE_PENDING").exists()


def test_restore_archive_commit_downloads_selected_commit_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = checkpoint(tmp_path)
    target = tmp_path / "restored"
    selected: list[str] = []

    def download(_self: object, commit: str, destination: Path) -> None:
        selected.append(commit)
        shutil.copytree(saved, destination, dirs_exist_ok=True)

    monkeypatch.setattr(cli, "hub", lambda: EmptyHub())
    monkeypatch.setattr("swingset.backup.huggingface.HuggingFaceArchive.download", download)
    monkeypatch.setenv("HF_TOKEN", "test-token")
    assert (
        main(
            [
                "restore",
                "--archive-commit",
                "archive-sha",
                "--writer-stopped",
                "--state",
                str(target),
            ]
        )
        == 0
    )
    assert selected == ["archive-sha"]
    assert (target / "database.json").is_file()
    with open_database(target, read_only=True) as restored:
        assert restored.schema_version == SCHEMA_VERSION


def test_registry_crosscheck_blob_replay_finishes_run_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[str] = []

    def replay(_database: object, digest: str, _archive: object, _now: object, _run_id: str) -> int:
        called.append(digest)
        return 3

    monkeypatch.setattr("swingset.schedule.registry.replay_crosscheck", replay)
    assert (
        main(
            [
                "registry-crosscheck",
                "--blob",
                "a" * 64,
                "--state",
                str(tmp_path),
                "--config",
                "config",
                "--overrides",
                "overrides",
            ]
        )
        == 0
    )
    assert called == ["a" * 64]
    assert capsys.readouterr().out.strip() == "3"
    with open_database(tmp_path) as database:
        row = database.connection.execute(
            "SELECT finished_at, summary_json FROM runs ORDER BY started_at DESC NULLS LAST LIMIT 1"
        ).fetchone()
        assert row[0] is not None
        summary = json.loads(row[1])
        assert summary["command"] == "registry-crosscheck"
        assert not summary["failed"]


def test_registry_crosscheck_archive_only_uses_dump_without_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dump = tmp_path / "data.json"
    dump.write_text("{}")
    called: list[Path] = []

    def archive_only(
        _database: object,
        path: Path,
        _archive: object,
        _now: object,
        _run_id: str,
    ) -> str:
        called.append(path)
        return "digest"

    monkeypatch.setattr("swingset.schedule.registry.archive_crosscheck_dump", archive_only)
    assert (
        main(
            [
                "registry-crosscheck",
                str(dump),
                "--archive-only",
                "--state",
                str(tmp_path / "state"),
                "--config",
                "config",
                "--overrides",
                "overrides",
            ]
        )
        == 0
    )
    assert called == [dump]
    assert capsys.readouterr().out.strip() == "digest"


@pytest.mark.parametrize(
    "outcome", [Outcome.BLOCKED, Outcome.INVALID, Outcome.THROTTLED, Outcome.SERVER_ERROR]
)
def test_fetch_one_failure_classification_exits_nonzero_and_finishes_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: Outcome
) -> None:
    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def fetch(self, *_args: object, **_kwargs: object) -> FetchResult:
            return FetchResult(Classification(outcome))

        def close(self) -> None:
            pass

    monkeypatch.setattr("swingset.fetch.client.FetchClient", FakeClient)
    assert (
        main(
            [
                "fetch-one",
                "https://worldsdc.com/events/",
                "--kind",
                "wsdc_calendar.events",
                "--state",
                str(tmp_path),
                "--config",
                "config",
                "--overrides",
                "overrides",
            ]
        )
        == 1
    )
    with open_database(tmp_path) as database:
        row = database.connection.execute(
            "SELECT finished_at, summary_json FROM runs ORDER BY started_at DESC NULLS LAST LIMIT 1"
        ).fetchone()
        assert row[0] is not None
        summary = json.loads(row[1])
        assert summary["failed"]
        assert outcome.value in summary["error"]


def test_migrate_opens_once_and_reports_both_schemas(tmp_path, capsys, monkeypatch, fresh_database):
    """The phase 3 switch migrates by hand under the hold (docs/guides/deployment.md)."""
    service, name, _owner = fresh_database
    monkeypatch.setenv("SWINGSET_OWNER_DATABASE_URL", service.dsn(name))
    assert main(["migrate", "--state", str(tmp_path)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["schema_before"] == 0 and first["schema_after"] == SCHEMA_VERSION
    assert main(["migrate", "--state", str(tmp_path)]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["schema_before"] == again["schema_after"] == SCHEMA_VERSION
    assert again["seconds"] >= 0


def test_graph_verify_and_rekey_commands_write_receipts(tmp_path, capsys):
    from swingset.state.db import open_database

    state = tmp_path / "state"
    proof = tmp_path / "proof.json"
    with open_database(state):
        pass
    shared = ["--state", str(state), "--config", "config", "--overrides", "overrides"]
    assert (
        main(["verify-derivations", *shared, "--checkpoint", str(state), "--receipt", str(proof)])
        == 0
    )
    capsys.readouterr()
    receipt = json.loads(proof.read_text())
    assert receipt["complete"] and receipt["clean"]
    assert main(["rekey", *shared, "--receipt", str(proof)]) == 0
    capsys.readouterr()
    result = json.loads((state / "rekey" / "receipt.json").read_text())
    assert result["complete"] and result["equivalences"] == {}


def test_remote_fresh_import_requires_prod_before_touching_source_or_destination(tmp_path, capsys):
    target = tmp_path / "not-created"
    assert (
        main(
            [
                "import-sqlite37",
                str(tmp_path / "absent.sqlite"),
                "--state",
                str(target),
                "--environment",
                "dryrun",
            ]
        )
        == 1
    )
    assert "--prod" in capsys.readouterr().err
    assert not target.exists()


def test_remote_control_requires_prod_and_prints_only_safe_identity(
    tmp_path, fresh_database, capsys
):
    from swingset.state import engine, postgres_schema

    service, name, owner = fresh_database
    postgres_schema.migrate(owner, environment="dryrun")
    engine.write_settings(
        tmp_path,
        service.dsn(name, role="worker"),
        environment="dryrun",
        control_dsn=service.dsn(name, role="control"),
    )
    assert main(["pause", "--all", "--state", str(tmp_path)]) == 1
    assert owner.execute("SELECT count(*) FROM ops.control_events").fetchone()[0] == 0
    assert main(["pause", "--all", "--state", str(tmp_path), "--prod"]) == 0
    output = capsys.readouterr()
    assert "operator_target" in output.err and name in output.err
    assert "postgresql://" not in output.err and "password=" not in output.err
    assert (
        owner.execute("SELECT environment FROM ops.environment_identity").fetchone()[0] == "dryrun"
    )


def test_prod_operator_guard_allows_explicit_forward_upgrade_without_current_schema_open(
    tmp_path, fresh_database, monkeypatch, capsys
):
    from swingset.state import engine, postgres_schema

    service, name, owner = fresh_database
    with monkeypatch.context() as older:
        older.setattr(postgres_schema, "SCHEMA_VERSION", 37)
        postgres_schema.migrate(owner, environment="production")
    original = owner.execute("SELECT database_id FROM ops.environment_identity").fetchone()[0]
    engine.write_settings(tmp_path, service.dsn(name, role="worker"), environment="production")
    monkeypatch.setenv("SWINGSET_OWNER_DATABASE_URL", service.dsn(name))
    assert main(["migrate", "--state", str(tmp_path)]) == 1
    assert main(["migrate", "--state", str(tmp_path), "--prod"]) == 0
    assert (
        owner.execute("SELECT database_id FROM ops.environment_identity").fetchone()[0] == original
    )
    assert postgres_schema.validate_schema(owner)["schema_version"] == 38
    assert "operator_target" in capsys.readouterr().err
