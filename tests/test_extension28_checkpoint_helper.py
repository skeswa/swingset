"""Exercise the held checkpoint CLI offline; only OS/source pins and review are doubled."""

import fcntl
import json
import socket
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from journal.tools.runtime import checkpoint_extension28_20260917 as helper
from swingset.backup import checkpoint as checkpoint_module
from swingset.history import review
from swingset.schedule import cycle
from swingset.state import db


@pytest.fixture
def environment(tmp_path, monkeypatch):
    state = tmp_path / "state"
    source = tmp_path / "source"
    source_file = source / "src/swingset/state/db.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# mocked frozen schema-28 source pin\n")
    inventory = {"files": {"src/swingset/state/db.py": helper.sha(source_file)}}
    (source / "extension-source.json").write_text(json.dumps(inventory))
    monkeypatch.setattr(db, "SCHEMA_VERSION", 28)
    with db.open_database(state) as database:
        database.connection.execute(
            "INSERT INTO meta(key,value) VALUES('input_bundle_hash',?)", (helper.BUNDLE,)
        )
        database.connection.execute("CREATE TABLE offline_paid_usage(requests INTEGER)")
        database.connection.execute("INSERT INTO offline_paid_usage VALUES(13)")
    (state / "inputs" / helper.BUNDLE).mkdir(parents=True)
    (state / "inputs" / helper.BUNDLE / "bundle.json").write_text("{}")
    candidate = state / "candidates/cand_8f31cad7226643ae"
    candidate.mkdir(parents=True)
    (candidate / "BUILT").write_text("{}")
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": helper.BASELINE}))
    (state / "baseline").symlink_to(candidate, target_is_directory=True)
    (state / "operator-hold").write_text("held for reviewed rollout\n")
    for name in ("phase1-catalog.json", "phase1-ledger.json"):
        (state / name).write_text("{}")
    (state / "checkpoints").mkdir()
    monkeypatch.setattr(helper, "STATE", state)
    monkeypatch.setattr(helper, "SOURCE", source)
    monkeypatch.setattr(helper, "SOURCE_RECEIPT", helper.sha(source / "extension-source.json"))
    monkeypatch.setattr(db, "__file__", str(source_file))
    monkeypatch.setattr(helper.inspect, "getfile", lambda _function: str(source_file))
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if str(path) in ("/run/current-system", "/nix/var/nix/profiles/system"):
            return Path(helper.SYSTEM)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(helper.subprocess, "check_output", lambda *args, **kwargs: "inactive\n")
    monkeypatch.setattr(cycle, "versions", lambda: {"offline": "schema28"})
    calls = []

    def review_pack(database, output, *, now, reconcile):
        assert reconcile is False
        assert database.schema_version == 28
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database.connection.execute("UPDATE offline_paid_usage SET requests=0")
        for name in ("state.lock", "control.lock"):
            with (state / name).open("a+b") as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir()
        calls.append("review")
        return {"offline": True, "reconcile": reconcile}

    monkeypatch.setattr(review, "review_pack", review_pack)

    def no_network(*args, **kwargs):
        pytest.fail("offline helper tests must not use network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    output = tmp_path / "review-output"
    checkpoint = state / "checkpoints/fresh28"

    def run(*arguments):
        monkeypatch.setattr(
            sys, "argv", ["checkpoint-extension28", "--output", str(output), *arguments]
        )
        helper.main()
        return json.loads((output / "receipt.json").read_text())

    return SimpleNamespace(
        state=state,
        source=source,
        output=output,
        checkpoint=checkpoint,
        run=run,
        calls=calls,
        original_review=review_pack,
    )


def test_default_is_read_only_review_with_both_locks(environment):
    env = environment
    before = helper.sha(env.state / "state.sqlite")
    receipt = env.run()
    assert receipt["passed"] is True
    assert receipt["live_database_changes"] == 0
    assert "checkpoint" not in receipt
    assert helper.sha(env.state / "state.sqlite") == before
    assert list((env.state / "checkpoints").iterdir()) == []
    assert env.calls == ["review"]


def test_checkpoint_captures_exact_28_paid_usage_bundle_baseline_and_hold(environment):
    env = environment
    before = helper.sha(env.state / "state.sqlite")
    receipt = env.run("--checkpoint", str(env.checkpoint))
    manifest = checkpoint_module.verify_checkpoint(env.checkpoint, maximum_schema_version=28)
    assert manifest["schema_version"] == 28
    assert manifest["input_bundle_hash"] == helper.BUNDLE
    assert manifest["baseline_candidate"] == "cand_8f31cad7226643ae"
    assert manifest["pending_candidate"] is None
    assert manifest["files"]["operator-hold"]["sha256"] == helper.sha(env.state / "operator-hold")
    with sqlite3.connect(env.checkpoint / "state.sqlite") as conn:
        assert conn.execute("SELECT requests FROM offline_paid_usage").fetchone()[0] == 13
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 28
    assert helper.sha(env.state / "state.sqlite") == before
    assert receipt["passed"] is True
    assert "private_archive_commit" not in receipt


@pytest.mark.parametrize(
    "failure", ["hold", "restore", "unit", "source", "extra_source", "baseline"]
)
def test_initial_guards_reject_before_review_or_checkpoint(environment, monkeypatch, failure):
    env = environment
    if failure == "hold":
        (env.state / "operator-hold").unlink()
    elif failure == "restore":
        (env.state / "RESTORE_PENDING").write_text("{}")
    elif failure == "unit":
        monkeypatch.setattr(helper.subprocess, "check_output", lambda *args, **kwargs: "active\n")
    elif failure == "source":
        (env.source / "src/swingset/state/db.py").write_text("changed")
    elif failure == "extra_source":
        (env.source / "unreviewed.py").write_text("pass")
    else:
        (env.state / "baseline/PUBLISHED").write_text('{"commit":"other"}')
    with pytest.raises(RuntimeError):
        env.run("--checkpoint", str(env.checkpoint))
    assert env.calls == []
    assert not env.checkpoint.exists()


@pytest.mark.parametrize("failure", ["schema", "bundle"])
def test_locked_database_guards_reject_before_review(environment, failure):
    env = environment
    with sqlite3.connect(env.state / "state.sqlite") as conn:
        if failure == "schema":
            conn.execute("PRAGMA user_version=29")
        else:
            conn.execute("UPDATE meta SET value='unreviewed' WHERE key='input_bundle_hash'")
    with pytest.raises(RuntimeError):
        env.run("--checkpoint", str(env.checkpoint))
    assert env.calls == []
    assert not env.checkpoint.exists()
    assert json.loads((env.output / "receipt.json").read_text())["passed"] is False


def test_hold_change_during_review_prevents_checkpoint_creation(environment, monkeypatch):
    env = environment

    def changed(*args, **kwargs):
        result = env.original_review(*args, **kwargs)
        (env.state / "operator-hold").unlink()
        return result

    monkeypatch.setattr(review, "review_pack", changed)
    with pytest.raises(RuntimeError, match="hold"):
        env.run("--checkpoint", str(env.checkpoint))
    assert not env.checkpoint.exists()


def test_resume_flag_is_rejected_before_any_review(environment):
    env = environment
    with pytest.raises(SystemExit) as error:
        env.run("--checkpoint", str(env.checkpoint), "--resume-checkpoint")
    assert error.value.code == 2
    assert env.calls == []
    assert not env.output.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "checkpoint_exists",
        "checkpoint_elsewhere",
        "output_in_source",
        "output_in_checkpoints",
        "upload_without_checkpoint",
        "mixed_runtime",
    ],
)
def test_cli_scope_and_runtime_guards(environment, monkeypatch, failure):
    env = environment
    arguments = ["--checkpoint", str(env.checkpoint)]
    if failure == "checkpoint_exists":
        env.checkpoint.mkdir()
    elif failure == "checkpoint_elsewhere":
        arguments = ["--checkpoint", str(env.output.parent / "elsewhere")]
    elif failure == "output_in_source":
        arguments += ["--output", str(env.source / "extra")]
    elif failure == "output_in_checkpoints":
        arguments += ["--output", str(env.state / "checkpoints/extra")]
    elif failure == "upload_without_checkpoint":
        arguments = ["--upload"]
    else:
        monkeypatch.setattr(db, "SCHEMA_VERSION", 29)
    with pytest.raises(RuntimeError):
        env.run(*arguments)
    assert env.calls == []


