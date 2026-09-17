"""A migration receipt must detect lost evidence and control or budget changes."""

import json
import sqlite3

import pytest

from journal.tools.runtime.rehearse_extension_migration import (
    compare,
    sha,
    table_receipts,
    verify_source,
)


@pytest.mark.parametrize("table", ["snapshots", "operator_pauses", "host_state"])
def test_protected_rows_cannot_change_behind_new_schema(table):
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY,value TEXT)")
    conn.execute(f"INSERT INTO {table} VALUES('one','original')")
    before = table_receipts(conn)
    conn.execute("CREATE TABLE added(id INTEGER)")
    assert compare(before, table_receipts(conn)) == []
    conn.execute(f"UPDATE {table} SET value='changed'")
    assert compare(before, table_receipts(conn)) == [table]


def test_only_schema_metadata_is_exempt_and_row_order_is_stable():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    conn.executemany(
        "INSERT INTO meta VALUES(?,?)",
        [("schema_version", "14"), ("input_bundle_hash", "accepted")],
    )
    before = table_receipts(conn)
    conn.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
    assert compare(before, table_receipts(conn)) == []
    conn.execute("UPDATE meta SET value='other' WHERE key='input_bundle_hash'")
    assert compare(before, table_receipts(conn)) == ["meta"]


def test_source_inventory_must_cover_all_files_and_remain_unchanged(tmp_path):
    root = tmp_path / "source"
    names = ("src/swingset/state/db.py", "src/swingset/backup/checkpoint.py")
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# frozen\n")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"files": {name: sha(root / name) for name in names}}))
    expected = sha(receipt)
    verify_source(root, receipt, expected)
    (root / "unlisted.py").write_text("# unknown\n")
    with pytest.raises(ValueError, match="incomplete"):
        verify_source(root, receipt, expected)
    (root / "unlisted.py").unlink()
    (root / names[0]).write_text("# changed\n")
    with pytest.raises(ValueError, match="source differs"):
        verify_source(root, receipt, expected)


@pytest.mark.parametrize("inventory", [{}, {"../escape.py": "a" * 64}, {"/escape.py": "a" * 64}])
def test_empty_or_escaping_source_inventory_is_rejected(tmp_path, inventory):
    source = tmp_path / "source"
    source.mkdir()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"files": inventory}))
    with pytest.raises(ValueError):
        verify_source(source, receipt, sha(receipt))


def test_source_symlink_directory_cannot_hide_imported_code(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.py").write_text("# not in inventory")
    (source / "hidden").symlink_to(outside, target_is_directory=True)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"files": {"placeholder": "a" * 64}}))
    with pytest.raises(ValueError, match="symlink"):
        verify_source(source, receipt, sha(receipt))
