"""The four catalog event-list page kinds that acquisition phase one reads.

A leaf with no swingset imports, so admission and state can test a kind
without importing history acquisition.
"""

PHASE1_KINDS = frozenset(
    {
        "swingdancecouncil.events",
        "wsdc_calendar.events",
        "wsdc_newsletter.events",
        "wsdc_newsletter.index",
    }
)

#: The calendar scope an archived capture's listings are stored under. They
#: are history, never the live calendar's current rows.
HISTORY_SCOPE = "wsdc-history"


def stored_scope_ref(independent_capture: bool, scope_kind: str, scope_ref: str) -> str:
    """The scope ref an observation is stored under (`state.observations`).

    An archived capture of a phase-one listing is its own source unit
    (`watch_id/snapshot_id`); its calendar rows are stored as history.
    Release support matches stored rows against the generation's result, so
    it applies the same rule (D-0257).
    """
    return HISTORY_SCOPE if independent_capture and scope_kind == "calendar" else scope_ref
