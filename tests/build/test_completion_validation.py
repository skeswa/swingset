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
        events.append("validate")
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
    assert events.count("validate") == 3
    # SQLite traces an INSERT again while entering its triggers. The rows stream
    # in first and the label is written from what was stored, so the generation
    # follows them; the re-check after the rows still rolls everything back
    # together.
    assert [kind for kind, _ in groupby(events)] == [
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
        pinned = selection.context["release_closure"]
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
