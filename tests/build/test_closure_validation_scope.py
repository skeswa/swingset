"""Completion-local reuse preserves closure checks across adversarial mutations."""

import sqlite3
from contextlib import closing
from copy import deepcopy

import pytest
from test_h16_acceptance import release_state as release_state

from swingset.build import closure, closure_support
from swingset.build.closure_manifest import ClosureError, digest
from swingset.build.closure_validation import validation_scope


def pinned(f):
    return closure.select(f.conn, cutoff=f.clock.now(), baseline=f.baseline).manifest()


def accepted_witness(manifest):
    item = next(row for row in manifest["source_support"] if row["source_generations"])
    return item, item["source_generations"][0]


def count_reconstruction(monkeypatch):
    original = closure_support.support
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(closure_support, "support", counted)
    return calls


def test_three_boundaries_reconstruct_once_without_caching_across_operations(
    release_state, monkeypatch
):
    f = release_state
    manifest = pinned(f)
    calls = count_reconstruction(monkeypatch)
    with f.db.transaction(), validation_scope(f.conn):
        for _ in range(3):
            closure.validate(f.conn, manifest)
    assert calls == [f.conn]
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
    assert calls == [f.conn, f.conn]
    closure.validate(f.conn, manifest)
    assert calls == [f.conn, f.conn, f.conn]


@pytest.mark.parametrize("at_boundary", [2, 3])
@pytest.mark.parametrize("mutation", ["revocation", "policy", "snapshot", "admission"])
def test_mutable_evidence_rejects_after_initial_success(release_state, mutation, at_boundary):
    f = release_state
    manifest = pinned(f)
    item, source = accepted_witness(manifest)
    with f.db.transaction(), validation_scope(f.conn):
        for _ in range(at_boundary - 1):
            closure.validate(f.conn, manifest)
        if mutation == "revocation":
            f.conn.execute(
                "UPDATE source_generations SET state='revoked' WHERE generation_id=?",
                (source["generation_id"],),
            )
        elif mutation == "policy":
            f.conn.execute(
                "UPDATE admission_policies SET policy_revision=policy_revision||'-new' WHERE page_kind=?",
                (source["page_kind"],),
            )
        elif mutation == "snapshot":
            f.conn.execute(
                "UPDATE snapshots SET body_sha256='replaced' WHERE snapshot_id=?",
                (item["snapshot_id"],),
            )
        else:
            f.conn.execute(
                "DELETE FROM admission_decisions WHERE generation_id=?",
                (source["generation_id"],),
            )
        with pytest.raises(ClosureError):
            closure.validate(f.conn, manifest)


def test_late_acceptance_of_selected_but_unaccepted_source_changes_witness(release_state):
    f = release_state
    initial = pinned(f)
    item, source = accepted_witness(initial)
    decision = dict(
        f.conn.execute(
            "SELECT * FROM admission_decisions WHERE generation_id=? AND state='accepted' LIMIT 1",
            (source["generation_id"],),
        ).fetchone()
    )
    with f.db.transaction():
        f.conn.execute(
            "DELETE FROM admission_decisions WHERE generation_id=?", (source["generation_id"],)
        )
    manifest = pinned(f)
    assert manifest["selected"] == initial["selected"]
    unassessed = next(
        row for row in manifest["source_support"] if row["observation_id"] == item["observation_id"]
    )
    assert unassessed["state"] == "legacy_unassessed" and not unassessed["source_generations"]
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        f.conn.execute(
            "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) VALUES (?,?,?,?,?)",
            tuple(
                decision[key]
                for key in ("generation_id", "state", "reason", "decided_at", "policy_revision")
            ),
        )
        with pytest.raises(ClosureError, match="admission_witness_mismatch"):
            closure.validate(f.conn, manifest)


def test_savepoint_rollback_cannot_reuse_a_proof_for_different_later_mutation(release_state):
    f = release_state
    manifest = pinned(f)
    item, source = accepted_witness(manifest)
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        f.conn.execute("SAVEPOINT changed_support")
        # Superseded evidence remains an admissible historical witness. This
        # successful validation must not hide a different mutation after rollback.
        f.conn.execute(
            "UPDATE source_generations SET state='superseded' WHERE generation_id=?",
            (source["generation_id"],),
        )
        closure.validate(f.conn, manifest)
        f.conn.execute("ROLLBACK TO changed_support")
        f.conn.execute(
            "UPDATE snapshots SET body_sha256='other-bytes' WHERE snapshot_id=?",
            (item["snapshot_id"],),
        )
        with pytest.raises(ClosureError, match="artifact_changed"):
            closure.validate(f.conn, manifest)
        f.conn.execute("ROLLBACK TO changed_support")
        f.conn.execute("RELEASE changed_support")
        closure.validate(f.conn, manifest)


