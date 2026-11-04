"""Restore checks run in an owner job; loop containers hold only its socket path."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
from pathlib import Path
from typing import Any

from swingset.state import engine, postgres_backup, postgres_schema
from swingset.state.postgres_copy import Copies

from .snapshot import current_runtime_identity
from .transport import TransportError

_LIMIT = 1 << 20


def restore_check(
    history: Path,
    admin_dsn: str,
    *,
    manifest_sha256: str,
    registry_root: Path | None = None,
    checker_lock_fd: int | None = None,
) -> dict[str, Any]:
    """Verify every byte, restore into a private database and audit before dropping it."""
    postgres_backup.verify(
        history,
        runtime_identity=current_runtime_identity(),
        expected_manifest_sha256=manifest_sha256,
    )
    with Copies.for_admin(
        admin_dsn, registry_root=registry_root, checker_lock_fd=checker_lock_fd
    ) as copies:
        with copies.empty() as connection:
            result = postgres_backup.restore(
                history,
                connection,
                runtime_identity=current_runtime_identity(),
                expected_manifest_sha256=manifest_sha256,
            )
            postgres_schema.verify(connection)
            return {
                "complete": True,
                "audit": result["audit"],
                "runtime_identity": result["runtime_identity"],
            }


def _drill_restore(
    history: Path,
    state: Path,
    admin_dsn: str,
    *,
    manifest_sha256: str,
    root: Path,
    registry_root: Path | None = None,
    checker_lock_fd: int | None = None,
) -> dict[str, Any]:
    """The privileged job owns both the fresh database and expanded file tree."""
    from .drill import _release

    work = history.parent.parent
    if (
        history.name != "history"
        or history.parent.name != "fetch"
        or state != work / "state"
        or not re.fullmatch(r"\.install-[a-zA-Z0-9_-]{8,64}", work.name)
        or not work.is_relative_to(root)
        or work == root
    ):
        raise ValueError("invalid drill work directory")
    with Copies.for_admin(
        admin_dsn, registry_root=registry_root, checker_lock_fd=checker_lock_fd
    ) as copies:
        copies.own_directory(work, root=root)
        metadata = postgres_backup.verify(
            history,
            runtime_identity=current_runtime_identity(),
            expected_manifest_sha256=manifest_sha256,
        )["metadata"]
        with copies.empty() as connection:
            result = postgres_backup.restore(
                history,
                connection,
                runtime_identity=current_runtime_identity(),
                expected_manifest_sha256=manifest_sha256,
            )
            pending = metadata.get("pending_candidates") or []
            installed = {
                "baseline_candidate": metadata.get("baseline_candidate"),
                "pending_candidate": pending[0] if pending else None,
            }
            private = engine.connect(
                connection.info.dsn, password=connection.info.password, read_only=True
            )
            private.state_dir = state
            try:
                with private.read_snapshot():
                    release = _release(state, installed, connection=private)
            finally:
                private.close()
            return {
                "complete": True,
                "audit": result["audit"],
                "runtime_identity": result["runtime_identity"],
                "release": release,
            }


def _dispatch(
    request: dict[str, Any],
    root: Path,
    admin_dsn: str,
    *,
    registry_root: Path | None = None,
    checker_lock_fd: int | None = None,
) -> dict[str, Any]:
    expected = {"history", "manifest_sha256"}
    drill = request.get("mode") == "drill"
    if drill:
        expected |= {"mode", "state"}
    if set(request) != expected:
        raise ValueError("invalid check request")
    paths = {}
    for key in ("history", "state") if drill else ("history",):
        relative = request[key]
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("invalid check path")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or path == root:
            raise ValueError("check path leaves the shared directory")
        paths[key] = path
    if drill:
        return _drill_restore(
            paths["history"],
            paths["state"],
            admin_dsn,
            manifest_sha256=request["manifest_sha256"],
            root=root,
            registry_root=registry_root,
            checker_lock_fd=checker_lock_fd,
        )
    return restore_check(
        paths["history"],
        admin_dsn,
        manifest_sha256=request["manifest_sha256"],
        registry_root=registry_root,
        checker_lock_fd=checker_lock_fd,
    )


def _failure(error: Exception) -> dict[str, Any]:
    # PostgreSQL DETAIL and candidate metadata may contain private row values.
    safe = {
        "public_closure_digest_mismatch",
        "private_closure_proof_unavailable",
        "private_closure_proof_corrupt",
    }
    return {
        "complete": False,
        "reason": str(error) if str(error) in safe else "restore_check_failed",
    }


def _request(history: Path, *, manifest_sha256: str, state: Path | None = None) -> dict[str, Any]:
    endpoint = os.environ.get("SWINGSET_BACKUP_CHECK_SOCKET")
    testing = endpoint is None and bool(os.environ.get("SWINGSET_TEST_RUN"))
    if endpoint is None and not testing:
        raise TransportError("backup check service is not configured; no backup can be recorded")
    root = Path(
        os.environ.get(
            "SWINGSET_BACKUP_CHECK_ROOT", "/tmp" if testing else "/var/lib/swingset/checkpoints"
        )
    ).resolve()
    try:
        request = {
            "history": history.resolve().relative_to(root).as_posix(),
            "manifest_sha256": manifest_sha256,
        }
        if state is not None:
            request.update(mode="drill", state=state.resolve().relative_to(root).as_posix())
    except ValueError:
        raise TransportError("backup check files are outside the shared check directory") from None
    if testing:
        from swingset.dev.services import ensure_service

        try:
            result = _dispatch(request, root, ensure_service().dsn())
        except Exception as error:
            result = _failure(error)
    else:
        assert endpoint is not None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1800)
                client.connect(endpoint)
                client.sendall(json.dumps(request).encode() + b"\n")
                with client.makefile("rb") as reply:
                    body = reply.readline(_LIMIT + 1)
            if len(body) > _LIMIT:
                raise ValueError("check response exceeded its bound")
            result = json.loads(body)
        except (OSError, ValueError):
            raise TransportError("backup restore check did not return a verified receipt") from None
    if not isinstance(result, dict) or result.get("complete") is not True:
        reason = (
            result.get("reason", "restore_check_failed")
            if isinstance(result, dict)
            else "restore_check_failed"
        )
        raise TransportError(f"backup restore check failed: {reason}")
    return result


def check(history: Path, *, manifest_sha256: str) -> dict[str, Any]:
    return _request(history, manifest_sha256=manifest_sha256)


def drill_check(history: Path, state: Path, *, manifest_sha256: str) -> dict[str, Any]:
    """Ask the owner job to restore and hydrate without lending its credential."""
    return _request(history, manifest_sha256=manifest_sha256, state=state)


def serve(endpoint: Path, root: Path, admin_dsn: str) -> None:
    """Serve bounded requests on a private shared-volume Unix socket."""
    import fcntl
    import socketserver
    import stat

    if not admin_dsn:
        raise ValueError("backup checker requires its own scratch administration credential")
    root = root.resolve(strict=True)
    endpoint.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    registry_root = root / ".owners"
    registry_root.mkdir(mode=0o700, exist_ok=True)
    if registry_root.is_symlink() or registry_root.stat().st_mode & 0o077:
        raise ValueError("backup checker ownership directory must be private")

    class Request(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.connection.settimeout(30)
            try:
                body = self.rfile.readline(_LIMIT + 1)
                if len(body) > _LIMIT:
                    raise ValueError("check request exceeded its bound")
                request = json.loads(body)
                if not isinstance(request, dict):
                    raise ValueError("invalid check request")
                result = _dispatch(
                    request,
                    root,
                    admin_dsn,
                    registry_root=registry_root,
                    checker_lock_fd=guard.fileno(),
                )
            except Exception as error:
                result = _failure(error)
            self.wfile.write(json.dumps(result).encode() + b"\n")

    # The singleton lease must survive the socket container's writable layer.
    lock = registry_root / "checker.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if endpoint.exists():
            if not stat.S_ISSOCK(endpoint.stat().st_mode):
                raise ValueError("backup checker endpoint is not a socket")
            endpoint.unlink()
        try:
            # Recover exact job-owned databases and directories after a killed owner.
            with Copies.for_admin(
                admin_dsn, registry_root=registry_root, checker_lock_fd=guard.fileno()
            ):
                pass
            with socketserver.UnixStreamServer(str(endpoint), Request) as server:
                os.chmod(endpoint, 0o600)
                server.serve_forever(poll_interval=0.2)
        finally:
            with contextlib.suppress(FileNotFoundError):
                endpoint.unlink()
