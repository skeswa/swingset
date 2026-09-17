"""Build a new exact DCN fixture packet from pinned, reviewed schema-14 code.

This offline builder never imports the acquisition runtime or opens production
state. Its explicit source transformations preserve the old retained packet.
The resulting new driver, closure, manifest and preparer require independent
review before the coordinator prepares an actual execution gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "journal/evidence/admission/fixture-exception-2026-09-16"
PROPOSAL = ROOT / "journal/evidence/admission/fixture-review-2026-09-17/dcn-index-proposal.json"
DRIVER_SHA = "81357b33c8862bdc4bdd2d84f2479a7913a9d783b00b435910cc2328bdcf69f7"
CLOSURE_SHA = "f8e0557026bc3bcfbd06624071bbc98ab7c7a46983fd6e3117d34140d6b16db3"
PROPOSAL_SHA = "8bff910bc1db8eb81685dd0d028d882692bb7208b5358b19536faf5893a45d2b"
OLD_MANIFEST_SHA = "c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93"
DRIVER_NAME = "dcn-index-fixture-h13-001.py"
APPROVAL = "approved_exact_dcn_index_fixture_only"


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def substitute(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise ValueError("reviewed source anchor differs: " + before[:80])
    return text.replace(before, after, 1)


TRANSPORT_CLASS = '''class DispatchReceiptTransport(httpx.BaseTransport):
    """Timestamp handoff to the underlying transport, not an inferred wire time."""

    def __init__(self, runner: Any, inner: httpx.BaseTransport) -> None:
        self.runner, self.inner = runner, inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        current = self.runner.receipt["requests"][-1]
        current["transport_dispatched_at"] = self.runner.clock.now().isoformat()
        current["transport_dispatched_elapsed_seconds"] = self.runner.monotonic() - self.runner.monotonic_origin
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.inner.close()


'''

COMPLETION_METHOD = """    def _anchor_completion(self, request: dict[str, Any]) -> None:
        # The stream has closed (or failed) before this conservative boundary.
        # Persist before artifact writes and admission settlement: subsequent
        # operations inherit the floor even if receipt retention then fails.
        completed = self.clock.now()
        elapsed = self.monotonic()
        request["exchange_completed_at"] = completed.isoformat()
        spacing = max(10, self.crawl_delay, self.config.host(HOST).min_gap_seconds)
        self.next_dispatch_monotonic = elapsed + spacing
        request["exchange_completed_elapsed_seconds"] = elapsed - self.monotonic_origin
        request["next_dispatch_not_before_elapsed_seconds"] = self.next_dispatch_monotonic - self.monotonic_origin
        until = completed + timedelta(seconds=spacing)
        row = self.conn.execute("SELECT next_allowed_at FROM hosts WHERE host=?", (HOST,)).fetchone()
        if row and row[0]:
            until = max(until, datetime.fromisoformat(row[0]))
        self.conn.execute("UPDATE hosts SET next_allowed_at=? WHERE host=?", (until.isoformat(), HOST))
        self.conn.commit()
        request["next_dispatch_not_before"] = until.isoformat()
        request["spacing_basis"] = "closed_exchange_completion_floor"

    def _wait_completion_floor(self) -> None:
        # UTC remains the shared durable gate. A process-local monotonic floor
        # independently prevents forward wall-clock adjustments shortening gaps.
        while (remaining := self.next_dispatch_monotonic - self.monotonic()) > 0:
            self._check()
            self.clock.sleep(min(1, remaining, (self.deadline - self.clock.now()).total_seconds()))

