"""A portable, offline identity review packet with verified retained evidence."""

from __future__ import annotations

import argparse
import html
import json
import zlib
from pathlib import Path
from typing import Any

from swingset.fetch.archive import Archive

from .evaluation import evaluate

_STYLE = """body{max-width:1000px;margin:2rem auto;padding:0 1rem;font:16px system-ui;line-height:1.5} article{border-top:1px solid #aaa;padding:1rem 0} pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f3f3;padding:1rem} label{display:block;margin:.6rem 0} input,textarea,select{font:inherit;max-width:100%;padding:.35rem} textarea{width:95%} .warning{color:#8a3000} button{padding:.6rem;font:inherit} code{overflow-wrap:anywhere}"""
_SCRIPT = """document.querySelector('#export').onclick=()=>{
const reviews=[];
for(const form of document.querySelectorAll('form')){
 const val=n=>form.elements.namedItem(n).value.trim();
 if(!val('decision'))continue;
 if(!val('reviewer')||!val('method')||!val('evidence')){alert('Complete reviewer, method and evidence for every selected decision.');return;}
 const decision=val('decision'), reference=val('reference_wsdc_id');
 if(['correct','matched'].includes(decision)&&(!/^\\d+$/.test(reference)||Number(reference)<1)){alert('Enter the independently verified WSDC ID.');return;}
 const complete=form.elements.namedItem('candidate_search_complete').checked;
 if(form.dataset.stream==='unresolved'&&decision!=='insufficient_evidence'&&!complete){alert('Unresolved decisions require a search beyond the frozen candidate list.');return;}
 reviews.push({sample_id:form.dataset.id,sample_fingerprint:form.dataset.fingerprint,reviewer:val('reviewer'),method:val('method'),evidence:val('evidence').split('\\n').filter(Boolean),decision,reference_wsdc_id:reference?Number(reference):null,candidate_search_complete:complete,reviewed_at:new Date().toISOString()});
}
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(reviews,null,2)+'\\n'],{type:'application/json'}));a.download='adjudications.json';a.click();URL.revokeObjectURL(a.href);
};"""


def _json(value: object) -> str:
    return html.escape(json.dumps(value, indent=2, sort_keys=True))


