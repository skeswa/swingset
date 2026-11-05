"""Execute only frozen b6cc restore/build APIs inside the private mounted image."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import resource
import stat
import sys
import time
from pathlib import Path, PosixPath
from typing import Any, cast

if __package__:
    from . import reference_policy as policy
else:
    # The checked standalone bundle runs outside the frozen application's package.
    import reference_policy as policy  # type: ignore[import-not-found,no-redef]

P: dict[str, Any]
ROOT: Path
MOUNT: Path
STATE: Path
CONTROL: Path
SOURCE: Path
RESULT: Path
START: float
report: dict[str, Any]


def configure(payload: dict[str, Any]) -> None:
    global P, ROOT, MOUNT, STATE, CONTROL, SOURCE, RESULT, START, report, written_since_sync
    P = policy.validate_runtime(payload)
    ROOT = Path(P["vm_root"])
    MOUNT = ROOT / "mounted"
    STATE, CONTROL, SOURCE = MOUNT / "state", MOUNT / "control", MOUNT / "checkpoint-source"
    RESULT = CONTROL / "worker-result.json"
    START = time.monotonic()
    written_since_sync = 0
    report = {
        "operation": P["operation"],
        "phases": [],
        "network": "private network namespace without external interfaces",
        "no_publication": True,
    }


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def phase(name: str, **data: Any) -> None:
    report["phases"].append({"phase": name, "seconds": round(time.monotonic() - START, 3), **data})
    RESULT.write_text(json.dumps(report, sort_keys=True, default=str) + "\n")


def guard() -> None:
    lease = json.loads((ROOT / "lease.json").read_text())
    if lease.get("nonce") != P["nonce"] or lease.get("stop"):
        raise RuntimeError("host storage lease stopped")
    if time.monotonic() - lease["vm_monotonic_received"] > 10:
        raise RuntimeError("host storage lease stale")
    if lease["host_free_bytes"] < P["stop_free_bytes"]:
        raise RuntimeError("Mac free-space stop")
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < P["stop_free_bytes"]:
        raise RuntimeError("VM free-space stop")
    image = policy.identity(ROOT / "filesystem.img")
    if (
        not policy.same_identity(image, P["image_identity"])
        or image["logical_bytes"] != P["backend_cap_bytes"]
    ):
        raise RuntimeError("backend identity or capacity changed")
    if image["allocated_bytes"] >= P["backend_cap_bytes"] - 128 * 1024**2:
        raise RuntimeError("backend allocated-byte stop")


# The pinned maintained restore calls Path.open for its streamed output. This
# path adapter adds bounded filesystem sync/space checks without rewriting its
# archive verification, SQL checks, extraction, or restore protocol.
written_since_sync = 0


class CheckedWriter:
    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def __enter__(self) -> CheckedWriter:
        self.wrapped.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self.wrapped.__exit__(*args)

    def write(self, data: bytes | str) -> int:
        global written_since_sync
        guard()
        n: int = self.wrapped.write(data)
        written_since_sync += len(data.encode() if isinstance(data, str) else data)
        if written_since_sync >= 64 * 1024**2:
            self.wrapped.flush()
            policy.checked_command(ROOT, ["sync", "-f", str(MOUNT)], timeout=30)
            written_since_sync = 0
            guard()
        return n


class CheckedPath(PosixPath):
    def open(self, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        f = super().open(mode, *args, **kwargs)
        return CheckedWriter(f) if any(x in mode for x in "wax+") else f


def readonly_preflight() -> dict[str, Any]:
    import platform

    stats_before = Path("/proc/self/status").read_text()
    # This process already has private mount+network namespaces. These are
    # per-mount read-only binds, never a shared-filesystem remount.
    for path in ["/tmp", "/var/tmp", "/var/lib/swingset", "/root"]:
        if Path(path).exists():
            policy.checked_command(ROOT, ["mount", "--bind", path, path], timeout=15)
            policy.checked_command(ROOT, ["mount", "-o", "remount,bind,ro", path], timeout=15)
    policy.checked_command(ROOT, ["mount", "-o", "remount,bind,ro", "/"], timeout=15)
    import pyarrow

    import swingset
    from swingset.state import inputs
    from swingset.versions import repository_identity, versions

    source = Path(P["source_tree"]) / "src/swingset"
    if (
        Path(swingset.__file__).resolve().parent != source
        or repository_identity(source) != P["source_identity"]
    ):
        raise RuntimeError("read-only preflight source/package differs")
    if digest(Path(P["runtime_wrapper"])) != P["runtime_wrapper_sha256"]:
        raise RuntimeError("read-only preflight wrapper differs")
    original = Path("/var/lib/swingset/inputs") / P["input_bundle"]
    manifest_raw = (original / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    if (
        hashlib.sha256(manifest_raw).hexdigest() != P["input_bundle"]
        or hashlib.sha256(canonical).hexdigest() != P["input_bundle"]
    ):
        raise RuntimeError("raw/canonical frozen input manifest differs")
    pretend = Path("/nonexistent-reference-r-" + P["nonce"])
    if pretend.exists():
        raise RuntimeError("read-only capture target unexpectedly exists")
    checked: list[str] = []

    def verify_write(path: Path, body: bytes) -> None:
        relative = path.relative_to(pretend / "inputs" / P["input_bundle"])
        if (original / relative).read_bytes() != body:
            raise RuntimeError("read-only runtime recapture changes a frozen file")
        checked.append(relative.as_posix())

    # Preserve the pinned capture implementation; replace only its write sink
    # with a byte verifier. All source paths are read-only at the OS layer too.
    # This pinned SQLite runtime exposes a writer outside the native module API.
    original_write = cast(Any, inputs).durable_write
    cast(Any, inputs).durable_write = verify_write
    try:
        bundle = inputs.capture(original / "config", original / "overrides", pretend, versions())
    finally:
        cast(Any, inputs).durable_write = original_write
    if bundle.digest != P["input_bundle"] or set(checked) != (set(manifest) | {"manifest.json"}):
        raise RuntimeError("read-only full input recapture differs or is incomplete")

    def memory(text: str) -> dict[str, str]:
        return {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith(("VmSize:", "VmPeak:", "VmRSS:", "VmHWM:"))
        }

    return {
        "result": "pass",
        "source_identity": P["source_identity"],
        "input_bundle": bundle.digest,
        "frozen_manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "frozen_manifest_canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "checked_files": len(checked),
        "package": str(source),
        "python": platform.python_version(),
        "pyarrow": pyarrow.__version__,
        "runtime_environment_names": sorted(os.environ),
        "memory_before_import": memory(stats_before),
        "memory_after_import_and_recapture": memory(Path("/proc/self/status").read_text()),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "application_writes": False,
    }


def require_buildable_state(state: Path) -> None:
    marker = state / "RESTORE_PENDING"
    if marker.exists() or marker.is_symlink():
        raise RuntimeError("reference restore left an activation barrier")


def fresh_candidate(directory: Path, before: set[str]) -> Path:
    candidates = [
        path
        for path in directory.iterdir()
        if path.name not in before and (path / "BUILT").is_file()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "build did not create exactly one fresh R; cached candidates are not accepted"
        )
    candidate = candidates[0]
    if candidate.is_symlink() or any(
        (candidate / name).exists() for name in ("PUBLISHING", "PUBLISHED")
    ):
        raise RuntimeError("reference candidate has an unsafe path or publication intent")
    return candidate


def build_reference() -> int:
    try:
        os.chdir(STATE)
        guard()
        for name in list(os.environ):
            if (
                "TOKEN" in name
                or "DSN" in name
                or name in {"DATABASE_URL", "SWINGSET_DATABASE_URL"}
            ):
                raise RuntimeError("secret-bearing environment name inherited")
        import swingset
        from swingset import cli
        from swingset.backup import restore_set
        from swingset.backup.huggingface import safe_relative
        from swingset.state.inputs import capture
        from swingset.versions import repository_identity, versions

        expected_package = Path(P["source_tree"]) / "src/swingset"
        if Path(swingset.__file__).resolve().parent != expected_package:
            raise RuntimeError("runtime imports another source tree")
        if digest(Path(P["runtime_wrapper"])) != P["runtime_wrapper_sha256"]:
            raise RuntimeError("frozen runtime wrapper differs")
        if repository_identity(expected_package) != P["source_identity"]:
            raise RuntimeError("frozen source digest differs")
        manifest_path = SOURCE / "sets" / P["set_id"] / "backup.json"
        if digest(manifest_path) != P["manifest_sha256"]:
            raise RuntimeError("frozen manifest differs")
        manifest = json.loads(manifest_path.read_text())
        if (
            manifest["schema_version"] != 37
            or manifest["versions"]["repository"] != P["source_identity"]
        ):
            raise RuntimeError("checkpoint schema/source differs")
        if (
            manifest["input_bundle_hash"] != P["input_bundle"]
            or manifest["baseline_candidate"] != P["baseline_candidate"]
        ):
            raise RuntimeError("checkpoint input/baseline identity differs")
        if manifest.get("pending_candidate") is not None or manifest.get("missing"):
            raise RuntimeError("checkpoint has pending publication or missing files")
        if (
            manifest["database"]["raw_bytes"] != P["raw_bytes"]
            or manifest["database"]["raw_sha256"] != P["raw_sha256"]
        ):
            raise RuntimeError("frozen raw database identity differs")
        allowed = {f"sets/{P['set_id']}/backup.json", f"sets/{P['set_id']}/index.jsonl.zst"}
        allowed.update(f"sets/{P['set_id']}/{x['path']}" for x in manifest["database"]["parts"])
        allowed.update(f"segments/{x}.tar" for x in manifest["segments"])
        if len(allowed) != 45:
            raise RuntimeError("unexpected frozen transport inventory")

        class ReadOnlyFrozenTransport:
            name = "local:frozen04-read-only-reference"

            def fetch(self, relative: str, scratch: Path) -> Path:
                del scratch
                guard()
                if relative not in allowed or safe_relative(relative).as_posix() != relative:
                    raise RuntimeError("restore requested a path outside frozen inventory")
                path = SOURCE / relative
                if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                    raise RuntimeError("frozen transport item is not a regular file")
                return path

        phase("restore-start", package=str(expected_package), source_identity=P["source_identity"])
        # This pinned API performs every compressed hash check, full SQLite/FK
        # check, indexed-member digest, and complete closure verification. Unlike
        # install(), this seam leaves its private partial output intact on failure.
        summary = cast(Any, restore_set)._install(
            ReadOnlyFrozenTransport(),
            P["set_id"],
            CheckedPath(STATE),
            CheckedPath(CONTROL / "unused-fetch"),
            37,
            "full",
        )
        phase("restore-verified", summary=summary)
        guard()
        database = STATE / "state.sqlite"
        if database.stat().st_size != P["raw_bytes"] or digest(database) != P["raw_sha256"]:
            raise RuntimeError("stored decompressed SQLite differs from frozen04")
        if (STATE / "baseline").resolve() != STATE / "candidates" / P["baseline_candidate"]:
            raise RuntimeError("restored baseline path differs")
        published = json.loads((STATE / "baseline" / "PUBLISHED").read_text())
        if published["commit"] != P["baseline_commit"]:
            raise RuntimeError("restored baseline commit differs")
        bundle_root = STATE / "inputs" / P["input_bundle"]
        if digest(bundle_root / "manifest.json") != P["input_bundle"]:
            raise RuntimeError("restored input manifest differs")
        bundle = capture(bundle_root / "config", bundle_root / "overrides", STATE, versions())
        if bundle.digest != P["input_bundle"]:
            raise RuntimeError("frozen runtime recapture changes the input bundle")
        phase(
            "pre-build-identities-verified",
            raw_bytes=P["raw_bytes"],
            raw_sha256=P["raw_sha256"],
            input_bundle=bundle.digest,
            baseline_commit=published["commit"],
        )
        require_buildable_state(STATE)
        before_candidates = {p.name for p in (STATE / "candidates").iterdir()}
        # Keep the maintained CLI's admission/lease/run bookkeeping and normal
        # offline build path. No Hub adapter, fetch, backup or publication command.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(
                [
                    "build",
                    "--state",
                    str(STATE),
                    "--config",
                    str(bundle_root / "config"),
                    "--overrides",
                    str(bundle_root / "overrides"),
                ]
            )
        (CONTROL / "build-stdout.txt").write_text(output.getvalue())
        if code != 0:
            raise RuntimeError("normal offline build refused or failed")
        candidate = fresh_candidate(STATE / "candidates", before_candidates)
        from swingset.publish.safety import verify_candidate_files

        verify_candidate_files(candidate)
        built = json.loads((candidate / "BUILT").read_text())
        release_manifest = json.loads((candidate / "_meta/manifest.json").read_text())
        if (candidate / "PUBLISHED").exists() or (candidate / "PUBLISHING").exists():
            raise RuntimeError("reference candidate unexpectedly has publication intent")
        files = []
        for path in sorted(candidate.rglob("*")):
            if path.is_file():
                guard()
                files.append(
                    {
                        "path": path.relative_to(candidate).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": digest(path),
                    }
                )
        export_manifest = {
            "format": "swingset-reference-R-files-v1",
            "nonce": P["nonce"],
            "source_identity": P["source_identity"],
            "raw_sqlite_sha256": P["raw_sha256"],
            "input_bundle": P["input_bundle"],
            "baseline_commit": P["baseline_commit"],
            "candidate_id": candidate.name,
            "candidate_relative_path": candidate.relative_to(MOUNT).as_posix(),
            "files": files,
            "control_files": [
                "control/worker.log",
                "control/build-stdout.txt",
                "control/worker-result.json",
            ],
            "export_requirement": "Retain all candidate files and the final logs/reports on the owned server, verify sizes+SHA256 there, then obtain an exact-image cleanup verdict. No local archive or duplicateSQLite is required.",
        }
        (CONTROL / "export-manifest.json").write_text(
            json.dumps(export_manifest, sort_keys=True) + "\n"
        )
        phase(
            "reference-built",
            candidate_id=candidate.name,
            candidate_path=str(candidate),
            built=built,
            release_manifest=release_manifest,
            files=files,
            export_manifest_sha256=digest(CONTROL / "export-manifest.json"),
        )
        report["result"] = "pass"
    except BaseException as e:
        report["result"] = "failed"
        report["error_class"] = type(e).__name__
        report["error"] = str(e)[:1000]
    finally:
        report["wall_seconds"] = round(time.monotonic() - START, 3)
        RESULT.write_text(json.dumps(report, sort_keys=True, default=str) + "\n")
    return 0 if report["result"] == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "build"))
    args = parser.parse_args(argv)
    configure(json.loads(sys.stdin.readline()))
    if args.mode == "preflight":
        os.nice(19)
        # Affinity exists on the Linux VM, while the host checker runs on macOS.
        cast(Any, os).sched_setaffinity(0, {max(cast(Any, os).sched_getaffinity(0))})
        print(json.dumps(readonly_preflight(), sort_keys=True), flush=True)
        return 0
    return build_reference()


if __name__ == "__main__":
    raise SystemExit(main())
