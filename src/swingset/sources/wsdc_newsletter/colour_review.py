"""Title-specific colour readings from exact retained newsletter bodies.

These are reviewed dispositions, not a general PDF colour interpreter. The
body and extracted pages must both match before a named, dated row can use one.
See journal/investigations/2026/newsletter-colour-review-2026-09-17.md.
"""

from __future__ import annotations

import hashlib
import json

RowKey = tuple[int, str, str, str]

# body SHA-256: (complete extracted-pages SHA-256, reviewed row labels)
_REVIEWED: dict[str, tuple[str, dict[RowKey, str]]] = {
    "008aaf4c693387eae071890592628e00e16476358268501bb436345acae9c987": (
        "750849a91c1a30cd8c0713e5fad8bfd254ad5ceb78469e3dc7dfcac59d8cf1a2",
        {
            (1, "SWING IN CAPITAL", "2018-04-20", "2018-04-22"): "Member Activity",
            (1, "GO WEST SWING FEST", "2018-04-27", "2018-04-29"): "Member Activity",
        },
    ),
    "478d7ddb5edd0e23c6ddb529d429fecde0468456a42093745939e7bcb5f9b998": (
        "8215e3cbf38039671e8f29f8d4e559fc2b40f603f3c487ae4d0be978e980df0e",
        {(1, "AVIGNON CITY SWING *", "2019-01-18", "2019-01-20"): "Member Activity"},
    ),
    "220a0f22f0f6bd1646ca42cd7e4c96ac38ca51d59e87836074d582deae692889": (
        "363e3fd892da4395625ae51b3ac153d165f8990739f232f7d980437bca8ca707",
        {(1, "BY-TOWN ONTARIO OPEN (BTO)", "2020-02-07", "2020-02-09"): "Trial Event"},
    ),
    "68c1360eb9f6a4bc487f4f08ea3f70fbd09e80cb1958cf72006f5a0c12803829": (
        "c64299b5667762d11c4f5e9a7989408a0393fb2324a8d45217113ac865b73127",
        {(2, "Swingvasion", "2023-03-10", "2023-03-12"): "Trial Event"},
    ),
}


def reviewed_colours(body_sha256: str | None, pages: list[str]) -> dict[RowKey, str]:
    review = _REVIEWED.get(body_sha256 or "")
    if review is None:
        return {}
    digest = hashlib.sha256(
        json.dumps(pages, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return review[1].copy() if digest == review[0] else {}
