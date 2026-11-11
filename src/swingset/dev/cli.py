"""CLI registration for local seeds and bounded replay."""

from __future__ import annotations

import argparse
import json

from . import diagnostics, replay, seed
from .scenarios import NAMES

COMMANDS = frozenset({"dev-seed", "dev-replay", "dev-drop", "dev-diagnostic"})


def register_arguments(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    shared: argparse.ArgumentParser | None = None,
) -> None:
    diagnostic = commands.add_parser(
        "dev-diagnostic",
        help="Prepare or run one reviewed bounded local CPU or driver-wait diagnostic",
    )
    diagnostic.add_argument("diagnostic_action", choices=("prepare", "run", "result"))
    diagnostic.add_argument("diagnostic_directory")
    diagnostic.add_argument("--image", dest="diagnostic_image")
    diagnostic.add_argument(
        "--case",
        dest="diagnostic_case",
        choices=tuple(diagnostics.CASES),
        default="scoring",
    )
    diagnostic.add_argument(
        "--mode", dest="diagnostic_mode", choices=("cpu", "waits"), default="cpu"
    )
    seed_command = commands.add_parser(
        "dev-seed", help="Create a named offline fixture database; existing names are refused"
    )
    seed_command.add_argument("name", nargs="?", default="default")
    seed_command.add_argument("--scenario", choices=NAMES, default="event-projection")
    replay_command = commands.add_parser(
        "dev-replay", help="Replay one unit or a bounded event on a disposable development copy"
    )
    replay_command.add_argument(
        "database", help="Development seed name or its local files directory"
    )
    choice = replay_command.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--unit", help="parse/snapshot/<id>, project/<kind>/<id> or link/event/<id>"
    )
    choice.add_argument("--event", help="Event id; includes the bounded seed's prerequisites")
    replay_command.add_argument("--max-units", type=int, default=replay.MAX_UNITS)
    replay_command.add_argument("--seconds", type=float, default=30.0)
    drop_command = commands.add_parser(
        "dev-drop", help="Drop a local development seed only when its stored identity matches"
    )
    drop_command.add_argument("name")


def command(arguments: argparse.Namespace) -> int:
    if arguments.command == "dev-diagnostic":
        from .diagnostics import main

        return main(
            [arguments.diagnostic_action, arguments.diagnostic_directory]
            + (["--image", arguments.diagnostic_image] if arguments.diagnostic_image else [])
            + ["--case", arguments.diagnostic_case, "--mode", arguments.diagnostic_mode]
        )
    if arguments.command == "dev-seed":
        result = seed.create(arguments.name, scenario=arguments.scenario)
    elif arguments.command == "dev-drop":
        result = seed.drop(arguments.name)
    elif arguments.command == "dev-replay":
        result = replay.run(
            arguments.database,
            unit=arguments.unit,
            event=arguments.event,
            max_units=arguments.max_units,
            seconds=arguments.seconds,
        )
    else:
        raise ValueError("unknown development command")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