"""


def build(destination: Path) -> dict[str, object]:
    if destination.exists():
        raise ValueError("new packet destination must not exist")
    old_driver = BASE / "fixture-exception-h13-002.py"
    helper_root = BASE / "helper-closure"
    if (
        digest(old_driver.read_bytes()) != DRIVER_SHA
        or digest((helper_root / "closure.json").read_bytes()) != CLOSURE_SHA
    ):
        raise ValueError("reviewed base packet pins changed")
    closure = json.loads((helper_root / "closure.json").read_bytes())
    if any(p.is_symlink() for p in helper_root.rglob("*")):
        raise ValueError("reviewed base closure contains a symlink")
    actual = {p.relative_to(helper_root).as_posix() for p in helper_root.rglob("*") if p.is_file()}
    if actual != set(closure["files"]) | {"closure.json"}:
        raise ValueError("reviewed base closure inventory changed")
    for name, expected in closure["files"].items():
        if digest((helper_root / name).read_bytes()) != expected:
            raise ValueError("reviewed base helper changed: " + name)
    if digest(PROPOSAL.read_bytes()) != PROPOSAL_SHA:
        raise ValueError("exact approved proposal differs")
    proposed = json.loads(PROPOSAL.read_bytes())
    target = proposed["target"]
    evidence = ROOT / target["evidence_path"]
    if digest(evidence.read_bytes()) != target["evidence_sha256"] or target[
        "cdx_row"
    ] not in json.loads(evidence.read_bytes()):
        raise ValueError("retained target CDX evidence differs")
    manifest = {
        "version": 1,
        "approval_reference": "journal/decisions/0060-approve-exact-dcn-index-fixture.md",
        "limits": {
            "archived_body_targets": 1,
            "total_http_requests_including_redirects_and_robots": 5,
            "max_archive_redirects_per_body": 3,
            "cdx_requests": 0,
            "max_elapsed_seconds": 900,
            "max_received_bytes_per_response": 8388608,
            "max_total_received_bytes": 16777216,
            "minimum_gap_seconds": 10,
            "max_in_flight": 1,
            "shared_archive_daily_request_budget": 200,
            "source_origin_requests": 0,
            "automatic_retries": 0,
            "automatic_alternate_captures": 0,
            "automatic_child_requests": 0,
        },
        "targets": [
            {
                "id": target["id"],
                "source": "dcn",
                "original_url": target["original_url"],
                "capture_timestamp": target["capture_timestamp"],
                "replay_url": target["replay_url"],
                "evidence": {
                    "path": "evidence/cdx-page0.json",
                    "row": target["cdx_row"],
                    "sha256": target["evidence_sha256"],
                },
            }
        ],
        "metadata_queries": [],
        "quarantine": proposed["quarantine"],
    }
    manifest_sha = digest(canonical(manifest))
    exception = (helper_root / "fixture_helpers/fixture_exception.py").read_text()
    exception = substitute(exception, OLD_MANIFEST_SHA, manifest_sha)
    exception = substitute(exception, "approved_new_source_fixture_exception_only", APPROVAL)
    transport = (helper_root / "fixture_helpers/fixture_transport.py").read_text()
    transport = substitute(
        transport,
        "import sqlite3\n",
        "import sqlite3\nimport time\nfrom collections.abc import Callable\n",
    )
    transport = substitute(
        transport,
        "        transport: httpx.BaseTransport | None = None,\n",
        "        transport: httpx.BaseTransport | None = None,\n        monotonic: Callable[[], float] | None = None,\n",
    )
    transport = substitute(
        transport,
        "        self.conn, self.clock, self.state, self.output = connection, clock, state, output\n",
        "        self.conn, self.clock, self.state, self.output = connection, clock, state, output\n"
        "        self.monotonic = monotonic or time.monotonic\n"
        "        self.monotonic_origin = self.monotonic()\n"
        "        self.next_dispatch_monotonic = self.monotonic_origin + policy.min_gap_seconds\n",
    )
    transport = substitute(
        transport,
        "        self._check()\n        while True:\n",
        "        self._check()\n        self._wait_completion_floor()\n        while True:\n",
    )
    transport = substitute(
        transport, "class FixtureRunner:", TRANSPORT_CLASS + "class FixtureRunner:"
    )
    start = transport.index('        query = manifest["metadata_queries"][0]')
    end = transport.index('        self.limits = manifest["limits"]', start)
    transport = (
        transport[:start]
        + """        self.allowed_urls = frozenset(
            [target["replay_url"] for target in manifest["targets"]] + [f"https://{HOST}/robots.txt"]
        )
