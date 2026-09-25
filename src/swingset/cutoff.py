"""The operator's switch for output cutoff (D-0205).

Cutoff lets a parse or a derivation whose output is identical keep the
generation it already has. `[derivation] cutoff = false` in
`config/sources.toml` turns it off without a schema change: every re-run mints
a new generation, as before phase 1, and recorded equivalences stay readable.
`schedule.derive.derive_one` sets the switch for each unit from its bundle,
and `history.intake.run_intake` and the `accept-year` command from their
config. Paths that take no config keep cutoff on: the offline imports
(`history.transfer.import_evidence`, `history.intake.import_recording`) and
`history.review.review_pack` with `reconcile=True`, which only the research
tool `journal/tools/collection/intake_phase1.py` calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_ENABLED: ContextVar[bool] = ContextVar("swingset_cutoff", default=True)


def enabled() -> bool:
    """Whether a re-run with identical output may keep its generation."""
    return _ENABLED.get()


@contextmanager
def configured(on: bool) -> Iterator[None]:
    """Run the block with cutoff on or off."""
    token = _ENABLED.set(on)
    try:
        yield
    finally:
        _ENABLED.reset(token)
