"""Build completion retains full closure checks inside the output transaction."""

from itertools import groupby

import pytest
from test_h16_acceptance import release_state as release_state

from swingset.build import closure, generations, service
from swingset.build.builder import BuildError


def test_completion_validates_before_output_after_rows_and_before_certificate(
    release_state, monkeypatch
):
    f = release_state
    complete = generations.complete
    validate = closure.validate
    events = []

    def checked(conn, value):
        assert conn.in_transaction
        events.append(
            "validate" if conn.execute("PRAGMA query_only").fetchone()[0] == 0 else "proof"
        )
        return validate(conn, value)

    def finishing(*args, **kwargs):
        monkeypatch.setattr(closure, "validate", checked)
        f.conn.set_trace_callback(
            lambda sql: (
                events.append("artifact")
                if sql.startswith("INSERT INTO derivation_row_refs")
                else events.append("generation")
                if sql.startswith("INSERT INTO derivation_generations")
                else None
            )
        )
        try:
            return complete(*args, **kwargs)
        finally:
            f.conn.set_trace_callback(None)
            monkeypatch.setattr(closure, "validate", validate)

    monkeypatch.setattr(generations, "complete", finishing)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    # The closure is proved once in a read snapshot before the bounded write,
    # then rechecked at the three write boundaries (D-0179).
    assert events.count("proof") == 1
    assert events.count("validate") == 3
    # SQLite traces an INSERT again while entering its triggers. The rows stream
    # in first and the label is written from what was stored, so the generation
    # follows them; the re-check after the rows still rolls everything back
    # together.
    assert [kind for kind, _ in groupby(events)] == [
        "proof",
        "validate",
        "artifact",
        "generation",
        "validate",
    ]
    assert generations.completed(f.conn, result.candidate_id, result.manifest_hash)


@pytest.mark.parametrize("mutation", ["revocation", "policy", "snapshot"])
def test_changed_retained_support_rejects_before_output_and_rolls_back_retention(
    release_state, monkeypatch, mutation
):
    f = release_state
    complete = generations.complete
    retain = generations.retain
    candidates = []
    writes = []
    expected = {
        "revocation": "revoked",
        "policy": "policy_changed",
        "snapshot": "artifact_changed",
    }[mutation]

    def retained(conn, pinned):
        retain(conn, pinned)
        # Witness a real write in the same retention transaction; a rejected
        # closure must leave neither retained proof nor output committed.
        conn.execute(
            "INSERT INTO derivation_dependency_sets VALUES ('completion-rollback-witness','[]')"
        )

    def finishing(database, selection, result, **kwargs):
        candidates.append(result)
        pinned = closure.hydrate(f.conn, selection.context["release_closure"])
        accepted = next(item for item in pinned["source_support"] if item["source_generations"])
        receipt = accepted["source_generations"][0]
        with f.db.transaction():
            if mutation == "revocation":
                f.conn.execute(
                    "UPDATE source_generations SET state='revoked' WHERE generation_id=?",
                    (receipt["generation_id"],),
                )
            elif mutation == "policy":
                f.conn.execute(
                    "UPDATE admission_policies SET contract_version=contract_version||'-changed' WHERE page_kind=?",
                    (receipt["page_kind"],),
                )
            else:
                f.conn.execute(
                    "UPDATE snapshots SET body_sha256='different' WHERE snapshot_id=?",
                    (accepted["snapshot_id"],),
                )
        before = tuple(
            f.conn.execute(
                "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
            )
        )
        f.conn.set_trace_callback(
            lambda sql: (
                writes.append(sql) if sql.startswith("INSERT INTO derivation_row_refs") else None
            )
        )
        try:
            return complete(database, selection, result, **kwargs)
        finally:
            f.conn.set_trace_callback(None)
            assert (
                tuple(
                    f.conn.execute(
                        "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
                    )
                )
                == before
            )
            assert (
                f.conn.execute(
                    "SELECT 1 FROM derivation_dependency_sets WHERE dependency_set_id='completion-rollback-witness'"
                ).fetchone()
                is None
            )

    monkeypatch.setattr(generations, "retain", retained)
    monkeypatch.setattr(generations, "complete", finishing)
    with pytest.raises(BuildError, match=expected):
        service.build_release(f.db, f.bundle, f.clock, f.run)
    assert writes == []
    assert len(candidates) == 1
    result = candidates[0]
    assert (result.path / "BUILT").is_file()
    assert (result.path / "REJECTED").is_file()
    assert not generations.completed(f.conn, result.candidate_id, result.manifest_hash)


