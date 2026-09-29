"""Load event evidence, resolve identities, and commit against the same inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from swingset.clock import Clock
from swingset.project.materialization import helper_recipe
from swingset.state import derivations
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import Database
from swingset.state.read_sets import guarding, recording
from swingset.state.work import WorkUnit

from .evidence import load_linking_snapshot, read_set
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
    Loading and committing run inside `recording`, which notes the canonical
    tables they read (`state/read_sets`). The run's read set, the keys it
    read with their values at load, selects the generation it commits.
    """
    delegated = selection is not None
    conn = database.connection
    unit = WorkUnit("link", "event", event_id)
    with guarding(conn):
        if selection is None and derivations.available(conn):
            with database.transaction():
                selection = derivations.capture(
                    conn, unit, now=clock.now(), recipe=helper_recipe(conn, bundle.files, "link")
                )
        try:
            with recording(conn, unit):
                snapshot = load_linking_snapshot(conn, event_id, bundle, selection)
                resolution = resolve_event(snapshot.evidence, snapshot.rules)
                return commit_resolution(
                    database,
                    snapshot.basis,
                    resolution,
                    bundle,
                    clock,
                    run_id,
                    LINKER_VERSION,
                    reads=read_set(conn, snapshot, resolution),
                )
        except (StaleIdentityResolution, SupersededWorkError) as exc:
            if delegated:
                raise SupersededWorkError("identity inputs changed during linking") from exc
            return False
