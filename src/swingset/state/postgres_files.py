"""Required history files resolved solely through the captured snapshot mapping."""

from __future__ import annotations

import codecs
import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from swingset.fetch.archive import Archive, canonical

from . import history_catalog as history
from . import postgres_schema as schema

RECIPE_TABLES = (
    "source_generations",
    "source_equivalences",
    "derivation_generations",
    "derivation_recipes",
)


def _legacy_scope_references(conn: Any) -> set[tuple[str, str]]:
    """Read only retained recipes, streaming oversized fields on the pinned snapshot."""
    from .legacy_recipes import scope_references
    from .postgres_import import LARGE, _postgres_text

    references: set[tuple[str, str]] = set()
    for table in RECIPE_TABLES:
        keys = history.keys(table)
        columns = ",".join(f'r."{name}"' for name in keys)
        with conn.cursor(name="history_legacy_" + table) as cursor:
            cursor.execute(
                f"SELECT {columns},CASE WHEN octet_length(r.recipe_json)>{LARGE} THEN NULL ELSE r.recipe_json END AS recipe,octet_length(r.recipe_json) AS recipe_bytes FROM {schema.qualified(table)} r WHERE {history.where(table, 'r')}"
            )
            for row in cursor:
                value, size = row[len(keys) :]
                if size is None:
                    continue
                blocks = (
                    _postgres_text(conn, table, "recipe_json", tuple(row[: len(keys)]))
                    if value is None
                    else iter([value.encode("utf-8")])
                )
                references.update(scope_references(blocks))
    return references


def _manifest_entries(blocks: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Decode an arbitrarily long capture list one bounded capture at a time."""
    decoder = json.JSONDecoder()
    buffer = ""
    state = "start"
    for block in blocks:
        buffer += block
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break
            if state == "start":
                if buffer[0] != "[":
                    raise ValueError("source manifest is not a capture list")
                buffer = buffer[1:]
                state = "first"
            elif state == "end":
                raise ValueError("source manifest has trailing data")
            elif state == "separator":
                if buffer[0] == "]":
                    state = "end"
                elif buffer[0] == ",":
                    state = "value"
                else:
                    raise ValueError("source manifest is missing a capture separator")
                buffer = buffer[1:]
            elif state == "first" and buffer[0] == "]":
                buffer = buffer[1:]
                state = "end"
            else:
                try:
                    value, end = decoder.raw_decode(buffer)
                except ValueError:
                    if len(buffer) > (8 << 20):
                        raise ValueError(
                            "source manifest contains an unsupported oversized capture"
                        ) from None
                    break
                if not isinstance(value, dict):
                    raise ValueError("source manifest has an unsupported capture")
                yield value
                buffer = buffer[end:]
                state = "separator"
    if state != "end" or buffer.strip():
        raise ValueError("source manifest is truncated")


def _packed_manifest(path: Path) -> Iterator[str]:
    """Read only the canonical pack's first string, without buffering its report."""
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        if stream.read(len('{"manifest":"')) != '{"manifest":"':
            raise ValueError("unsupported source-body pack prefix")
        output: list[str] = []
        replacements = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }
        while True:
            char = stream.read(1)
            if char == '"':
                if output:
                    yield "".join(output)
                return
            if not char or ord(char) < 32:
                raise ValueError("source-body pack manifest string is truncated")
            if char == "\\":
                escaped = stream.read(1)
                if escaped == "u":
                    value = int(stream.read(4), 16)
                    if 0xD800 <= value <= 0xDBFF:
                        if stream.read(2) != "\\u":
                            raise ValueError("source-body pack contains an invalid Unicode pair")
                        low = int(stream.read(4), 16)
                        if not 0xDC00 <= low <= 0xDFFF:
                            raise ValueError("source-body pack contains an invalid Unicode pair")
                        value = 0x10000 + ((value - 0xD800) << 10) + low - 0xDC00
                    char = chr(value)
                elif escaped in replacements:
                    char = replacements[escaped]
                else:
                    raise ValueError("source-body pack contains an invalid JSON escape")
            output.append(char)
            if len(output) >= (1 << 16):
                yield "".join(output)
                output.clear()


