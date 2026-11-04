"""DuckDB scratch scans remain usable across nested lookups and writes."""

from swingset import scratch_db


def test_key_scan_keeps_order_across_payload_lookups_and_mutations(tmp_path):
    with scratch_db.connect(tmp_path / "rows.duckdb") as conn:
        conn.execute("CREATE TABLE rows(key TEXT PRIMARY KEY,payload TEXT)")
        scratch_db.insert_many(
            conn,
            "INSERT INTO rows VALUES (?,?)",
            ((f"{index:04d}", "payload-" + str(index)) for index in range(400)),
        )
        seen = []
        for (key,) in scratch_db.scan_keys(conn, "SELECT key FROM rows ORDER BY key"):
            assert (
                conn.execute("SELECT payload FROM rows WHERE key=?", (key,))
                .fetchone()[0]
                .startswith("payload-")
            )
            conn.execute("DELETE FROM rows WHERE key=?", (key,))
            seen.append(key)
        assert seen == [f"{index:04d}" for index in range(400)]
        assert conn.execute("SELECT count(*) FROM rows").fetchone()[0] == 0


def test_empty_insert_batch_is_valid_for_an_empty_omission_set(tmp_path):
    with scratch_db.connect(tmp_path / "empty.duckdb") as conn:
        conn.execute("CREATE TABLE omitted_events(event_id TEXT PRIMARY KEY)")
        scratch_db.insert_many(conn, "INSERT INTO omitted_events VALUES (?)", iter(()))
        assert conn.execute("SELECT count(*) FROM omitted_events").fetchone()[0] == 0


def test_segment_recovery_keeps_one_index_load_and_one_tar_scan(tmp_path, monkeypatch):
    import io
    import tarfile

    from swingset.backup import recovery
    from swingset.backup.segments import Entry

    segment = "a" * 64
    checkpoints = tmp_path / "checkpoints"
    archive_path = checkpoints / "segments" / (segment + ".tar")
    archive_path.parent.mkdir(parents=True)
    body = b"one retained member"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("objects/pack")
        member.size = len(body)
        archive.addfile(member, io.BytesIO(body))
    loads = []
    scans = []
    real_open = recovery.tarfile.open

    def index(path):
        loads.append(path)
        return iter((Entry("label_body", "objects/pack", "b" * 64, len(body), segment),))

    def tar(*args, **kwargs):
        scans.append(args[0])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(recovery, "read_index", index)
    monkeypatch.setattr(recovery.tarfile, "open", tar)
    lookup = recovery.SegmentRecovery(checkpoints)
    try:
        assert lookup._entry("set", "objects/pack") == (segment, len(body), "b" * 64)
        assert lookup._entry("set", "objects/pack") == (segment, len(body), "b" * 64)
        assert lookup._entry("set", "absent") is None
        for _ in range(2):
            path, offset, size = lookup._member(segment, "objects/pack")
            with path.open("rb") as source:
                source.seek(offset)
                assert source.read(size) == body
        assert len(loads) == len(scans) == 1
    finally:
        lookup.close()
