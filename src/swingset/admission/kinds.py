"""Which contract module assesses each page kind, and its contract version.

A dispatch table, like `sources.registry`: the table is literal and
`contract` imports one module by name. Recipe scopes hold this file's code
with the table blanked, not its table; each parse scope names its own kind's
module instead, so an edit to one kind's rules re-parses only the kinds that
module lists (D-0261). A kind not
listed here is unassessed: `contracts.inspect` blocks it.

Each module defines `VERSIONS` (its kinds and their contract versions) and
`accounting(ctx, body, extract, result)`, which returns an
`accounting.Accounting`.
"""

from __future__ import annotations

import importlib
from types import ModuleType

CONTRACTS: dict[str, str] = {
    "wsdc_calendar.events": "swingset.admission.wsdc_calendar_contract",
    "wsdc_registry.dancer": "swingset.admission.registry_contract",
    "eepro.autoindex": "swingset.admission.listing_contract",
    "eepro.index": "swingset.admission.listing_contract",
    "eepro.round": "swingset.admission.round_contract",
    "scoringdance.event": "swingset.admission.scoringdance_event_contract",
    "scoringdance.recent": "swingset.admission.listing_contract",
    "scoringdance.round": "swingset.admission.round_contract",
    "scoringdance.sitemap": "swingset.admission.listing_contract",
    "wdr.rounds": "swingset.admission.round_contract",
    "steprightsolutions.event": "swingset.admission.stepright_contract",
    "steprightsolutions.index": "swingset.admission.stepright_contract",
    "steprightsolutions.round": "swingset.admission.stepright_contract",
    "dcn.archive": "swingset.admission.dcn_contract",
    "dcn.event": "swingset.admission.dcn_contract",
    "swingdirector.round": "swingset.admission.swingdirector_contract",
}


def contract(page_kind: str) -> ModuleType | None:
    """Import the contract module of one page kind, or None when it has none."""
    name = CONTRACTS.get(page_kind)
    return None if name is None else importlib.import_module(name)


def contract_version(page_kind: str) -> str:
    """A changed page contract does not invalidate unrelated reviewed contracts."""
    module = contract(page_kind)
    return "unassessed" if module is None else str(module.VERSIONS[page_kind])
