import json
import shutil
import threading
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.fetch.archive import digest
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture


def _capture_background(state):
    result = {}
    finished = threading.Event()

    def run():
        try:
            result["bundle"] = capture(Path("config"), Path("overrides"), state, {})
        except BaseException as error:
            result["error"] = error
        finally:
            finished.set()

    worker = threading.Thread(target=run)
    worker.start()
    return worker, finished, result


def test_capture_waits_for_every_durable_member_before_publishing_manifest(tmp_path, monkeypatch):
    from swingset.state import inputs

    runtime = {"runtime/blocked": b"blocked"} | {
        f"runtime/member-{number}": b"member" for number in range(8)
    }
    monkeypatch.setattr(inputs, "capture_runtime", lambda: runtime)
    blocked, release, progressed = (threading.Event() for _ in range(3))
    lock = threading.Lock()
    active = peak = 0
    real_write = inputs.durable_write

    def write(path, body):
        nonlocal active, peak
        manifest = path.name == "manifest.json" and path.parent.parent.name == "inputs"
        if manifest:
            assert release.is_set()
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if path.name == "blocked":
                blocked.set()
                assert release.wait(10)
            real_write(path, body)
            if not manifest and path.name != "blocked" and blocked.is_set():
                progressed.set()
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(inputs, "durable_write", write)
    state = tmp_path / "state"
    worker, finished, result = _capture_background(state)
    try:
        assert blocked.wait(10)
        assert progressed.wait(10), "other members must progress while one writer is blocked"
        assert not finished.is_set()
        assert not list((state / "inputs").glob("*/manifest.json"))
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert "error" not in result, result.get("error")
    assert 2 <= peak <= 8
    bundle = result["bundle"]
    assert json.loads((bundle.path / "manifest.json").read_bytes()) == bundle.file_hashes
    assert all((bundle.path / name).read_bytes() == body for name, body in bundle.files.items())


def test_capture_member_failure_drains_writers_and_leaves_no_manifest(tmp_path, monkeypatch):
    from swingset.state import inputs

    monkeypatch.setattr(
        inputs,
        "capture_runtime",
        lambda: {"runtime/failure": b"failure", "runtime/blocked": b"blocked"},
    )
    blocked, release, failed = (threading.Event() for _ in range(3))
    real_write = inputs.durable_write

    def write(path, body):
        if path.name == "failure":
            failed.set()
            raise OSError("injected member write failure")
        if path.name == "blocked":
            blocked.set()
            assert release.wait(10)
        real_write(path, body)

    monkeypatch.setattr(inputs, "durable_write", write)
    state = tmp_path / "state"
    worker, finished, result = _capture_background(state)
    try:
        assert blocked.wait(10)
        assert failed.wait(10)
        assert not finished.wait(0.02), "a failed capture must drain its other writers"
        assert not list((state / "inputs").glob("*/manifest.json"))
    finally:
        release.set()
        worker.join(timeout=10)
    assert not worker.is_alive()
    assert isinstance(result.get("error"), OSError)
    assert not list((state / "inputs").glob("*/manifest.json"))
    monkeypatch.setattr(inputs, "durable_write", real_write)
    retried = capture(Path("config"), Path("overrides"), state, {})
    assert json.loads((retried.path / "manifest.json").read_bytes()) == retried.file_hashes
    assert all((retried.path / name).read_bytes() == body for name, body in retried.files.items())


def test_capture_validates_existing_members_before_starting_any_new_write(tmp_path, monkeypatch):
    from swingset.state import inputs

    monkeypatch.setattr(inputs, "capture_runtime", lambda: {"runtime/member": b"member"})
    state = tmp_path / "state"
    original = capture(Path("config"), Path("overrides"), state, {})
    manifest = (original.path / "manifest.json").read_bytes()
    # The missing first member must remain missing when a later member is corrupt.
    missing = original.path / "config" / "hosts.toml"
    missing.unlink()
    (original.path / "policy" / "retention.json").write_bytes(b"corrupt retained member")
    monkeypatch.setattr(
        inputs, "durable_write", lambda *_args: pytest.fail("wrote before validating all members")
    )
    with pytest.raises(ValueError, match="corrupt captured input"):
        capture(Path("config"), Path("overrides"), state, {})
    assert not missing.exists()
    assert (original.path / "manifest.json").read_bytes() == manifest


def test_removed_override_invalidates_links_once_and_survives_restart(tmp_path):
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock()
    with open_database(state) as db:
        db.connection.execute(
            (
                "INSERT INTO events (event_id, series_id, name, year, start_date, end_date, wsdc_status, "
                "sources, source, snapshot_id, parser_version, first_seen_at, last_seen_at, run_id) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            (
                "2026-09-example",
                "slug-example",
                "Example",
                2026,
                "2026-09-01",
                "2026-09-02",
                "registry",
                "[]",
                "wsdc_calendar",
                "snapshot",
                "1",
                clock.now().isoformat(),
                clock.now().isoformat(),
                "run",
            ),
        )
        accept(db, capture(Path("config"), overrides, state, {}), clock)
        db.connection.execute("DELETE FROM pending_work")
        (overrides / "nicknames.csv").unlink()
        assert "overrides/nicknames.csv" in accept(
            db, capture(Path("config"), overrides, state, {}), clock
        )
    with open_database(state) as db:
        assert tuple(
            db.connection.execute(
                "SELECT stage, unit_kind, unit_id FROM pending_work WHERE stage = 'link'"
            ).fetchone()
        ) == ("link", "event", "2026-09-example")
        assert db.connection.execute(
            "SELECT digest FROM accepted_inputs WHERE input_name = 'overrides/nicknames.csv'"
        ).fetchone()[0] == digest(b"")
        db.connection.execute("DELETE FROM pending_work")
        assert not accept(db, capture(Path("config"), overrides, state, {}), clock)
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def test_invalid_override_is_rejected_before_acceptance(tmp_path):
    import pytest

    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock()
    with open_database(state) as db:
        original = capture(Path("config"), overrides, state, {})
        accept(db, original, clock)
        db.connection.execute("DELETE FROM pending_work")
        (overrides / "identity_overrides.csv").write_text(
            "entry_id,wsdc_id,reason,author,date\nentry,not-a-number,correction,owner,2026-09-09\n"
        )
        with pytest.raises(ValueError, match="convert legacy overrides first"):
            capture(Path("config"), overrides, state, {})
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key = 'input_bundle_hash'"
            ).fetchone()[0]
            == original.digest
        )
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0


def test_load_retains_original_bundle_identity_without_recapturing_runtime(tmp_path, monkeypatch):
    from swingset.state import inputs

    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    original = inputs.capture(Path("config"), overrides, state, {})

    def must_not_capture():
        raise AssertionError("retained bundle loading must not capture current code")

    monkeypatch.setattr(inputs, "capture_runtime", must_not_capture)
    (overrides / "nicknames.csv").write_text("unaccepted replacement")
    restored = inputs.load(state, original.digest)
    assert restored.digest == original.digest
    assert dict(restored.files) == dict(original.files)
    assert restored.config == original.config


def test_load_rejects_corrupt_retained_member_before_using_configuration(tmp_path):
    import pytest

    from swingset.state.inputs import load

    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    original = capture(Path("config"), overrides, state, {})
    with (original.path / "config" / "hosts.toml").open("ab") as output:
        output.write(b"\n# corrupt retained input\n")
    with pytest.raises(ValueError, match="member digest"):
        load(state, original.digest)
