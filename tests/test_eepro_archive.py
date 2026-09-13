import hashlib
import json
from pathlib import Path

from swingset.sources.base import ParseContext
from swingset.sources.eepro import AutoIndexPage

FIXTURES = Path(__file__).parent / "fixtures" / "sources" / "eepro-archive"


def test_retained_directory_preserves_every_file_despite_truncated_labels() -> None:
    metadata = json.loads((FIXTURES / "freedomswing2019.json").read_text())
    body = (FIXTURES / "freedomswing2019.html").read_bytes()
    assert hashlib.sha256(body).hexdigest() == metadata["body_sha256"]
    assert len(body) == metadata["body_bytes"]
    page = AutoIndexPage()
    result = page.parse(
        page.extract(body),
        ParseContext(
            "retained-snapshot",
            "memory-only",
            metadata["url"],
            "eepro",
            page.kind,
            "eepro:freedomswing2019",
            metadata["captured_at"],
        ),
    )
    names = [
        "allamericanfinals.html",
        "allamericanprelims.html",
        "jackandjillfinals.html",
        "jackandjillprelimssemis.html",
        "proamjj.html",
        "proamstrictly.html",
        "strictlyswingfinals.html",
    ]
    assert [row.payload.name for row in result.observations] == names
    assert [row.payload.url for row in result.observations] == [
        metadata["url"] + name for name in names
    ]
    assert result.observations[3].payload.modified_raw == "2019-01-20 00:06"
    assert result.observations[3].payload.size_raw == "80K"
    assert result.observations[6].payload.size_raw == "12K"
    # These are in-memory proposals only: no scheduler, DB, or fetcher runs.
    assert [watch.url for watch in result.watches] == [metadata["url"] + name for name in names]
    assert all(watch.source_ref == "eepro:freedomswing2019" for watch in result.watches)
    assert all(watch.archive_url is None for watch in result.watches)


def test_directory_link_target_controls_file_type_and_full_name() -> None:
    body = b"""<table>
    <tr><td><a href="long-results.pdf">long-res..&gt;</a></td></tr>
    <tr><td><a href="round.htm?download=1">shortened..&gt;</a></td></tr>
    <tr><td><a href="?sort=name">misleading.html</a></td></tr>
    <tr><td><a href="/results/">Parent Directory</a></td></tr>
    </table>"""
    rows = AutoIndexPage().extract(body)
    assert [(row["name"], row["href"]) for row in rows] == [
        ("long-results.pdf", "long-results.pdf"),
        ("round.htm", "round.htm?download=1"),
    ]
