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
