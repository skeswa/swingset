"""Conservative row witnesses for WDR's non-authoritative updates."""

import sqlite3
from collections import Counter
from dataclasses import asdict

from swingset.fetch.archive import canonical, digest
from swingset.model.observations import decode_payload
from swingset.sources.base import ParseResult
from swingset.sources.records import RoundSheet

from .report import Guard


def wdr_preserves_rows(conn: sqlite3.Connection, watch_id: str, result: ParseResult) -> Guard:
    """Do not infer native row identity to authorize an omission or correction.

    Raw rows must survive verbatim, including duplicate counts and column context.
    A changed row needs explicit review/revocation of its old evidence. Repeated
    snapshots of the same row do not multiply the required count.
    """
    previous: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT payload_json FROM observations WHERE watch_id=? AND kind='round_sheet'",
        (watch_id,),
    ):
        sheet = decode_payload("round_sheet", row[0])
        if isinstance(sheet, RoundSheet):
            previous |= _rows(sheet)
    proposed: Counter[str] = Counter()
    for observation in result.observations:
        if isinstance(observation.payload, RoundSheet):
            proposed.update(_rows(observation.payload))
    missing = sum((previous - proposed).values())
    return Guard(
        "non_authoritative_row_loss",
        not missing,
        f"{missing} previously selected raw rows lack an unchanged witness; WDR has no removal authority",
    )


def _rows(sheet: RoundSheet) -> Counter[str]:
    return Counter(
        digest(
            canonical(
                {
                    "round": sheet.source_round_ref,
                    "contest": sheet.contest_name_raw,
                    "round_name": sheet.round_name_raw,
                    "heading": table.heading_raw,
                    "headers": [asdict(cell) for cell in table.headers],
                    "redacted": table.redacted,
                    "attributes": table.attribute_group,
                    "row": asdict(row),
                }
            )
        )
        for table in sheet.tables
        for row in table.rows
    )
