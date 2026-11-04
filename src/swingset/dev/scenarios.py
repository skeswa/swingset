"""Named, bounded fixture scenarios; every write stays in a new dev seed."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from swingset.clock import FakeClock
from swingset.state.environment import ensure_dev

NAMES = ("source-parse", "event-projection", "identity-correction", "release-gate")


def prepare(state: Path, scenario: str) -> dict[str, Any]:
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture
    from swingset.versions import versions

    from . import seed

    if scenario not in NAMES:
        raise ValueError("Unknown development scenario")
    with open_database(state) as database:
        ensure_dev(database.connection)
        if scenario == "source-parse":
            row = database.connection.execute(
                "SELECT s.snapshot_id FROM snapshots s JOIN watches w ON w.watch_id=s.watch_id WHERE w.parser='wsdc_registry.dancer' ORDER BY s.snapshot_id LIMIT 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("Source scenario is missing its captured registry input")
            return {"name": scenario, "unit": "parse/snapshot/" + str(row[0])}
        if scenario == "event-projection":
            return {
                "name": scenario,
                "unit": "project/event/" + seed.EVENT_ID,
                "event": seed.EVENT_ID,
            }
        clock = FakeClock(seed.NOW)
        bundle = capture(state / "config", state / "overrides", state, versions())
        accept(database, bundle, clock)
        if scenario == "identity-correction":
            from swingset.fetch.archive import Archive
            from swingset.schedule.derive import derive_one
            from swingset.state.identity_journal import Decision, encode_journal
            from swingset.state.identity_references import references_for_subject
            from swingset.state.work import WorkUnit

            row = database.connection.execute(
                "SELECT entry_id FROM entries WHERE event_id=%s ORDER BY entry_id LIMIT 1",
                (seed.EVENT_ID,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Identity scenario requires a projected fixture participant")
            bindings = references_for_subject(database.connection, "entry", str(row[0]))
            if not bindings:
                raise RuntimeError("Identity scenario is missing its retained source reference")
            first = Decision(
                "dev-review-1",
                **asdict(bindings[0].reference),
                wsdc_id="1",
                decision="insufficient_evidence",
                evidence="offline synthetic fixture",
                reason="Fixture names alone do not establish identity",
                author="Development fixture",
                date=seed.NOW.date().isoformat(),
                supersedes="",
            )
            corrected = replace(
                first,
                decision_id="dev-review-2",
                wsdc_id="NONE",
                decision="hold_unlinked",
                reason="Reviewed fixture stays unlinked",
                supersedes=first.decision_id,
            )
            run_id = database.start_run(clock.now())
            for decisions in ((first,), (first, corrected)):
                (state / "overrides" / "identity_overrides.csv").write_bytes(
                    encode_journal(decisions)
                )
                bundle = capture(state / "config", state / "overrides", state, versions())
                accept(database, bundle, clock)
                outcome = derive_one(
                    database,
                    Archive(state),
                    WorkUnit("link", "event", seed.EVENT_ID),
                    bundle,
                    clock,
                    run_id,
                )
                if outcome.failed:
                    raise RuntimeError("Identity correction fixture did not relink successfully")
                clock.sleep(1)
            database.connection.execute(
                "UPDATE runs SET finished_at=%s,summary_json=%s WHERE run_id=%s",
                (clock.now().isoformat(), json.dumps({"development_scenario": scenario}), run_id),
            )
            return {
                "name": scenario,
                "unit": "link/event/" + seed.EVENT_ID,
                "subject": str(row[0]),
                "decisions": [first.decision_id, corrected.decision_id],
            }
        from swingset.build import BuildMetadata, build_candidate, read_build_input
        from swingset.publish import gate

        with database.reader():
            data = read_build_input(database.connection, bundle)
        metadata = BuildMetadata(
            run_id="dev-release-gate",
            repository_commit="offline-fixture",
            expected_parent=None,
            schema_version=1,
            versions=versions(),
            card=b"Offline development fixture\n",
            built_at=seed.NOW,
            candidate_id="dev-gate-baseline",
        )
        baseline = build_candidate(state, data, metadata)
        # A real candidate built from a deliberately incomplete local view lets
        # an edit exercise the release-decrease gate without publishing anything.
        tables = {name: list(rows) for name, rows in data.tables.items()}
        if not tables.get("dancers"):
            raise RuntimeError("Release gate scenario requires its captured dancer")
        tables["dancers"] = tables["dancers"][1:]
        candidate = build_candidate(
            state,
            replace(data, tables=tables, input_bundle_hash=data.input_bundle_hash + ":incomplete"),
            replace(metadata, candidate_id="dev-gate-incomplete"),
        )
        verdict = gate.evaluate(
            gate.measure(candidate.path),
            gate.baseline_measures(baseline.path),
            baseline_candidate=baseline.candidate_id,
            candidate_contests=gate.contest_results(candidate.path),
            baseline_contests=gate.contest_results(baseline.path),
        )
        if verdict["state"] != "refused":
            raise RuntimeError("Release gate fixture unexpectedly accepted its missing dancer")
        (state / "development-gate.json").write_text(json.dumps(verdict, sort_keys=True) + "\n")
        return {
            "name": scenario,
            "baseline": baseline.candidate_id,
            "candidate": candidate.candidate_id,
            "verdict": verdict,
        }
