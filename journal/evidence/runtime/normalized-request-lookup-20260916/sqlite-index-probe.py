"""Repeat the disposable SQLite compatibility probe; never open project state."""
import json
import sqlite3
import tempfile
from pathlib import Path

from swingset.schedule.event_evidence import request, request_id


def key(method, url, form):
    return request_id(request("", method, url, form))


out = {"sqlite_version": sqlite3.sqlite_version, "cases": []}
with tempfile.TemporaryDirectory(prefix="swingset-request-index-") as directory:
    path = Path(directory) / "test.db"
    conn = sqlite3.connect(path)
    conn.create_function("request_key_v1", 3, key, deterministic=True)
    conn.executescript(
        "CREATE TABLE s(id TEXT PRIMARY KEY, method TEXT,url TEXT,form TEXT); "
        "CREATE TABLE unrelated(id INTEGER); "
        "INSERT INTO s VALUES('one','GET','https://EXAMPLE.com:443/a#fragment',NULL); "
        "CREATE INDEX request_lookup ON s(request_key_v1(method,url,form));"
    )
    target = (key("GET", "https://example.com/a", None),)
    out["query_plan"] = list(conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM s WHERE request_key_v1(method,url,form)=?", target
    ))
    out["matching_aliases"] = list(conn.execute(
        "SELECT id FROM s WHERE request_key_v1(method,url,form)=?", target
    ))
    try:
        conn.execute("ALTER TABLE s ADD COLUMN stored_key TEXT GENERATED ALWAYS AS (request_key_v1(method,url,form)) STORED")
    except sqlite3.Error as error:
        out["stored_column_alter_error"] = str(error)
    conn.commit()
    conn.close()
    for label, sql in (
        ("plain read without UDF", "SELECT * FROM s"),
        ("unrelated write without UDF", "INSERT INTO unrelated VALUES(1)"),
        ("snapshot write without UDF", "INSERT INTO s VALUES('two','GET','https://example.com/b',NULL)"),
        ("integrity check without UDF", "PRAGMA integrity_check"),
    ):
        conn = sqlite3.connect(path)
        try:
            out["cases"].append({"case": label, "result": list(conn.execute(sql))})
        except sqlite3.Error as error:
            out["cases"].append({"case": label, "error": str(error)})
        finally:
            conn.rollback()
            conn.close()
print(json.dumps(out, indent=2))