"""
        + transport[end:]
    )
    transport = substitute(
        transport,
        "    def _exchange(self, client: httpx.Client, url: str, purpose: str) -> httpx.Response:\n",
        COMPLETION_METHOD
        + "    def _exchange(self, client: httpx.Client, url: str, purpose: str) -> httpx.Response:\n",
    )
    transport = substitute(
        transport,
        "        finally:\n            sha = digest(bytes(body))",
        "        finally:\n            self._anchor_completion(request)\n            sha = digest(bytes(body))",
    )
    transport = substitute(
        transport,
        "                transport=self.transport,",
        "                transport=DispatchReceiptTransport(self, self.transport or httpx.HTTPTransport(trust_env=False)),",
    )
    transport = substitute(
        transport,
        "                and datetime.fromisoformat(row[1]) + timedelta(days=1) > self.clock.now()",
        "                and datetime.fromisoformat(row[1]) <= self.clock.now()\n                and datetime.fromisoformat(row[1]) + timedelta(days=1) > self.clock.now()",
    )
    start = transport.index('                query = self.manifest["metadata_queries"][0]')
    end = transport.index(
        '                self.receipt["status"] = "captured_pending_independent_review"', start
    )
    transport = transport[:start] + transport[end:]
    files = {
        "fixture_helpers/__init__.py": (helper_root / "fixture_helpers/__init__.py").read_bytes(),
        "fixture_helpers/fixture_exception.py": exception.encode(),
        "fixture_helpers/fixture_transport.py": transport.encode(),
        "evidence/cdx-page0.json": evidence.read_bytes(),
        "proposal.json": canonical(manifest),
    }
    new_closure = canonical(
        {
            "format": "fixture-helper-closure-v1",
            "files": {name: digest(body) for name, body in sorted(files.items())},
        }
    )
    driver = substitute(old_driver.read_text(), CLOSURE_SHA, digest(new_closure))
    driver = substitute(
        driver,
        "H13 lifecycle adapter for the existing exact, still-unapproved fixture runner.",
        "H13 lifecycle adapter for the separately approved exact DCN index fixture.",
    )
    driver = substitute(
        driver,
        "No new transport/allowlist, no parser activation. Default original dry-run is pure.",
        "Exact single-body allowlist and completion spacing; no parser activation. Dry-run is pure.",
    )
    prepare_source = (Path(__file__).parent / "prepare_fixture_exception.py").read_text()
    preparer = substitute(
        prepare_source, 'DRIVER = "fixture-exception-h13-002.py"', f'DRIVER = "{DRIVER_NAME}"'
    )
    preparer = substitute(preparer, DRIVER_SHA, digest(driver.encode()))
    preparer = substitute(preparer, CLOSURE_SHA, digest(new_closure))
    preparer = substitute(preparer, OLD_MANIFEST_SHA, manifest_sha)
    preparer = substitute(preparer, "approved_new_source_fixture_exception_only", APPROVAL)
    destination.mkdir(parents=True)
    for name, body in {
        **{f"helper-closure/{name}": body for name, body in files.items()},
        "helper-closure/closure.json": new_closure,
        DRIVER_NAME: driver.encode(),
        "prepare_fixture_exception.py": preparer.encode(),
    }.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    receipt = {
        "format": "exact-dcn-fixture-build-v1",
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "base_driver_sha256": DRIVER_SHA,
        "base_closure_sha256": CLOSURE_SHA,
        "approved_proposal_sha256": PROPOSAL_SHA,
        "manifest_canonical_sha256": manifest_sha,
        "driver_sha256": digest(driver.encode()),
        "helper_manifest_sha256": digest(new_closure),
        "preparer_input_sha256": digest(prepare_source.encode()),
        "files": {
            p.relative_to(destination).as_posix(): digest(p.read_bytes())
            for p in sorted(destination.rglob("*"))
            if p.is_file()
        },
        "network_requests": 0,
        "executed": False,
    }
    (destination / "build-receipt.json").write_bytes(canonical(receipt) + b"\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(json.dumps(build(parser.parse_args().destination), indent=2))
