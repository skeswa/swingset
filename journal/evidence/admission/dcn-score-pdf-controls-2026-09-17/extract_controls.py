from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[4]
PDFS = {
    "finals": ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17/quarantine-001/bodies/b91e9e0a82edbd455b21b5aad50f211c590a05afd9a8bb34c81493d134e70e5e",
    "prelims": ROOT / "journal/evidence/admission/dcn-origin-runner-2026-09-17/quarantine-001/bodies/f2ee4660315ab79703e8095312f75922e84271b38e19e784236bee5c2fbebb0b",
}
OUT = Path(__file__).resolve().parent / "extracted.json"

result = {"format": "dcn-score-pdf-controls-extraction-v1", "extractor": "pypdf", "version": "6.18.1", "inputs": {}}
for name, path in PDFS.items():
    body = path.read_bytes()
    reader = PdfReader(path, strict=True)
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append({"page": index, "width": float(page.mediabox.width), "height": float(page.mediabox.height), "text": page.extract_text(extraction_mode="layout") or ""})
    result["inputs"][name] = {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "pages": pages}
OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
