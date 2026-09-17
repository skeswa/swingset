"""Exact retained PDF colour controls cannot classify other bytes or rows."""

from pathlib import Path

import pytest

from swingset.sources.base import ParseContext
from swingset.sources.wsdc_newsletter.adapter import EventsPage
from swingset.sources.wsdc_newsletter.colour_review import reviewed_colours

FIXTURES = Path("src/swingset/sources/wsdc_newsletter/fixtures")


def parse(extract):
    page = EventsPage()
    return page.parse(
        extract,
        ParseContext(
            "snapshot",
            "watch",
            "https://example.test/",
            "wsdc_newsletter",
            page.kind,
            None,
            "2026-09-17T00:00:00+00:00",
        ),
    )


@pytest.mark.parametrize(
    "volume, labels",
    [
        (6, {"SWING IN CAPITAL": "Member Activity", "GO WEST SWING FEST": "Member Activity"}),
        (9, {"AVIGNON CITY SWING *": "Member Activity"}),
        (13, {"BY-TOWN ONTARIO OPEN (BTO)": "Trial Event"}),
        (25, {"Swingvasion": "Trial Event"}),
    ],
)
def test_actual_pdf_named_colours_preserve_other_rows_and_findings(volume, labels):
    extract = EventsPage().extract((FIXTURES / f"newsletter-vol{volume}.pdf").read_bytes())
    current = parse(extract)
    # Exact pre-review fallback, using the same real PDF text without its body binding.
    previous = parse(extract["pages"])
    assert len(current.observations) == len(previous.observations)
    changed = []
    for new, old in zip(current.observations, previous.observations, strict=True):
        row, prior = new.payload, old.payload
        if row.name_raw not in labels:
            assert row == prior
            continue
        changed.append(row.name_raw)
        assert row.event_type_raw == labels[row.name_raw]
        assert row.row_classes == (
            "newsletter_status_colour_reviewed",
            "newsletter_member_activity"
            if row.event_type_raw == "Member Activity"
            else "newsletter_trial_event",
        )
        assert (row.name_raw, row.start_date_raw, row.end_date_raw, row.location_raw) == (
            prior.name_raw,
            prior.start_date_raw,
            prior.end_date_raw,
            prior.location_raw,
        )
    assert set(changed) == set(labels)
    warning = next(w for w in current.warnings if w.code == "newsletter_colour_unverified")
    assert warning.evidence["colour_recovered"] is False
    assert warning.evidence["reviewed_colour_rows"] == len(labels)
    assert [w for w in current.warnings if w.code != warning.code] == [
        w for w in previous.warnings if w.code != warning.code
    ]
    assert current.legitimate_empty == previous.legitimate_empty


@pytest.mark.parametrize("volume", [6, 9, 13, 25])
def test_changed_body_or_extract_loses_entire_colour_disposition(volume):
    body = (FIXTURES / f"newsletter-vol{volume}.pdf").read_bytes()
    original = EventsPage().extract(body)
    assert reviewed_colours(original["body_sha256"], original["pages"])
    # PDF comments after EOF change the evidence body without changing its page text.
    changed_body = EventsPage().extract(body + b"\n% changed evidence\n")
    assert changed_body["pages"] == original["pages"]
    assert not reviewed_colours(changed_body["body_sha256"], changed_body["pages"])
    changed_pages = original["pages"].copy()
    changed_pages[0] += "\nChanged interpretation"
    assert not reviewed_colours(original["body_sha256"], changed_pages)
    assert all(o.payload.event_type_raw == "registry" for o in parse(changed_body).observations)


def test_legacy_missing_wrong_body_binding_never_recovers_colour():
    extract = EventsPage().extract((FIXTURES / "newsletter-vol6.pdf").read_bytes())
    for digest in (None, "", "f" * 64):
        assert not reviewed_colours(digest, extract["pages"])
    review = reviewed_colours(extract["body_sha256"], extract["pages"])
    assert (2, "SWING IN CAPITAL", "2018-04-20", "2018-04-22") not in review
    assert (1, "SWING IN CAPITAL", "2019-04-20", "2019-04-22") not in review
    assert (1, "Swing in Capital", "2018-04-20", "2018-04-22") not in review
    review.clear()
    assert reviewed_colours(extract["body_sha256"], extract["pages"])


def test_unreviewed_legend_remains_fallback_and_real_warning():
    result = parse(
        [
            "Upcoming Registry Events\nTrial Events are shown in gray\n"
            "• Swingvasion\n  March 10 - 12, 2023"
        ]
    )
    assert result.observations[0].payload.event_type_raw == "registry"
    assert result.observations[0].payload.row_classes == ("newsletter_status_colour_unverified",)
    assert result.warnings[0].evidence == {
        "snapshot_id": "snapshot",
        "fallback": "registry",
        "colour_recovered": False,
    }


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize(
    "volume,name,status",
    [(6, "SWING IN CAPITAL", "unknown"), (25, "Swingvasion", "trial")],
)
def test_real_colour_rows_project_conservatively_and_preserve_registry_evidence(
    tmp_path, held, volume, name, status
):
    from datetime import datetime

    from swingset.model.observations import encode_payload
    from swingset.project.history import reconcile_history
    from swingset.project.registry import project_dancer
    from swingset.project.writer import replace_scope
    from swingset.sources.records import DancerLookup, RegistryPlacement
    from swingset.state.db import open_database

    result = parse(EventsPage().extract((FIXTURES / f"newsletter-vol{volume}.pdf").read_bytes()))
    row = next(o.payload for o in result.observations if o.payload.name_raw == name)
    now = "2026-09-17T00:00:00+00:00"
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (now,))

        def evidence(payload, source, scope, ref, snapshot):
            conn.execute(
                "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES (?,?,'index','GET','https://example.test/','events','live')",
                (snapshot, source),
            )
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET','https://example.test/',?,200,1,1,'run','Ok')",
                (snapshot, snapshot, now),
            )
            conn.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,0,'1','8',?)",
                (snapshot, snapshot, snapshot, payload.kind, scope, ref, encode_payload(payload)),
            )

        if held:
            lookup = DancerLookup(
                "dancer_lookup",
                "found",
                1,
                1,
                first_name="Test",
                last_name="Dancer",
                primary_role_raw="L",
                placements=(
                    RegistryPlacement(
                        "leader",
                        "NOV",
                        "7",
                        name,
                        datetime.fromisoformat(row.end_date_raw).strftime("%B %Y"),
                        "1",
                        1,
                        "wcs",
                    ),
                ),
            )
            evidence(lookup, "wsdc_registry", "dancer", "1", "registry")
            replace_scope(
                conn,
                scope_kind="dancer",
                scope_id="1",
                projection=project_dancer(conn, "1", now, "run"),
                run_id="run",
                projected_at=now,
            )
            reconcile_history(conn, now=now, run_id="run")
        evidence(row, "wsdc_newsletter", "calendar", "wsdc-history", "newsletter")
        aliases = (
            b""
            if held
            else f"printed_name,series_id,source,note\n{name},listed-example,newsletter,test fixture\n".encode()
        )
        reconcile_history(conn, now=now, run_id="run", aliases=aliases)
        events = conn.execute("SELECT held,wsdc_status,start_date,end_date FROM events").fetchall()
        assert len(events) == 1
        assert tuple(events[0]) == (
            "held" if held else "listed",
            "registry" if held else status,
            row.start_date_raw,
            row.end_date_raw,
        )
