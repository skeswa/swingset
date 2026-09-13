"""Quiet publication cadence is independent of successful verification polling."""

import json
from datetime import timedelta

import pytest
from build.test_generations import empty_release as release_fixture
from publish.test_recovery import FakeHub

from swingset.build import service
from swingset.publish.service import publish


@pytest.fixture
def empty_release(tmp_path):
    yield from release_fixture.__wrapped__(tmp_path)


def test_same_health_bucket_reuses_exact_candidate_then_daily_cutoff_advances(empty_release):
    db, bundle, clock, run = empty_release
    first = service.build_release(db, bundle, clock, run)
    original = (first.path / "_meta/manifest.json").read_bytes()
    clock.sleep(60)
    second = service.build_release(db, bundle, clock, run)
    assert second.reused and second.path == first.path
    assert (second.path / "_meta/manifest.json").read_bytes() == original
    clock.sleep(timedelta(days=1).total_seconds())
    third = service.build_release(db, bundle, clock, run)
    assert not third.reused and third.path != first.path
    assert (
        json.loads((third.path / "_meta/manifest.json").read_bytes())["release_policy"]["closure"][
            "cutoff"
        ]
        != json.loads(original)["release_policy"]["closure"]["cutoff"]
    )


def test_acknowledged_release_is_quiet_without_a_second_remote_commit(empty_release):
    db, bundle, clock, run = empty_release
    hub = FakeHub()
    first = service.build_release(db, bundle, clock, run, hub)
    assert publish(db.state_dir, first.path, hub).state == "published"
    receipt = (first.path / "PUBLISHED").read_bytes()
    clock.sleep(60)
    again = service.build_release(db, bundle, clock, run, hub)
    assert again.reused and again.path == first.path
    assert publish(db.state_dir, again.path, hub).state == "unchanged"
    assert hub.calls == 1 and (first.path / "PUBLISHED").read_bytes() == receipt


def test_interrupted_build_cannot_publish_until_its_durable_completion(empty_release, monkeypatch):
    from swingset.publish.service import PublishError
    from swingset.state import derivations

    db, bundle, clock, run = empty_release
    hub = FakeHub(None)
    complete = derivations.complete

    def interrupted(*args, **kwargs):
        complete(*args, **kwargs)
        raise KeyboardInterrupt("before output transaction commit")

    monkeypatch.setattr(derivations, "complete", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.build_release(db, bundle, clock, run, hub)
    path = next((db.state_dir / "candidates").glob("*/BUILT")).parent
    with pytest.raises(PublishError, match="durable build completion"):
        publish(db.state_dir, path, hub)
    assert hub.calls == 0 and not (path / "REJECTED").exists()
    monkeypatch.setattr(derivations, "complete", complete)
    rebuilt = service.build_release(db, bundle, clock, run, hub)
    assert rebuilt.path == path and rebuilt.reused
    assert publish(db.state_dir, path, hub).state == "published"
    assert hub.calls == 1
