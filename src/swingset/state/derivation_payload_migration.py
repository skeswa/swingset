"""Fill the interned derivation payload tables, verify them, then drop the old one.

SQLite has no sha256, so migration 32 leaves this half to Python. Everything
here runs inside the migration's own transaction: a mismatch raises, the
transaction rolls back, and the old ``derivation_rows`` table is still there
with every row in it.
"""

from __future__ import annotations

import hashlib
import sqlite3

LEGACY_TABLE = "derivation_rows_legacy"


def intern_derivation_payloads(conn: sqlite3.Connection) -> None:
    """Store each distinct output row once, prove nothing changed, then drop."""
    fill(conn)
    verify(conn)
    # Only a complete match may drop the last copy of the old rows.
    conn.execute(f"DROP TABLE {LEGACY_TABLE}")


def fill(conn: sqlite3.Connection) -> None:
    """Stream the old rows into one shared payload each plus one reference."""
    legacy = conn.cursor()
    for generation_id, ordinal, table_name, record_key, payload_json in legacy.execute(
        f"SELECT generation_id,ordinal,table_name,record_key,payload_json FROM {LEGACY_TABLE} "
        "ORDER BY generation_id,ordinal"
    ):
        payload_sha256 = hashlib.sha256(str(payload_json).encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO derivation_payloads VALUES (?,?)",
            (payload_sha256, payload_json),
        )
        conn.execute(
            "INSERT INTO derivation_row_refs VALUES (?,?,?,?,?)",
            (generation_id, ordinal, table_name, record_key, payload_sha256),
        )


def verify(conn: sqlite3.Connection) -> None:
    """Recompute every generation's output fingerprint through the new view."""
    from .derivations import canonical

    labels = conn.cursor()
    rows = conn.cursor()
    for generation_id, output_digest, row_count in labels.execute(
        "SELECT generation_id,output_digest,row_count FROM derivation_generations "
        "ORDER BY generation_id"
    ):
        output = hashlib.sha256()
        count = 0
        for table_name, record_key, payload_json in rows.execute(
            "SELECT table_name,record_key,payload_json FROM derivation_rows "
            "WHERE generation_id=? ORDER BY ordinal",
            (generation_id,),
        ):
            count += 1
            output.update(canonical((table_name, record_key, payload_json)).encode() + b"\n")
        if (output.hexdigest(), count) != (output_digest, row_count):
            raise RuntimeError(
                f"interned derivation output does not match the label of generation {generation_id}"
            )
