"""Loopback-only SSH forwarding and a read-only psql session using one keychain item."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess


def ssh_arguments(host: str, local_port: int, remote_port: int) -> list[str]:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,253}", host):
        raise ValueError("invalid SSH destination")
    if not 1024 <= local_port <= 65535 or not 1024 <= remote_port <= 65535:
        raise ValueError("tunnel ports must be unprivileged TCP ports")
    return [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-L",
        f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
        host,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    forward = commands.add_parser("tunnel")
    forward.add_argument("--host", default="rhuidean")
    forward.add_argument("--local-port", type=int, default=15435)
    forward.add_argument("--remote-port", type=int, default=15435)
    sql = commands.add_parser("psql")
    sql.add_argument("--database", required=True)
    sql.add_argument("--user", required=True)
    sql.add_argument("--port", type=int, default=15435)
    sql.add_argument("--keychain-service", required=True)
    args = parser.parse_args()
    if args.command == "tunnel":
        arguments = ssh_arguments(args.host, args.local_port, args.remote_port)
        os.execvp(arguments[0], arguments)
    if not shutil.which("psql"):
        raise RuntimeError("psql must be installed before opening the SQL session")
    if not 1024 <= args.port <= 65535 or any(
        not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", value)
        for value in (args.database, args.user)
    ):
        raise ValueError("invalid database, user or loopback port")
    credential = subprocess.run(
        ["security", "find-generic-password", "-s", args.keychain_service, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if credential.returncode or not credential.stdout.rstrip("\n"):
        raise RuntimeError("the named database credential is absent from the keychain")
    environment = {
        **os.environ,
        "PGPASSWORD": credential.stdout.rstrip("\n"),
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000",
    }
    arguments = [
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--dbname",
        args.database,
        "--username",
        args.user,
    ]
    os.execvpe(arguments[0], arguments, environment)


if __name__ == "__main__":
    main()
