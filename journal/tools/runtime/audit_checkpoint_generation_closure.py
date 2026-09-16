"""Read-only audit of generation artifact references against a private checkpoint."""

import argparse
import gzip
import hashlib
import json
import sqlite3
import zlib
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(checkpoint, live):
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    database = checkpoint / "state.sqlite"
    referenced = {}
    generations = Counter()
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as conn:
        for identifier, state, raw in conn.execute(
            "SELECT generation_id,state,manifest_json FROM source_generations ORDER BY generation_id"
        ):
            generations[state] += 1
            for item in json.loads(raw):
                for field, kind in (("body_sha256", "body"), ("extract_sha256", "extract")):
                    digest = item.get(field)
                    if not digest:
                        continue
                    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                        raise ValueError("generation contains invalid artifact digest")
                    relative = (
                        f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
                        if kind == "body"
                        else f"extracts/{digest}"
                    )
                    record = referenced.setdefault(
                        relative,
                        {
                            "kind": kind,
                            "sha256": digest,
                            "generation_states": Counter(),
                            "generation_examples": [],
                        },
                    )
                    record["generation_states"][state] += 1
                    if len(record["generation_examples"]) < 3:
                        record["generation_examples"].append(identifier)
    omitted = []
    for relative, record in sorted(referenced.items()):
        if relative in manifest["files"] and (checkpoint / relative).is_file():
            continue
        path = live / relative
        record = {
            "path": relative,
            **record,
            "live_exists": path.is_file(),
            "live_digest_valid": False,
        }
        if record["live_exists"]:
            try:
                body = path.read_bytes()
                decoded = gzip.decompress(body) if record["kind"] == "body" else body
                record["live_digest_valid"] = (
                    hashlib.sha256(decoded).hexdigest() == record["sha256"]
                )
                record["live_bytes"] = len(body)
            except (OSError, ValueError, EOFError, zlib.error) as exc:
                record["live_error"] = type(exc).__name__
        omitted.append(record)
    return {
        "at": datetime.now(UTC).isoformat(),
        "checkpoint": str(checkpoint),
        "live_state": str(live),
        "checkpoint_manifest_sha256": sha256(checkpoint / "checkpoint.json"),
        "checkpoint_database_sha256": sha256(database),
        "checkpoint_recorded_database_sha256": manifest["files"]["state.sqlite"]["sha256"],
        "schema_version": manifest["schema_version"],
        "generation_counts": dict(generations),
        "unique_generation_artifacts": len(referenced),
        "omitted_artifacts": omitted,
        "omitted_by_kind": dict(Counter(row["kind"] for row in omitted)),
        "omitted_live_exists": sum(row["live_exists"] for row in omitted),
        "omitted_live_verified": sum(row["live_digest_valid"] for row in omitted),
        "immutable_checkpoint_reader": True,
        "network_requests": 0,
        "checkpoint_mutations": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--live", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.output.exists()
        or args.output.resolve().is_relative_to(args.checkpoint.resolve())
        or args.output.resolve().is_relative_to(args.live.resolve())
    ):
        raise ValueError("receipt must be new and outside checkpoint and live state")
    report = audit(args.checkpoint, args.live)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "omitted_artifacts"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
