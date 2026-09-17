"""Compare original and refactored linking writes on the same regression fixtures."""
import hashlib
import subprocess
import sys
import types

import pytest

from swingset.link.service import link_event as current


BASELINE = "b1b5b856bd6e8bae80555dd9f51b6b3e78ca10f2"


def load(name, path, expected_hash):
    source = subprocess.check_output(["jj", "file", "show", "-r", BASELINE, path])
    assert hashlib.sha256(source).hexdigest() == expected_hash
    module = types.ModuleType(name)
    module.__file__ = f"{BASELINE}/{path}"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


baseline = load("swingset.link._baseline", "src/swingset/link/service.py", "68b022c4ac93f8d69147cf3da300251164790b31cf43e4b1dd522998b2d17d0e")
old_policy = load("swingset.link._old_policy", "src/swingset/link/decisions.py", "18977cccdd7dcc9cbd77f5d1d02318402863a38ed0a6ab1754b7061c3b10d984")
baseline.DecisionResolver = old_policy.DecisionResolver
TABLES = ('identity_links', 'link_candidates', 'entries', 'judges', 'placements',
          'findings', 'identity_link_resolutions', 'identity_link_history',
          'identity_reference_bindings', 'watches', 'revisions', 'pending_work')
COUNT = 0


def snapshot(conn):
    result = {}
    for table in TABLES:
        rows = [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')]
        if table == 'identity_link_history':
            # SQLite's deletion trigger uses wall time rather than the test clock.
            # Compare its full assertion and resolution, excluding only that time.
            rows = [(*row[:-1], '<trigger-time>') if row[3] == 'superseded' else row for row in rows]
        result[table] = sorted(rows, key=repr)
    return result


def compare(database, *args, **kwargs):
    global COUNT
    conn = database.connection
    conn.execute('SAVEPOINT equivalence_baseline')
    try:
        old_result = baseline.link_event(database, *args, **kwargs)
        old = snapshot(conn)
    finally:
        conn.execute('ROLLBACK TO equivalence_baseline')
        conn.execute('RELEASE equivalence_baseline')
    result = current(database, *args, **kwargs)
    new = snapshot(conn)
    assert old_result == result
    for table in TABLES:
        assert old[table] == new[table], f'Link behavior changed in {table}'
    COUNT += 1
    return result


@pytest.fixture(autouse=True)
def compare_linkers(monkeypatch, request):
    # That test deliberately intercepts the new persistence seam; its existing
    # assertions separately cover concurrent acceptance and rollback.
    if 'concurrent_acceptance' in request.node.name:
        return
    for module in list(sys.modules.values()):
        if module and (module.__name__.startswith('test_') or module.__name__ in ('swingset.link', 'swingset.link.service')):
            if getattr(module, 'link_event', None) is current:
                monkeypatch.setattr(module, 'link_event', compare)


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f'Compared {COUNT} link calls across {len(TABLES)} persisted tables.')
