"""Exact newsletter bodies reviewed as having no registry-event listings.

Review evidence: journal/investigations/2026/newsletter-empty-review-2026-09-13.md.
Unknown or changed bodies never inherit an empty classification.
"""

from __future__ import annotations

import hashlib
import json

# body SHA-256: (extracted pages SHA-256, volume, page count)
REVIEWED_EMPTY = {
    "085674f10a5da1ab09fe968b42eee10a1301a39cc04a7aebce4c173a973d8fc7": (
        "dbfecabdbcc6a8ad187251bfb7789091a19bbac0d390bf84b02a8e0d9329ee03",
        14,
        1,
    ),
    "aafbd00bb65715cc286d65033bb1f8fbb52b1c1f6eac9fea01cc149683b31566": (
        "5c8e6057d75a9122391fac60e8911aa9449b6db9632fbf2e3e6ab3d6fd289f73",
        15,
        1,
    ),
    "b00e313eecfaf55361c87040d3e056d1902664f38e6dded7e37373a91dc9923d": (
        "02ff91b558e9d5eda8d24de983bb86383af7f790389dbf9330b22f4882d80e37",
        16,
        1,
    ),
    "e10a45b1cbafd884f1d9be5a8fba406170f1cd6e57e0f908d9a2655396aa6f90": (
        "f7d578c99180738bac3d08ce0592da0e26e890fe3e3585a882e9b2644a42e18a",
        17,
        1,
    ),
    "59a6bf606025adc6cb8f809124cb3414ebb47f0b84c5f8e64fe66c59221ac759": (
        "dbbe78dbb24476ed45e1301fa6bd51255f8d6c8ae9c46ec566ee7d89d948cd07",
        18,
        1,
    ),
    "793ee2d9e0810bc4cc1983efaeadb5d3c7dd820f8eed08664519af08746c5936": (
        "b77d4dc0e3e369799e290274756a4d47538878d47860648fb6dfdd23a39b56f4",
        19,
        1,
    ),
    "12545410f858740ff9ac6a85852eb8b32bf64767bdc59aaf860c56a3e617dcfd": (
        "1dc8996eb4ae0239659c784d61d8a007aefc9039033c3067c6adf957b1901ea8",
        20,
        3,
    ),
    "b7a224d7799f3eb1ca13ec9307ac1fc2149cccdabe680585d6aa1abf268fc82b": (
        "de3332864772f4152168ae5a8c18e7f5795c8a7fcc460a2542aeb9d184508c3c",
        21,
        2,
    ),
}


def reviewed_empty(body_sha256: str | None, pages: list[str]) -> bool:
    receipt = REVIEWED_EMPTY.get(body_sha256 or "")
    if receipt is None or len(pages) != receipt[2]:
        return False
    digest = hashlib.sha256(
        json.dumps(pages, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return digest == receipt[0]
