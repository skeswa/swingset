"""Read a journal for review; acceptance and resolution live in state policy."""

from pathlib import Path

from swingset.state.identity_journal import Decision, parse_journal


def load_overrides(path: Path) -> tuple[Decision, ...]:
    return parse_journal(path.read_bytes())
