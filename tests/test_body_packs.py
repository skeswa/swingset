"""Parse and build-recipe bodies in packs, and every reader of them (phase 4, WP4)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_admission import BODY, Corpus, _runtime
from test_h15_acceptance import drain_project
from test_h15_acceptance import source_fixture as source_fixture

from swingset.cli import main
from swingset.state import body_packs, objects, retention, storage
from swingset.state.body_packs import STUB
from swingset.state.db import Database, open_database
from swingset.state.work import WorkUnit, enqueue

NOW = datetime(2026, 10, 1, tzinfo=UTC)
BEFORE = body_packs.cutoff(NOW)


def body(index: int) -> bytes:
    return BODY.replace(b"Alice", f"Alice{index}".encode())


def staged(c: Corpus, name: str, index: int) -> str:
    content = body(index)
    generation, _ = c.stage(c.snapshot(name, content), body=content)
    return generation


def accepted(c: Corpus, name: str, index: int) -> str:
    generation = staged(c, name, index)
    assert c.admit(generation) == "accepted"
    return generation


def decide(conn: sqlite3.Connection, generation: str, state: str) -> None:
    conn.execute("UPDATE source_generations SET state=? WHERE generation_id=?", (state, generation))
    conn.execute(
        "INSERT INTO admission_decisions(generation_id,state,reason,decided_at,policy_revision) "
        "VALUES (?,?,'test','2026-01-01T00:00:00+00:00','test')",
        (generation, state),
    )


def columns(conn: sqlite3.Connection, generation: str) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT manifest_json,report_json,result_json FROM source_generations "
        "WHERE generation_id=?",
        (generation,),
    ).fetchone()
    return str(row[0]), str(row[1]), str(row[2])


def pack(database: Database, generation: str) -> str:
    """Pack one source row whatever protects it (the tests' forced path)."""
    conn = database.connection
    store = objects.store_for(conn)
    digest, _ = body_packs.write_source(conn, store, generation, now=NOW)
    store.verify(body_packs.PARSE, digest)
    with database.transaction() as txn:
        assert body_packs.stub_source(txn, generation, digest, now=NOW)
    return digest


def restore(database: Database, generation: str) -> str | None:
    with database.transaction() as txn:
        return body_packs.restore(txn, generation, now=NOW)


class Units:
    """One unit with a generation for each protection rule, and two targets."""

    def __init__(self, database: Database) -> None:
        self.db, self.conn = database, database.connection
        c = self.corpus = Corpus(database)
        self.review = staged(c, "s1", 1)
        c.review(self.review)
        assert c.admit(self.review) == "accepted"
        self.old = accepted(c, "s2", 2)
        self.shadow = staged(c, "s3", 3)
        self.conn.execute(
            "UPDATE source_generations SET state='superseded' WHERE generation_id=?",
            (self.shadow,),
        )
        self.equivalence = accepted(c, "s4", 4)
        self.finding = accepted(c, "s5", 5)
        self.candidate = accepted(c, "s6", 6)
        self.target = accepted(c, "s7", 7)
        self.chain = accepted(c, "s8", 8)
        self.current = accepted(c, "s9", 9)
        self.held = staged(c, "s10", 10)
        decide(self.conn, self.held, "needs_review")
        self.newest = staged(c, "s11", 11)
        decide(self.conn, self.newest, "superseded")
        unit, fingerprint = self.conn.execute(
            "SELECT unit_key,input_fingerprint FROM source_generations WHERE generation_id=?",
            (self.equivalence,),
        ).fetchone()
        self.conn.execute(
            "INSERT INTO source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,"
            "work_token,created_at,run_id) VALUES (?,?,?,'{}',NULL,?,?)",
            (unit, "other-" + fingerprint, self.equivalence, "2026-01-01", c.run),
        )
        self.conn.execute(
            "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,"
            "severity,summary,evidence_json,opened_at,run_id) VALUES "
            "('f1','test','test','test','watch','w','warning','test','{}','2026-01-01',?)",
            (c.run,),
        )
        self.conn.execute(
            "INSERT INTO finding_support_references VALUES ('f1','generation',?)", (self.finding,)
        )

    @property
    def rules(self) -> dict[str, str]:
        return {
            self.review: "review",
            self.shadow: "shadow",
            self.equivalence: "equivalence",
            self.finding: "finding",
            self.candidate: "candidate",
            self.chain: "chain",
            self.current: "accepted",
            self.held: "state",
            self.newest: "newest",
        }


@pytest.fixture
def units(tmp_path):
    with open_database(tmp_path / "state") as database:
        yield Units(database)


# ---------------------------------------------------------------------------
# The protected set
# ---------------------------------------------------------------------------


def test_each_rule_protects_its_generation_and_only_old_unprotected_rows_are_targets(units):
    conn = units.conn
    body_packs.protect_sources(conn, before=BEFORE, candidates=[units.candidate, "gen_absent"])
    protected = {str(g): str(w) for g, w in conn.execute("SELECT * FROM temp.body_protected")}
    assert protected == units.rules
    assert list(body_packs.source_targets(conn)) == sorted(
        [(units.old, False), (units.target, False)]
    )
    # The point check every incremental writer uses agrees, rule for rule.
    for generation, why in [*units.rules.items(), (units.old, None), (units.target, None)]:
        assert (
            body_packs.source_protection(
                conn, generation, before=BEFORE, candidates=frozenset({units.candidate})
            )
            == why
        )
    # Everything created on or after the cutoff is young.
    body_packs.protect_sources(conn, before="2025-12-01T00:00:00+00:00")
    assert list(body_packs.source_targets(conn)) == []
    assert (
        body_packs.source_protection(
            conn, units.old, before="2025-12-01T00:00:00+00:00", candidates=frozenset()
        )
        == "young"
    )
    body_packs.drop_temporary(conn)


def _enumerate(conn: sqlite3.Connection, own: str, parent: str, claim: str) -> None:
    """An event enumeration (migration 16) naming three generations three ways."""
    decision = conn.execute(
        "SELECT decision_id FROM admission_decisions WHERE generation_id=? LIMIT 1", (own,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO source_event_enumerations(enumeration_id,source,source_ref,predecessor_id,"
        "generation_id,decision_id,parent_support_json,membership_digest,pagination,"
        "pagination_reason,added_json,removed_json,recorded_at) VALUES "
        "('en1','eepro','e1',NULL,?,?,?,'m','complete','test','[]','[]','2026-01-01')",
        (own, decision, json.dumps({parent: {"generation_id": parent, "snapshots": []}})),
    )
    conn.execute(
        "INSERT INTO source_event_enumeration_members(enumeration_id,request_id,request_json,"
        "support_json,first_known_at) VALUES ('en1','r1','{}',?,NULL)",
        (json.dumps([{"generation_id": claim, "roles": ["direct_result"], "snapshots": []}]),),
    )


def test_generations_the_event_subsystem_reads_are_protected(units):
    """F35: event coverage decodes an enumeration's parents, and event
    retirement its members' claims, so none of them is ever packed."""
    conn = units.conn
    _enumerate(conn, units.old, units.target, units.candidate)
    body_packs.protect_sources(conn, before=BEFORE, candidates=[])
    protected = {str(g): str(w) for g, w in conn.execute("SELECT * FROM temp.body_protected")}
    assert {protected[g] for g in (units.old, units.target, units.candidate)} == {"event"}
    assert list(body_packs.source_targets(conn)) == []
    events = body_packs.event_reads(conn)
    assert events.generations == {units.old, units.target, units.candidate}
    for generation in (units.old, units.target, units.candidate):
        assert (
            body_packs.source_protection(conn, generation, before=BEFORE, candidates=frozenset())
            == "event"
        )
    body_packs.drop_temporary(conn)


def test_an_enumeration_written_after_the_snapshot_still_protects(units):
    conn = units.conn
    events = body_packs.event_reads(conn)
    assert events.generations == frozenset()
    assert (
        body_packs.source_protection(
            conn, units.target, before=BEFORE, candidates=frozenset(), events=events
        )
        is None
    )
    _enumerate(conn, units.old, units.target, units.candidate)
    for generation in (units.old, units.target, units.candidate):
        assert (
            body_packs.source_protection(
                conn, generation, before=BEFORE, candidates=frozenset(), events=events
            )
            == "event"
        )


def test_a_closure_proof_protects_every_generation_it_names_anywhere(units, monkeypatch):
    """`storage.retained` reads the whole proof, `event_coverage` included."""
    from swingset.build.closure_manifest import canonical

    conn = units.conn
    proof = {
        "source_support": [{"source_generations": [{"generation_id": units.old}]}],
        "event_coverage": {"entries": [{"parents": [{"generation_id": units.target}]}]},
        "selected": [{"generation_id": "dg_not_a_source"}],
    }
    body = canonical(proof)
    digest = hashlib.sha256(body.encode()).hexdigest()
    conn.execute(
        "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES (?,?)",
        (digest, body),
    )
    candidate = units.db.state_dir / "candidates" / "cand_closure"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "_meta" / "manifest.json").write_text(
        json.dumps({"release_policy": {"mode": "closure", "closure": {"private_digest": digest}}})
    )
    monkeypatch.setattr(retention, "referenced_candidates", lambda state_dir: {candidate})
    sources, _ = storage.retained(conn, units.db.state_dir)
    assert sources == sorted([units.old, units.target])


def test_the_chain_reaches_back_through_unaccepted_rows_to_the_previous_accepted_one(units):
    conn = units.conn
    # Revoking the generation before the current one leaves a revoked link in
    # the chain; the walk passes it and protects the accepted one behind it.
    previous = conn.execute(
        "SELECT previous_generation_id FROM source_generations WHERE generation_id=?",
        (units.current,),
    ).fetchone()[0]
    assert previous == units.chain
    conn.execute(
        "UPDATE source_generations SET state='revoked' WHERE generation_id=?", (units.chain,)
    )
    body_packs.protect_sources(conn, before=BEFORE, candidates=[units.candidate])
    protected = {str(g): str(w) for g, w in conn.execute("SELECT * FROM temp.body_protected")}
    assert protected[units.chain] == "state"
    assert protected[units.target] == "chain"
    assert body_packs.source_protection(
        conn, units.target, before=BEFORE, candidates=frozenset()
    ) == ("chain")
    body_packs.drop_temporary(conn)


# ---------------------------------------------------------------------------
# Packing and restoring
# ---------------------------------------------------------------------------


def test_pack_and_restore_are_byte_exact_and_the_triggers_hold(units):
    conn, database = units.conn, units.db
    before = columns(conn, units.old)
    digest = pack(database, units.old)
    assert columns(conn, units.old) == (STUB, STUB, STUB)
    assert body_packs.source_pack(conn, units.old) == digest
    raw = body_packs.encode_source(*before)
    assert digest == hashlib.sha256(raw).hexdigest()
    assert body_packs.source_body(conn, units.old) == body_packs.SourceBody(*before)
    assert body_packs.pack_manifest(objects.store_for(conn), digest) == before[0]
    # Writing the same body again keeps the object.
    with pytest.raises(body_packs.BodyUnavailable, match="already packed"):
        body_packs.write_source(conn, objects.store_for(conn), units.old, now=NOW)
    again, new = body_packs.write_source(conn, objects.store_for(conn), units.target, now=NOW)
    assert not new or again != digest
    # Without the authority nothing moves; the pack row stays while the row is a stub.
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE source_generations SET manifest_json=?,report_json=?,result_json=? "
            "WHERE generation_id=?",
            (*before, units.old),
        )
    with pytest.raises(sqlite3.IntegrityError, match="restored row"):
        conn.execute("DELETE FROM source_generation_packs")
    assert restore(database, units.old) == "source"
    assert columns(conn, units.old) == before
    assert body_packs.source_pack(conn, units.old) is None
    assert restore(database, units.old) is None
    # A damaged object is refused before anything is written back.
    pack(database, units.old)
    path = objects.store_for(conn).path(body_packs.PARSE, digest)
    path.write_bytes(path.read_bytes()[:-8])
    with pytest.raises(body_packs.BodyUnavailable):
        restore(database, units.old)
    assert columns(conn, units.old) == (STUB, STUB, STUB)


def build_label(
    database: Database, generation: str, size: int, *, created: str = "2026-01-01T00:00:00+00:00"
) -> str:
    conn = database.connection
    recipe = json.dumps(
        {
            "context": {
                "release_closure": {"digest": "a" * 64, "private_digest": generation[-64:]},
                "padding": "x" * size,
            }
        }
    )
    run = database.start_run(NOW)
    empty = hashlib.sha256(b"[]").hexdigest()
    with database.transaction():
        conn.execute(
            "INSERT OR IGNORE INTO derivation_dependency_sets(dependency_set_id,manifest_json) "
            "VALUES (?,'[]')",
            (empty,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
            "VALUES ('build','release','all',?)",
            (created,),
        )
        conn.execute(
            "INSERT INTO derivation_generations VALUES (?,'build','release','all','fp',?,?,NULL,?,0,?,?)",
            (generation, recipe, empty, hashlib.sha256(b"").hexdigest(), created, run),
        )
    return recipe


def test_large_build_recipes_pack_unless_protected_and_restore_byte_exact(units):
    conn, database = units.conn, units.db
    big = "dg_" + "1" * 64
    recipe = build_label(database, big, 1_100_000)
    build_label(database, "dg_" + "2" * 64, 10)
    pointed = "dg_" + "3" * 64
    build_label(database, pointed, 1_100_000)
    held = "dg_" + "4" * 64
    build_label(database, held, 1_100_000)
    young = "dg_" + "5" * 64
    build_label(database, young, 1_100_000, created="2026-09-30T00:00:00+00:00")
    conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=? WHERE stage='build'",
        (pointed,),
    )
    body_packs.protect_labels(conn, before=BEFORE, candidates=[held])
    protected = {str(g): str(w) for g, w in conn.execute("SELECT * FROM temp.label_protected")}
    assert protected == {pointed: "pointer", held: "candidate", young: "young"}
    assert list(body_packs.label_targets(conn)) == [(big, False)]
    body_packs.drop_temporary(conn)
    store = objects.store_for(conn)
    digest, _ = body_packs.write_label(conn, store, big, now=NOW)
    with database.transaction() as txn:
        assert body_packs.stub_label(txn, big, digest, now=NOW)
    assert (
        conn.execute(
            "SELECT recipe_json FROM derivation_generations WHERE generation_id=?", (big,)
        ).fetchone()[0]
        == STUB
    )
    assert body_packs.recipe(conn, big) == recipe
    # A packed recipe stays a target, so a resumed plan keeps its digest.
    body_packs.protect_labels(conn, before=BEFORE, candidates=[held])
    assert list(body_packs.label_targets(conn)) == [(big, True)]
    body_packs.drop_temporary(conn)
    assert restore(database, big) == "label"
    assert body_packs.recipe(conn, big) == recipe
    assert body_packs.label_pack(conn, big) is None


def test_the_proof_collector_still_reads_a_packed_build_recipe(units, tmp_path):
    from swingset.state import closure_collection

    conn, database = units.conn, units.db
    proof = '{"baseline":{},"selected":[]}'
    proof_id = hashlib.sha256(proof.encode()).hexdigest()
    conn.execute(
        "INSERT INTO derivation_dependency_sets(dependency_set_id,manifest_json) VALUES (?,?)",
        (proof_id, proof),
    )
    big = "dg_" + proof_id
    build_label(database, big, 1_100_000)
    before = closure_collection.plan(conn, database.state_dir)
    assert before["kept"][proof_id] == [f"build_recipe:{big}"]
    store = objects.store_for(conn)
    digest, _ = body_packs.write_label(conn, store, big, now=NOW)
    with database.transaction() as txn:
        body_packs.stub_label(txn, big, digest, now=NOW)
    after = closure_collection.plan(conn, database.state_dir)
    assert after["kept"] == before["kept"]


# ---------------------------------------------------------------------------
# Parse-scope readers fail closed on a stub
# ---------------------------------------------------------------------------


def test_admission_generations_cutoff_declines_a_packed_generation(tmp_path):
    """`admission/generations.py` `_cutoff`: the stub is not the new triple, so no cutoff."""
    from swingset.schedule.parse import parse_snapshot

    with open_database(tmp_path / "state") as database:
        c = Corpus(database)
        first = staged(c, "one", 1)
        c.review(first)
        assert c.admit(first) == "accepted"
        pack(database, first)
        _runtime(c.conn, "runtime-2")
        enqueue(c.conn, (WorkUnit("parse", "snapshot", "one"),), enqueued_at="2099-01-01")
        attempt = parse_snapshot(
            database, c.archive, WorkUnit("parse", "snapshot", "one"), c.clock, c.run
        )
        assert not attempt.failed
        # Unpacked, the same reparse cuts off to `first` and stages nothing
        # (test_admission); packed, it stages a new generation instead.
        assert c.conn.execute("SELECT count(*) FROM source_generations").fetchone()[0] == 2
        assert c.conn.execute("SELECT count(*) FROM source_equivalences").fetchone()[0] == 0


def revocation_case(database: Database) -> tuple[Corpus, str, str]:
    c = Corpus(database)
    first = staged(c, "one", 1)
    c.review(first)
    assert c.admit(first) == "accepted"
    second = accepted(c, "two", 2)
    return c, first, second


def test_a_revocation_that_reads_a_packed_body_fails_with_the_hint_until_restore_body(tmp_path):
    """`admission/select.py` `revoke_generation`: the stub's result does not decode."""
    from swingset.admission.select import revoke_generation

    state = tmp_path / "state"
    with open_database(state) as database:
        c, first, second = revocation_case(database)
        pack(database, first)
        conn = database.connection
        observations = conn.execute("SELECT * FROM observations ORDER BY 1").fetchall()
        with pytest.raises(KeyError):
            revoke_generation(
                database, second, reason="Synthetic revocation", now="x", run_id=c.run
            )
        # Nothing moved: the transaction rolled back.
        assert (
            conn.execute("SELECT accepted_generation_id FROM source_units").fetchone()[0] == second
        )
        assert (
            conn.execute(
                "SELECT state FROM source_generations WHERE generation_id=?", (second,)
            ).fetchone()[0]
            == "accepted"
        )
        assert conn.execute("SELECT * FROM observations ORDER BY 1").fetchall() == observations
        assert body_packs.revocation_blockers(conn, second) == [first]
        with pytest.raises(body_packs.BodyPacked, match=f"storage restore-body {first}"):
            body_packs.check_revocation(conn, second)
        run = c.run
    assert main(["storage", "restore-body", first, "--state", str(state)]) == 0
    assert main(["storage", "restore-body", first, "--state", str(state)]) == 1
    with open_database(state) as database:
        body_packs.check_revocation(database.connection, second)
        revoke_generation(database, second, reason="Synthetic revocation", now="x", run_id=run)
        assert (
            database.connection.execute(
                "SELECT accepted_generation_id FROM source_units"
            ).fetchone()[0]
            == first
        )


def test_admission_support_failure_codes_raise_on_a_packed_report(units):
    """`admission/support.py:14` `generation_failure_codes`."""
    from swingset.admission.support import generation_failure_codes

    assert generation_failure_codes(units.conn, units.old) == ("contract_passed",)
    pack(units.db, units.old)
    with pytest.raises(KeyError):
        generation_failure_codes(units.conn, units.old)


def test_admission_policy_refuses_a_packed_cohort(tmp_path):
    """`admission/policy.py`: review and activation hash the cohort's reports."""
    from swingset.admission.policy import activate_contract, record_review

    with open_database(tmp_path / "state") as database:
        c = Corpus(database)
        generation = staged(c, "one", 1)
        conn = database.connection
        with database.transaction():
            receipt = record_review(
                conn,
                (generation,),
                reviewer="offline-test-reviewer",
                reviewed_at="2026-01-01T00:00:00+00:00",
                evidence="Synthetic fixture",
            )
        pack(database, generation)
        kind = conn.execute(
            "SELECT page_kind FROM source_generations WHERE generation_id=?", (generation,)
        ).fetchone()[0]
        with pytest.raises(ValueError, match="Reviewed corpus changed"):
            activate_contract(conn, kind, receipt)
        with pytest.raises(KeyError):
            record_review(
                conn,
                (generation,),
                reviewer="offline-test-reviewer",
                reviewed_at="2026-01-01T00:00:00+00:00",
                evidence="Synthetic fixture",
            )


def test_page_evidence_rejects_a_packed_generation(units):
    """`admission/page_evidence.py` and every `event_*` reader decode through `decode_generation`."""
    from swingset.admission.page_evidence import GENERATION
    from swingset.schedule.event_evidence import decode_generation, generation

    def row() -> dict[str, object]:
        found = units.conn.execute(
            "SELECT * FROM source_generations WHERE generation_id=?", (units.old,)
        ).fetchone()
        return {name: found[name] for name in GENERATION}

    assert decode_generation(row(), units.old)["generation_id"] == units.old
    pack(units.db, units.old)
    with pytest.raises(ValueError, match="source_generation_fingerprint_changed"):
        decode_generation(row(), units.old)
    with pytest.raises(ValueError):
        generation(units.conn, units.old)


# ---------------------------------------------------------------------------
# Readers outside parse scopes
# ---------------------------------------------------------------------------


def test_the_current_accepted_rows_history_reads_are_never_targets(units):
    """`history/platform.py`, `backfill.py`, `captures.py` and `origin_dispatch.py`
    read the accepted generation, or report only of held states."""
    database = units.db
    # No retained candidate here, so `units.candidate` is a target too.
    result = storage.pack_bodies(database, now=NOW, budget_rows=100)
    assert result["parse_body"] == 3
    conn = units.conn
    assert (
        conn.execute(
            "SELECT count(*) FROM source_units u JOIN source_generations g "
            "ON g.generation_id=u.accepted_generation_id WHERE g.result_json=?",
            (STUB,),
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM source_generations WHERE result_json=? "
            "AND state NOT IN ('accepted','superseded')",
            (STUB,),
        ).fetchone()[0]
        == 0
    )
    assert {
        str(row[0])
        for row in conn.execute(
            "SELECT generation_id FROM source_generations WHERE result_json=?", (STUB,)
        )
    } == {units.old, units.target, units.candidate}


def test_partition_support_is_the_same_before_and_after_packing(units):
    from swingset.build.partition_checks import SupportIndex

    conn = units.conn
    parser = str(
        json.loads(
            conn.execute(
                "SELECT recipe_json FROM source_generations WHERE generation_id=?", (units.old,)
            ).fetchone()[0]
        )["parser_version"]
    )
    pairs = [(f"s{index}", parser) for index in range(1, 12)]
    generations = [units.old, units.target, units.current]

    def read() -> tuple[object, ...]:
        index = SupportIndex(conn)
        return (
            [index(*pair) for pair in pairs],
            [index.generation_observations(g) for g in generations],
        )

    before = read()
    assert before[0][1].state == "accepted" and before[1][0]
    pack(units.db, units.old)
    pack(units.db, units.target)
    assert read() == before


def test_closure_support_reads_packed_generations_through_the_accessor(units):
    from swingset.build import closure_support

    conn = units.conn
    expected = closure_support._accepted(conn, units.old)
    assert expected is not None
    observations = list(closure_support._observations(expected))
    pack(units.db, units.old)
    found = closure_support._accepted(conn, units.old)
    assert found is not None and dict(found) == dict(expected)
    assert list(closure_support._observations(found)) == observations


def test_a_closure_mode_baseline_validates_through_the_accessor(source_fixture):
    from swingset.build.closure import select, validate
    from swingset.build.closure_support import selected_observations

    f = source_fixture
    drain_project(f)
    closure = select(f.conn, cutoff=f.corpus.clock.now())
    manifest = closure.manifest()
    expected = list(selected_observations(f.conn, manifest))
    assert expected
    sources = sorted(
        {
            receipt["generation_id"]
            for item in manifest["source_support"]
            for receipt in item["source_generations"]
        }
    )
    assert sources
    for generation in sources:
        pack(f.db, generation)
    validate(f.conn, closure)
    assert list(selected_observations(f.conn, manifest)) == expected


def test_the_proof_fingerprint_names_the_pack_of_a_packed_source(units):
    from swingset.build.closure_validation import _ReadSet

    read_set = _ReadSet("[]", "[]", json.dumps([units.old]), "[]")
    with units.db.transaction() as txn:
        before = read_set.fingerprint(txn)
    pack(units.db, units.old)
    with units.db.transaction() as txn:
        packed = read_set.fingerprint(txn)
        assert packed != before and read_set.fingerprint(txn) == packed
    restore(units.db, units.old)
    with units.db.transaction() as txn:
        assert read_set.fingerprint(txn) == before


def test_the_backup_closure_still_names_what_a_packed_manifest_names(units):
    conn, state = units.conn, units.db.state_dir
    before = retention.artifact_closure(state, conn, set())
    pack(units.db, units.old)
    pack(units.db, units.target)
    assert retention.artifact_closure(state, conn, set()) == before
    digest = body_packs.source_pack(conn, units.old)
    assert digest is not None
    objects.store_for(conn).path(body_packs.PARSE, digest).unlink()
    assert objects.store_for(conn).path(body_packs.PARSE, digest) in [
        Path(path) for path in retention.missing_artifacts(state, conn, set())
    ]


# ---------------------------------------------------------------------------
# storage plan and apply, and the maintain pass
# ---------------------------------------------------------------------------


def test_storage_plan_and_apply_pack_both_stages_and_resume(units, monkeypatch):
    database, conn = units.db, units.conn
    big = "dg_" + "6" * 64
    recipe = build_label(database, big, 1_100_000)
    (database.state_dir / "operator-hold").write_text("test\n")
    written = storage.plan(conn, database.state_dir, now=NOW)
    content = written.content
    # No retained candidate here, so `units.candidate` is a target too.
    assert content["parse_body"]["generations"] == 3
    assert content["parse_body"]["protected"]["review"] == 1
    assert content["label_body"]["generations"] == 1
    assert written.estimate is not None and written.estimate["parse_bodies_to_pack"] == 3

    def kill(name: str) -> None:
        if name == "bodies_stubbed":
            raise RuntimeError("killed")

    monkeypatch.setattr(storage, "_stage", kill)
    with pytest.raises(RuntimeError, match="killed"):
        storage.apply(database, plan_digest=written.digest, now=NOW)
    assert columns(conn, units.old) == (STUB, STUB, STUB)
    # Packed rows stay targets: the same digest resumes.
    assert storage.plan(conn, database.state_dir, now=NOW).digest == written.digest
    monkeypatch.setattr(storage, "_stage", lambda name: None)
    receipt = storage.apply(database, plan_digest=written.digest, now=NOW)
    assert receipt["bodies"]["parse_body"]["stubbed"] == 0
    assert receipt["bodies"]["label_body"]["stubbed"] == 0
    assert receipt["resumed"]
    assert columns(conn, units.target) == (STUB, STUB, STUB)
    assert body_packs.recipe(conn, big) == recipe
    for generation in set(units.rules) - {units.candidate}:
        assert STUB not in columns(conn, generation)


def test_the_maintain_pass_is_bounded_and_rechecks_protection(units, monkeypatch):
    database, conn = units.db, units.conn
    assert storage.pack_bodies(database, now=NOW, budget_rows=1)["parse_body"] == 1
    # A row that turns protected between the scan and the write is skipped.
    real = body_packs.source_protection

    def protected(*args, **kwargs):
        return "equivalence"

    monkeypatch.setattr(body_packs, "source_protection", protected)
    assert storage.pack_bodies(database, now=NOW, budget_rows=10)["skipped"] == 2
    monkeypatch.setattr(body_packs, "source_protection", real)
    result = storage.pack_bodies(database, now=NOW, budget_rows=10)
    assert result["parse_body"] == 2
    assert storage.pack_bodies(database, now=NOW, budget_rows=10)["parse_body"] == 0
    assert {
        str(row[0]) for row in conn.execute("SELECT generation_id FROM source_generation_packs")
    } == {units.old, units.target, units.candidate}


def test_one_body_that_cannot_be_packed_is_isolated_with_a_guard(units, monkeypatch):
    from swingset.state import guards

    database, conn = units.db, units.conn
    real = body_packs.source_columns

    def failing(conn_, generation_id):
        if generation_id == units.old:
            raise OSError(5, "Input/output error")
        return real(conn_, generation_id)

    monkeypatch.setattr(body_packs, "source_columns", failing)
    result = storage.pack_bodies(database, now=NOW, budget_rows=10)
    assert result["failed"] == 1 and result["parse_body"] == 2
    assert result["failures"][0]["generation_id"] == units.old
    assert STUB not in columns(conn, units.old) and columns(conn, units.target)[2] == STUB
    assert guards.get(conn, storage.GUARD_SUBJECT, storage.PACK_FAILED) is not None
    monkeypatch.setattr(body_packs, "source_columns", real)
    result = storage.pack_bodies(database, now=NOW, budget_rows=10)
    assert result["failed"] == 0 and result["parse_body"] == 1
    assert guards.get(conn, storage.GUARD_SUBJECT, storage.PACK_FAILED) is None


def test_a_refused_body_pass_is_a_task_failure_with_a_guard(units, monkeypatch):
    """A refusal before any row (a retained proof missing) stops every body,
    so it carries `error` ([storage-failed]) and the clocked guard, and its
    backlog counts the unpacked rows rather than zero."""
    from swingset.state import guards

    database, conn = units.db, units.conn

    def refuse(conn_, state_dir):
        raise storage.StorageError("closure proof p of candidate c is not in the database")

    monkeypatch.setattr(storage, "retained", refuse)
    result = storage.pack_bodies(database, now=NOW, budget_rows=10)
    assert result["error"] == (
        "StorageError: closure proof p of candidate c is not in the database"
    )
    unpacked = conn.execute(
        "SELECT count(*) FROM source_generations WHERE result_json<>?", (STUB,)
    ).fetchone()[0]
    assert result["backlog"] == unpacked > 0 and result["parse_body"] == 0
    guard = guards.get(conn, storage.GUARD_SUBJECT, storage.PACK_FAILED)
    assert guard is not None and guard.expires_at == guards.at(NOW + storage.GUARD_CLOCK)
    monkeypatch.undo()
    assert "error" not in storage.pack_bodies(database, now=NOW, budget_rows=10)
    assert guards.get(conn, storage.GUARD_SUBJECT, storage.PACK_FAILED) is None


def test_an_old_enumeration_parent_is_never_packed_and_its_witness_validates(tmp_path):
    """F35, the `event` rule: a closure-mode release's event coverage decodes
    each enumeration's parent generation again (`_Reader.parent`). A parent two
    accepted generations back is in no other rule; packed, the witness would
    fail with `event_coverage_support_changed`."""
    from test_event_enumerations import admit_parent, finish_bootstrap
    from test_event_enumerations import event as event_fixture

    from swingset.build import event_coverage

    generator = event_fixture.__wrapped__(tmp_path / "event")  # type: ignore[attr-defined]
    f = next(generator)
    try:
        first = admit_parent(f, ["one.htm"], authority=True)
        finish_bootstrap(f)
        witness = event_coverage.capture(
            f.conn, cutoff=f.corpus.clock.now().isoformat(), selected_support=()
        )
        for name in (["two.htm"], ["three.htm"]):
            f.corpus.clock.sleep(10)
            admit_parent(f, name, authority=True)
            finish_bootstrap(f)
        conn = f.conn
        # Production parents mostly carry no admission review (4,560 of them
        # on the diag copy); without one, no other rule names the first.
        conn.execute("DELETE FROM admission_reviews")
        future = "2100-01-01T00:00:00+00:00"
        body_packs.protect_sources(conn, before=future, candidates=[])
        why = conn.execute(
            "SELECT why FROM temp.body_protected WHERE generation_id=?", (first,)
        ).fetchone()
        assert why is not None and why[0] == "event"
        targets = [generation for generation, _ in body_packs.source_targets(conn)]
        body_packs.drop_temporary(conn)
        assert first not in targets
        for generation in targets:
            pack(f.db, generation)
        event_coverage.validate(conn, witness, selected_support=())
    finally:
        generator.close()