def write_packet(sample: dict[str, Any], archive: Archive, destination: Path) -> dict[str, Any]:
    """Copy hash-verified artifacts into a new directory; never infer a review."""
    evaluation = evaluate(sample, [])  # Validate frozen membership before exporting.
    destination.mkdir(parents=True, exist_ok=False)
    evidence_dir = destination / "evidence"
    evidence_dir.mkdir()
    missing: list[dict[str, str]] = []
    exported: dict[tuple[str, str], str | None] = {}

    def evidence(snapshot: dict[str, Any]) -> str:
        links = []
        for kind, field in (("body", "body_sha256"), ("extract", "extract_sha256")):
            digest = snapshot.get(field)
            if not digest:
                continue
            key = kind, str(digest)
            if key not in exported:
                try:
                    if kind == "body":
                        body = archive.read_body(digest)
                        suffix = ".pdf" if body.startswith(b"%PDF-") else ".txt"
                    else:
                        body = (
                            json.dumps(archive.read_extract(digest), indent=2, ensure_ascii=False)
                            + "\n"
                        ).encode()
                        suffix = ".txt"
                    filename = kind + "-" + digest + suffix
                    (evidence_dir / filename).write_bytes(body)
                    exported[key] = filename
                except (OSError, ValueError, EOFError, zlib.error) as exc:
                    exported[key] = None
                    missing.append(
                        {"kind": kind, "digest": str(digest), "reason": type(exc).__name__}
                    )
            filename = exported[key]
            if filename:
                links.append(f'<a href="evidence/{html.escape(filename)}">Retained {kind}</a>')
            else:
                links.append(f'<strong class="warning">Retained {kind} unavailable</strong>')
        return (
            " · ".join(links)
            + "<details><summary>Snapshot provenance</summary><pre>"
            + _json(snapshot)
            + "</pre></details>"
        )

    cards = []
    templates = []
    for row in sample["samples"]:
        accepted = row["stream"] == "accepted"
        choices = (
            ("correct", "incorrect", "insufficient_evidence")
            if accepted
            else ("matched", "no_registry_identity", "insufficient_evidence")
        )
        options = '<option value="">Unreviewed</option>' + "".join(
            f"<option>{choice}</option>" for choice in choices
        )
        identity = html.escape(str(row.get("accepted_wsdc_id") or "unresolved"))
        candidate_html = []
        for candidate in row.get("registry_evidence", []):
            candidate_html.append(
                f"<details><summary>WSDC {html.escape(str(candidate['wsdc_id']))}</summary><pre>{_json(candidate.get('dancer'))}</pre>{evidence(candidate.get('snapshot') or {})}</details>"
            )
        cards.append(f'''<article><h2>{html.escape(row["name_raw"])} — {html.escape(row["role"])}</h2>
<p>{html.escape(row["split"])} · {html.escape(row["stream"])} · {html.escape(row["source"])} · {html.escape(str(row["year"]))} · current ID {identity}</p>
<p>Sample <code>{row["sample_id"]}</code>; flags: {html.escape(", ".join(row["flags"]) or "none")}</p>
<pre>{_json(row["source_reference"])}</pre>{evidence(row.get("evidence") or {})}
<details><summary>Frozen link reasoning and candidates</summary><pre>{_json(row.get("link_evidence"))}</pre>{"".join(candidate_html)}</details>
<form data-id="{row["sample_id"]}" data-fingerprint="{row["sample_fingerprint"]}" data-stream="{row["stream"]}">
<label>Decision <select name="decision">{options}</select></label>
<label>Independently verified WSDC ID <input name="reference_wsdc_id" inputmode="numeric"></label>
<label>Reviewer <input name="reviewer"></label><label>Review method <input name="method"></label>
<label>Evidence references, one per line <textarea name="evidence" rows="3"></textarea></label>
<label><input type="checkbox" name="candidate_search_complete"> I searched beyond the frozen candidate list (required for unresolved decisions).</label>
</form></article>''')
        templates.append(
            {
                "sample_id": row["sample_id"],
                "sample_fingerprint": row["sample_fingerprint"],
                "reviewer": "",
                "reviewed_at": "",
                "method": "",
                "evidence": [],
                "decision": "",
                "reference_wsdc_id": None,
                "candidate_search_complete": False,
            }
        )
    split_counts = {
        split: sum(row["split"] == split for row in sample["samples"])
        for split in ("evaluation", "tuning")
    }
    split_notice = (
        "Held-out evaluation is unavailable: the frozen sample has no evaluation subjects. "
        "The connected population must stay together; do not change seeds to move it into evaluation."
        if not split_counts["evaluation"]
        else "Evaluation subjects remain unreviewed and separate from tuning."
    )
    manifest = {
        "sample_digest": sample["sample_digest"],
        "sample_count": len(cards),
        "split_counts": split_counts,
        "split_notice": split_notice,
        "copied_artifacts": sum(value is not None for value in exported.values()),
        "missing_artifacts": missing,
        "reviewed_precision": "unavailable; no human adjudications supplied",
        "network_requests": 0,
    }
    heading = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Identity review: {html.escape(sample["cohort"])}</title><style>{_STYLE}</style>
<h1>Identity review: {html.escape(sample["cohort"])}</h1>
<p><strong>Reviewed precision is unavailable. No human adjudications have been supplied.</strong></p>
<p class="warning">{html.escape(split_notice)}</p>
<p>Evaluation subjects: {split_counts["evaluation"]}; tuning subjects: {split_counts["tuning"]}; connected groups: {sample["split_groups"]}.</p>
<p>Inspect retained source evidence and independently verify the person. A candidate or score is not a review. For unresolved entries, search outside the proposed candidates before concluding that no registry identity exists. Keep evaluation decisions separate from tuning changes.</p>
<p>All evidence links are local copies, verified against their captured digest. HTML source is saved as plain text. Missing evidence is marked. Form entries stay in this browser until downloaded; they are not saved automatically.</p>
<p><a href="sample.json">Frozen sample</a> · <a href="adjudications-template.json">Blank review template</a> · <a href="packet.json">Packet receipt</a></p>
<details><summary>Sampling, denominators and provenance</summary><pre>{_json({k: v for k, v in sample.items() if k != "samples"})}</pre></details>
<button id="export">Download completed adjudications</button>"""
    (destination / "index.html").write_text(
        heading + "".join(cards) + f"<script>{_SCRIPT}</script></html>"
    )
    for name, value in (
        ("sample.json", sample),
        ("adjudications-template.json", templates),
        ("evaluation-unreviewed.json", evaluation),
        ("packet.json", manifest),
    ):
        (destination / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            write_packet(json.loads(args.sample.read_text()), Archive(args.state), args.output),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
