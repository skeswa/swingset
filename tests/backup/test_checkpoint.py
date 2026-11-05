from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.backup.checkpoint import (
    CheckpointError,
    activate_restored_state,
    create_checkpoint,
    restore_checkpoint,
    restore_from_checkpoint,
    verify_checkpoint,
    verify_restored_public,
)
from swingset.build.files import canonical_json
from swingset.clock import FakeClock
from swingset.publish.service import RemoteCommit
from swingset.state import engine
from swingset.state.db import SCHEMA_VERSION
from swingset.state.environment import FixtureIO

BUNDLE_HASH = hashlib.sha256(b"{}").hexdigest()


def test_checkpoint_version_uses_native_identity_not_imported_source_row(tmp_path):
    from swingset.backup.checkpoint import _check_database_schema
    from swingset.state.db import open_database

    with open_database(tmp_path / "native") as database:
        assert (
            database.connection.execute(
                "SELECT value FROM ops.meta WHERE key='schema_version'"
            ).fetchone()[0]
            == "37"
        )
        _check_database_schema(database.connection, SCHEMA_VERSION)
        with pytest.raises(CheckpointError, match="declared checkpoint schema"):
            _check_database_schema(database.connection, SCHEMA_VERSION - 1)


def test_staged_restore_audits_bytes_but_waits_for_policy_before_public_io(tmp_path, monkeypatch):
    from swingset.backup import checkpoint as module
    from swingset.state.db import open_database

    source = tmp_path / "source"
    connection = state(source)
    saved = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()

    def premature(*args, **kwargs):
        raise AssertionError("staged restore must wait for the fresh UUID's policy")

    monkeypatch.setattr(module, "verify_restored_public", premature)
    monkeypatch.setattr(module, "activate_restored_state", premature)
    target = tmp_path / "restored"
    restore_from_checkpoint(
        saved.path,
        target,
        object(),
        FakeClock(datetime(2026, 11, 5, tzinfo=UTC)),
        lock_timeout=0,
        defer_activation=True,
    )
    assert (target / "RESTORE_PENDING").is_file()
    assert (target / "tmp/restore-history/history.json").is_file()
    with pytest.raises(RuntimeError, match="restore verification is pending"):
        open_database(target)


def state(root: Path) -> engine.Connection:
    from swingset.state.db import open_database

    database = open_database(root, lock=False)
    connection = database.connection
    connection.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES('fixture-run','2026-01-01T00:00:00+00:00',1)"
    )
    connection.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES('fixture-watch','eepro','round','GET','https://example.test/round','eepro.round','live')"
    )
    candidate = root / "candidates" / "cand_base"
    (candidate / "_meta").mkdir(parents=True)
    manifest = canonical_json(
        {"candidate_id": "cand_base", "input_bundle_hash": BUNDLE_HASH, "files": {}}
    )
    (candidate / "_meta/manifest.json").write_bytes(manifest)
    (candidate / "BUILT").write_bytes(
        canonical_json(
            {"manifest_hash": hashlib.sha256(manifest).hexdigest(), "expected_parent": None}
        )
    )
    (candidate / "PUBLISHED").write_text('{"commit":"abc"}')
    (root / "baseline").symlink_to(Path("candidates/cand_base"))
    from swingset.fetch.archive import Archive

    body_hash = Archive(root).store_body(b"evidence")
    connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES('fixture-snapshot','fixture-watch','GET','https://example.test/round','2026-01-01T00:00:00+00:00',200,%s,8,1,'fixture-run','Ok')",
        (body_hash,),
    )
    connection.execute(
        "INSERT INTO meta(key,value) VALUES('input_bundle_hash',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (BUNDLE_HASH,),
    )
    bundle = root / "inputs" / BUNDLE_HASH
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}")
    (root / "venv").mkdir()
    (root / "venv" / "secret").write_text("excluded")
    return connection


