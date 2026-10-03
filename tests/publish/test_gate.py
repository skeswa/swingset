"""The strictly-better publication gate (D-0225)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from swingset.build.builder import BuildInput, BuildMetadata, BuildResult, build_candidate
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.publish import gate
from swingset.publish.safety import StaleCandidateError, verify_candidate_files
from swingset.publish.service import RemoteCommit, publish

HEADER = ",".join(gate.HEADER)


class Hub:
    def __init__(self, head: str | None = None) -> None:
        self.current = head
        self.commits: dict[str, RemoteCommit] = {}
        self.calls = 0

    def head(self) -> str | None:
        return self.current

    def is_initial_head(self, commit: str) -> bool:
        return False

    def inspect(self, commit: str) -> RemoteCommit:
        return self.commits[commit]

    def create_commit(
        self,
        *,
        parent: str | None,
        message: str,
        additions: dict[str, Path],
        deletions: tuple[str, ...],
    ) -> str:
        self.calls += 1
        sha = f"commit-{self.calls}"
        candidate = additions["_meta/manifest.json"].parents[1]
        built = json.loads((candidate / "BUILT").read_text())
        files = {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in additions.items()
        }
        self.commits[sha] = RemoteCommit(sha, parent, candidate.name, built["manifest_hash"], files)
        self.current = sha
        return sha


def _row(table: str, **values: object) -> dict[str, object]:
    row: dict[str, object] = {field.name: None for field in SCHEMAS[table]}
    row.update(values)
    return row


def tables(linked: int, unlinked: int = 0) -> dict[str, list[dict[str, object]]]:
    """`linked` judges with a confirmed link and WSDC id, plus `unlinked` judges without."""
    judges = [_row("judges", judge_id=f"j{n}", wsdc_id=n + 1) for n in range(linked)]
    judges += [_row("judges", judge_id=f"u{n}") for n in range(unlinked)]
    links = [
        _row(
            "identity_links",
            link_id=f"l{n}",
            subject_kind="judge",
            subject_id=f"j{n}",
            wsdc_id=n + 1,
            status="confirmed",
        )
        for n in range(linked)
    ]
    return {"judges": judges, "identity_links": links}


def build(
    state: Path,
    name: str,
    rows: dict[str, list[dict[str, object]]],
    *,
    overrides: str = "",
    raw: bytes | None = None,
    correction: bool = False,
    parent: str | None = None,
    policy: dict[str, Any] | None = None,
) -> BuildResult:
    data = BuildInput(
        tables=rows,
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={"canonical": 1},
        # The bundle names the override file, so a new override builds afresh.
        captured_file_hashes={"overrides/release_decreases.csv": overrides},
        input_bundle_hash=f"bundle-{name}",
        release_policy=policy,
    )
    meta = BuildMetadata(
        run_id="run",
        repository_commit="code",
        expected_parent=parent,
        schema_version=1,
        versions={"parser": "1"},
        card=b"card\n",
        built_at=datetime(2026, 9, 29, tzinfo=UTC),
        candidate_id=name,
        decrease_overrides=raw
        if raw is not None
        else tuple(
            dict(zip(gate.HEADER, line.split(","), strict=True))
            for line in overrides.splitlines()
            if line
        ),
        correction_only=correction,
    )
    return build_candidate(state, data, meta)


def publish_baseline(state: Path, candidate: BuildResult, commit: str) -> None:
    (candidate.path / "PUBLISHED").write_text(json.dumps({"commit": commit}))
    link = state / "baseline"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(Path("candidates") / candidate.candidate_id)


def verdict(candidate: BuildResult) -> dict[str, Any]:
    return dict(json.loads((candidate.path / gate.VERDICT).read_bytes()))


@pytest.fixture
def baseline(tmp_path: Path) -> Path:
    publish_baseline(tmp_path, build(tmp_path, "base", tables(2)), "c-base")
    return tmp_path


def test_fewer_links_are_refused_with_the_reason_and_nothing_is_submitted(baseline: Path) -> None:
    fewer = build(baseline, "fewer", tables(1, unlinked=1), parent="c-base")
    result = verdict(fewer)
    assert result["state"] == "refused"
    assert result["reasons"] == [
        "decrease:confirmed_links 2->1",
        "decrease:linked_judges 2->1",
    ]
    assert result["measures"]["judges"] == {"baseline": 2, "candidate": 2}
    assert result["baseline"] == {"commit": "c-base", "candidate_id": "base"}
    hub = Hub("c-base")
    published = publish(baseline, fewer.path, hub)
    assert published.state == "refused"
    assert published.reason == "decrease:confirmed_links 2->1; decrease:linked_judges 2->1"
    assert hub.calls == 0
    assert not (fewer.path / "PUBLISHING").exists()
    # A refusal is not a rejection: the candidate stays whole.
    assert not (fewer.path / "REJECTED").exists()
    verify_candidate_files(fewer.path)
    assert publish(baseline, fewer.path, hub, dry_run=True).state == "refused"


def test_an_exact_override_passes_and_is_named_in_the_verdict(baseline: Path) -> None:
    rows = [
        "confirmed_links,2,1,duplicate judge merged,owner,2026-09-29",
        "linked_judges,2,1,duplicate judge merged,owner,2026-09-29",
    ]
    passed = build(
        baseline, "approved", tables(1, unlinked=1), overrides="\n".join(rows), parent="c-base"
    )
    result = verdict(passed)
    assert result["state"] == "pass"
    assert [row["measure"] for row in result["overrides_used"]] == [
        "confirmed_links",
        "linked_judges",
    ]
    assert all(row["override"] for row in result["decreases"])
    hub = Hub("c-base")
    assert publish(baseline, passed.path, hub).state == "published"
    assert hub.calls == 1


def test_an_override_is_a_floor_for_its_baseline(baseline: Path) -> None:
    """A row approves any candidate value from the one it names up to the
    baseline: a build a day later with a little more still passes."""
    rows = [
        "confirmed_links,2,0,judges may merge,owner,2026-09-29",
        "linked_judges,2,0,judges may merge,owner,2026-09-29",
    ]
    passed = build(
        baseline, "floor", tables(1, unlinked=1), overrides="\n".join(rows), parent="c-base"
    )
    result = verdict(passed)
    assert result["state"] == "pass"
    assert [row["measure"] for row in result["overrides_used"]] == [
        "confirmed_links",
        "linked_judges",
    ]


def test_a_larger_decrease_or_another_baseline_is_refused(baseline: Path) -> None:
    rows = [
        "confirmed_links,2,1,one judge merged,owner,2026-09-29",
        "linked_judges,3,1,wrong baseline,owner,2026-09-29",
    ]
    refused = build(
        baseline, "mismatch", tables(0, unlinked=2), overrides="\n".join(rows), parent="c-base"
    )
    result = verdict(refused)
    assert result["state"] == "refused"
    assert result["overrides_used"] == []
    assert result["reasons"] == [
        "decrease:confirmed_links 2->0",
        "decrease:linked_judges 2->0",
    ]


def test_a_malformed_override_file_refuses_the_release_with_its_line(baseline: Path) -> None:
    """The build completes; the verdict names the line, and nothing publishes."""
    body = f"{HEADER}\nevent,2105,1801,approved,owner,2026-09-29\n".encode()
    refused = build(baseline, "typo", tables(3), raw=body, parent="c-base")
    result = verdict(refused)
    assert result["state"] == "refused"
    assert result["reasons"] == [
        "overrides_invalid:release_decreases.csv:2: unknown measure 'event'"
    ]
    assert publish(baseline, refused.path, Hub("c-base")).state == "refused"
    # Unreadable bytes refuse the same way.
    garbled = build(baseline, "garbled", tables(3), raw=b"\xff\xfe\x00", parent="c-base")
    assert verdict(garbled)["reasons"][0].startswith("overrides_invalid:")


def test_capturing_a_malformed_override_file_stops_no_loop(tmp_path: Path) -> None:
    """Every loop captures the bundle; only the gate reads release_decreases.csv."""
    import shutil

    from swingset.state.inputs import capture
    from swingset.versions import versions

    overrides = tmp_path / "overrides"
    shutil.copytree(Path(__file__).parents[2] / "overrides", overrides)
    (overrides / gate.FILE).write_text(f"{HEADER}\nevent,2105,1801,x,owner,2026-09-29\n")
    bundle = capture(Path(__file__).parents[2] / "config", overrides, tmp_path, versions())
    assert bundle.files[f"overrides/{gate.FILE}"].startswith(HEADER.encode())
    # Another override file is still validated at capture.
    (overrides / "suppressions.csv").write_text("wsdc_id,wsdc_id\n")
    with pytest.raises(ValueError, match="duplicate CSV header"):
        capture(Path(__file__).parents[2] / "config", overrides, tmp_path, versions())


@pytest.mark.parametrize(
    ("measure_name", "candidate", "reason"),
    [
        # A table the candidate lacks has no rows.
        ("heats", None, "decrease:heats 5->0"),
        ("linked_entries", 4, "decrease:linked_entries 5->4"),
        ("events", 3, "decrease:events 5->3"),
    ],
)
def test_every_measure_is_judged(measure_name: str, candidate: int | None, reason: str) -> None:
    before = dict.fromkeys(gate.MEASURES, 5)
    after: dict[str, int | None] = {**before, measure_name: candidate}
    result = gate.evaluate(after, before)
    assert result["state"] == "refused" and result["reasons"] == [reason]
    assert gate.evaluate(before, before)["state"] == "pass"


def test_increases_pass(baseline: Path) -> None:
    more = build(baseline, "more", tables(3), parent="c-base")
    result = verdict(more)
    assert result["state"] == "pass"
    assert result["decreases"] == []
    assert result["measures"]["confirmed_links"] == {"baseline": 2, "candidate": 3}
    assert publish(baseline, more.path, Hub("c-base")).state == "published"


def test_no_baseline_passes(tmp_path: Path) -> None:
    first = build(tmp_path, "first", tables(0, unlinked=1))
    result = verdict(first)
    assert result["state"] == "pass"
    assert result["baseline"] is None
    assert result["measures"]["judges"] == {"baseline": None, "candidate": 1}


def test_the_first_release_after_the_zero_link_baseline_passes(tmp_path: Path) -> None:
    publish_baseline(tmp_path, build(tmp_path, "zero", tables(0, unlinked=3)), "c-zero")
    linked = build(tmp_path, "linked", tables(3), parent="c-zero")
    result = verdict(linked)
    assert result["state"] == "pass"
    assert result["measures"]["confirmed_links"] == {"baseline": 0, "candidate": 3}
    assert result["measures"]["linked_judges"] == {"baseline": 0, "candidate": 3}


def test_the_verdict_is_in_the_hashed_closure_but_not_the_content_hash(baseline: Path) -> None:
    fewer = build(baseline, "fewer", tables(1, unlinked=1), parent="c-base")
    manifest = json.loads((fewer.path / "_meta" / "manifest.json").read_bytes())
    body = (fewer.path / gate.VERDICT).read_bytes()
    assert manifest["files"][gate.VERDICT] == hashlib.sha256(body).hexdigest()
    # The same rows as the baseline, with another verdict, are unchanged.
    again = build(baseline, "again", tables(2), parent="c-base")
    base = json.loads((baseline / "baseline" / "BUILT").read_bytes())
    assert again.content_hash == base["content_hash"]
    assert not again.changed
    # Editing the verdict breaks the closure, and publication refuses it.
    (fewer.path / gate.VERDICT).write_bytes(body.replace(b'"refused"', b'"pass"'))
    with pytest.raises(StaleCandidateError, match="changed after build"):
        verify_candidate_files(fewer.path)
    refused = publish(baseline, fewer.path, Hub("c-base"))
    assert refused.state == "refused"
    assert refused.reason == "verdict_changed_after_build"


def test_a_correction_records_its_decreases_but_is_exempt(baseline: Path) -> None:
    corrected = build(baseline, "corrected", tables(1), correction=True, parent="c-base")
    result = verdict(corrected)
    assert result["state"] == "pass"
    assert result["exempt"] == "correction"
    assert {row["measure"] for row in result["decreases"]} == {
        "confirmed_links",
        "judges",
        "linked_judges",
    }
    assert result["reasons"] == []


def test_a_candidate_without_a_verdict_is_evaluated_at_publication(baseline: Path) -> None:
    fewer = build(baseline, "legacy", tables(1, unlinked=1), parent="c-base")
    manifest_path = fewer.path / "_meta" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["files"][gate.VERDICT]
    (fewer.path / gate.VERDICT).unlink()
    manifest_path.write_bytes(json.dumps(manifest).encode())
    result = gate.read(baseline, fewer.path)
    assert result["evaluated"] == "at_publication"
    assert result["reasons"] == [
        "decrease:confirmed_links 2->1",
        "decrease:linked_judges 2->1",
    ]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"measure,reason\nlinked_judges,x\n", "header"),
        (f"{HEADER}\nlinks,2,1,r,o,2026-09-29\n".encode(), "unknown measure"),
        (f"{HEADER}\nlinked_judges,1,2,r,o,2026-09-29\n".encode(), "names a decrease"),
        (f"{HEADER}\nlinked_judges,2,1,,o,2026-09-29\n".encode(), "empty reason"),
        (f"{HEADER}\nlinked_judges,2,1,r,o,yesterday\n".encode(), "isoformat"),
        (
            f"{HEADER}\nlinked_judges,2,1,r,o,2026-09-29\nlinked_judges,2,1,s,o,2026-09-29\n".encode(),
            "duplicate",
        ),
    ],
)
def test_malformed_overrides_are_rejected(body: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        gate.parse_overrides(body)


def test_the_shipped_override_file_is_valid() -> None:
    body = (Path(__file__).parents[2] / "overrides" / gate.FILE).read_bytes()
    assert body.decode().strip() == HEADER
    assert gate.parse_overrides(body) == ()


def test_a_refused_build_is_recorded_in_the_summary_and_manifest_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swingset.schedule import release_steps
    from swingset.state import release_store
    from swingset.state.db import open_database

    state = tmp_path / "state"
    with open_database(state, lock=False) as database:
        publish_baseline(state, build(state, "base", tables(2)), "c-base")
        with database.transaction() as conn:
            release_store.upsert(
                conn, release_store.ManifestRow("m1", 10, "2026-09-29T00:00:00+00:00")
            )
        fewer = build(
            state,
            "fewer",
            tables(1, unlinked=1),
            parent="c-base",
            policy={"manifest_digest": "m1"},
        )
        monkeypatch.setattr(release_steps, "build", lambda *_args, **_kwargs: fewer)
        hub = Hub("c-base")
        for dry_run in (True, False):
            summary: dict[str, Any] = {"stages": []}
            # `build` is patched, so the bundle and clock are never read.
            release_steps.build_and_publish(
                database, cast(Any, None), cast(Any, None), "run", summary, hub=hub, dry_run=dry_run
            )
            assert summary["verdict"]["state"] == "refused"
            assert summary["verdict"]["measures"]["confirmed_links"] == [2, 1]
        assert summary["publication"]["state"] == "refused"
        assert hub.calls == 0
        row = release_store.get(database.connection, release_store.ManifestRow, "m1")
        assert row is not None and row.verdict_json is not None
        assert json.loads(row.verdict_json)["reasons"][0] == "decrease:confirmed_links 2->1"


def test_events_with_placements_count_distinct_events(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory = tmp_path / "data" / "placements"
    directory.mkdir(parents=True)
    rows = [
        _row("placements", placement_id=f"p{n}", event_id=event)
        for n, event in enumerate(["a", "a", "b", None])
    ]
    pq.write_table(
        pa.Table.from_pylist(rows, schema=SCHEMAS["placements"]), directory / "p.parquet"
    )
    measures = gate.measure(tmp_path)
    assert measures["events_with_placements"] == 2
    assert measures["placements"] == 4
    assert measures["events"] is None
    result = gate.evaluate(measures, {**measures, "events_with_placements": 3})
    assert result["reasons"] == ["decrease:events_with_placements 3->2"]


def _write(directory: Path, table: str, rows: list[dict[str, object]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = directory / "data" / table
    path.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMAS[table]), path / f"{table}.parquet")


def test_linked_entries_and_judges_are_counted_from_the_candidate_files(tmp_path: Path) -> None:
    """`measure` counts the non-null WSDC ids of entries and judges itself."""

    def candidate(name: str, entries: list[int | None], judges: list[int | None]) -> Path:
        directory = tmp_path / name
        _write(
            directory,
            "entries",
            [_row("entries", entry_id=f"e{n}", wsdc_id=value) for n, value in enumerate(entries)],
        )
        _write(
            directory,
            "judges",
            [_row("judges", judge_id=f"j{n}", wsdc_id=value) for n, value in enumerate(judges)],
        )
        return directory

    before = gate.measure(candidate("base", [1, 2, None, 3], [None, 4]))
    assert (before["entries"], before["linked_entries"]) == (4, 3)
    assert (before["judges"], before["linked_judges"]) == (2, 1)
    after = gate.measure(candidate("next", [1, None, None, 3], [None, 4]))
    assert after["linked_entries"] == 2 and after["entries"] == 4
    result = gate.evaluate(after, before)
    assert result["state"] == "refused"
    assert result["reasons"] == ["decrease:linked_entries 3->2"]
