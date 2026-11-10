"""Container startup checks network authorization before loading credentials."""

from __future__ import annotations

import argparse
import json
import os
import stat
import time
from pathlib import Path

from . import scheduler, security
from .configuration import runtime_artifact

SECRET_NAMES = {
    "worker-dsn": "SWINGSET_DATABASE_URL",
    "control-dsn": "SWINGSET_CONTROL_DATABASE_URL",
    "owner-dsn": "SWINGSET_OWNER_DATABASE_URL",
    "scratch-admin-dsn": "SWINGSET_SCRATCH_ADMIN_DSN",
    "hf-token": "HF_TOKEN",
    "r2-access-key": "AWS_ACCESS_KEY_ID",
    "r2-secret-key": "AWS_SECRET_ACCESS_KEY",
}


def secrets(directory: Path, role: str) -> None:
    names: tuple[str, ...]
    if role == "checker":
        names = ("scratch-admin-dsn",)
    elif role == "verifier":
        names = ("worker-dsn", "control-dsn", "scratch-admin-dsn")
    elif role == "operator":
        names = (
            "worker-dsn",
            "control-dsn",
            "owner-dsn",
            "hf-token",
            "r2-access-key",
            "r2-secret-key",
        )
    elif role in {"release-maintain", "jobs"}:
        names = ("worker-dsn", "hf-token", "r2-access-key", "r2-secret-key")
    else:
        names = ("worker-dsn",)
    allowed = {SECRET_NAMES[name] for name in names}
    forbidden = set(SECRET_NAMES.values()) - allowed
    if any(name in os.environ for name in forbidden):
        raise RuntimeError("ordinary workers cannot receive administration credentials")
    for name in names:
        path = directory / name
        if not path.exists():
            if name in {"worker-dsn", "scratch-admin-dsn"}:
                raise RuntimeError("required service credential is missing")
            continue
        if (
            not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o077
            or path.stat().st_size > 65536
        ):
            raise RuntimeError("service credential permissions or size differ")
        value = path.read_text().rstrip("\n")
        if not value or "\n" in value or "\r" in value or "\0" in value:
            raise RuntimeError("service credential is empty or malformed")
        os.environ[SECRET_NAMES[name]] = value


def authorized(policy: Path, directory: Path, *, wait: bool) -> dict[str, object]:
    deadline = time.monotonic() + (120 if wait else 0)
    while True:
        try:
            return security.require_authorization(policy, directory)
        except (OSError, RuntimeError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def readiness(state: Path, holds: Path) -> dict[str, object]:
    from swingset.state import engine, postgres_schema
    from swingset.state.environment import identity

    from .rehearsal import Policy

    blocked = scheduler.held(holds)  # Check out-of-band state before opening even a reader.
    if blocked:
        return {"ready": True, "held": True}
    with engine.connect(engine.resolve_dsn(state), read_only=True) as connection:
        postgres_schema.validate_schema(connection)
        privileges = connection.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,pg_has_role(current_user,'swingset_owner','USAGE') AS owner_role,pg_has_role(current_user,'swingset_worker','USAGE') AS worker_role FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if privileges is None or any(privileges[:4]) or not privileges[4]:
            raise RuntimeError(
                "ordinary services require a worker credential without owner or cluster privileges"
            )
        stored = identity(connection)
        if stored.environment != "dryrun":
            raise RuntimeError("the rehearsal scheduler requires a stored dryrun identity")
        Policy.load().require_database(stored.database_id)
        if (state / "RESTORE_PENDING").exists():
            raise RuntimeError("restore activation is pending")
    return {"ready": True, "held": blocked, "database_id": stored.database_id}


def liveness(runtime: Path, role: str) -> None:
    record = json.loads((runtime / role / "heartbeat.json").read_text())
    maximum_age = 390 if role == "health" else 90
    if record.get("group") != role or not 0 <= time.time() - record["utc"] < maximum_age:
        raise RuntimeError("service heartbeat is stale")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("scheduler", "checker", "checker-readiness", "health", "readiness", "liveness"),
    )
    parser.add_argument("--group", default="jobs")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/swingset"))
    parser.add_argument("--runtime", type=Path, default=Path("/var/lib/swingset-runtime"))
    parser.add_argument("--holds", type=Path, default=Path("/run/swingset-holds"))
    parser.add_argument(
        "--network-policy", type=Path, default=Path("/run/swingset-config/network.json")
    )
    parser.add_argument("--authorization", type=Path, default=Path("/run/swingset-authorization"))
    parser.add_argument("--secrets", type=Path, default=Path("/run/secrets"))
    args = parser.parse_args()
    os.umask(0o077)
    proof = authorized(
        args.network_policy,
        args.authorization,
        wait=args.command in {"scheduler", "checker", "health"},
    )
    role = str(proof["role"])
    if os.environ.get("SWINGSET_ENV") != "dryrun":
        raise RuntimeError("this entry point requires the explicit dryrun environment")
    runtime_artifact(os.environ.get("SWINGSET_RUNTIME_IMAGE", ""))
    if args.command == "liveness":
        liveness(args.runtime, role)
        return
    if args.command == "checker-readiness":
        from swingset.backup.check import probe

        if role != "checker":
            raise RuntimeError("checker probe role differs")
        probe(Path("/run/swingset-checker/check.sock"))
        return
    if args.command == "scheduler":
        if role != args.group:
            raise RuntimeError("scheduler group differs from its authorized role")
        scheduler.held(args.holds)

        def before_task() -> bool:
            if scheduler.held(args.holds):
                return False
            secrets(args.secrets, role)
            return not bool(readiness(args.state, args.holds)["held"])

        scheduler.run(
            role,
            args.state,
            args.runtime / role,
            args.holds,
            before_task=before_task,
        )
        return
    if args.command == "readiness" and scheduler.held(args.holds):
        liveness(args.runtime, role)
        print(json.dumps({"ready": True, "held": True}))
        return
    secrets(args.secrets, role)
    if args.command == "health":
        from .health import main as health_main

        if role != "health":
            raise RuntimeError("health service role differs")
        health_main(
            [
                "watchdog",
                "--state",
                str(args.state),
                "--runtime",
                str(args.runtime / role),
                "--watch",
                "--interval",
                "300",
            ]
        )
        return
    if args.command == "readiness":
        value = readiness(args.state, args.holds)
        liveness(args.runtime, role)
        print(json.dumps(value))
        return
    if args.command == "checker":
        from swingset.backup.check import serve

        if role != "checker":
            raise RuntimeError("only the checker receives scratch administration credentials")
        serve(
            Path("/run/swingset-checker/check.sock"),
            args.state / "checkpoints",
            os.environ["SWINGSET_SCRATCH_ADMIN_DSN"],
        )
        return


if __name__ == "__main__":
    main()
