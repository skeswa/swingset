from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from swingset.build.files import canonical_json
from swingset.publish.service import PublishError, RemoteCommit, expected_parent, publish, reconcile


class FakeHub:
    def __init__(self, head: str | None = "base", *, lose_response: bool = False) -> None:
        self.current = head
        self.commits: dict[str, RemoteCommit] = {}
        self.calls = 0
        self.lose_response = lose_response

    def head(self) -> str | None:
        return self.current

    def inspect(self, commit: str) -> RemoteCommit:
        return self.commits[commit]

    def is_initial_head(self, commit: str) -> bool:
        return commit == "base" and commit not in self.commits

    def create_commit(
        self,
        *,
        parent: str | None,
        message: str,
        additions: dict[str, Path],
        deletions: tuple[str, ...],
    ) -> str:
        assert parent == self.current
        self.calls += 1
        sha = f"commit-{self.calls}"
        candidate = additions["_meta/manifest.json"].parents[1]
        built = json.loads((candidate / "BUILT").read_text())
        files = {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in additions.items()
        }
        self.commits[sha] = RemoteCommit(sha, parent, candidate.name, built["manifest_hash"], files)
        self.current = sha
        if self.lose_response:
            self.lose_response = False
            raise ConnectionError("response lost")
        return sha


def candidate(state: Path, name: str = "cand_a") -> Path:
    path = state / "candidates" / name
    (path / "_meta").mkdir(parents=True)
    (path / "data").mkdir()
    (path / "data" / "x").write_bytes(b"rows")
    manifest = {"files": {"data/x": hashlib.sha256(b"rows").hexdigest()}}
    manifest_bytes = canonical_json(manifest)
    (path / "_meta" / "manifest.json").write_bytes(manifest_bytes)
    built = {
        "changed": True,
        "baseline_commit": None,
        "expected_parent": "base",
        "manifest_hash": hashlib.sha256(manifest_bytes).hexdigest(),
        "content_hash": "new",
    }
    (path / "BUILT").write_bytes(canonical_json(built))
    return path


def test_lost_response_is_reconciled_without_duplicate_commit(tmp_path: Path) -> None:
    proposed = candidate(tmp_path)
    hub = FakeHub(lose_response=True)
    with pytest.raises(ConnectionError):
        publish(tmp_path, proposed, hub)
    assert (proposed / "PUBLISHING").exists()
    result = publish(tmp_path, proposed, hub)
    assert result.state == "recovered"
    assert hub.calls == 1
    assert (proposed / "PUBLISHED").exists()
    assert (tmp_path / "baseline").resolve() == proposed.resolve()


def test_third_party_head_stops_pending_publish(tmp_path: Path) -> None:
    proposed = candidate(tmp_path)
    hub = FakeHub()
    with pytest.raises(ConnectionError):
        hub.lose_response = True
        publish(tmp_path, proposed, hub)
    hub.current = "stranger"
    with pytest.raises(PublishError, match="public head changed"):
        publish(tmp_path, proposed, hub)
    assert hub.calls == 1


def test_dry_run_never_creates_intent_or_commit(tmp_path: Path) -> None:
    proposed = candidate(tmp_path)
    hub = FakeHub()
    assert publish(tmp_path, proposed, hub, dry_run=True).state == "dry-run"
    assert hub.calls == 0
    assert not (proposed / "PUBLISHING").exists()


def test_saved_receipt_promotes_without_network(tmp_path: Path) -> None:
    proposed = candidate(tmp_path)
    (proposed / "PUBLISHING").write_text("{}")
    (proposed / "PUBLISHED").write_text('{"commit":"landed"}')

    class OfflineHub(FakeHub):
        def head(self) -> str | None:
            raise AssertionError("network must not be read after acknowledgment")

    result = publish(tmp_path, proposed, OfflineHub())
    assert result.state == "promoted"
    assert (tmp_path / "baseline").resolve() == proposed.resolve()
    assert not (proposed / "PUBLISHING").exists()


def test_failure_after_receipt_recovers_without_second_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposed = candidate(tmp_path)
    hub = FakeHub()
    import swingset.publish.service as service

    real_promote = service._promote
    monkeypatch.setattr(service, "_promote", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        publish(tmp_path, proposed, hub)
    assert (proposed / "PUBLISHED").exists()
    assert (proposed / "PUBLISHING").exists()
    monkeypatch.setattr(service, "_promote", real_promote)
    publish(tmp_path, proposed, hub)
    assert hub.calls == 1
    assert not (proposed / "PUBLISHING").exists()


def test_request_that_never_lands_retries_same_candidate(tmp_path: Path) -> None:
    proposed = candidate(tmp_path)

    class InitiallyOffline(FakeHub):
        def create_commit(
            self,
            *,
            parent: str | None,
            message: str,
            additions: dict[str, Path],
            deletions: tuple[str, ...],
        ) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("request did not arrive")
            self.calls -= 1
            return super().create_commit(
                parent=parent, message=message, additions=additions, deletions=deletions
            )

    hub = InitiallyOffline()
    with pytest.raises(ConnectionError):
        publish(tmp_path, proposed, hub)
    result = publish(tmp_path, proposed, hub)
    assert result.state == "published"
    assert hub.current == "commit-2"


def test_bootstrap_accepts_repository_without_manifest_and_rejects_dataset(tmp_path: Path) -> None:
    hub = FakeHub()
    assert expected_parent(tmp_path, hub) == "base"
    hub.commits["base"] = RemoteCommit("base", None, "existing", "hash", {})
    with pytest.raises(PublishError, match="not empty"):
        expected_parent(tmp_path, hub)


def test_two_successful_publications_do_not_leave_historical_intent(tmp_path: Path) -> None:
    hub = FakeHub()
    first = candidate(tmp_path, "cand_first")
    assert publish(tmp_path, first, hub).commit == "commit-1"
    assert not (first / "PUBLISHING").exists()

    second = candidate(tmp_path, "cand_second")
    built = json.loads((second / "BUILT").read_text())
    built.update(baseline_commit="commit-1", expected_parent="commit-1")
    (second / "BUILT").write_bytes(canonical_json(built))
    assert publish(tmp_path, second, hub).commit == "commit-2"
    assert not (second / "PUBLISHING").exists()
    assert (tmp_path / "baseline").resolve() == second.resolve()

    # A marker retained by an older release is an ancestor receipt, not pending work.
    (first / "PUBLISHING").write_text("{}")
    assert reconcile(tmp_path, hub, dry_run=False).state == "none"
    assert (tmp_path / "baseline").resolve() == second.resolve()
    assert hub.calls == 2