def test_same_manifest_on_another_connection_requires_its_own_evidence(release_state):
    f = release_state
    manifest = pinned(f)
    item, _ = accepted_witness(manifest)
    with closing(sqlite3.connect(":memory:")) as other:
        other.row_factory = sqlite3.Row
        f.conn.backup(other)
        other.execute(
            "UPDATE snapshots SET body_sha256='other-database' WHERE snapshot_id=?",
            (item["snapshot_id"],),
        )
        with f.db.transaction(), validation_scope(f.conn):
            closure.validate(f.conn, manifest)
            with pytest.raises(ClosureError, match="artifact_changed"):
                closure.validate(other, manifest)
            closure.validate(f.conn, manifest)


def test_manifest_mutation_is_not_hidden_by_same_declared_digest(release_state):
    f = release_state
    manifest = pinned(f)
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        manifest["selected"][0]["output_digest"] = "forged"
        with pytest.raises(ClosureError, match="manifest_digest_mismatch"):
            closure.validate(f.conn, manifest)


def test_rehashed_manifest_must_match_actual_selected_generation(release_state):
    f = release_state
    manifest = pinned(f)
    changed = deepcopy(manifest)
    changed["selected"][0]["output_digest"] = "forged"
    changed["digest"] = digest({key: value for key, value in changed.items() if key != "digest"})
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        with pytest.raises(ClosureError, match="generation_receipt_mismatch"):
            closure.validate(f.conn, changed)


def test_dependency_blob_mutation_bypassing_sql_triggers_is_detected(release_state):
    f = release_state
    manifest = pinned(f)
    dependency = manifest["dependency_sets"][0]
    rowid = f.conn.execute(
        "SELECT rowid FROM derivation_dependency_sets WHERE dependency_set_id=?", (dependency,)
    ).fetchone()[0]
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        with f.conn.blobopen(
            "derivation_dependency_sets", "manifest_json", rowid, readonly=False
        ) as blob:
            blob.write(b"!")
        with pytest.raises(ClosureError, match="manifest_hash_mismatch"):
            closure.validate(f.conn, manifest)


def test_selected_generation_blob_change_is_not_treated_as_immutable(release_state):
    f = release_state
    manifest = pinned(f)
    identifier = manifest["selected"][0]["generation_id"]
    rowid = f.conn.execute(
        "SELECT rowid FROM derivation_generations WHERE generation_id=?", (identifier,)
    ).fetchone()[0]
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        with f.conn.blobopen(
            "derivation_generations", "output_digest", rowid, readonly=False
        ) as blob:
            first = blob.read(1)
            blob.seek(0)
            blob.write(b"1" if first == b"0" else b"0")
        with pytest.raises(ClosureError, match="generation_receipt_mismatch"):
            closure.validate(f.conn, manifest)


def test_source_result_blob_change_forces_actual_witness_reconstruction(release_state):
    f = release_state
    manifest = pinned(f)
    _, source = accepted_witness(manifest)
    rowid = f.conn.execute(
        "SELECT rowid FROM source_generations WHERE generation_id=?", (source["generation_id"],)
    ).fetchone()[0]
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        with f.conn.blobopen("source_generations", "result_json", rowid, readonly=False) as blob:
            content = blob.read()
            offset = content.index(b"Alice")
            blob.seek(offset)
            blob.write(b"Elise")
        with pytest.raises(ClosureError, match="admission_witness_mismatch"):
            closure.validate(f.conn, manifest)


