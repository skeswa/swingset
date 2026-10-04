"""Additional finite-request constraints; existing host/history gates still apply.

It also handles content encodings httpx leaves undecoded. httpx decodes gzip
and deflate itself. It decodes `zstd` only when the `zstandard` package is
installed and `br` only with `brotli`; neither is (adding one would change
the environment and re-parse every snapshot). An unknown encoding passes
through as raw bytes. Wayback replays a capture in its original encoding
whatever the request accepts, so a zstd body can arrive although the client
asks for gzip only. Both readers decode zstd with pyarrow inside a byte limit
and refuse every other encoding left undecoded, so compressed bytes are never
stored as the page.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
import pyarrow as pa

#: Encodings httpx always decodes, plus `identity`.
DECODED_BY_HTTPX = frozenset({"identity", "gzip", "deflate"})
ZSTD = "zstd"
#: A zstd frame's first four bytes.
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
#: The most an unbounded request's zstd body may decode to: the bounded
#: reader's default total (`RequestContext.max_total_bytes`).
MAX_DECODED_BYTES = 64 * 1024 * 1024
UNSUPPORTED = "unsupported content encoding"
UNDECODABLE = "undecodable compressed response"
DECODED_CEILING = "decoded response byte ceiling"


class EncodingRefused(ValueError):
    """The body cannot be decoded within its limit; the reason is the message."""


def encodings(headers: httpx.Headers) -> list[str]:
    """The Content-Encoding values, in the order they were applied."""
    return [
        value.strip().lower()
        for value in headers.get_list("content-encoding", split_commas=True)
        if value.strip()
    ]


def zstd_read(raw: bytes, limit: int) -> bytes:
    """Decode a zstd body, stopping after `limit + 1` bytes so a bomb stays small.

    A truncated frame, trailing bytes or a body that is not zstd is refused.
    """
    try:
        with pa.input_stream(pa.BufferReader(raw), compression="zstd") as stream:
            return bytes(stream.read(max(0, limit) + 1))
    except OSError as error:
        raise EncodingRefused(UNDECODABLE) from error


def zstd_decode(raw: bytes, limit: int) -> bytes:
    """Decode a zstd body to at most `limit` bytes, or refuse it."""
    decoded = zstd_read(raw, limit)
    if len(decoded) > limit:
        raise EncodingRefused(DECODED_CEILING)
    return decoded


def _httpx_decodes_zstd() -> bool:
    """Whether httpx can decode zstd itself: it imports `zstandard` when installed."""
    return importlib.util.find_spec("zstandard") is not None


def undecoded(response: httpx.Response, limit: int) -> httpx.Response:
    """The response with a body httpx left encoded decoded, or `EncodingRefused`.

    Call it after the body is read. A lone `zstd` body is decoded, unless
    httpx decoded it already (`zstandard` installed, no zstd magic left);
    bytes that do not decode are refused. Any other encoding httpx does not
    decode, alone or stacked, is refused. The decoded response drops
    Content-Encoding and Content-Length, as the bounded reader does.
    """
    applied = encodings(response.headers)
    left = [value for value in applied if value not in DECODED_BY_HTTPX]
    if not left:
        return response
    if applied != [ZSTD]:
        raise EncodingRefused(UNSUPPORTED)
    body = response.content
    if not body or (not body.startswith(ZSTD_MAGIC) and _httpx_decodes_zstd()):
        return response
    decoded = zstd_decode(body, limit)
    headers = httpx.Headers(
        [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in {"content-encoding", "content-length"}
        ]
    )
    result = httpx.Response(
        response.status_code,
        headers=headers,
        content=decoded,
        request=response.request,
        extensions={"original_headers": dict(response.headers)},
    )
    return result


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
        # zstd has no streaming decoder here: the wire bytes are kept under
        # the wire ceiling, then decoded at once under the decoded ceiling.
        compressed = bytearray() if encoding == ZSTD else None

        def room() -> int:
            return min(self.max_response_bytes, self.max_total_bytes - self.decoded_bytes)

        try:
            if encoding not in {"identity", "", "gzip", ZSTD}:
                reason = UNSUPPORTED
            elif response.is_stream_consumed:
                # Mock/prebuffered transports have already decoded the payload,
                # except zstd, which httpx cannot decode without `zstandard`.
                raw = response.content
                wire = max(response.num_bytes_downloaded, len(raw))
                if compressed is not None and raw.startswith(ZSTD_MAGIC):
                    raw = zstd_read(raw, room())
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
                    if compressed is not None:
                        compressed.extend(raw)
                    else:
                        remaining = room() - decoded
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
                if compressed is not None and not reason:
                    chunk = zstd_read(bytes(compressed), room())
                    decoded = len(chunk)
                    body.extend(chunk[: max(0, room()) + 1])
                    if (
                        decoded > self.max_response_bytes
                        or self.decoded_bytes + decoded > self.max_total_bytes
                    ):
                        reason = DECODED_CEILING
            if (
                max(wire, decoded) > self.max_response_bytes
                or max(self.wire_bytes + wire, self.decoded_bytes + decoded) > self.max_total_bytes
            ):
                reason = reason or "response byte ceiling"
        except EncodingRefused as refused:
            reason = str(refused)
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
