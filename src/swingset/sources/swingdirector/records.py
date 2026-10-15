"""Source vocabulary for SwingDirector score sheets; `project/swingdirector.py` reads it."""

from dataclasses import dataclass

from swingset.model.observations import register_observation_type


@dataclass(frozen=True)
class DirectorRow:
    """One printed bib row. Cells keep the sheet's own text, in column order."""

    bib_raw: str
    heat_raw: str | None
    entry_raw: str
    marks_raw: tuple[str | None, ...]
    tally_raw: tuple[str | None, ...] = ()
    place_raw: str | None = None


@register_observation_type
@dataclass(frozen=True)
class DirectorRoundSheet:
    kind: str
    source_event_ref: str
    source_round_ref: str
    title_raw: str
    contest_name_raw: str
    round_name_raw: str
    role_raw: str | None
    layout: str
    judges_raw: tuple[str, ...]
    tally_headers_raw: tuple[str, ...]
    rows: tuple[DirectorRow, ...]
    pages: tuple[int, ...]
    footer_raw: str


#: The prelim legend printed by every captured sheet. A1-A3 are ranked
#: alternates; the sheet prints no callback decision.
PRELIM_MARKS = frozenset({"Y", "N", "A1", "A2", "A3"})
