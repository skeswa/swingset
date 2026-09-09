"""Structured journal-friendly logging without secrets or response bodies."""

import json
import sys


def log(event: str, **fields: object) -> None:
    values = {"event": event, **fields}
    print(
        " ".join(f"{key}={json.dumps(value, default=str)}" for key, value in values.items()),
        file=sys.stderr,
        flush=True,
    )
