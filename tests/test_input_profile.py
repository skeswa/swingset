"""Acceptance profiling observes a worker transaction on a disposable copy."""

import json
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.state import engine, input_profile, inputs, postgres_schema, work
from swingset.state.db import open_database
from swingset.state.write_deadline import WriteDeadlineExceeded


@pytest.mark.parametrize(
    "source_version,outcome",
    [(None, "success"), (None, "deadline"), (None, "error"), (38, "success")],
)
def test_profile_rolls_back_as_worker_collects_functions_and_cleans_only_copy(
    tmp_path, monkeypatch, source_version, outcome
):
    from swingset.dev.services import ensure_service

    registry = tmp_path / "registry"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    # A normal temporary checkpoint can live below the scratch parent.
    scratch_parent = Path("/tmp") if outcome == "success" else scratch
    existing_scratch = set(scratch_parent.glob("swingset-input-profile-*"))
    monkeypatch.setenv("TMPDIR", str(scratch_parent))
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    bundle = inputs.capture(Path("config"), overrides, tmp_path / "capture", {})
    receipt = tmp_path / "profile.json"
    original_accept = inputs.accept
    original_execute = engine.Connection.execute
    observed = []
    private_marker = "private-profiling-parameter-must-not-be-recorded"

    with monkeypatch.context() as legacy:
        if source_version is not None:
            legacy.setattr(postgres_schema, "SCHEMA_VERSION", source_version)
        source = open_database(tmp_path / "source", lock=False)
    with source:
        source_name = source.connection.info.dbname
        source_worker = source.connection.info.user
        work.enqueue(
            source.connection,
            [work.WorkUnit("parse", "snapshot", "original")],
            enqueued_at="2026-11-10T00:00:00Z",
        )
        before = input_profile._fingerprint(source)

        def acceptance(database, bundle, clock):
            assert database.connection.info.dbname != source_name
            assert source.connection.execute.__func__ is original_execute
            assert tuple(
                database.connection.execute("SELECT current_user,session_user").fetchone()
            ) == (source_worker, source_worker)
            database.connection.execute(
                "INSERT INTO ops.meta(key,value) VALUES('profile-private',%s)", (private_marker,)
            )
            observed.append(database.connection.info.dbname)
            result = original_accept(database, bundle, clock)
            if outcome == "deadline":
                raise WriteDeadlineExceeded("injected atomic deadline")
            if outcome == "error":
                raise RuntimeError("injected unexpected failure")
            return result

        monkeypatch.setattr(inputs, "accept", acceptance)
        if outcome == "error":
            with pytest.raises(RuntimeError, match="unexpected failure"):
                input_profile.run(source.state_dir, bundle, FakeClock(), receipt_path=receipt)
        else:
            result = input_profile.run(source.state_dir, bundle, FakeClock(), receipt_path=receipt)
            assert result["diagnostic_complete"] and result["owned_database_cleanup_complete"]
            assert result["graph_proof"] is False
            assert result["trial"]["acceptance_complete"] == (outcome == "success")
            assert result["trial"]["write_budget_seconds"] == 75
            assert result["trial"]["rollback_fingerprint_equal"]
            assert result["functions"]
            assert all(row["calls"] > 0 for row in result["functions"])
            assert any(
                row["function"] == "tr_history_timing_accepted_inputs_insert"
                for row in result["functions"]
            )
        assert observed and len(observed) == 1
        assert input_profile._fingerprint(source) == before
        assert engine.Connection.execute is original_execute
    assert not list(registry.iterdir())
    assert not list(scratch.iterdir())
    assert set(scratch_parent.glob("swingset-input-profile-*")) == existing_scratch
    with ensure_service().connect() as admin:
        assert (
            admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (observed[0],)).fetchone()
            is None
        )
        assert admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (source_name,)
        ).fetchone()
    written = receipt.read_text()
    assert private_marker not in written
    assert "password" not in written
    assert json.loads(written)["diagnostic_complete"] == (outcome != "error")


def test_profile_refuses_nonempty_registry_before_opening_a_source(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    registry.mkdir(mode=0o700)
    claim = registry / "previous.json"
    claim.write_text("previous ownership must survive")
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    monkeypatch.setattr(
        input_profile,
        "open_database",
        lambda *args, **kwargs: pytest.fail("opened a database with an old ownership registry"),
    )
    with pytest.raises(ValueError, match="existing ownership"):
        input_profile.run(
            tmp_path / "source", None, FakeClock(), receipt_path=tmp_path / "receipt.json"
        )
    assert claim.read_text() == "previous ownership must survive"
    assert not (tmp_path / "receipt.json").exists()


def test_statement_measurement_bound_still_allows_real_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(input_profile, "_MAX_STATEMENTS", 4)
    completed = []
    with open_database(tmp_path / "state", lock=False) as database:
        before = input_profile._fingerprint(database)

        def writes():
            for number in range(20):
                work.enqueue(
                    database.connection,
                    [work.WorkUnit("parse", "snapshot", str(number))],
                    enqueued_at="2026-11-10T00:00:00Z",
                )
                completed.append(number)

        with pytest.raises(RuntimeError, match="statement bound"):
            input_profile._trial(database, writes)
        assert completed
        assert not database.connection.in_transaction
        assert input_profile._fingerprint(database) == before


def test_profile_refuses_a_production_checkpoint_before_allocating_copy(tmp_path, monkeypatch):
    from swingset.dev.services import ensure_service

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(tmp_path / "registry"))
    with open_database(tmp_path / "source", lock=False) as source:
        settings = engine.read_settings(source.state_dir)
        with ensure_service().connect(source.connection.info.dbname) as admin:
            admin.execute("UPDATE ops.environment_identity SET environment='production'")
        engine.write_settings(
            source.state_dir,
            settings["dsn"],
            environment="production",
            control_dsn=settings["control_dsn"],
        )
        monkeypatch.setattr(
            input_profile,
            "Copies",
            lambda *args: pytest.fail("allocated a copy before checking dev identity"),
        )
        with pytest.raises(ValueError, match="private dev"):
            input_profile.run(
                source.state_dir, None, FakeClock(), receipt_path=tmp_path / "receipt.json"
            )
    assert not (tmp_path / "registry").exists()
    assert not list(scratch.iterdir())


