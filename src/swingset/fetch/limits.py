"""Additional finite-request constraints; existing host/history gates still apply."""

from __future__ import annotations

import sqlite3
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx


@dataclass
class RequestContext:
    validate: Callable[[sqlite3.Connection, str], str | None]
    deadline: datetime
    monotonic_deadline: float
    max_attempts: int = 8
    max_response_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    admitted_attempts: int = 0
    uncertain_admissions: int = 0
    wire_bytes: int = 0
    decoded_bytes: int = 0
    received: Callable[[httpx.Response, str], None] | None = None

    def check(self, conn: sqlite3.Connection, url: str, now: datetime) -> str | None:
        if now >= self.deadline or time.monotonic() >= self.monotonic_deadline:
            return "request deadline"
        if self.admitted_attempts >= self.max_attempts:
            return "request attempt ceiling"
        if max(self.wire_bytes, self.decoded_bytes) >= self.max_total_bytes:
            return "request byte ceiling"
        return self.validate(conn, url)

    def read(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        form: dict[str, str] | None,
        timeout: float,
    ) -> tuple[httpx.Response, int]:
        """Stream wire and decoded bytes separately; never decompress an unbounded body."""
        request = client.build_request(method, url, headers=headers, data=form, timeout=timeout)
        response = client.send(request, stream=True)
        body = bytearray()
        wire = decoded = 0
        reason = None
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        decoder = zlib.decompressobj(31) if encoding == "gzip" else None
        try:
            if encoding not in {"identity", "", "gzip"}:
                reason = "unsupported content encoding"
            elif response.is_stream_consumed:
                # Mock/prebuffered transports have already decoded the payload.
                raw = response.content
                wire = max(response.num_bytes_downloaded, len(raw))
                decoded = len(raw)
                body.extend(raw[: self.max_response_bytes + 1])
            else:
                for raw in response.iter_raw():
                    wire += len(raw)
                    if (
                        wire > self.max_response_bytes
                        or self.wire_bytes + wire > self.max_total_bytes
                    ):
                        reason = "compressed response byte ceiling"
                        break
                    remaining = (
                        min(self.max_response_bytes, self.max_total_bytes - self.decoded_bytes)
                        - decoded
                    )
                    chunk = decoder.decompress(raw, max(1, remaining + 1)) if decoder else raw
                    decoded += len(chunk)
                    body.extend(chunk[: max(0, remaining + 1)])
                    if (
                        decoded > self.max_response_bytes
                        or self.decoded_bytes + decoded > self.max_total_bytes
                    ):
                        reason = "decoded response byte ceiling"
                        break
                    if time.monotonic() >= self.monotonic_deadline:
                        reason = "response deadline"
                        break
                if decoder and not reason and (not decoder.eof or decoder.unused_data):
                    reason = "incomplete or trailing compressed response"
            if (
                max(wire, decoded) > self.max_response_bytes
                or max(self.wire_bytes + wire, self.decoded_bytes + decoded) > self.max_total_bytes
            ):
                reason = reason or "response byte ceiling"
        except (zlib.error, httpx.HTTPError):
            reason = "incomplete response transport"
        finally:
            try:
                response.close()
            finally:
                self.wire_bytes += wire
                self.decoded_bytes += decoded
        result_headers = dict(response.headers)
        result_headers.pop("content-encoding", None)
        result_headers.pop("content-length", None)
        result = httpx.Response(
            response.status_code, headers=result_headers, content=bytes(body), request=request
        )
        result.extensions.update(
            {
                "swingset_limit": reason,
                "wire_bytes": wire,
                "decoded_bytes": decoded,
                "original_headers": dict(response.headers),
            }
        )
        return result, max(wire, decoded)
