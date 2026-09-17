"""Build an exact metadata-only Archive lookup packet; never authorize or execute it.

Uses the complete reviewed runtime003 inventory and its maintained fixture helpers.
A copied H13 wrapper is adapted explicitly for schema28. Existing retained packets
and the frozen runtime remain immutable; every result goes in a new directory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
SOURCE_SHA = "60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6"
PROPOSAL = ROOT / "journal/evidence/admission/dcn-results-locator-proposal-2026-09-17/proposal.json"
PROPOSAL_SHA = "c982f5124b396783eff8b4565600358127667e85f51a96a94255190e4a26734c"
OLD_DRIVER = (
    ROOT / "journal/evidence/admission/fixture-exception-2026-09-16/fixture-exception-h13-002.py"
)
OLD_DRIVER_SHA = "81357b33c8862bdc4bdd2d84f2479a7913a9d783b00b435910cc2328bdcf69f7"
OLD_CLOSURE_SHA = "f8e0557026bc3bcfbd06624071bbc98ab7c7a46983fd6e3117d34140d6b16db3"
OLD_SOURCE = "/nix/store/z689qy41inndill3d92ym8im852x3649-source"
OLD_SOURCE_SHA = "0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb"
OLD_MANIFEST_SHA = "c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93"
DRIVER_NAME = "dcn-results-lookup-h13-001.py"
APPROVAL = "approved_exact_dcn_results_cdx_only"


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sub(text: str, before: str, after: str, count: int = 1) -> str:
    if text.count(before) != count:
        raise ValueError("reviewed source anchor differs: " + before[:80])
    return text.replace(before, after)


def verified_source(source: Path) -> None:
    receipt = source / "extension-source.json"
    if digest(receipt.read_bytes()) != SOURCE_SHA:
        raise ValueError("runtime003 source receipt differs")
    files = json.loads(receipt.read_bytes())["files"]
    children = list(source.rglob("*"))
    if not files or any(path.is_symlink() for path in children):
        raise ValueError("empty or linked runtime inventory")
    actual = {
        p.relative_to(source).as_posix()
        for p in children
        if p.is_file() and "__pycache__" not in p.parts and p != receipt
    }
    if actual != set(files):
        raise ValueError("runtime inventory differs")
    for name, expected in files.items():
        path = source / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or not path.resolve().is_relative_to(source)
        ):
            raise ValueError("runtime inventory escapes root")
        if digest(path.read_bytes()) != (
            expected["sha256"] if isinstance(expected, dict) else expected
        ):
            raise ValueError("runtime bytes differ: " + name)


def literal_template(source: str, name: str) -> str:
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            result = ast.literal_eval(statement.value)
            if isinstance(result, str):
                return result
    raise ValueError("missing reviewed literal template: " + name)


def build(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    if destination.exists():
        raise ValueError("packet must be new")
    verified_source(source)
    if (
        digest(OLD_DRIVER.read_bytes()) != OLD_DRIVER_SHA
        or digest(PROPOSAL.read_bytes()) != PROPOSAL_SHA
    ):
        raise ValueError("base wrapper or exact proposal differs")
    proposed = json.loads(PROPOSAL.read_bytes())
    for evidence in [proposed["locator_evidence"], *proposed["existing_capture_evidence"]]:
        if digest((ROOT / evidence["path"]).read_bytes()) != evidence["sha256"]:
            raise ValueError("retained locator evidence differs")
    if proposed["printed_href"] not in (ROOT / proposed["locator_evidence"]["path"]).read_text():
        raise ValueError("source does not print exact locator")
    limits = proposed["limits"]
    manifest = dict(
        version=1,
        approval_reference="Specific owner authorization matching this exact manifest is required; proposal alone grants no requests.",
        proposal_sha256=PROPOSAL_SHA,
        limits=dict(
            archived_body_targets=0,
            total_http_requests_including_redirects_and_robots=3,
            max_archive_redirects_per_body=0,
            cdx_requests=2,
            max_elapsed_seconds=900,
            max_received_bytes_per_response=2097152,
            max_total_received_bytes=4194304,
            minimum_gap_seconds=10,
            max_in_flight=1,
            shared_archive_daily_request_budget=200,
            source_origin_requests=0,
            pdf_requests=0,
            automatic_retries=0,
            automatic_alternate_captures=0,
            automatic_child_requests=0,
        ),
        targets=[],
        metadata_queries=[
            dict(
                source="dcn",
                original_url=proposed["original_url"],
                probe_url=proposed["probe_url"],
                page0_url=proposed["page0_url"],
                max_pages=1,
            )
        ],
        quarantine="Separate single-use metadata quarantine; no production observations or watches.",
    )
    assert limits["total_http_requests_including_robots"] == 3 and limits["metadata_requests"] == 2
    manifest_sha = digest(canonical(manifest))
    helper_dir = source / "journal/tools/admission"
    exception = (helper_dir / "fixture_exception.py").read_text()
    exception = sub(exception, OLD_MANIFEST_SHA, manifest_sha)
    exception = sub(exception, "approved_new_source_fixture_exception_only", APPROVAL)
    exception = sub(exception, "starts + timedelta(days=1)", "starts + timedelta(minutes=15)")
    exception = sub(exception, "at most 24 hours", "at most 15 minutes")
    exception = sub(
        exception,
        "from journal.tools.admission.fixture_transport import FixtureRunner",
        "from fixture_helpers.fixture_transport import FixtureRunner",
    )
    transport = (helper_dir / "fixture_transport.py").read_text()
    transport = sub(
        transport,
        "from journal.tools.admission.fixture_exception import (",
        "from fixture_helpers.fixture_exception import (",
    )
    templates = (helper_dir / "build_dcn_index_fixture.py").read_text()
    transport = sub(
        transport,
        "import sqlite3\n",
        "import sqlite3\nimport time\nfrom collections.abc import Callable\n",
    )
    transport = sub(
        transport,
        "        transport: httpx.BaseTransport | None = None,\n",
        "        transport: httpx.BaseTransport | None = None,\n        monotonic: Callable[[], float] | None = None,\n",
    )
    transport = sub(
        transport,
        "        self.conn, self.clock, self.state, self.output = connection, clock, state, output\n",
        "        self.conn, self.clock, self.state, self.output = connection, clock, state, output\n        self.monotonic = monotonic or time.monotonic\n        self.monotonic_origin = self.monotonic()\n        self.next_dispatch_monotonic = self.monotonic_origin + policy.min_gap_seconds\n",
    )
    transport = sub(
        transport,
        "        self._check()\n        while True:\n",
        "        self._check()\n        self._wait_completion_floor()\n        while True:\n",
    )
    dispatch = literal_template(templates, "TRANSPORT_CLASS")
    dispatch = sub(
        dispatch,
        "        return self.inner.handle_request(request)",
        '        current["transport_dispatched_elapsed_seconds"] = self.runner.monotonic() - self.runner.monotonic_origin\n        return self.inner.handle_request(request)',
    )
    completion = literal_template(templates, "COMPLETION_METHOD")
    completion = sub(
        completion,
        "        completed = self.clock.now()",
        '        completed = self.clock.now()\n        elapsed = self.monotonic()\n        request["exchange_completed_elapsed_seconds"] = elapsed - self.monotonic_origin',
    )
    completion = sub(
        completion,
        "        until = completed + timedelta(seconds=spacing)",
        '        self.next_dispatch_monotonic = elapsed + spacing\n        request["next_dispatch_not_before_elapsed_seconds"] = self.next_dispatch_monotonic - self.monotonic_origin\n        until = completed + timedelta(seconds=spacing)',
    )
    completion += """    def _wait_completion_floor(self) -> None:
        while (remaining := self.next_dispatch_monotonic - self.monotonic()) > 0:
            self._check()
            self.clock.sleep(min(1, remaining, (self.deadline - self.clock.now()).total_seconds()))

