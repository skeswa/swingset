"""Offline disposable schema14 state; real frozen capture/accept, mocked external preflight only."""
import importlib.util
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_admission import Corpus

from swingset.clock import SystemClock
from swingset.fetch.archive import canonical, digest
from swingset.schedule.cycle import versions
from swingset.state import db as state_db
from swingset.state import inputs
from swingset.state.db import open_database
from swingset.state.recipes import captured_recipe_inputs

HERE = Path(__file__).parent
FROZEN = Path('/Users/skeswa/.cache/swingset-h16-event-preservation-tests-20260916')


@pytest.fixture
def driver(monkeypatch, tmp_path):
    assert state_db.SCHEMA_VERSION == 14
    assert Path(inputs.__file__).resolve().is_relative_to(FROZEN)
    spec = importlib.util.spec_from_file_location('legacy_initializer_002', HERE/'initialize-002.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE', tmp_path/'state')
    return module


def fixture(driver, tmp_path, monkeypatch, *, missing):
    from research import accept_h16

    state = driver.STATE
    with open_database(state) as database:
        new = inputs.capture(FROZEN/'config', FROZEN/'overrides', state, versions())
        files = dict(new.files)
        old_versions = json.loads(files['versions.json'])
        old_versions['repository'] = 'older-fixture-source'
        files['versions.json'] = canonical(old_versions)
        if missing:
            files = {name:body for name,body in files.items() if not name.startswith(('runtime/','recipes/'))}
        hashes = {name:digest(body) for name,body in files.items()}
        old_digest = digest(canonical(hashes))
        old_path = state/'inputs'/old_digest
        for name,body in files.items():
            target = old_path/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(body)
        (old_path/'manifest.json').write_bytes(canonical(hashes))
        old = replace(new, digest=old_digest, path=old_path, files=files)
        inputs.accept(database, old, SystemClock())
        corpus = Corpus(database)
        context = corpus.snapshot('retained-legacy-child')
        database.connection.execute("UPDATE watches SET extract_version='retained-cache-version'")
        database.connection.execute("DELETE FROM pending_work WHERE stage='parse'")
        before = driver.protected(database.connection)
        controls = driver.controls(database.connection)
        original_cache = database.connection.execute('SELECT extract_version FROM watches').fetchone()[0]
        assert bool(database.connection.execute("SELECT 1 FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()) is not missing
        twin = tmp_path/'rehearsal'
        twin.mkdir()
        with sqlite3.connect(twin/'state.sqlite') as target:
            database.connection.backup(target)
    with open_database(twin) as rehearsal:
        inputs.accept(rehearsal, new, SystemClock())
        authority = driver.input_authority(rehearsal.connection)
    ops = state/'operations';ops.mkdir()
    gate_path = ops/'gate.json'
    gate = {'source_receipt_sha256':'offline-frozen-source', 'input_bundle_hash':new.digest,
        'config':str(FROZEN/'config'),'overrides':str(FROZEN/'overrides'),
        'receipts':{'preflight_gate':'preflight.json'}, '_rehearsed_input_authority':authority}
    driver.new_json(gate_path, gate)
    preflights = []
    def preflight(*args):
        preflights.append(args)
        return {'baseline':{'commit':'offline-acknowledged-baseline'},
            'gate':{'checkpoint':'offline-retained-checkpoint','checkpoint_manifest_sha256':'offline-checkpoint-hash'}}
    monkeypatch.setattr(accept_h16,'preflight',preflight)
    return SimpleNamespace(gate=gate,gate_path=gate_path,marker=ops/'marker.json',new=new,
        old=old,before=before,controls=controls,cache=original_cache,context=context,preflights=preflights)


def prepare(driver, case):
    return driver.prepare(case.gate,FROZEN,case.gate_path,case.marker)


@pytest.mark.parametrize('missing',[True,False])
def test_real_prepare_missing_recipe_invalidates_and_existing_recipe_preserves_cache(driver,tmp_path,monkeypatch,missing):
    case=fixture(driver,tmp_path,monkeypatch,missing=missing)
    result=prepare(driver,case)
    marker=json.loads(case.marker.read_bytes())
    assert result['status']=='prepared' and result['parse_execution_authorized'] is False
    assert result['extract_cache_invalidated'] is missing
    assert marker['extract_cache_invalidated'] is missing
    assert marker['previous_input_bundle_hash']==case.old.digest
    assert marker['input_bundle_hash']==case.new.digest
    assert marker['driver_sha256']==driver.sha(Path(driver.__file__))
    with open_database(driver.STATE) as database:
        conn=database.connection
        assert driver.protected(conn)==case.before
        assert driver.controls(conn)==case.controls
        driver.check_cache(conn,marker)
        driver.check_input_authority(conn,marker)
        recipe=captured_recipe_inputs(case.new.files,history_start=case.new.config.history_start.isoformat())['recipe/runtime']
        assert conn.execute("SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()[0]==recipe
        assert conn.execute('SELECT extract_version FROM watches').fetchone()[0]==(None if missing else case.cache)
        assert bool(conn.execute("SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?",(case.context.snapshot_id,)).fetchone()) is missing
        assert not conn.execute('SELECT 1 FROM work_attempts').fetchone()
        assert not conn.execute('SELECT 1 FROM host_budget WHERE requests>0').fetchone()
    bytes_before=case.marker.read_bytes()
    assert prepare(driver,case)['changed_inputs']==[]
    assert case.marker.read_bytes()==bytes_before and len(case.preflights)==1


def test_marker_survives_interrupted_acceptance_and_retry_uses_sqlite_truth(driver,tmp_path,monkeypatch):
    case=fixture(driver,tmp_path,monkeypatch,missing=True)
    real=inputs.accept
    def interrupted(database,bundle,clock):
        real(database,bundle,clock)
        raise RuntimeError('interrupted before acceptance commit')
    with monkeypatch.context() as scope:
        scope.setattr(inputs,'accept',interrupted)
        with pytest.raises(RuntimeError,match='interrupted'):
            prepare(driver,case)
    marker_bytes=case.marker.read_bytes()
    assert json.loads(marker_bytes)['extract_cache_invalidated'] is True
    with open_database(driver.STATE) as database:
        conn=database.connection
        assert not conn.execute("SELECT 1 FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()
        assert conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()[0]==case.old.digest
        assert conn.execute('SELECT extract_version FROM watches').fetchone()[0]==case.cache
        assert driver.protected(conn)==case.before
    assert prepare(driver,case)['status']=='prepared'
    assert case.marker.read_bytes()==marker_bytes and len(case.preflights)==1
    with open_database(driver.STATE) as database:
        driver.check_input_authority(database.connection,json.loads(marker_bytes))
        assert database.connection.execute('SELECT extract_version FROM watches').fetchone()[0] is None


def test_policy_mismatch_still_refuses_before_marker_or_acceptance(driver,tmp_path,monkeypatch):
    case=fixture(driver,tmp_path,monkeypatch,missing=True)
    manifest=case.old.path/'manifest.json'
    previous=json.loads(manifest.read_bytes())
    previous['config/sources.toml']='different-policy-bytes'
    manifest.write_bytes(canonical(previous))
    with pytest.raises(ValueError,match='not source/identity/scheduling policy'):
        prepare(driver,case)
    assert not case.marker.exists()
    with open_database(driver.STATE) as database:
        assert not database.connection.execute("SELECT 1 FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()
        assert database.connection.execute('SELECT extract_version FROM watches').fetchone()[0]==case.cache
        assert driver.protected(database.connection)==case.before


def test_only_nullable_recipe_lookup_changed():
    import hashlib

    original=(HERE.parent/'initialize.py').read_bytes()
    assert hashlib.sha256(original).hexdigest()=='b54c02a15c3d86a603bef4d6926e66bde4cf1ef9d23f273aa5db771d3a99d233'
    old=b'''            prior_recipe=conn.execute("SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()[0]'''
    new=b'''            prior_recipe_row=conn.execute("SELECT digest FROM accepted_inputs WHERE consumer='pipeline' AND input_name='recipe/runtime'").fetchone()
            prior_recipe=None if prior_recipe_row is None else prior_recipe_row[0]'''
    assert original.count(old)==1
    assert (HERE/'initialize-002.py').read_bytes()==original.replace(old,new)