def verify(conn: Any, files: Mapping[str, Path], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on a selected row's absent file; unused object rows are harmless."""
    required: set[str] = set()
    bundles: set[str] = set()
    manifests: set[str] = set()
    artifacts: set[str] = set()
    missing_declarations: list[dict[str, str]] = []

    def need(name: str) -> Path:
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or name not in files
            or not Path(files[name]).is_file()
        ):
            raise ValueError("a selected history reference names a missing captured file: " + name)
        required.add(name)
        return Path(files[name])

    def raw(value: Any, kind: str) -> None:
        if value is None:
            return
        digest = str(value)
        Archive._validate_hash(digest)
        name = (
            f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
            if kind == "body"
            else f"extracts/{digest}"
        )
        need(name)

    def packed(kind: str, digest: str) -> Path:
        Archive._validate_hash(digest)
        if (
            conn.execute(
                "SELECT 1 FROM ops.objects o WHERE o.class=%s AND o.digest=%s AND "
                + history.where("objects", "o"),
                (kind, digest),
            ).fetchone()
            is None
        ):
            raise ValueError("a retained packed object has no retained storage ledger row")
        return need(f"objects/{kind}/{digest[:2]}/{digest}")

    for table, columns in (
        ("snapshots", ("body_sha256", "extract_sha256")),
        ("hosts", ("robots_sha256",)),
    ):
        selected = ",".join("r." + column for column in columns)
        with conn.cursor(name="history_raw_" + table) as cursor:
            cursor.execute(
                f"SELECT {selected} FROM {schema.qualified(table)} r WHERE {history.where(table, 'r')}"
            )
            for row in cursor:
                raw(row[0], "body")
                if len(row) > 1:
                    raw(row[1], "extract")
    query = f"SELECT item->>'body_sha256' AS body,item->>'extract_sha256' AS extract FROM evidence.source_generations r CROSS JOIN LATERAL jsonb_array_elements(r.manifest_json::jsonb) item WHERE {history.where('source_generations', 'r')} AND r.manifest_json<>'{{\"packed\":true}}'"
    with conn.cursor(name="history_source_raw") as cursor:
        cursor.execute(query)
        for body, extract in cursor:
            raw(body, "body")
            raw(extract, "extract")
    for kind, digest in conn.execute(
        f"SELECT r.kind,r.sha256 FROM evidence.finding_support_references r WHERE {history.where('finding_support_references', 'r')} AND r.kind IN ('body','extract')"
    ):
        name = (
            f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
            if kind == "body"
            else f"extracts/{digest}"
        )
        if name not in files:
            missing_declarations.append(
                {
                    "kind": str(kind),
                    "digest": str(digest),
                    "reason": "untrusted_finding_declaration_missing",
                }
            )
        else:
            raw(digest, kind)
    for (digest,) in conn.execute(
        f"SELECT DISTINCT r.pack_digest FROM evidence.source_generation_packs r WHERE {history.where('source_generation_packs', 'r')}"
    ):
        for item in _manifest_entries(_packed_manifest(packed("parse_body", str(digest)))):
            raw(item.get("body_sha256"), "body")
            raw(item.get("extract_sha256"), "extract")
    for (digest,) in conn.execute(
        f"SELECT DISTINCT r.pack_digest FROM derive.label_body_packs r WHERE {history.where('label_body_packs', 'r')}"
    ):
        packed("label_body", str(digest))
    for (digest,) in conn.execute(
        f"SELECT g.output_digest FROM derive.derivation_generations g JOIN {history.marker('derivation_evicted')} e USING(generation_id)"
    ):
        packed("derivation_output", str(digest))
    row = conn.execute("SELECT value FROM ops.meta WHERE key='input_bundle_hash'").fetchone()
    if row and row[0]:
        bundles.add(str(row[0]))
    if metadata.get("input_bundle_hash"):
        bundles.add(str(metadata["input_bundle_hash"]))
    for table in RECIPE_TABLES:
        column = "recipe_json"
        query = f"SELECT DISTINCT token #>> '{{}}' AS digest FROM {schema.qualified(table)} r CROSS JOIN LATERAL jsonb_path_query(r.{column}::jsonb,'$.**.input_bundle_hash') token WHERE {history.where(table, 'r')}"
        for (digest,) in conn.execute(query):
            if digest:
                bundles.add(str(digest))
    # The token index came from retained JSON and captured metadata, rather than
    # the directory currently present after the publication fence was released.
    for (token,) in conn.execute(
        "SELECT token FROM pg_temp._swingset_history_tokens WHERE token ~ '^[a-f0-9]{64}$'"
    ):
        if f"inputs/{token}/manifest.json" in files:
            bundles.add(str(token))

    def generation_required(identifier: Any) -> None:
        if (
            conn.execute(
                f"SELECT 1 FROM derive.derivation_generations r WHERE r.generation_id=%s AND {history.where('derivation_generations', 'r')}",
                (str(identifier),),
            ).fetchone()
            is None
        ):
            raise ValueError("retained publication or hold generation leaves the history closure")

    candidates = {
        value
        for value in (
            metadata.get("baseline_candidate"),
            *(metadata.get("pending_candidates") or []),
        )
        if value is not None
    }
    for candidate in candidates:
        if (
            not isinstance(candidate, str)
            or Path(candidate).name != candidate
            or candidate in {".", ".."}
        ):
            raise ValueError("captured publication identity is unsafe")
        prefix = "candidates/" + candidate
        built = json.loads(need(prefix + "/BUILT").read_text())
        manifest_path = need(prefix + "/_meta/manifest.json")
        if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != built["manifest_hash"]:
            raise ValueError("captured candidate manifest differs from BUILT")
        manifest = json.loads(manifest_path.read_text())
        need(
            prefix
            + ("/PUBLISHED" if candidate == metadata.get("baseline_candidate") else "/PUBLISHING")
        )
        if manifest.get("input_bundle_hash"):
            bundles.add(str(manifest["input_bundle_hash"]))
        policy = manifest.get("release_policy") or {}
        if policy.get("mode") == "closure":
            selected = policy.get("closure", {}).get("selected_generations")
            if not isinstance(selected, list):
                raise ValueError("captured candidate has no selected generation closure")
            for generation in selected:
                generation_required(generation)
        if policy.get("mode") == "partitioned":
            manifests.add(str(policy["manifest_digest"]))
    for name in files:
        if name.startswith("holds/"):
            hold = json.loads(need(name).read_text())
            for generation in hold.get("generations", []):
                generation_required(generation)
            for digest in hold.get("artifacts", []):
                body = f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
                raw(digest, "body" if body in files else "extract")
    # Objects can retain index rows after unreferenced files are collected.
    # Follow only supplied retained files and explicit publication pointers.
    for name in files:
        parts = Path(name).parts
        if name.startswith("objects/release_manifest/"):
            manifests.add(parts[-1])
        elif name.startswith("partitions/manifests/") and name.endswith(".json"):
            manifests.add(Path(name).stem)
    for digest in manifests:
        preferred = f"objects/release_manifest/{digest[:2]}/{digest}"
        path = (
            packed("release_manifest", digest)
            if preferred in files
            else need(f"partitions/manifests/{digest}.json")
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("retained release manifest identity differs")
        manifest = json.loads(path.read_text())
        artifacts.update(str(digest) for _, digest in manifest["built"])
    for digest in artifacts:
        path = need(f"partitions/{digest[:2]}/{digest}/sidecar.json")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("retained partition sidecar identity differs")
        for generation in json.loads(path.read_text())["generations"]:
            if (
                conn.execute(
                    f"SELECT 1 FROM derive.derivation_generations r WHERE r.generation_id=%s AND {history.where('derivation_generations', 'r')}",
                    (generation,),
                ).fetchone()
                is None
            ):
                raise ValueError("retained publication generation leaves the history closure")
    from .legacy_recipes import resolve_scope_bundles

    legacy_references = _legacy_scope_references(conn)
    legacy_bundles = resolve_scope_bundles(files, legacy_references, preferred=bundles)
    bundles.update(legacy_bundles.values())
    for digest in bundles:
        Archive._validate_hash(digest)
        manifest = json.loads(need(f"inputs/{digest}/manifest.json").read_text())
        if hashlib.sha256(canonical(manifest)).hexdigest() != digest:
            raise ValueError("retained input bundle identity differs")
        for name, wanted in manifest.items():
            member = Path(name)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError("input bundle member has an unsafe path")
            path = need(f"inputs/{digest}/{name}")
            checksum = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1 << 20), b""):
                    checksum.update(block)
            if checksum.hexdigest() != wanted:
                raise ValueError("retained input bundle member digest differs")
    return {
        "complete": True,
        "missing_finding_declarations": missing_declarations,
        "required_files": len(required),
        "input_bundles": len(bundles),
        "input_bundle_hashes": sorted(bundles),
        "release_manifests": len(manifests),
        "legacy_code_scopes": len(legacy_references),
        "legacy_code_bundles": sorted(set(legacy_bundles.values())),
    }


