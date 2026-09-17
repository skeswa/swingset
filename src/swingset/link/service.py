"""Load event evidence, resolve identities, and commit against the same inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from swingset.clock import Clock
from swingset.project.materialization import helper_recipe
from swingset.state import derivations
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import Database
from swingset.state.work import WorkUnit

from .evidence import load_linking_snapshot
from .persistence import StaleIdentityResolution, commit_resolution
from .resolution import resolve_event

if TYPE_CHECKING:
    from swingset.state.derivations import Selection
    from swingset.state.inputs import InputBundle

LINKER_VERSION = "9"


def link_event(
    database: Database,
    event_id: str,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    *,
    selection: Selection | None = None,
) -> bool:
    """Resolve and replace an event atomically; return whether its links changed.

    Coordinate three phases: load a LinkingSnapshot of evidence/rules/input
    versions, compute an EventResolution of subject answers, and commit it
    against those versions. A subject is an entry or judge; its answer retains
    candidate matches, review restrictions, the conclusion, and findings.

    A superseded standalone call returns False and leaves work queued. A caller
    supplying a selection receives SupersededWorkError so its attempt can retry.
    Matching policy lives in resolve_event; persistence stores its answer.
    """
    delegated = selection is not None
    if selection is None and derivations.available(database.connection):
        with database.transaction():
            selection = derivations.capture(
                database.connection,
                WorkUnit("link", "event", event_id),
                now=clock.now(),
                recipe=helper_recipe(database.connection, bundle.files, "link"),
            )
    snapshot = load_linking_snapshot(database.connection, event_id, bundle, selection)
    resolution = resolve_event(snapshot.evidence, snapshot.rules)
    try:
        return commit_resolution(
            database, snapshot.basis, resolution, bundle, clock, run_id, LINKER_VERSION
        )
    except (StaleIdentityResolution, SupersededWorkError) as exc:
        if delegated:
            raise SupersededWorkError("identity inputs changed during linking") from exc
        return False
