"""A captured, finite phase-1 target catalog from retained CDX evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from swingset.fetch.archive import canonical, durable_write
from swingset.fetch.wayback import replay_url

CALENDAR_PATHS = frozenset(
    {"/events/", "/event-list/", "/print-event-list/", "/event-calendar/", "/events-map/"}
)
COUNCIL_PATHS = frozenset(
    {"/activeserverpages/upcomingevents.asp", "/activeserverpages/nonregupcomingevents.asp"}
)


@dataclass(frozen=True)
class Target:
    source: str
    url: str
    parser: str
    timestamp: str | None = None
    digest: str | None = None
    catalog_evidence: str = ""

    @property
    def target_id(self) -> str:
        return hashlib.sha256(canonical([self.source, self.url, self.timestamp])).hexdigest()[:24]

    @property
    def archive_url(self) -> str | None:
        return replay_url(self.url, self.timestamp) if self.timestamp else None


def retained_catalog(repository: Path) -> tuple[Target, ...]:
    evidence = repository / "research/verification/2026-09-12/event-list"
    inputs = [
        (evidence / "asp/r00_web_archive_org.json", "swingdancecouncil"),
        (evidence / "asp3/r05_web_archive_org.json", "wsdc_calendar"),
    ]
    targets: dict[str, Target] = {}
    for path, source in inputs:
        rows = json.loads(path.read_bytes())
        for values in rows[1:]:
            row = dict(zip(rows[0], values, strict=True))
            url = urlsplit(row["original"])
            stamp = row["timestamp"]
            allowed = (
                (url.path.lower().rstrip("/") in COUNCIL_PATHS and "2009" <= stamp[:4] <= "2016")
                if source == "swingdancecouncil"
                else (url.path.lower() in CALENDAR_PATHS and stamp[:4] >= "2016")
            )
            if row.get("statuscode", "200") != "200" or url.query or not allowed:
                continue
            target = Target(
                source,
                row["original"],
                source + ".events",
                stamp,
                row.get("digest"),
                str(path.relative_to(repository)),
            )
            targets[target.target_id] = target
    return tuple(
        sorted(targets.values(), key=lambda item: (item.timestamp or "", item.source, item.url))
    )


def save_catalog(path: Path, targets: tuple[Target, ...]) -> str:
    body = canonical({"version": 1, "targets": [asdict(target) for target in targets]})
    durable_write(path, body)
    return hashlib.sha256(body).hexdigest()


def load_catalog(path: Path) -> tuple[Target, ...]:
    catalog = json.loads(path.read_bytes())
    if catalog.get("version") != 1:
        raise ValueError("unsupported phase1 catalog version")
    result = tuple(Target(**row) for row in catalog["targets"])
    if any(
        target.parser
        not in {
            "swingdancecouncil.events",
            "wsdc_calendar.events",
            "wsdc_newsletter.events",
            "wsdc_newsletter.index",
        }
        for target in result
    ):
        raise ValueError("phase1 catalog contains a non-event-list parser")
    return result
