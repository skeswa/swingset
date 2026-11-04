from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from swingset.build.files import canonical_json, durable_write, fsync_dir
from swingset.publish import gate
from swingset.publish.journal import (
    Hub as Hub,
)
from swingset.publish.journal import (
    PublishError as PublishError,
)
from swingset.publish.journal import (
    RemoteCommit as RemoteCommit,
)
from swingset.publish.journal import (
    _is_baseline,
)
from swingset.publish.journal import (
    expected_parent as expected_parent,
)
from swingset.publish.journal import (
    pending_candidates as pending_candidates,
)
from swingset.publish.safety import (
    PublicationHeldError,
    StaleCandidateError,
    publication_boundary,
    publication_in_flight,
    publication_receipt,
    reject_candidate,
    settle_publication,
)
from swingset.state.control_lock import control_lock
from swingset.state.controls import check_admission
from swingset.state.db import open_database
from swingset.state.engine import Connection
from swingset.state.environment import ensure_external_allowed


@dataclass(frozen=True)
class PublishResult:
    state: str
    candidate_id: str | None = None
    commit: str | None = None
    reason: str | None = None


def _pending(state_dir: Path) -> Path | None:
    found = pending_candidates(state_dir)
    if len(found) > 1:
        raise PublishError("more than one pending publication")
    return found[0] if found else None


def _promote(state_dir: Path, candidate: Path) -> None:
    relative = os.path.relpath(candidate, state_dir)
    temporary = state_dir / f".baseline.tmp-{os.getpid()}"
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    os.symlink(relative, temporary)
    os.replace(temporary, state_dir / "baseline")
    fsync_dir(state_dir)
    try:
        (candidate / "PUBLISHING").unlink()
    except FileNotFoundError:
        pass
    fsync_dir(candidate)


def _verify_remote(candidate: Path, remote: RemoteCommit, built: dict[str, object]) -> None:
    if (
        remote.candidate_id != candidate.name
        or remote.manifest_hash != built["manifest_hash"]
        or remote.parent != built.get("expected_parent")
    ):
        raise PublishError("remote commit metadata does not match pending candidate")
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_text())
    expected = dict(manifest["files"])
    expected["_meta/manifest.json"] = str(built["manifest_hash"])
    for name, digest in expected.items():
        if remote.files.get(name) != digest:
            raise PublishError(f"remote file hash mismatch: {name}")


def _landed(
    state_dir: Path,
    candidate: Path,
    connection: Connection,
    *,
    outcome: str,
    receipt: bytes | None = None,
) -> None:
    """The receipt, baseline and admission acknowledgement share control turn B."""
    with control_lock(state_dir, connection=connection):
        if receipt is not None:
            durable_write(candidate / "PUBLISHED", receipt)
        _promote(state_dir, candidate)
        settle_publication(state_dir, candidate, outcome=outcome, connection=connection)


def reconcile(
    state_dir: Path, hub: Hub, *, dry_run: bool, connection: Connection | None = None
) -> PublishResult:
    if connection is None:
        with open_database(state_dir) as database:
            return reconcile(state_dir, hub, dry_run=dry_run, connection=database.connection)
    ensure_external_allowed(connection, "publication reads", hub)
    candidate = _pending(state_dir)
    if candidate is None:
        baseline = state_dir / "baseline"
        if baseline.is_symlink():
            settle_publication(
                state_dir, baseline.resolve(), outcome="receipt_promoted", connection=connection
            )
        return PublishResult("none")
    built = json.loads((candidate / "BUILT").read_text())
    receipt = candidate / "PUBLISHED"
    if receipt.is_file():
        commit = str(json.loads(receipt.read_text())["commit"])
        _landed(state_dir, candidate, connection, outcome="receipt_promoted")
        return PublishResult("promoted", candidate.name, commit)
    head = hub.head()
    expected = built.get("expected_parent")
    if head != expected:
        if head is None:
            raise PublishError(f"public head changed: expected {expected!r}, actual None")
        try:
            remote = hub.inspect(head)
        except (KeyError, FileNotFoundError) as error:
            raise PublishError(
                f"public head changed: expected {expected!r}, actual {head!r}"
            ) from error
        if remote.parent != expected:
            raise PublishError(f"public head changed: expected {expected!r}, actual {head!r}")
        _verify_remote(candidate, remote, built)
        _landed(
            state_dir,
            candidate,
            connection,
            outcome="landed_recovered",
            receipt=canonical_json(publication_receipt(candidate, head, recovered=True)),
        )
        return PublishResult("recovered", candidate.name, head)
    if publication_in_flight(state_dir, candidate, connection=connection):
        return PublishResult(
            "draining",
            candidate.name,
            reason="active publication awaits completion or startup recovery",
        )
    settle_publication(state_dir, candidate, outcome="not_landed", connection=connection)
    if dry_run:
        return PublishResult("pending", candidate.name)
    return _submit(state_dir, candidate, hub, connection=connection)


def _files(candidate: Path) -> dict[str, Path]:
    return {
        path.relative_to(candidate).as_posix(): path
        for path in candidate.rglob("*")
        if path.is_file() and path.name not in {"BUILT", "PUBLISHING", "PUBLISHED", "REJECTED"}
    }


