"""Which events a dancer's registry change can reach through the candidate pool.

A link reads every unmerged dancer: its candidates come from pools by
surname initial, judge pools keep only eligible dancers, and a candidate's
name rarity counts every dancer with the same surname word. Its read set
names only the dancers it used, so a dancer who becomes a candidate, or who
changes a rarity count, would reach no link through it. The dancer unit runs
this probe around its write instead (plan F20). When the dancer's signature
changes, or the dancer appears or disappears, the probe bumps the pool
version (`link_pool`) of each event the change can reach, and queues that
event's link.

- Name, initial or pool membership: entries and judges whose surname initial
  matches the old or the new signature are tested with `generate_candidates`.
- Judge eligibility alone: judges only.
- Surname word or pool membership: every event whose live candidates name a
  dancer with the old or the new word.

A probe that fails bumps every event with a subject of either initial, and
logs `pool_probe_widened`: over-approximation is the safe direction.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from swingset.log import log
from swingset.normalize.names import normalize_name
from swingset.state import read_sets

from .candidates import DancerRecord, Subject, generate_candidates

#: Highest levels that make a registry dancer an eligible judge.
JUDGE_LEVELS = frozenset({"allstar", "als", "champion", "chmp"})


@dataclass(frozen=True)
class Signature:
    """What of one dancer the candidate pools read, besides the dancer's own row.

    ``name`` is the normalized name and ``initial`` its surname initial, which
    candidate generation compares; ``word`` is the surname word that name
    rarity counts. A merged dancer is in no pool.
    """

    name: str
    word: str
    initial: str
    merged_into: int | None
    eligible: bool
    raw: str = field(compare=False)

    @property
    def pooled(self) -> bool:
        return self.merged_into is None and bool(self.initial)


def signature(conn: sqlite3.Connection, wsdc_id: int) -> Signature | None:
    row = conn.execute(
        "SELECT first_name,last_name,merged_into_wsdc_id,is_pro,leader_highest_level,"
        "follower_highest_level FROM dancers WHERE wsdc_id=?",
        (wsdc_id,),
    ).fetchone()
    if row is None:
        return None
    raw = f"{row[0]} {row[1]}"
    normalized = normalize_name(raw)
    words = raw.casefold().split()
    return Signature(
        name=normalized.value,
        word=words[-1] if words else "",
        initial=normalized.last_token[:1],
        merged_into=None if row[2] is None else int(row[2]),
        eligible=bool(row[3])
        or str(row[4]).casefold() in JUDGE_LEVELS
        or str(row[5]).casefold() in JUDGE_LEVELS,
        raw=raw,
    )


def nicknames(files: Mapping[str, bytes]) -> dict[str, str]:
    """The nickname map linking reads (`overrides/nicknames.csv`)."""
    body = files.get("overrides/nicknames.csv", b"").decode()
    return {
        row["nickname"].casefold(): row["canonical"].casefold()
        for row in csv.DictReader(io.StringIO(body))
    }


def probe(
    conn: sqlite3.Connection,
    wsdc_id: int,
    before: Signature | None,
    after: Signature | None,
    *,
    nicknames: Mapping[str, str],
    now: str,
) -> frozenset[str]:
    """Bump the pool version of every event the change can reach; return them.

    Runs in the dancer unit's transaction. Nothing is bumped while no link
    has run: a link that never ran is not current anyway.
    """
    if before == after or not _links_ran(conn):
        return frozenset()
    try:
        events = _reached(conn, wsdc_id, before, after, dict(nicknames))
    except Exception as exc:
        initials = {item.initial for item in (before, after) if item is not None}
        log("pool_probe_widened", wsdc_id=wsdc_id, error=f"{type(exc).__name__}: {exc}"[:200])
        read_sets.signal("pool_probe_widened")
        events = _sharing_initial(conn, initials)
    bump(conn, events, now=now)
    return frozenset(events)


def bump(conn: sqlite3.Connection, events: Iterable[str], *, now: str) -> None:
    """Move each event's pool version and queue its link (a hint; currency decides)."""
    from swingset.state.derivation_dependencies import POOL_NAMESPACE

    for event in sorted(set(events)):
        conn.execute(
            "INSERT INTO derivation_input_versions VALUES (?,?,1) "
            "ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1",
            (POOL_NAMESPACE, event),
        )
        conn.execute(
            "INSERT OR IGNORE INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "VALUES ('link','event',?,?)",
            (event, now),
        )