@pytest.fixture
def archive_double(environment, monkeypatch):
    from swingset.backup import archive as archive_module
    from swingset.backup import huggingface

    state = SimpleNamespace(uploads=[], head="a" * 40, manifest=None)

    class Archive:
        def __init__(self, repo_id, *, token):
            assert repo_id == "skeswa/swingset-archive"
            assert token == "offline-test-token"

        def head(self):
            return state.head

        def manifest_hash(self, commit):
            assert commit == "a" * 40
            return state.manifest

    def upload(checkpoint, archive):
        state.uploads.append(checkpoint)
        if state.manifest is None:
            state.manifest = checkpoint.manifest_hash
        return "a" * 40

    monkeypatch.setenv("HF_TOKEN", "offline-test-token")
    monkeypatch.setattr(huggingface, "HuggingFaceArchive", Archive)
    monkeypatch.setattr(archive_module, "upload_checkpoint", upload)
    return state


def test_upload_acknowledges_exact_created_manifest(environment, archive_double):
    env = environment
    receipt = env.run("--checkpoint", str(env.checkpoint), "--upload")
    assert receipt["passed"] is True
    assert receipt["private_archive_commit"] == "a" * 40
    assert len(archive_double.uploads) == 1
    assert receipt["checkpoint"]["manifest_sha256"] == archive_double.manifest