"""
    transport = sub(transport, "class FixtureRunner:", dispatch + "class FixtureRunner:")
    transport = sub(
        transport,
        "    def _exchange(self, client: httpx.Client, url: str, purpose: str) -> httpx.Response:\n",
        completion
        + "    def _exchange(self, client: httpx.Client, url: str, purpose: str) -> httpx.Response:\n",
    )
    transport = sub(
        transport,
        "        finally:\n            sha = digest(bytes(body))",
        "        finally:\n            self._anchor_completion(request)\n            sha = digest(bytes(body))",
    )
    transport = sub(
        transport,
        "                transport=self.transport,",
        "                transport=DispatchReceiptTransport(self, self.transport or httpx.HTTPTransport(trust_env=False)),",
    )
    transport = sub(
        transport,
        "                and datetime.fromisoformat(row[1]) + timedelta(days=1) > self.clock.now()",
        "                and datetime.fromisoformat(row[1]) <= self.clock.now()\n                and datetime.fromisoformat(row[1]) + timedelta(days=1) > self.clock.now()",
    )
    # Bound bytes exposed by the HTTP reader; wire/socket buffering is not measured.
    # Rounding down avoids ever slicing a delivered chunk or issuing an EOF probe
    # after the remaining reserved capacity has been consumed.
    transport = sub(
        transport,
        "        if capacity <= 0:",
        "        capacity = (capacity // 65536) * 65536\n        if capacity <= 0:",
    )
    transport = sub(
        transport,
        "streamed.iter_raw(chunk_size=min(65536, reserved))",
        "streamed.iter_raw(chunk_size=65536)",
    )
    transport = sub(
        transport,
        "                    remaining = reserved - len(body)\n                    body.extend(chunk[:remaining])",
        "                    body.extend(chunk)",
    )
    transport = sub(
        transport,
        '                    if self.clock.now() >= self.deadline:\n                        raise FixtureStopped("elapsed-time ceiling or execution window expired")\n                    body.extend(chunk)',
        '                    body.extend(chunk)\n                    if self.clock.now() >= self.deadline:\n                        raise FixtureStopped("elapsed-time ceiling or execution window expired")',
    )
    transport = sub(
        transport,
        '                    "classification": outcome.outcome.value,',
        '                    "classification": outcome.outcome.value,\n                    "budget_charged_bytes": len(body) if request["complete"] else reserved,',
    )
    transport = sub(
        transport,
        "body_bytes=len(body) - reserved, request_day=day",
        "body_bytes=(len(body) - reserved if request['complete'] else 0), request_day=day",
    )
    transport = sub(
        transport,
        '            "manifest_canonical_sha256": MANIFEST_SHA,',
        '            "manifest_canonical_sha256": MANIFEST_SHA,\n            "byte_measurement": "response_body_bytes_exposed_by_iter_raw",\n            "transport_buffering_not_measured": True,\n            "read_chunk_bytes": 65536,\n            "incomplete_response_keeps_byte_reservation": True,',
    )
    # CDX page content is retained for independent review, never followed or parsed into watches.
    files = {
        "fixture_helpers/__init__.py": b"",
        "fixture_helpers/fixture_exception.py": exception.encode(),
        "fixture_helpers/fixture_transport.py": transport.encode(),
        "proposal.json": canonical(manifest),
        "source-proposal.json": PROPOSAL.read_bytes(),
    }
    closure = canonical(
        dict(
            format="fixture-helper-closure-v1",
            files={name: digest(body) for name, body in sorted(files.items())},
        )
    )
    driver = OLD_DRIVER.read_text()
    driver = sub(driver, OLD_CLOSURE_SHA, digest(closure))
    driver = sub(driver, OLD_SOURCE, SOURCE)
    driver = sub(driver, OLD_SOURCE_SHA, SOURCE_SHA)
    driver = sub(driver, "if schema!=14 or", "if schema!=28 or")
    driver = sub(
        driver,
        "    if schema!=28 or schema!=gate['schema'] or schema!=db_module.SCHEMA_VERSION:",
        "    if int(conn.execute('PRAGMA user_version').fetchone()[0])!=28 or schema!=28 or schema!=gate['schema'] or schema!=db_module.SCHEMA_VERSION:",
    )
    driver = sub(
        driver,
        "        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)\n        conn=",
        "        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)\n        if (state/'RESTORE_PENDING').exists():raise existing.FixtureStopped('restore verification pending after writer lock')\n        conn=",
    )
    start = driver.index("                original_clock=self.clock\n")
    end = driver.index("                acquired=isinstance(grant,Grant)", start)
    driver = (
        driver[:start] + "                grant=super().acquire(host,**kwargs)\n" + driver[end:]
    )
    driver = sub(
        driver,
        "self.action=action;self.request_day=at.date().isoformat()",
        "self.action=action\n            if grant.debited_at is None:raise existing.FixtureStopped('request grant omitted debit time')\n            self.request_day=grant.debited_at.date().isoformat()",
    )
    driver = sub(
        driver,
        "H13 lifecycle adapter for the existing exact, still-unapproved fixture runner.",
        "H13 schema28 adapter for one separately authorized metadata-only lookup.",
    )
    driver = sub(
        driver,
        "No new transport/allowlist, no parser activation. Default original dry-run is pure.",
        "Exact CDX allowlist, no body requests or activation. Default dry-run is pure.",
    )
    # Reject extra runtime files in addition to checking every declared file.
    driver = sub(
        driver,
        "    for name,value in json.loads(receipt.read_bytes())['files'].items():",
        "    inventory=json.loads(receipt.read_bytes())['files']\n    children=tuple(source.rglob('*'))\n    actual={p.relative_to(source).as_posix() for p in children if p.is_file() and '__pycache__' not in p.parts and p!=receipt}\n    if not inventory or any(p.is_symlink() for p in children) or actual!=set(inventory):raise existing.FixtureStopped('runtime inventory differs')\n    for name,value in inventory.items():",
    )
    prepare = (helper_dir / "prepare_fixture_exception.py").read_text()
    for before, after in [
        (OLD_SOURCE, SOURCE),
        (OLD_SOURCE_SHA, SOURCE_SHA),
        (OLD_DRIVER_SHA, digest(driver.encode())),
        (OLD_CLOSURE_SHA, digest(closure)),
        (OLD_MANIFEST_SHA, manifest_sha),
        ('DRIVER = "fixture-exception-h13-002.py"', f'DRIVER = "{DRIVER_NAME}"'),
        ("approved_new_source_fixture_exception_only", APPROVAL),
    ]:
        prepare = sub(prepare, before, after)
    prepare = sub(prepare, '"h16-source.json"', '"extension-source.json"', 2)
    prepare = sub(prepare, "schema14", "schema28", 2)
    prepare = sub(prepare, "int(schema[0]) != 14", "int(schema[0]) != 28")
    prepare = sub(
        prepare,
        "if schema is None or int(schema[0]) != 28:",
        "if schema is None or int(schema[0]) != 28 or int(conn.execute('PRAGMA user_version').fetchone()[0]) != 28:",
    )
    prepare = sub(prepare, "schema=14,", "schema=28,", 2)
    prepare = sub(prepare, "timedelta(hours=24)", "timedelta(minutes=15)")
    prepare = sub(
        prepare,
        "    verify_files(source, files)\n",
        "    children = list(source.rglob('*'))\n    actual = {p.relative_to(source).as_posix() for p in children if p.is_file() and '__pycache__' not in p.parts and p != receipt}\n    if any(p.is_symlink() for p in children) or actual != set(files):\n        raise ValueError('runtime inventory differs')\n    verify_files(source, files)\n",
    )
    destination.mkdir(parents=True)
    outputs = {
        **{f"helper-closure/{name}": body for name, body in files.items()},
        "helper-closure/closure.json": closure,
        DRIVER_NAME: driver.encode(),
        "prepare_fixture_exception.py": prepare.encode(),
    }
    for name, body in outputs.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    verified_source(source)
    report = dict(
        format="exact-dcn-results-lookup-build-v1",
        builder_sha256=digest(Path(__file__).read_bytes()),
        runtime_source=SOURCE,
        runtime_receipt_sha256=SOURCE_SHA,
        base_driver_sha256=OLD_DRIVER_SHA,
        proposal_sha256=PROPOSAL_SHA,
        manifest_canonical_sha256=manifest_sha,
        driver_sha256=digest(driver.encode()),
        helper_manifest_sha256=digest(closure),
        files={name: digest(body) for name, body in sorted(outputs.items())},
        network_requests=0,
        executed=False,
        owner_approval="required_separately",
    )
    (destination / "build-receipt.json").write_bytes(canonical(report) + b"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination), indent=2))
