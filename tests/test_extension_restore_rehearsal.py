"""Use the actual restore protocol; the public transport alone is mocked."""

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint
from swingset.clock import FakeClock
from swingset.publish.service import RemoteCommit
from swingset.state.db import SCHEMA_VERSION, open_database

PATH = Path(__file__).parents[1] / "journal/tools/runtime/rehearse_extension_restore.py"
SPEC = importlib.util.spec_from_file_location("restore_rehearsal", PATH)
assert SPEC is not None and SPEC.loader is not None
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


@pytest.fixture
def checkpoint(tmp_path):
    state = tmp_path / "original"
    with open_database(state) as db:
        (state / "operator-hold").write_text("rehearsal hold\n")
        candidate = state / "candidates" / HELPER.CANDIDATE
        (candidate / "_meta").mkdir(parents=True)
        content = json.dumps(
            {"candidate_id": HELPER.CANDIDATE, "expected_parent": None, "files": {}}
        ).encode()
        fingerprint = hashlib.sha256(content).hexdigest()
        (candidate / "_meta/manifest.json").write_bytes(content)
        (candidate / "BUILT").write_text(
            json.dumps({"manifest_hash": fingerprint, "expected_parent": None})
        )
        (candidate / "PUBLISHED").write_text(json.dumps({"commit": HELPER.BASELINE}))
        (state / "baseline").symlink_to(Path("candidates") / HELPER.CANDIDATE)
        target = tmp_path / "checkpoint"
        create_checkpoint(
            state,
            db.connection,
            target,
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
    return target, verify_checkpoint(target, maximum_schema_version=SCHEMA_VERSION), fingerprint


class Hub:
    def __init__(self, fingerprint, heads):
        self.fingerprint = fingerprint
        self.heads = iter(heads)
        self.inspections = 0

    def head(self):
        return next(self.heads)

    def inspect(self, commit):
        self.inspections += 1
        return RemoteCommit(commit, None, HELPER.CANDIDATE, self.fingerprint, {})


def test_actual_restore_verifies_remote_twice_and_preserves_hold(checkpoint, tmp_path):
    path, manifest, fingerprint = checkpoint
    state = tmp_path / "restored"
    hub = Hub(fingerprint, [HELPER.BASELINE, HELPER.BASELINE])
    checked = HELPER.exercise_restore(
        path, state, manifest, hub, FakeClock(), maximum_schema=SCHEMA_VERSION
    )
    assert checked.heads == [HELPER.BASELINE, HELPER.BASELINE]
    assert hub.inspections == 1
    assert not (state / "RESTORE_PENDING").exists()
    assert (state / "operator-hold").read_bytes() == (path / "operator-hold").read_bytes()
    assert json.loads((state / "baseline/PUBLISHED").read_bytes())["commit"] == HELPER.BASELINE
    with pytest.raises(ValueError, match="remote writes"):
        checked.create_commit()
    with pytest.raises(ValueError, match="target must be new"):
        HELPER.exercise_restore(
            path, state, manifest, hub, FakeClock(), maximum_schema=SCHEMA_VERSION
        )


@pytest.mark.parametrize("heads", [["remote-ahead"], [HELPER.BASELINE, "remote-ahead"]])
def test_remote_mismatch_keeps_restore_barrier_and_operator_hold(checkpoint, tmp_path, heads):
    path, manifest, fingerprint = checkpoint
    state = tmp_path / "restored"
    with pytest.raises(ValueError, match="public baseline differs"):
        HELPER.exercise_restore(
            path,
            state,
            manifest,
            Hub(fingerprint, heads),
            FakeClock(),
            maximum_schema=SCHEMA_VERSION,
        )
    assert (state / "RESTORE_PENDING").is_file()
    assert (state / "operator-hold").read_bytes() == (path / "operator-hold").read_bytes()
    with pytest.raises(RuntimeError, match="restore verification is pending"):
        open_database(state)


def test_remote_manifest_mismatch_cannot_activate(checkpoint, tmp_path):
    from swingset.backup.checkpoint import CheckpointError

    path, manifest, _ = checkpoint
    state = tmp_path / "restored"
    with pytest.raises(CheckpointError, match="public commit does not match"):
        HELPER.exercise_restore(
            path,
            state,
            manifest,
            Hub("0" * 64, [HELPER.BASELINE]),
            FakeClock(),
            maximum_schema=SCHEMA_VERSION,
        )
    assert (state / "RESTORE_PENDING").is_file()


def test_serial_transport_spaces_from_body_completion_despite_dispatch_jitter():
    now = [0.0]
    starts = []
    delays = iter([11.0, 0.0, 3.0])

    def sleep(seconds):
        now[0] += seconds

    def dispatch(request):
        sleep(next(delays))
        starts.append(now[0])
        assert request.headers["User-Agent"] == "project-agent"
        return httpx.Response(200, content=b"body")

    transport = HELPER.SerialReadTransport(
        httpx.MockTransport(dispatch), "project-agent", monotonic=lambda: now[0], sleep=sleep
    )
    with httpx.Client(transport=transport) as client:
        with client.stream("GET", "https://example.test/first") as response:
            assert transport.lock.locked()
            sleep(7.0)  # Hold a streamed body beyond response headers.
            assert response.read() == b"body"
        assert not transport.lock.locked()
        client.get("https://example.test/second")
        client.get("https://example.test/third")
        assert starts == [11.0, 23.0, 31.0]
        with pytest.raises(ValueError, match="remote writes"):
            client.post("https://example.test/forbidden")
    assert len(transport.requests) == 3


def test_serial_transport_exception_retains_a_completion_spacing_boundary():
    now = [0.0]
    starts = []

    def sleep(seconds):
        now[0] += seconds

    def dispatch(request):
        starts.append(now[0])
        if len(starts) == 1:
            sleep(9.0)
            raise httpx.ConnectError("mock transport failure")
        return httpx.Response(200, content=b"ok")

    transport = HELPER.SerialReadTransport(
        httpx.MockTransport(dispatch), "project-agent", monotonic=lambda: now[0], sleep=sleep
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("https://example.test/first")
        assert not transport.lock.locked()
        assert client.get("https://example.test/second").content == b"ok"
    assert starts == [0.0, 14.0]
