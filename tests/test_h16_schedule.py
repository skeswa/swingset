"""A backed-up offline queue cannot consume every publication opportunity."""

import json

import httpx
import pytest
from test_h16_acceptance import release_state as release_fixture

from swingset.build import service
from swingset.schedule.cycle import run_cycle
from swingset.state.work import WorkUnit, enqueue, unfinished_units


@pytest.fixture
def release_state(tmp_path):
    yield from release_fixture.__wrapped__(tmp_path)


def test_retained_release_gets_opportunity_before_more_acquisition(release_state, monkeypatch):
    f = release_state
    pending = WorkUnit("parse", "snapshot", "unrelated-unavailable-body")
    enqueue(f.conn, (pending,), enqueued_at=f.clock.now().isoformat())
    monkeypatch.setattr(service, "correction_needed", lambda *_: False)
    requests = []

    def response(request):
        baseline = f.db.state_dir / "baseline"
        assert f.hub.calls >= 1
        assert (
            json.loads((baseline / "_meta/manifest.json").read_bytes())["release_policy"]["mode"]
            == "closure"
        )
        requests.append(str(request.url))
        return httpx.Response(404)

    summary = run_cycle(
        f.db,
        config_dir=f.bundle.config_dir,
        overrides_dir=f.bundle.overrides_dir,
        clock=f.clock,
        dry_run=False,
        hub=f.hub,
        budget=10,
        transport=httpx.MockTransport(response),
    )
    assert summary["candidate_id"] and f.hub.calls >= 1
    assert requests
    assert pending in set(unfinished_units(f.conn))