class OfflineClosure:
    """Required files derived from COPY values, independent of declared file maps."""

    COLUMNS = {
        "snapshots": frozenset({"body_sha256", "extract_sha256"}),
        "hosts": frozenset({"robots_sha256"}),
        "source_generation_packs": frozenset({"pack_digest"}),
        "label_body_packs": frozenset({"pack_digest"}),
        "derivation_generations": frozenset({"generation_id", "output_digest"}),
        "derivation_evicted": frozenset({"generation_id"}),
        "objects": frozenset({"class", "digest"}),
        "meta": frozenset({"key"}),
    }

    def __init__(self) -> None:
        self.required: set[str] = set()
        self.bundles: set[str] = set()
        self.packs: set[tuple[str, str]] = set()
        self.objects: set[tuple[str, str]] = set()
        self.outputs: dict[str, str] = {}
        self.evicted: set[str] = set()
        self.meta_value: str | None = None
        self.legacy_scopes: set[tuple[str, str]] = set()

    def raw(self, digest: Any, kind: str) -> None:
        if digest is not None:
            digest = str(digest)
            Archive._validate_hash(digest)
            self.required.add(
                f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
                if kind == "body"
                else f"extracts/{digest}"
            )

    def row(self, table: str, row: Mapping[str, Any]) -> None:
        if table == "snapshots":
            self.raw(row["body_sha256"], "body")
            self.raw(row["extract_sha256"], "extract")
        elif table == "hosts":
            self.raw(row["robots_sha256"], "body")
        elif table in {"source_generation_packs", "label_body_packs"}:
            self.packs.add(
                (
                    "parse_body" if table == "source_generation_packs" else "label_body",
                    row["pack_digest"],
                )
            )
        elif table == "derivation_generations":
            self.outputs[row["generation_id"]] = row["output_digest"]
        elif table == "derivation_evicted":
            self.evicted.add(row["generation_id"])
        elif table == "objects":
            self.objects.add((row["class"], row["digest"]))
        elif table == "meta" and row["key"] == "input_bundle_hash":
            if self.meta_value is None:
                raise ValueError("decoded input bundle identity is invalid")
            self.bundles.add(self.meta_value)

    def text(self, table: str, column: str, blocks: Iterable[bytes]) -> None:
        if table == "meta" and column == "value":
            small_value = bytearray()
            oversized = False
            for block in blocks:
                if len(small_value) + len(block) > 64:
                    oversized = True
                elif not oversized:
                    small_value.extend(block)
            self.meta_value = None if oversized else small_value.decode("utf-8")
        elif table == "source_generations" and column == "manifest_json":

            def text_blocks() -> Iterator[str]:
                decoder = codecs.getincrementaldecoder("utf-8")("strict")
                for block in blocks:
                    yield decoder.decode(block)
                yield decoder.decode(b"", final=True)

            chunks = iter(text_blocks())
            first = next(chunks, "")
            if first == '{"packed":true}':
                for tail in chunks:
                    if tail:
                        raise ValueError("packed source stub has trailing data")
                return
            import itertools

            for entry in _manifest_entries(itertools.chain([first], chunks)):
                self.raw(entry.get("body_sha256"), "body")
                self.raw(entry.get("extract_sha256"), "extract")
        elif column == "recipe_json":
            import re

            from .legacy_recipes import scope_references

            def observed_blocks() -> Iterator[bytes]:
                recipe_tail = b""
                for block in blocks:
                    recipe_block = recipe_tail + block
                    for match in re.finditer(
                        rb'"input_bundle_hash"\s*:\s*"([a-f0-9]{64})"', recipe_block
                    ):
                        self.bundles.add(match[1].decode("ascii"))
                    recipe_tail = recipe_block[-160:]
                    yield block

            self.legacy_scopes.update(scope_references(observed_blocks()))

    def verify(self, source: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
        entries = manifest["files"]

        def need(name: str) -> Path:
            path = source / "files" / name
            if name not in entries or not path.is_file():
                raise ValueError("decoded COPY history requires a missing backup file: " + name)
            self.required.add(name)
            return path

        for identifier in self.evicted:
            if identifier not in self.outputs:
                raise ValueError("evicted history generation is absent from COPY data")
            self.packs.add(("derivation_output", self.outputs[identifier]))
        for kind, digest in self.packs:
            if (kind, digest) not in self.objects:
                raise ValueError("decoded packed history has no storage ledger row")
            path = need(f"objects/{kind}/{digest[:2]}/{digest}")
            if kind == "parse_body":
                for entry in _manifest_entries(_packed_manifest(path)):
                    self.raw(entry.get("body_sha256"), "body")
                    self.raw(entry.get("extract_sha256"), "extract")
        metadata = manifest["metadata"]
        if metadata.get("input_bundle_hash"):
            self.bundles.add(metadata["input_bundle_hash"])
        manifests: set[str] = set()
        candidates = {
            value
            for value in (
                metadata.get("baseline_candidate"),
                *(metadata.get("pending_candidates") or []),
            )
            if value is not None
        }
        for candidate in candidates:
            if (
                not isinstance(candidate, str)
                or Path(candidate).name != candidate
                or candidate in {".", ".."}
            ):
                raise ValueError("backup candidate identity is unsafe")
            prefix = "candidates/" + candidate
            built = json.loads(need(prefix + "/BUILT").read_text())
            candidate_manifest = need(prefix + "/_meta/manifest.json")
            if (
                hashlib.sha256(candidate_manifest.read_bytes()).hexdigest()
                != built["manifest_hash"]
            ):
                raise ValueError("backup candidate manifest differs from BUILT")
            value = json.loads(candidate_manifest.read_text())
            need(
                prefix
                + (
                    "/PUBLISHED"
                    if candidate == metadata.get("baseline_candidate")
                    else "/PUBLISHING"
                )
            )
            if value.get("input_bundle_hash"):
                self.bundles.add(str(value["input_bundle_hash"]))
            policy = value.get("release_policy") or {}
            if policy.get("mode") == "partitioned":
                manifests.add(str(policy["manifest_digest"]))
        for name in entries:
            if name.startswith("holds/"):
                hold = json.loads(need(name).read_text())
                for digest in hold.get("artifacts", []):
                    blob = f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
                    self.raw(digest, "body" if blob in entries else "extract")
        for digest in manifests:
            preferred = f"objects/release_manifest/{digest[:2]}/{digest}"
            path = need(
                preferred if preferred in entries else f"partitions/manifests/{digest}.json"
            )
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("backup release manifest identity differs")
            for _, artifact in json.loads(path.read_text())["built"]:
                need(f"partitions/{artifact[:2]}/{artifact}/sidecar.json")
        from .legacy_recipes import resolve_scope_bundles

        legacy_bundles = resolve_scope_bundles(
            {name: source / "files" / name for name in entries},
            self.legacy_scopes,
            preferred=self.bundles,
        )
        self.bundles.update(legacy_bundles.values())
        for digest in self.bundles:
            Archive._validate_hash(digest)
            value = json.loads(need(f"inputs/{digest}/manifest.json").read_text())
            if hashlib.sha256(canonical(value)).hexdigest() != digest:
                raise ValueError("backup input bundle identity differs")
            for name, wanted in value.items():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("backup input bundle member path is unsafe")
                path = need(f"inputs/{digest}/{name}")
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != wanted:
                        raise ValueError("backup input bundle member digest differs")
        for name in entries:
            parts = Path(name).parts
            if parts and parts[0] == "inputs" and (len(parts) < 3 or parts[1] not in self.bundles):
                raise ValueError("backup contains an unreferenced captured input bundle")
        for name in tuple(self.required):
            need(name)
        return {"complete": True, "required_files": len(self.required)}