@pytest.mark.parametrize("failure", ["head", "manifest"])
def test_remote_mismatch_cannot_pass(environment, archive_double, failure):
    env = environment
    if failure == "head":
        archive_double.head = "b" * 40
    else:
        archive_double.manifest = "b" * 64
    with pytest.raises(RuntimeError):
        env.run("--checkpoint", str(env.checkpoint), "--upload")
    assert json.loads((env.output / "receipt.json").read_text())["passed"] is False


@pytest.mark.parametrize("failure", ["hold", "source"])
def test_change_during_checkpoint_prevents_upload(
    environment, monkeypatch, archive_double, failure
):
    env = environment
    original = checkpoint_module.create_checkpoint

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if failure == "hold":
            (env.state / "operator-hold").write_text("different hold bytes")
        else:
            (env.source / "src/swingset/state/db.py").write_text("different source")
        return result

    monkeypatch.setattr(checkpoint_module, "create_checkpoint", changed)
    with pytest.raises(RuntimeError):
        env.run("--checkpoint", str(env.checkpoint), "--upload")
    assert archive_double.uploads == []
    assert json.loads((env.output / "receipt.json").read_text())["passed"] is False


@pytest.mark.parametrize("path", ["/run/current-system", "/nix/var/nix/profiles/system"])
def test_both_active_and_persistent_system_pins_are_required(environment, monkeypatch, path):
    env = environment
    original = Path.resolve

    def changed(candidate, *args, **kwargs):
        return (
            Path("/nix/store/other-system")
            if str(candidate) == path
            else original(candidate, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "resolve", changed)
    with pytest.raises(RuntimeError, match="system pin"):
        env.run("--checkpoint", str(env.checkpoint))
    assert env.calls == []


def test_symlink_in_frozen_inventory_is_rejected(environment):
    env = environment
    (env.source / "unexpected-link").symlink_to(env.source / "src/swingset/state/db.py")
    with pytest.raises(RuntimeError, match="inventory"):
        env.run()
    assert env.calls == []


def test_source_import_outside_frozen_tree_is_rejected(environment, monkeypatch):
    env = environment
    monkeypatch.setattr(helper.inspect, "getfile", lambda _function: "/tmp/unreviewed.py")
    with pytest.raises(RuntimeError, match="outside frozen source"):
        env.run()
    assert env.calls == []
