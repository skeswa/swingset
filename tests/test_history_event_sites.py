"""Event-site queries and retained CDX hits: exact evidence, never acquisition.

Proposals are an offline tool (`tests/test_event_site_review_tool.py`).
"""

import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_platform_backfill import EVENT, accept
from test_platform_backfill import fixture as fixture

from swingset.fetch.archive import Archive, canonical, digest, durable_write
from swingset.history.event_sites import Site, query_urls, retained_hits

SITE = Site(EVENT.event_id, 2019, "https://dance.example/festival/")
PDF = "https://dance.example/festival/2019-finals.pdf"


@pytest.fixture
def sites(fixture):
    fixture.conn.execute("UPDATE events SET website=?", (SITE.website,))
    accept(fixture)
    return fixture


def retained(f, *, year=2019, mime="application/pdf", urls=(PDF,), bad_filter=False):
    query_url = next(
        url
        for url in query_urls(SITE)
        if parse_qs(urlsplit(url).query)["from"] == [str(year)]
        and "mimetype:" + mime in parse_qs(urlsplit(url).query)["filter"]
    )
    if bad_filter:
        query_url = query_url.replace("mimetype%3Aapplication%2Fpdf", "mimetype%3Atext%2Fplain")
    identifier = digest((query_url + repr(urls)).encode())
    body = canonical(
        [
            ["timestamp", "original", "digest", "mimetype", "length"],
            *[
                [f"{year}0601000000", url, f"digest-{index}", mime, "100"]
                for index, url in enumerate(urls)
            ],
        ]
    )
    archive = Archive(f.db.state_dir)
    body_sha = archive.store_body(body)
    receipt = {
        "query_id": identifier,
        "page": "0",
        "url": query_url,
        "fetched_at": f.clock.now().isoformat(),
        "http_status": 200,
        "headers": {},
        "body_sha256": body_sha,
    }
    durable_write(f.db.state_dir / "archive-cdx" / identifier / "0.json", canonical(receipt))
    f.conn.execute(
        "INSERT INTO archive_queries VALUES (?,?,?, ?,1,1,?,?)",
        (
            identifier,
            "event_sites",
            SITE.prefix,
            year,
            f.clock.now().isoformat(),
            f.clock.now().isoformat(),
        ),
    )
    f.conn.executemany(
        "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,?)",
        [
            (
                "event_sites",
                url,
                f"{year}0601000000",
                f"digest-{index}",
                mime,
                100,
                f.clock.now().isoformat(),
                identifier,
            )
            for index, url in enumerate(urls)
        ],
    )
    return identifier, body_sha


def test_query_specs_are_site_scoped_filtered_and_only_event_year_plus_next():
    urls = query_urls(SITE)
    assert len(urls) == 4
    params = [parse_qs(urlsplit(url).query) for url in urls]
    assert {tuple(row["from"]) for row in params} == {("2019",), ("2020",)}
    assert all(row["from"] == row["to"] and row["url"] == [SITE.prefix] for row in params)
    assert all("statuscode:200" in row["filter"] for row in params)
    assert all(
        any(value.startswith("original:") for value in row["filter"])
        for row in params
        if "mimetype:text/html" in row["filter"]
    )
    assert not query_urls(replace(SITE, year=2009))
    assert not query_urls(replace(SITE, website="https://untrusted@dance.example/"))


@pytest.mark.parametrize(
    "url",
    [
        "https://[broken/results/",
        "https://dance.example:bad/festival/",
        "https://dance.example:444/festival/",
    ],
)
def test_malformed_or_nonstandard_site_locator_is_not_a_query_target(url):
    assert not query_urls(replace(SITE, website=url))


@pytest.mark.parametrize(
    "change",
    [
        lambda url: url.replace("web.archive.org", "user@web.archive.org"),
        lambda url: url.replace("web.archive.org", "web.archive.org:444"),
        lambda url: url + "#fragment",
        lambda url: url.replace("output=json", "output=xml"),
        lambda url: url.replace("collapse=digest", "collapse=urlkey"),
        lambda url: url.replace(
            "fl=timestamp%2Coriginal%2Cdigest%2Cmimetype%2Clength", "fl=original"
        ),
    ],
)
def test_receipt_must_match_exact_supported_cdx_endpoint_and_projection(sites, change):
    identifier, _ = retained(sites)
    path = sites.db.state_dir / "archive-cdx" / identifier / "0.json"
    receipt = json.loads(path.read_bytes())
    receipt["url"] = change(receipt["url"])
    durable_write(path, canonical(receipt))
    with pytest.raises(ValueError):
        retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
