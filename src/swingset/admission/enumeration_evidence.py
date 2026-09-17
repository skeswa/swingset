"""Verify a retained enumeration's content address within one evidence budget."""

from __future__ import annotations

import json
from typing import Any

from swingset.fetch.archive import canonical, digest
from swingset.schedule.event_evidence import request, request_id

from .page_evidence import Session


def members(
    session: Session,
    *,
    enumeration_id: str,
    source: str,
    source_ref: str,
    limit: int = 128,
) -> list[dict[str, Any]]:
    """No parent artifact verdict or completeness claim follows from this check."""
    headers = session.read(
        "SELECT source,source_ref,predecessor_id,generation_id,parent_support_json,pagination,membership_digest "
        "FROM source_event_enumerations WHERE enumeration_id=?",
        (enumeration_id,),
        (
            "source",
            "source_ref",
            "predecessor_id",
            "generation_id",
            "parent_support_json",
            "pagination",
            "membership_digest",
        ),
        cap=1,
    )
    if not headers or (headers[0]["source"], headers[0]["source_ref"]) != (source, source_ref):
        raise ValueError("event_enumeration_identity_changed")
    header = headers[0]
    rows = session.read(
        "SELECT request_id,request_json,support_json,first_known_at FROM source_event_enumeration_members "
        "WHERE enumeration_id=? ORDER BY request_id",
        (enumeration_id,),
        ("request_id", "request_json", "support_json", "first_known_at"),
        cap=min(limit, session.limits.rows),
    )
    decoded = [
        {
            "request_id": row["request_id"],
            "request": json.loads(row["request_json"]),
            "support": json.loads(row["support_json"]),
            "first_known_at": row["first_known_at"],
        }
        for row in rows
    ]
    content = {
        "source": source,
        "source_ref": source_ref,
        "predecessor": header["predecessor_id"],
        "generation_id": header["generation_id"],
        "parents": json.loads(header["parent_support_json"]),
        "members": decoded,
        "pagination": header["pagination"],
    }
    if (
        "enumeration_" + digest(canonical(content)) != enumeration_id
        or digest(canonical([row["request_id"] for row in decoded])) != header["membership_digest"]
    ):
        raise ValueError("event_enumeration_content_changed")
    for member in decoded:
        page = member["request"]
        if (
            page != request(source, page["method"], page["url"], page.get("form"))
            or request_id(page) != member["request_id"]
        ):
            raise ValueError("event_request_identity_changed")
    return decoded
