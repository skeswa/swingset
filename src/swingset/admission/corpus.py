"""Stream an offline shadow corpus from read-only archives into review files."""

from __future__ import annotations

import hashlib
import html
import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from swingset.fetch.archive import Archive, canonical, digest
from swingset.sources import get_page_kind
from swingset.sources.base import ParseContext
from swingset.state.db import open_database

from .contracts import inspect


def write_corpus(
    states: tuple[Path, ...],
    output: Path,
    *,
    cutoff: str,
    examples_per_reason: int = 2,
    page_kinds: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Re-extract every retained body; never migrate, accept, or fetch inputs."""
    if output.exists():
        raise ValueError("Corpus output already exists")
    output.mkdir(parents=True)
    (output / "examples").mkdir()
    totals: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {}
    source_receipts = []
    memberships: dict[tuple[str, str, str, str], list[str]] = {}
    for state in states:
        with open_database(state, read_only=True, lock=False) as db:
            for row in db.connection.execute(
                "SELECT snapshot_id,snapshots.body_sha256,parser,source_ref FROM snapshots JOIN watches USING(watch_id)"
            ):
                if page_kinds is not None and str(row[2]) not in page_kinds:
                    continue
                input_key = (str(row[0]), str(row[1]), str(row[2]), str(row[3]))
                memberships.setdefault(input_key, []).append(str(state))
    seen: set[tuple[str, str, str, str]] = set()
    skipped = 0
    cohort_hash = hashlib.sha256()
    started = perf_counter()
    with (output / "reports.jsonl").open("wb") as stream:
        for state in states:
            archive = Archive(state)
            with open_database(state, read_only=True, lock=False) as db:
                with db.transaction(immediate=False) as conn:
                    source_receipts.append(
                        {
                            "state": str(state),
                            "schema_version": db.schema_version,
                            "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[
                                0
                            ],
                            "selected_observations": conn.execute(
                                "SELECT COUNT(*) FROM observations"
                            ).fetchone()[0],
                            "accepted_inputs": [
                                tuple(row)
                                for row in conn.execute(
                                    "SELECT * FROM accepted_inputs ORDER BY consumer,input_name"
                                )
                            ],
                        }
                    )
                    rows = conn.execute(
                        "SELECT s.*,w.source,w.parser,w.source_ref,w.kind AS watch_kind FROM snapshots s JOIN watches w USING(watch_id) ORDER BY w.parser,s.snapshot_id"
                    )
                    for row in rows:
                        if page_kinds is not None and str(row["parser"]) not in page_kinds:
                            continue
                        input_key = (
                            str(row["snapshot_id"]),
                            str(row["body_sha256"]),
                            str(row["parser"]),
                            str(row["source_ref"]),
                        )
                        if input_key in seen:
                            skipped += 1
                            continue
                        seen.add(input_key)
                        ctx = ParseContext(
                            str(row["snapshot_id"]),
                            str(row["watch_id"]),
                            str(row["url"]),
                            str(row["source"]),
                            str(row["parser"]),
                            row["source_ref"],
                            str(row["observed_at"] or row["fetched_at"]),
                        )
                        record: dict[str, Any] = {
                            "state_root": str(state),
                            "snapshot_id": ctx.snapshot_id,
                            "watch_id": ctx.watch_id,
                            "page_kind": ctx.kind,
                            "body_sha256": row["body_sha256"],
                            "url": ctx.url,
                            "observed_at": ctx.fetched_at,
                            "captured_at": row["captured_at"],
                            "archive_url": row["archive_url"],
                            "via": row["via"],
                            "state_roots": memberships[input_key],
                        }
                        body: bytes | None = None
                        extract: Any = None
                        try:
                            page = get_page_kind(ctx.kind)
                            record.update(
                                {
                                    "extract_version": str(page.EXTRACT_VERSION),
                                    "parser_version": str(page.PARSER_VERSION),
                                }
                            )
                            body = archive.read_body(str(row["body_sha256"]))
                            extract = page.extract(body)
                            result = page.parse(extract, ctx)
                            report = inspect(ctx, body, extract, result)
                            record.update(
                                {
                                    "extract_sha256": digest(canonical(extract)),
                                    "report": report.as_dict(),
                                    "report_digest": report.digest,
                                    "observation_count": len(result.observations),
                                    "child_count": len(result.watches),
                                }
                            )
                            reasons = report.failures or ("passed",)
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            reasons = ("archived_interpretation_failed",)
                            record.update({"error": str(exc), "error_type": type(exc).__name__})
                        encoded = canonical(record) + b"\n"
                        stream.write(encoded)
                        cohort_hash.update(encoded)
                        totals[ctx.kind + ":total"] += 1
                        for reason in reasons:
                            key = ctx.kind + ":" + reason
                            totals[key] += 1
                            selected = examples.setdefault(key, [])
                            if len(selected) >= examples_per_reason:
                                continue
                            name = digest(canonical((str(state), ctx.snapshot_id)))[:20]
                            (output / "examples" / (name + ".json")).write_bytes(canonical(record))
                            if body is not None:
                                suffix = ".pdf" if body.startswith(b"%PDF") else ".txt"
                                (output / "examples" / (name + suffix)).write_bytes(body)
                            else:
                                suffix = None
                            if extract is not None:
                                (output / "examples" / (name + ".extract.json")).write_bytes(
                                    canonical(extract)
                                )
                            (output / "examples" / (name + ".html")).write_text(
                                render_example(record, name, suffix)
                            )
                            selected.append(
                                {
                                    "name": name,
                                    "snapshot_id": ctx.snapshot_id,
                                    "body_suffix": suffix,
                                    "extract": extract is not None,
                                }
                            )
    receipt = {
        "cutoff": cutoff,
        "page_kinds": page_kinds,
        "states": source_receipts,
        "counts": dict(sorted(totals.items())),
        "reports_sha256": cohort_hash.hexdigest(),
        "examples": examples,
        "elapsed_seconds": perf_counter() - started,
        "network_requests": 0,
        "input_mutations": 0,
        "promotions": 0,
        "review_status": "unreviewed",
        "duplicate_inputs_skipped": skipped,
    }
    (output / "receipt.json").write_bytes(canonical(receipt))
    lines = [
        "<!doctype html><meta charset=utf-8><title>Source admission shadow corpus</title>",
        "<style>body{max-width:1100px;margin:40px auto;font:16px system-ui}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}code{overflow-wrap:anywhere}</style>",
        "<h1>Source admission shadow corpus</h1><p>Review status: <b>unreviewed</b>. No promotions, source requests, or input mutations occurred. A passing report is not an activation receipt.</p>",
        "<p><a href=receipt.json>Corpus receipt</a> · <a href=reports.jsonl>Every retained report</a></p>",
        "<p>Each example links its exact body, extracted source structure, accounting, coverage and guard result. Unknown source contracts remain unassessed. Archive captures retain their own snapshot scope.</p><table><tr><th>Kind / outcome</th><th>Count</th><th>Examples</th></tr>",
    ]
    for key, count in sorted(totals.items()):
        links = []
        for sample in examples.get(key, []):
            name = sample["name"]
            links.append(f'<a href="examples/{name}.html">{html.escape(sample["snapshot_id"])}</a>')
            if sample["body_suffix"]:
                links.append(f'<a href="examples/{name}{sample["body_suffix"]}">body</a>')
            if sample["extract"]:
                links.append(f'<a href="examples/{name}.extract.json">extract</a>')
        lines.append(
            f"<tr><td>{html.escape(key)}</td><td>{count}</td><td>{' · '.join(links)}</td></tr>"
        )
    lines.append("</table>")
    (output / "index.html").write_text("\n".join(lines))
    return receipt


def render_example(record: dict[str, Any], name: str, suffix: str | None) -> str:
    report = record.get("report", {})
    lines = [
        "<!doctype html><meta charset=utf-8><title>Admission report</title>",
        "<style>body{max-width:1100px;margin:40px auto;font:16px system-ui}td,th{padding:7px;text-align:left;border-bottom:1px solid #ddd}.fail{color:#a20}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>",
        "<p><a href=../index.html>Corpus index</a> · <a href="
        + name
        + ".json>Machine report</a></p>",
        "<h1>" + html.escape(record["page_kind"]) + "</h1>",
        "<p>Snapshot <code>"
        + html.escape(record["snapshot_id"])
        + "</code>. This report is unreviewed and has not activated any policy.</p>",
    ]
    if suffix:
        lines.append(f'<p><a href="{name}{suffix}">Exact source body</a></p>')
    if record.get("extract_sha256"):
        lines.append(f'<p><a href="{name}.extract.json">Extracted structure</a></p>')
    lines.append(
        "<h2>Source provenance</h2><pre>"
        + html.escape(
            json.dumps(
                {key: value for key, value in record.items() if key != "report"},
                indent=2,
                sort_keys=True,
            )
        )
        + "</pre>"
    )
    lines.append(
        "<h2>Coverage</h2><pre>"
        + html.escape(json.dumps(report.get("coverage", {}), indent=2))
        + "</pre><h2>Guards</h2><table><tr><th>Result</th><th>Reason</th><th>Check</th></tr>"
    )
    for guard in report.get("guards", []):
        lines.append(
            f'<tr class="{"" if guard["passed"] else "fail"}"><td>{"pass" if guard["passed"] else "blocked"}</td><td>{html.escape(guard["code"])}</td><td>{html.escape(guard["detail"])}</td></tr>'
        )
    lines.append(
        "</table><h2>Interpretation accounting</h2><table><tr><th>Field</th><th>Disposition</th><th>Reason</th></tr>"
    )
    for field in report.get("fields", []):
        lines.append(
            f"<tr><td><code>{html.escape(field['path'])}</code></td><td>{html.escape(field['disposition'])}</td><td>{html.escape(field['reason'])}</td></tr>"
        )
    lines.append("</table>")
    return "\n".join(lines)
