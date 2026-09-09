"""Classify before changing host state, including expected unpublished URLs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

import httpx

from swingset.sources.base import PageKind


class Outcome(StrEnum):
    OK = "Ok"
    NOT_MODIFIED = "NotModified"
    EXPECTED_UNAVAILABLE = "ExpectedUnavailable"
    GONE = "Gone"
    THROTTLED = "Throttled"
    BLOCKED = "Blocked"
    SERVER_ERROR = "ServerError"
    REDIRECT = "Redirect"
    INVALID = "Invalid"


@dataclass(frozen=True)
class Classification:
    outcome: Outcome
    retry_after: float | None = None


def retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = (date - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(60, seconds)


def classify(
    response: httpx.Response | Exception, page_kind: PageKind, watch: object, now: datetime
) -> Classification:
    if isinstance(response, Exception):
        return Classification(Outcome.SERVER_ERROR)
    body = response.content.lower()
    if any(marker in body for marker in (b"cf-chl", b"just a moment", b"challenge-platform")):
        return Classification(Outcome.BLOCKED)
    code = response.status_code
    if page_kind.kind == "wsdc_registry.dancer" and code == 404:
        from swingset.sources.wsdc_registry.adapter import is_verified_miss

        # A verified absent lookup is successful evidence, not a gone endpoint.
        if is_verified_miss(response.content, code):
            return Classification(Outcome.OK)
        return Classification(Outcome.INVALID)
    if code in page_kind.expected_statuses(watch):
        return Classification(Outcome.EXPECTED_UNAVAILABLE)
    if code == 304:
        return Classification(Outcome.NOT_MODIFIED)
    if code == 200:
        return Classification(Outcome.OK)
    if code in (301, 302, 303, 307, 308):
        return Classification(Outcome.REDIRECT)
    if code in (429, 503):
        return Classification(
            Outcome.THROTTLED, retry_after(response.headers.get("retry-after"), now)
        )
    if code in (401, 403):
        return Classification(Outcome.BLOCKED)
    if code in (404, 410):
        return Classification(Outcome.GONE)
    if code >= 500:
        return Classification(Outcome.SERVER_ERROR)
    return Classification(Outcome.INVALID)