@pytest.mark.parametrize("at_boundary", [2, 3])
@pytest.mark.parametrize("mutation", ["snapshot", "admission"])
def test_actual_completion_rejects_late_mutation_and_rolls_back_proof_and_output(
    release_state, monkeypatch, mutation, at_boundary
):
    from swingset.build import generations, service
    from swingset.build.builder import BuildError

    f = release_state
    original_complete = generations.complete
    original_validate = closure.validate
    original_retain = generations.retain
    candidates = []
    calls = []
    witness = "late-completion-rollback-witness"

    def retain(conn, manifest):
        original_retain(conn, manifest)
        conn.execute("INSERT INTO derivation_dependency_sets VALUES (?, '[]')", (witness,))

    def finishing(database, selection, result, **kwargs):
        candidates.append(result)
        manifest = selection.context["release_closure"]
        item, source = accepted_witness(manifest)
        generations_before = tuple(
            f.conn.execute(
                "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
            )
        )
        retained_before = f.conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
            (manifest["digest"],),
        ).fetchone()
        body_before = f.conn.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (item["snapshot_id"],)
        ).fetchone()[0]
        decisions_before = tuple(
            f.conn.execute(
                "SELECT * FROM admission_decisions WHERE generation_id=? ORDER BY decision_id",
                (source["generation_id"],),
            )
        )

        def validate(conn, pinned_manifest):
            assert conn.in_transaction
            calls.append(len(calls) + 1)
            if len(calls) == at_boundary:
                # At the second boundary output rows already exist. At the
                # third, the generation and selected pointer also exist, but
                # none may survive rejection by this actual completion call.
                assert conn.execute(
                    "SELECT 1 FROM derivation_rows WHERE table_name='artifact' AND record_key=?",
                    (result.candidate_id,),
                ).fetchone()
                if at_boundary == 3:
                    assert conn.execute(
                        "SELECT 1 FROM derivation_generations WHERE generation_id=?",
                        (selection.generation_id,),
                    ).fetchone()
                if mutation == "snapshot":
                    conn.execute(
                        "UPDATE snapshots SET body_sha256='late-other-body' WHERE snapshot_id=?",
                        (item["snapshot_id"],),
                    )
                else:
                    conn.execute(
                        "DELETE FROM admission_decisions WHERE generation_id=?",
                        (source["generation_id"],),
                    )
            return original_validate(conn, pinned_manifest)

        with monkeypatch.context() as scoped:
            scoped.setattr(closure, "validate", validate)
            try:
                return original_complete(database, selection, result, **kwargs)
            finally:
                assert (
                    tuple(
                        f.conn.execute(
                            "SELECT generation_id FROM derivation_generations ORDER BY generation_id"
                        )
                    )
                    == generations_before
                )
                assert (
                    f.conn.execute(
                        "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id=?",
                        (manifest["digest"],),
                    ).fetchone()
                    == retained_before
                )
                assert (
                    f.conn.execute(
                        "SELECT 1 FROM derivation_dependency_sets WHERE dependency_set_id=?",
                        (witness,),
                    ).fetchone()
                    is None
                )
                assert (
                    f.conn.execute(
                        "SELECT 1 FROM derivation_rows WHERE table_name='artifact' AND record_key=?",
                        (result.candidate_id,),
                    ).fetchone()
                    is None
                )
                assert (
                    f.conn.execute(
                        "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?",
                        (item["snapshot_id"],),
                    ).fetchone()[0]
                    == body_before
                )
                assert (
                    tuple(
                        f.conn.execute(
                            "SELECT * FROM admission_decisions WHERE generation_id=? ORDER BY decision_id",
                            (source["generation_id"],),
                        )
                    )
                    == decisions_before
                )

    monkeypatch.setattr(generations, "retain", retain)
    monkeypatch.setattr(generations, "complete", finishing)
    expected = "artifact_changed" if mutation == "snapshot" else "support_changed"
    with pytest.raises(BuildError, match=expected):
        service.build_release(f.db, f.bundle, f.clock, f.run)
    assert calls == list(range(1, at_boundary + 1))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.path / "BUILT").is_file() and (candidate.path / "REJECTED").is_file()
    assert not generations.completed(f.conn, candidate.candidate_id, candidate.manifest_hash)


def test_reuse_does_not_retain_reconstructed_graph_or_decoded_source_documents(
    release_state, monkeypatch
):
    import gc
    import weakref

    f = release_state
    manifest = pinned(f)
    original_init = closure._Selection.__init__
    original_loads = closure_support.json.loads
    graphs = []
    documents = []

    class Document(dict):
        pass

    def constructed(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        graphs.append(weakref.ref(self))

    def decoded(*args, **kwargs):
        result = original_loads(*args, **kwargs)
        if isinstance(result, dict):
            result = Document(result)
            documents.append(weakref.ref(result))
        return result

    monkeypatch.setattr(closure._Selection, "__init__", constructed)
    monkeypatch.setattr(closure_support.json, "loads", decoded)
    with f.db.transaction(), validation_scope(f.conn):
        closure.validate(f.conn, manifest)
        gc.collect()
        # The operation is still active: only compact proof may remain alive.
        # Observe collection rather than depending on private cache fields.
        assert graphs and documents
        assert all(reference() is None for reference in graphs + documents)
        closure.validate(f.conn, manifest)
        assert len(graphs) == 1
