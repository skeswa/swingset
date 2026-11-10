"""Run reviewed operator commands separately from the private graph verifier."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from . import runtime, security
from .configuration import runtime_artifact

COMMANDS = {
    "operator": frozenset(
        {
            "import-sqlite37",
            "inspect-import",
            "migrate",
            "rekey",
            "restore",
            "restore-resume",
            "backup",
            "release",
            "pause",
            "resume",
            "hold",
            "doctor",
            "summary",
        }
    ),
    "verifier": frozenset({"verify-derivations", "profile-input-acceptance", "doctor", "summary"}),
}


def configure(role: str, directory: Path, member: dict[str, object]) -> None:
    """Every database credential must name this utility's one allowed endpoint."""
    runtime.secrets(directory, role)
    names = ["SWINGSET_DATABASE_URL", "SWINGSET_CONTROL_DATABASE_URL"]
    names += (
        ["SWINGSET_SCRATCH_ADMIN_DSN"] if role == "verifier" else ["SWINGSET_OWNER_DATABASE_URL"]
    )
    for name in names:
        if name not in os.environ:
            raise RuntimeError("utility database credential is missing")
        endpoint = conninfo_to_dict(os.environ[name])
        if (
            endpoint.get("host") != member["database"]
            or endpoint.get("port", "5432") != str(member.get("database_port", 5432))
            or endpoint.get("hostaddr") not in {None, member["database"]}
            or endpoint.get("service")
        ):
            raise RuntimeError(
                "utility database endpoint differs from its authorized private address"
            )
    if role == "verifier":
        # Private verification copies are deliberately dev. No third-party
        # credentials or shared database credentials are available here.
        os.environ["SWINGSET_ENV"] = "dev"
        from swingset.state import engine
        from swingset.state.environment import identity

        with engine.connect(os.environ["SWINGSET_DATABASE_URL"], read_only=True) as connection:
            stored = identity(connection)
            if stored.environment != "dev":
                raise RuntimeError("private verification requires a dev copy")
            print(
                json.dumps(
                    {
                        "environment": "dev",
                        "database_id": stored.database_id,
                        "database": connection.info.dbname,
                    }
                ),
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=tuple(COMMANDS), required=True)
    parser.add_argument(
        "--network-policy", type=Path, default=Path("/run/swingset-config/network.json")
    )
    parser.add_argument("--authorization", type=Path, default=Path("/run/swingset-authorization"))
    parser.add_argument("--secrets", type=Path, default=Path("/run/secrets"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments or arguments[0] not in COMMANDS[args.role]:
        raise RuntimeError("command is outside this utility's permitted operations")
    os.umask(0o077)
    proof = runtime.authorized(args.network_policy, args.authorization, wait=True)
    if proof["role"] != args.role or os.environ.get("SWINGSET_ENV") != "dryrun":
        raise RuntimeError("utility role or explicit rehearsal environment differs")
    runtime_artifact(os.environ.get("SWINGSET_RUNTIME_IMAGE", ""))
    policy = security.load(args.network_policy)
    configure(args.role, args.secrets, policy["workers"][args.role])
    from swingset.cli import main as command

    return command(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