def test_checkpoint_uses_postgres_copy_and_restores_pending(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={"parser": "1"},
        input_bundle_hash=None,
    )
    connection.close()
    assert "venv/secret" not in checkpoint.files
    assert f"database/files/inputs/{BUNDLE_HASH}/manifest.json" in checkpoint.files
    assert (
        verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION)[
            "baseline_candidate"
        ]
        == "cand_base"
    )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    assert (restored / "RESTORE_PENDING").exists()
    assert (restored / "baseline").resolve().name == "cand_base"
    from swingset.state.db import open_database

    with open_database(restored, allow_restore_pending=True) as copy:
        assert (
            copy.connection.execute(
                "SELECT watch_id FROM snapshots WHERE snapshot_id='fixture-snapshot'"
            ).fetchone()[0]
            == "fixture-watch"
        )
        assert (
            copy.connection.execute(
                "SELECT run_id FROM runs WHERE run_id='fixture-run'"
            ).fetchone()[0]
            == "fixture-run"
        )
    assert not (checkpoint.path / "state.sqlite").exists()
    assert (checkpoint.path / "database/history.json").is_file()


def test_checkpoint_excludes_service_home_tool_caches(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    cache = source / ".cache" / "huggingface"
    cache.mkdir(parents=True)
    (cache / ".agent_harnesses.json").write_text('{"tool":"metadata"}')
    (cache / "token").write_text("credential")

    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()

    assert not any(name == ".cache" or name.startswith(".cache/") for name in checkpoint.files)
    assert f"database/files/inputs/{BUNDLE_HASH}/manifest.json" in checkpoint.files
    verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION)


def test_nested_checkpoint_manifest_is_retained_verified_and_restored(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    relative = Path("history/restore-specimen/checkpoint/checkpoint.json")
    nested = source / relative
    nested.parent.mkdir(parents=True)
    body = b'{"format": "retained specimen evidence"}'
    nested.write_bytes(body)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    assert (
        "database/files/" + relative.as_posix()
        in verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION)["files"]
    )
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    assert (restored / relative).read_bytes() == body
    assert not (restored / "checkpoint.json").exists()
    (checkpoint.path / "database/files" / relative).write_bytes(b"changed nested evidence")
    with pytest.raises(CheckpointError, match="file failed verification"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION)


def test_changed_checkpoint_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    connection.close()
    blob = next(
        path for path in (checkpoint.path / "database/files/blobs").rglob("*") if path.is_file()
    )
    blob.write_bytes(b"corrupt")
    with pytest.raises(CheckpointError, match="failed verification"):
        verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION)