def _publication_backup(
    state_dir: Path, candidate: Path, connection: Connection, action_id: str
) -> None:
    from datetime import UTC, datetime

    from swingset.backup.segments import LOCAL_TRANSPORT, create_set
    from swingset.backup.transport import LocalDirectoryTransport, Transport
    from swingset.state.db import Database
    from swingset.versions import versions

    transport: Transport
    if (
        connection.execute("SELECT environment FROM ops.environment_identity").fetchone()[0]
        == "production"
    ):
        from swingset.backup.r2 import configured

        transport = configured(connection)
    else:
        transport = LocalDirectoryTransport(state_dir / "checkpoints", name=LOCAL_TRANSPORT)
    result = create_set(
        Database(state_dir, connection, None),
        transport,
        now=datetime.now(UTC),
        versions=versions(),
        prune=False,
    )
    if result.get("verified") is not True:
        raise PublishError("publication backup did not pass its read-back check")
    check_admission(connection, action_id)
    with control_lock(state_dir, connection=connection):
        # The intent names precisely the completed, read-back backup for this push.
        pending = json.loads((candidate / "PUBLISHING").read_text())
        if pending.get("action_id") != action_id:
            raise PublishError("publication intent changed during its backup")
        pending["backup"] = {
            "set_id": result["set_id"],
            "manifest_sha256": result["manifest_sha256"],
        }
        durable_write(candidate / "PUBLISHING", canonical_json(pending))


def _submit(state_dir: Path, candidate: Path, hub: Hub, *, connection: Connection) -> PublishResult:
    ensure_external_allowed(connection, "publication", hub)
    built = json.loads((candidate / "BUILT").read_text())
    manifest = json.loads((candidate / "_meta" / "manifest.json").read_text())
    expected_files = set(manifest["files"]) | {"_meta/manifest.json"}
    baseline = state_dir / "baseline"
    old_files = set(_files(baseline.resolve())) if baseline.is_symlink() else set()
    from swingset.backup.snapshot import BackupBusy
    from swingset.state.controls import ControlPaused

    # A partitioned candidate's check reads only its own tables, so its fence
    # is narrow and the loops tick through it (plan phase 3, WP10).
    narrow = (manifest.get("release_policy") or {}).get("mode") == "partitioned"

    def reserve_intent(action_id: str) -> None:
        durable_write(
            candidate / "PUBLISHING",
            canonical_json(
                {
                    "expected_parent": built.get("expected_parent"),
                    "manifest_hash": built["manifest_hash"],
                    "action_id": action_id,
                }
            ),
        )

    try:
        with publication_boundary(
            state_dir,
            candidate,
            narrow=narrow,
            connection=connection,
            reserve_intent=reserve_intent,
        ) as action_id:
            try:
                _publication_backup(state_dir, candidate, connection, action_id)
            except Exception:
                _backup_failed(state_dir, candidate, action_id)
                raise
            ensure_external_allowed(connection, "publication", hub)
            check_admission(connection, action_id)
            commit = hub.create_commit(
                parent=built.get("expected_parent"),
                message=f"Publish {candidate.name} ({built['manifest_hash']})",
                additions=_files(candidate),
                deletions=tuple(sorted(old_files - expected_files)),
            )
            ensure_external_allowed(connection, "publication reads", hub)
            check_admission(connection, action_id)
            remote = hub.inspect(commit)
            _verify_remote(candidate, remote, built)
            _landed(
                state_dir,
                candidate,
                connection,
                outcome="published",
                receipt=canonical_json(publication_receipt(candidate, commit)),
            )
    except BackupBusy as exc:
        return PublishResult("held", candidate.name, reason=str(exc))
    except ControlPaused as exc:
        return PublishResult("held", candidate.name, reason=str(exc))
    except StaleCandidateError as exc:
        reject_candidate(candidate, str(exc))
        raise PublishError(str(exc)) from exc
    except PublicationHeldError as exc:
        raise PublishError(str(exc)) from exc
    return PublishResult("published", candidate.name, commit)


def publish(
    state_dir: Path,
    candidate: Path,
    hub: Hub,
    *,
    dry_run: bool = False,
    connection: Connection | None = None,
) -> PublishResult:
    if connection is None:
        with open_database(state_dir) as database:
            return publish(
                state_dir, candidate, hub, dry_run=dry_run, connection=database.connection
            )
    ensure_external_allowed(connection, "publication", hub)
    if (state_dir / "RESTORE_PENDING").exists():
        raise PublishError("publication is disabled while restore verification is pending")
    recovered = reconcile(state_dir, hub, dry_run=dry_run, connection=connection)
    if recovered.state != "none":
        return recovered
    if _is_baseline(state_dir, candidate) and (candidate / "PUBLISHED").is_file():
        return PublishResult("unchanged", candidate.name)
    built_path = candidate / "BUILT"
    if not built_path.is_file():
        raise PublishError("candidate is incomplete")
    built = json.loads(built_path.read_text())
    if not bool(built["changed"]):
        return PublishResult("unchanged", candidate.name)
    baseline = state_dir / "baseline"
    baseline_commit = None
    if baseline.is_symlink():
        receipt = baseline.resolve() / "PUBLISHED"
        baseline_commit = json.loads(receipt.read_text())["commit"]
    if built.get("baseline_commit") != baseline_commit:
        raise PublishError("candidate was built against a different baseline")
    # The strictly-better gate (D-0225): a refused verdict submits nothing and
    # leaves the candidate as it is. It is not an error.
    refused = gate.refusal(gate.read(state_dir, candidate))
    if refused is not None:
        return PublishResult("refused", candidate.name, reason=refused)
    if dry_run:
        return PublishResult("dry-run", candidate.name)
    return _submit(state_dir, candidate, hub, connection=connection)


def _backup_failed(state_dir: Path, candidate: Path, action_id: str) -> None:
    """A failed prerequisite stays visible even if the publishing loop exits."""
    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (state_dir / "ALERTS.log").open("a") as stream:
        stream.write(
            f"{stamp} [publication-backup-failed] candidate={candidate.name} action={action_id}; publication intent retained; no push admitted\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