@pytest.mark.parametrize("capture_name", ["source", "source/capture", "."])
def test_cli_refuses_overlapping_capture_before_reading_inputs(tmp_path, monkeypatch, capture_name):
    from swingset import cli

    monkeypatch.setattr(
        inputs, "capture", lambda *args: pytest.fail("captured inside retained checkpoint")
    )
    args = cli.parser().parse_args(
        [
            "profile-input-acceptance",
            "--checkpoint",
            str(tmp_path / "source"),
            "--state",
            str(tmp_path / capture_name),
            "--receipt",
            str(tmp_path / "receipt.json"),
        ]
    )
    with pytest.raises(ValueError, match="separate writable capture"):
        cli._profile_input_acceptance(args)


@pytest.mark.parametrize("suffix", [".db", ".sqlite"])
@pytest.mark.parametrize(
    ("entrypoint", "destination"),
    [
        ("cli", "capture"),
        ("cli", "receipt"),
        ("cli", "registry"),
        ("cli", "scratch"),
        ("api", "receipt"),
        ("api", "registry"),
        ("api", "scratch"),
    ],
)
def test_legacy_checkpoint_alias_cannot_receive_any_profiling_write(
    tmp_path, monkeypatch, suffix, entrypoint, destination
):
    from swingset import cli

    retained = tmp_path / "retained"
    checkpoint = retained / ("state" + suffix)
    capture = tmp_path / "capture"
    receipt = tmp_path / "receipt.json"
    registry = tmp_path / "registry"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    if destination == "capture":
        capture = retained / "capture"
    elif destination == "receipt":
        receipt = retained / "profile.json"
    elif destination == "registry":
        registry = retained / "registry"
    else:
        scratch = retained / "scratch"
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(
        inputs, "capture", lambda *args: pytest.fail("captured inside the actual checkpoint")
    )
    monkeypatch.setattr(
        input_profile, "open_database", lambda *args, **kwargs: pytest.fail("opened a database")
    )
    with pytest.raises(ValueError):
        if entrypoint == "api":
            input_profile.run(checkpoint, None, FakeClock(), receipt_path=receipt)
        else:
            cli._profile_input_acceptance(
                cli.parser().parse_args(
                    [
                        "profile-input-acceptance",
                        "--checkpoint",
                        str(checkpoint),
                        "--state",
                        str(capture),
                        "--receipt",
                        str(receipt),
                    ]
                )
            )
    assert not retained.exists()
    assert not capture.exists()
    assert not receipt.exists()
    assert not registry.exists()


@pytest.mark.parametrize(
    ("entrypoint", "destination"),
    [
        ("cli", "capture_child"),
        ("cli", "capture_parent"),
        ("cli", "receipt_child"),
        ("cli", "receipt_equal"),
        ("api", "receipt_child"),
        ("api", "receipt_equal"),
    ],
)
def test_registry_is_separate_from_capture_and_receipt_before_any_write(
    tmp_path, monkeypatch, entrypoint, destination
):
    from swingset import cli

    capture, registry, receipt = (
        tmp_path / name for name in ("capture", "registry", "receipt.json")
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    if destination == "capture_child":
        capture = registry / "capture"
    elif destination == "capture_parent":
        registry = capture / "registry"
    elif destination == "receipt_child":
        receipt = registry / "profile.json"
    else:
        receipt = registry
    monkeypatch.setenv("SWINGSET_COPY_ROOT", str(registry))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(inputs, "capture", lambda *args: pytest.fail("captured into the registry"))
    monkeypatch.setattr(
        input_profile, "open_database", lambda *args, **kwargs: pytest.fail("opened a database")
    )
    with pytest.raises(ValueError, match="registry must be separate"):
        if entrypoint == "api":
            input_profile.run(tmp_path / "source", None, FakeClock(), receipt_path=receipt)
        else:
            cli._profile_input_acceptance(
                cli.parser().parse_args(
                    [
                        "profile-input-acceptance",
                        "--checkpoint",
                        str(tmp_path / "source"),
                        "--state",
                        str(capture),
                        "--receipt",
                        str(receipt),
                    ]
                )
            )
    assert not capture.exists() and not registry.exists() and not receipt.exists()