@pytest.mark.parametrize("quick", [True, False])
def test_a_copy_hashed_after_it_went_bad_fails_the_database_check(
    tmp_path: Path, quick: bool
) -> None:
    """The file hashes only prove the copy is what was hashed; the database
    check must catch a copy that was already bad (D-0203)."""
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    connection.close()
    history_path = checkpoint.path / "database/history.json"
    history = json.loads(history_path.read_text())
    entry = history["tables"]["snapshots"]
    damaged = checkpoint.path / "database" / entry["data"]
    import pyarrow as pa

    with pa.output_stream(str(damaged), compression="zstd") as stream:
        stream.write(b"invalid binary COPY signature")
    entry["data_sha256"] = hashlib.sha256(damaged.read_bytes()).hexdigest()
    history_path.write_bytes(canonical_json(history))
    manifest_path = checkpoint.path / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["history_manifest_sha256"] = hashlib.sha256(history_path.read_bytes()).hexdigest()
    for path in (damaged, history_path):
        name = path.relative_to(checkpoint.path).as_posix()
        manifest["files"][name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
    manifest_path.write_bytes(canonical_json(manifest))
    with pytest.raises((CheckpointError, ValueError)) as caught:
        verify_checkpoint(checkpoint.path, maximum_schema_version=SCHEMA_VERSION, quick=quick)
    assert "file failed verification" not in str(caught.value)


def test_missing_referenced_extract_blocks_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    connection.execute(
        "UPDATE snapshots SET extract_sha256=%s WHERE snapshot_id='fixture-snapshot'", ("a" * 64,)
    )
    connection.commit()
    with pytest.raises(
        (CheckpointError, ValueError), match="referenced artifact is missing|missing captured file"
    ):
        create_checkpoint(
            source,
            connection,
            tmp_path / "checkpoint",
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
    connection.close()


class StaticHub(FixtureIO):
    def __init__(
        self, head: str, commits: dict[str, RemoteCommit] | None = None, files=None
    ) -> None:
        self.value = head
        self.commits = commits or {}
        self.files = files or {}
        self.downloads = []

    def download(self, name: str, commit: str) -> Path:
        self.downloads.append((name, commit))
        return self.files[(commit, name)]

    def head(self) -> str:
        return self.value

    def inspect(self, commit: str) -> RemoteCommit:
        return self.commits[commit]

    def is_initial_head(self, commit: str) -> bool:
        return False

    def create_commit(self, **_kwargs: object) -> str:
        raise AssertionError("restore never publishes")


def test_restore_public_verification_keeps_activation_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    verify_restored_public(restored, StaticHub("abc"))
    assert (restored / "RESTORE_PENDING").exists()
    activate_restored_state(restored)
    assert not (restored / "RESTORE_PENDING").exists()


def test_remote_ahead_keeps_restore_disabled(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    with pytest.raises(CheckpointError, match="differs"):
        verify_restored_public(restored, StaticHub("newer"))
    assert (restored / "RESTORE_PENDING").exists()


def test_restore_ignores_historical_published_intent_from_old_checkpoint(
    tmp_path: Path,
) -> None:
    restored = tmp_path / "restored"
    connection = state(restored)
    connection.close()
    historical = restored / "candidates" / "cand_historical"
    historical.mkdir()
    (historical / "BUILT").write_text('{"expected_parent":"initial"}')
    (historical / "PUBLISHED").write_text('{"commit":"old"}')
    (historical / "PUBLISHING").write_text("{}")
    (restored / "RESTORE_PENDING").write_text("verification pending\n")

    verify_restored_public(restored, StaticHub("abc"))

    assert (restored / "baseline").resolve().name == "cand_base"
    assert (historical / "PUBLISHING").exists()


def test_restore_recovers_landed_pending_candidate_without_publish(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    pending = source / "candidates" / "cand_pending"
    (pending / "_meta").mkdir(parents=True)
    (pending / "data").mkdir()
    (pending / "data" / "x").write_bytes(b"new")
    data_hash = hashlib.sha256(b"new").hexdigest()
    manifest = {
        "candidate_id": pending.name,
        "expected_parent": "abc",
        "input_bundle_hash": BUNDLE_HASH,
        "files": {"data/x": data_hash},
    }
    manifest_bytes = canonical_json(manifest)
    (pending / "_meta" / "manifest.json").write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    (pending / "BUILT").write_bytes(
        canonical_json({"manifest_hash": manifest_hash, "expected_parent": "abc"})
    )
    (pending / "PUBLISHING").write_text("{}")
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    remote = RemoteCommit(
        "landed",
        "abc",
        pending.name,
        manifest_hash,
        {"data/x": data_hash, "_meta/manifest.json": manifest_hash},
    )

    class PromotedHub(StaticHub):
        def download(self, name, commit):
            # The cache fetch must observe B already promoted, never baseline A.
            assert (restored / "baseline").resolve().name == pending.name
            assert commit == "landed"
            return super().download(name, commit)

    hub = PromotedHub("landed", {"landed": remote}, {("landed", "data/x"): pending / "data/x"})
    verify_restored_public(restored, hub)
    assert hub.downloads == [("data/x", "landed")]
    restored_pending = restored / "candidates" / pending.name
    assert (restored_pending / "data/x").read_bytes() == b"new"
    assert json.loads((restored_pending / "PUBLISHED").read_text())["commit"] == "landed"
    assert (restored / "baseline").resolve() == restored_pending.resolve()
    assert (restored / "RESTORE_PENDING").exists()


def test_restore_from_checkpoint_holds_protocol_through_activation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_from_checkpoint(
        checkpoint.path, restored, StaticHub("abc"), FakeClock(), lock_timeout=1
    )
    assert not (restored / "RESTORE_PENDING").exists()
    assert (restored / "baseline").resolve().name == "cand_base"


def test_restore_activation_keeps_schema_11_compatible(legacy_sqlite):
    legacy_sqlite.assert_case(
        "backup/test_checkpoint.py", "test_restore_activation_keeps_schema_11_compatible"
    )


def test_restore_activation_recovers_schema_12_admissions_before_removing_marker(legacy_sqlite):
    legacy_sqlite.assert_case(
        "backup/test_checkpoint.py",
        "test_restore_activation_recovers_schema_12_admissions_before_removing_marker",
    )


def test_restore_activation_recovers_schema_19_admissions_and_advances_pressure_epoch(
    legacy_sqlite,
):
    legacy_sqlite.assert_case(
        "backup/test_checkpoint.py",
        "test_restore_activation_recovers_schema_19_admissions_and_advances_pressure_epoch",
    )


def test_restore_lock_timeout_makes_no_staging_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    from swingset.dev.testing import provision_empty
    from swingset.state.engine import connect
    from swingset.state.rwlock import LockBusy, exclusive

    target = tmp_path / "target"
    owner = provision_empty(target)
    with connect(owner) as guard:
        held = exclusive(target, connection=guard)
        try:
            with pytest.raises(LockBusy, match="already in use"):
                restore_from_checkpoint(
                    checkpoint.path,
                    target,
                    StaticHub("abc"),
                    FakeClock(),
                    lock_timeout=0.2,
                    owner_dsn=owner,
                )
            assert not (target / "RESTORE_PENDING").exists()
        finally:
            held.release()


def test_restore_refuses_valid_unmarked_state_and_resumes_marked_staging(tmp_path: Path) -> None:
    source = tmp_path / "source"
    connection = state(source)
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "unmarked-state"
    sentinel.write_bytes(b"valid state sentinel")
    with pytest.raises(CheckpointError, match="active or interrupted"):
        restore_from_checkpoint(
            checkpoint.path, target, StaticHub("abc"), FakeClock(), lock_timeout=1
        )
    assert sentinel.read_bytes() == b"valid state sentinel"
    sentinel.unlink()
    (target / "RESTORE_PENDING").write_text("pending")
    (target / "partial").write_text("discard me")
    with pytest.raises(CheckpointError, match="active or interrupted"):
        restore_from_checkpoint(
            checkpoint.path, target, StaticHub("abc"), FakeClock(), lock_timeout=1
        )
    assert (target / "partial").read_text() == "discard me"
    assert (target / "RESTORE_PENDING").exists()
    fresh = tmp_path / "fresh-retry"
    restore_from_checkpoint(checkpoint.path, fresh, StaticHub("abc"), FakeClock(), lock_timeout=1)
    assert not (fresh / "partial").exists()
    assert not (fresh / "RESTORE_PENDING").exists()


@pytest.mark.parametrize("blocked", [False, True])
def test_checkpoint_retains_generation_extract_not_on_snapshot_and_restores_it(tmp_path, blocked):
    from dataclasses import replace

    from test_admission import Corpus

    from swingset.admission.report import Guard
    from swingset.fetch.archive import Archive
    from swingset.state.db import open_database

    source = tmp_path / "source"
    with open_database(source) as db:
        c = Corpus(db)
        context = c.snapshot("staged-only")
        generation, _ = c.stage(
            context,
            report_change=lambda report: (
                replace(
                    report,
                    guards=(
                        *report.guards,
                        Guard("review_required", False, "Synthetic blocked source evidence"),
                    ),
                )
                if blocked
                else report
            ),
        )
        row = db.connection.execute(
            "SELECT manifest_json, state FROM source_generations WHERE generation_id = %s",
            (generation,),
        ).fetchone()
        manifest = json.loads(row["manifest_json"])
        extract_sha = manifest[0]["extract_sha256"]
        assert (
            db.connection.execute(
                "SELECT extract_sha256 FROM snapshots WHERE snapshot_id = %s",
                (context.snapshot_id,),
            ).fetchone()[0]
            is None
        )
        assert (
            db.connection.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0]
            is None
        )
        value = c.archive.read_extract(extract_sha)
        saved = create_checkpoint(
            source,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=db.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        schema = db.schema_version
    assert f"database/files/extracts/{extract_sha}" in saved.files
    before = {
        path.relative_to(saved.path).as_posix(): path.read_bytes()
        for path in saved.path.rglob("*")
        if path.is_file()
    }
    verify_checkpoint(saved.path, maximum_schema_version=schema)
    restored = tmp_path / "restored"
    restore_checkpoint(saved.path, restored, maximum_schema_version=schema)
    assert Archive(restored).read_extract(extract_sha) == value
    assert {
        path.relative_to(saved.path).as_posix(): path.read_bytes()
        for path in saved.path.rglob("*")
        if path.is_file()
    } == before
    assert not (saved.path / "state.sqlite-wal").exists()
    assert not (saved.path / "state.sqlite-shm").exists()


def test_missing_generation_only_extract_blocks_checkpoint_instead_of_losing_evidence(tmp_path):
    from test_admission import Corpus

    from swingset.state.db import open_database

    with open_database(tmp_path / "source") as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("only-in-generation"))
        manifest = json.loads(
            db.connection.execute(
                "SELECT manifest_json FROM source_generations WHERE generation_id = %s",
                (generation,),
            ).fetchone()[0]
        )
        c.archive.extract_path(manifest[0]["extract_sha256"]).unlink()
        with pytest.raises(
            (CheckpointError, ValueError),
            match="referenced artifact is missing|missing captured file",
        ):
            create_checkpoint(
                db.state_dir,
                db.connection,
                tmp_path / "incomplete",
                schema_version=db.schema_version,
                versions={},
                input_bundle_hash=None,
            )
        assert not (tmp_path / "incomplete").exists()


def test_new_closure_validation_rejects_backup_that_omitted_generation_extract(tmp_path):
    from test_admission import Corpus

    from swingset.state.db import open_database

    with open_database(tmp_path / "source") as db:
        c = Corpus(db)
        generation, _ = c.stage(c.snapshot("staged"))
        item = json.loads(
            db.connection.execute(
                "SELECT manifest_json FROM source_generations WHERE generation_id = %s",
                (generation,),
            ).fetchone()[0]
        )[0]
        saved = create_checkpoint(
            db.state_dir,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=db.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        schema = db.schema_version
    relative = "extracts/" + item["extract_sha256"]
    member = "database/files/" + relative
    (saved.path / member).unlink()
    history_path = saved.path / "database/history.json"
    history = json.loads(history_path.read_bytes())
    del history["files"][relative]
    history_path.write_bytes(canonical_json(history))
    manifest = json.loads((saved.path / "checkpoint.json").read_bytes())
    del manifest["files"][member]
    digest = hashlib.sha256(history_path.read_bytes()).hexdigest()
    manifest["history_manifest_sha256"] = digest
    manifest["files"]["database/history.json"] = {
        "sha256": digest,
        "size": history_path.stat().st_size,
    }
    (saved.path / "checkpoint.json").write_bytes(canonical_json(manifest))
    with pytest.raises(
        (CheckpointError, ValueError),
        match="referenced artifact is missing|missing captured file|missing.*reference|requires a missing backup file",
    ):
        verify_checkpoint(saved.path, maximum_schema_version=schema)


def test_a_hold_added_during_a_checkpoint_waits_for_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The closure and the copy run under the control lock, so no hold can slip in.

    `add_hold` checks that a held file is there and writes the hold under the
    control lock. The checkpoint used to compute its file closure and copy under
    the writer lock alone, so a hold could be committed for a file between the
    two. Files a hold names are filtered by `is_file()`, so the checkpoint would
    have carried the hold and not the file, and nothing would have said so.
    """
    import threading
    from datetime import UTC, datetime

    from swingset.backup import snapshot as snapshot_module
    from swingset.state.control_lock import ControlTimeout
    from swingset.state.retention import add_hold, holds

    now = datetime(2026, 9, 18, tzinfo=UTC)
    source = tmp_path / "source"
    connection = state(source)
    body = hashlib.sha256(b"evidence").hexdigest()
    original = snapshot_module.shutil.copyfile
    closure_done = threading.Event()
    attempted = threading.Event()
    outcome: dict[str, str] = {}

    def paused_closure(*args: object, **kwargs: object) -> object:
        if not closure_done.is_set():
            closure_done.set()
            attempted.wait(20)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    def add() -> None:
        try:
            closure_done.wait(20)
            from swingset.state.engine import connect

            other = connect(connection.info.dsn, password=connection.info.password)
            try:
                add_hold(
                    source,
                    other,
                    who="operator",
                    why="checking the retained page",
                    now=now,
                    artifacts=(body,),
                    timeout=0.5,
                )
            finally:
                other.close()
            outcome["result"] = "written"
        except ControlTimeout:
            outcome["result"] = "waited"
        except BaseException as error:  # pragma: no cover - reported by the assert
            outcome["result"] = repr(error)
        finally:
            attempted.set()

    monkeypatch.setattr(snapshot_module.shutil, "copyfile", paused_closure)
    waiting = threading.Thread(target=add)
    waiting.start()
    try:
        checkpoint = create_checkpoint(
            source,
            connection,
            tmp_path / "checkpoint",
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
    finally:
        waiting.join(30)
    monkeypatch.undo()
    assert outcome == {"result": "waited"}
    assert holds(source) == []
    # Nothing dangling: the checkpoint has no hold, and the file the hold would
    # have named is in it anyway because a snapshot declares it.
    assert not [name for name in checkpoint.files if name.startswith("database/files/holds/")]
    relative = f"blobs/sha256/{body[:2]}/{body[2:4]}/{body}"
    assert "database/files/" + relative in checkpoint.files

    # After the copy the same hold is accepted, and the next checkpoint carries
    # the hold and the file together.
    add_hold(
        source,
        connection,
        who="operator",
        why="checking the retained page",
        now=now,
        artifacts=(body,),
    )
    after = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint-2",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    connection.close()
    assert [name for name in after.files if name.startswith("database/files/holds/")]
    assert "database/files/" + relative in after.files
    verify_checkpoint(after.path, maximum_schema_version=SCHEMA_VERSION)


def test_restore_downloads_only_reconciled_baseline_and_verifies_sealed_hashes(tmp_path):
    source = tmp_path / "source"
    connection = state(source)
    baseline = (source / "baseline").resolve()
    payload = tmp_path / "published.parquet"
    payload.write_bytes(b"exact published parquet bytes")
    manifest = json.loads((baseline / "_meta/manifest.json").read_text())
    manifest["files"] = {"data/current.parquet": hashlib.sha256(payload.read_bytes()).hexdigest()}
    body = canonical_json(manifest)
    (baseline / "_meta/manifest.json").write_bytes(body)
    built = json.loads((baseline / "BUILT").read_text())
    built["manifest_hash"] = hashlib.sha256(body).hexdigest()
    (baseline / "BUILT").write_bytes(canonical_json(built))
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    hub = StaticHub("abc", files={("abc", "data/current.parquet"): payload})
    verify_restored_public(restored, hub)
    assert hub.downloads == [("data/current.parquet", "abc")]
    assert (
        (restored / "baseline").resolve() / "data/current.parquet"
    ).read_bytes() == payload.read_bytes()
    assert (restored / "RESTORE_PENDING").is_file()


def test_changed_public_baseline_download_keeps_restore_barrier(tmp_path):
    source = tmp_path / "source"
    connection = state(source)
    baseline = (source / "baseline").resolve()
    manifest = json.loads((baseline / "_meta/manifest.json").read_text())
    manifest["files"] = {"data/current.parquet": hashlib.sha256(b"original").hexdigest()}
    body = canonical_json(manifest)
    (baseline / "_meta/manifest.json").write_bytes(body)
    built = json.loads((baseline / "BUILT").read_text())
    built["manifest_hash"] = hashlib.sha256(body).hexdigest()
    (baseline / "BUILT").write_bytes(canonical_json(built))
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    corrupt = tmp_path / "corrupt.parquet"
    corrupt.write_bytes(b"changed")
    with pytest.raises(CheckpointError, match="differs from its manifest"):
        verify_restored_public(
            restored, StaticHub("abc", files={("abc", "data/current.parquet"): corrupt})
        )
    assert (restored / "RESTORE_PENDING").is_file()
    assert not ((restored / "baseline").resolve() / "data/current.parquet").exists()


def test_unlanded_restored_intent_is_abandoned_before_rebuild(tmp_path):
    source = tmp_path / "source"
    connection = state(source)
    pending = source / "candidates/cand_unlanded"
    (pending / "_meta").mkdir(parents=True)
    manifest = canonical_json(
        {
            "candidate_id": pending.name,
            "expected_parent": "abc",
            "input_bundle_hash": BUNDLE_HASH,
            "files": {},
        }
    )
    (pending / "_meta/manifest.json").write_bytes(manifest)
    (pending / "BUILT").write_bytes(
        canonical_json(
            {"expected_parent": "abc", "manifest_hash": hashlib.sha256(manifest).hexdigest()}
        )
    )
    (pending / "PUBLISHING").write_text("{}")
    checkpoint = create_checkpoint(
        source,
        connection,
        tmp_path / "checkpoint",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=BUNDLE_HASH,
    )
    connection.close()
    restored = tmp_path / "restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    verify_restored_public(restored, StaticHub("abc"))
    candidate = restored / "candidates/cand_unlanded"
    assert not (candidate / "PUBLISHING").exists()
    assert (candidate / "REJECTED").is_file()
    assert (candidate / "BUILT").is_file()
    assert (restored / "baseline").resolve().name == "cand_base"
    assert (restored / "RESTORE_PENDING").is_file()