def _links_ran(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM derivation_scopes WHERE stage='link' "
            "AND materialized_generation_id IS NOT NULL LIMIT 1"
        ).fetchone()
        is not None
    )


def _reached(
    conn: sqlite3.Connection,
    wsdc_id: int,
    before: Signature | None,
    after: Signature | None,
    names: dict[str, str],
) -> set[str]:
    def pooled(item: Signature | None) -> Signature | None:
        return item if item is not None and item.pooled else None

    old, new = pooled(before), pooled(after)
    renamed = (old and (old.name, old.initial)) != (new and (new.name, new.initial))
    eligibility = (old is not None and old.eligible) != (new is not None and new.eligible)
    events: set[str] = set()
    if renamed or eligibility:
        subjects = _subjects(conn)
        for item in (old, new):
            if item is None:
                continue
            dancer = DancerRecord(wsdc_id, item.raw, "unknown", 0)
            for (kind, name, role), named in subjects.get(item.initial, {}).items():
                if kind == "entry" and not renamed:
                    continue
                if kind == "judge" and not item.eligible:
                    continue
                if generate_candidates(Subject(kind, "probe", name, role=role), [dancer], names):
                    events |= named
    if (old and old.word) != (new and new.word):
        for word in {item.word for item in (old, new) if item is not None and item.word}:
            events |= _candidate_word(conn, word)
    return events


Subjects = dict[str, dict[tuple[str, str, str], set[str]]]
_cache: dict[tuple[object, ...], Subjects] = {}


def _subjects(conn: sqlite3.Connection) -> Subjects:
    """Every entry and judge name by surname initial, with the events it appears in.

    Names repeat across events, so each distinct name is tested once. The
    index is kept until an event projection changes the canonical revision.
    """
    path = next(
        (str(row[2]) for row in conn.execute("PRAGMA database_list") if row[1] == "main"), ""
    )
    revision = conn.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM entries),(SELECT count(*) FROM judges)"
    ).fetchone()
    key = (path, None if revision is None else int(revision[0]), *counts)
    if path and key in _cache:
        return _cache[key]
    index: Subjects = {}
    rows = [
        *(
            ("entry", str(row[1] or ""), str(row[2]), str(row[0]))
            for row in conn.execute("SELECT event_id,name_raw,role FROM entries")
        ),
        *(
            ("judge", str(row[1] or ""), "unknown", str(row[0]))
            for row in conn.execute("SELECT event_id,name_raw FROM judges")
        ),
    ]
    for kind, name, role, event in rows:
        initial = normalize_name(name).last_token[:1]
        if initial:
            index.setdefault(initial, {}).setdefault((kind, name, role), set()).add(event)
    _cache.clear()
    if path:
        _cache[key] = index
    return index


def _candidate_word(conn: sqlite3.Connection, word: str) -> set[str]:
    """Events whose live candidates name a dancer with this surname word (rarity)."""
    ids = [
        int(row[0])
        for row in conn.execute("SELECT wsdc_id,first_name,last_name FROM dancers")
        if (words := f"{row[1]} {row[2]}".casefold().split()) and words[-1] == word
    ]
    if not ids:
        return set()
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT e.event_id FROM link_candidates c JOIN entries e "
            "ON c.subject_kind='entry' AND e.entry_id=c.subject_id "
            "WHERE c.wsdc_id IN (SELECT value FROM json_each(:ids)) "
            "UNION SELECT j.event_id FROM link_candidates c JOIN judges j "
            "ON c.subject_kind='judge' AND j.judge_id=c.subject_id "
            "WHERE c.wsdc_id IN (SELECT value FROM json_each(:ids))",
            {"ids": json.dumps(ids)},
        )
    }


def _sharing_initial(conn: sqlite3.Connection, initials: set[str]) -> set[str]:
    """Every event with a subject of one of these surname initials (the widening)."""
    events: set[str] = set()
    for table in ("entries", "judges"):
        for event, name in conn.execute(f"SELECT event_id,name_raw FROM {table}"):
            if not initials or normalize_name(str(name or "")).last_token[:1] in initials:
                events.add(str(event))
    return events