def closure_proofs(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT dependency_set_id FROM derivation_dependency_sets "
            "WHERE substr(manifest_json,1,40) LIKE '{\"baseline\":%'"
        )
    }


def test_failed_build_attempt_retains_no_closure_proof(release_state, monkeypatch):
    # Each attempt's closure carries its own cutoff, so a proof retained before
    # the files stored a new 235 MB copy on every failed attempt (D-0199).
    f = release_state
    before = closure_proofs(f.conn)

    def failing(*args, **kwargs):
        raise BuildError("callback_marks references missing entry injected")

    monkeypatch.setattr(service, "build_candidate", failing)
    for _ in range(2):
        with pytest.raises(BuildError, match="injected"):
            service.build_release(f.db, f.bundle, f.clock, f.run)
        f.clock.sleep(60)
    assert closure_proofs(f.conn) == before


def test_build_generation_names_its_closure_proof_by_digest(release_state):
    import json

    f = release_state
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    assert generations.completed(f.conn, result.candidate_id, result.manifest_hash)
    (recipe,) = (
        json.loads(row[0])
        for row in f.conn.execute(
            "SELECT recipe_json FROM derivation_generations WHERE stage='build' AND unit_kind='release'"
        )
    )
    named = recipe["context"]["release_closure"]
    assert named["format"] == "release-closure-public-v1"
    assert named["private_digest"] in closure_proofs(f.conn)
    assert closure.hydrate(f.conn, named)["digest"] == named["private_digest"]


def test_candidate_left_without_its_proof_is_rebuilt_not_reused(release_state, monkeypatch):
    f = release_state

    def interrupted(conn, value):
        raise KeyboardInterrupt("after the candidate files, before the proof")

    import json

    def built():
        return {path.parent for path in (f.db.state_dir / "candidates").glob("*/BUILT")}

    existing = built()
    monkeypatch.setattr(closure, "retain", interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.build_release(f.db, f.bundle, f.clock, f.run)
    monkeypatch.undo()
    (orphan,) = built() - existing
    policy = json.loads((orphan / "_meta/manifest.json").read_bytes())["release_policy"]
    assert policy["closure"]["private_digest"] not in closure_proofs(f.conn)
    f.clock.sleep(60)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    assert result.path != orphan and not result.reused
    assert (orphan / "REJECTED").is_file()
    assert generations.completed(f.conn, result.candidate_id, result.manifest_hash)


def test_completion_uses_the_proof_the_build_holds(release_state, monkeypatch):
    # Each read decodes the whole 235 MB proof; completion used to repeat it at
    # every boundary inside its bounded write, beside the build's own copy
    # (D-0199).
    f = release_state
    complete = generations.complete
    reads = []

    def finishing(*args, **kwargs):
        f.conn.set_trace_callback(
            lambda sql: (
                reads.append(sql)
                if sql.startswith("SELECT manifest_json FROM derivation_dependency_sets")
                else None
            )
        )
        try:
            return complete(*args, **kwargs)
        finally:
            f.conn.set_trace_callback(None)

    monkeypatch.setattr(generations, "complete", finishing)
    result = service.build_release(f.db, f.bundle, f.clock, f.run)
    assert generations.completed(f.conn, result.candidate_id, result.manifest_hash)
    assert reads == []


def test_a_completion_overrun_is_a_build_error_not_a_cycle_abort(release_state, monkeypatch):
    from swingset.state import derivations
    from swingset.state.write_deadline import WriteDeadlineExceeded

    f = release_state

    def overran(*args, **kwargs):
        raise WriteDeadlineExceeded("atomic write exceeded 45 seconds; output rolled back")

    monkeypatch.setattr(derivations, "complete", overran)
    with pytest.raises(BuildError, match="exceeded 45 seconds"):
        service.build_release(f.db, f.bundle, f.clock, f.run)
    assert list((f.db.state_dir / "candidates").glob("*/REJECTED"))
