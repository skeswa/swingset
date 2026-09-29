"""A link depends on what it read: its read set, pool versions and the reverse index (plan F20).

The fixture is twenty events whose entries and judges share names with a
small registry. Dancers run as real dancer units, so a registry change moves
their generations and runs the pool probe; links run as real link units.
"""

import random
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType, SimpleNamespace

import pytest

from swingset.clock import FakeClock
from swingset.link import link_event
from swingset.model.canonical import Dancer
from swingset.model.ids import observation_id
from swingset.model.observations import encode_payload
from swingset.project import process
from swingset.project.materialization import output_rows
from swingset.project.process import process_unit
from swingset.schedule.watches import upsert_watch
from swingset.sources.records import DancerLookup
from swingset.sources.records import RegistryPlacement as RawPlacement
from swingset.sources.wsdc_registry import DancerPage
from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY_SOURCE
from swingset.state import (
    derivation_dependencies,
    derivation_query,
    derivation_readiness,
    derivations,
    read_index,
)
from swingset.state.attempts import SupersededWorkError
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = datetime(2026, 3, 1, tzinfo=UTC)
EVENTS = tuple(f"e{index:02d}" for index in range(20))
#: Registry dancers: id -> (first, last).
REGISTRY = {
    1: ("Alex", "Lee"),
    2: ("Sam", "Lee"),
    3: ("Jordan", "Park"),
    4: ("Casey", "Park"),
    5: ("Robin", "Stone"),
    6: ("Taylor", "Stone"),
    7: ("Morgan", "Hale"),
    8: ("Jamie", "Hale"),
    9: ("Riley", "Quinn"),
    10: ("Avery", "Quinn"),
    11: ("Drew", "Novak"),
    12: ("Kim", "Ortiz"),
    13: ("Lee", "Chang"),
    14: ("Pat", "Chang"),
    15: ("Chris", "Bell"),
    16: ("Dana", "Bell"),
}
#: Names entries print: registry names, near misses and unregistered people.
ENTRY_NAMES = (
    *(f"{first} {last}" for first, last in REGISTRY.values() if last != "Ortiz"),
    "Alexander Lee",
    "Terry Lee",
    "Jess Parkes",
    "Robyn Stone",
    "Quincy Adams",
    "Zoe Marsh",
)
JUDGE_NAMES = ("Kim Ortiz", "Morgan Hale", "Nina Vale", "Chris Bell")


class Bundle:
    # No helper files: the default weights, and a recipe that currency, which
    # reads only accepted inputs, recomputes the same.
    files: MappingProxyType[str, bytes] = MappingProxyType({})

    def csv(self, name: str) -> list[dict[str, str]]:
        return []


def _events(conn):
    for index, event in enumerate(EVENTS):
        conn.execute(
            "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,event_month,"
            "wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
            "VALUES (?,?,?,2026,'2026-01-10','2026-01-12','2026-01','registry','[]','test','snap',"
            "'1','t','t','run')",
            (event, f"wsdc-{100 + index}", f"Series {index}"),
        )
        contest = f"{event}/novice"
        conn.execute(
            "INSERT INTO contests(contest_id,event_id,name_raw,division,age_division,contest_type,"
            "partner_mode,dance_style,wsdc_points_eligible,combined_from,parse_status,"
            "source_contest_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
            "VALUES (?,?,'Novice J&J','novice','none','jack_and_jill','random_partner','wcs',1,'[]',"
            "'parsed',?,'test','snap','1','t','t','run')",
            (contest, event, contest),
        )
        conn.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,"
            "callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,"
            "parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,'final',1,'Final',"
            "'relative_placement','{}',2,6,'final','test','snap','1','t','t','run')",
            (f"{contest}/final", contest),
        )
        names = random.Random(index).sample(ENTRY_NAMES, 6)
        for position, name in enumerate(names):
            role = "leader" if position % 2 == 0 else "follower"
            conn.execute(
                "INSERT INTO entries(entry_id,contest_id,event_id,role,bib,name_raw,name_norm,"
                "link_status,link_confidence,rounds_danced,source,snapshot_id,parser_version,"
                "first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,'unmatched',0,"
                "'[\"final\"]','test','snap','1','t','t','run')",
                (f"{event}/{position}", contest, event, role, str(position), name, name.casefold()),
            )
        for place in (1, 2):
            conn.execute(
                "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,"
                "leader_entry_id,follower_entry_id,tally,source,snapshot_id,parser_version,"
                "first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,'','test','snap','1',"
                "'t','t','run')",
                (
                    f"{event}/place-{place}",
                    f"{contest}/final",
                    contest,
                    event,
                    place,
                    f"{event}/{2 * place - 2}",
                    f"{event}/{2 * place - 1}",
                ),
            )
        for position, name in enumerate(random.Random(100 + index).sample(JUDGE_NAMES, 2)):
            conn.execute(
                "INSERT INTO judges(judge_id,event_id,name_raw,anonymous,source,snapshot_id,"
                "parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,0,'test','snap',"
                "'1','t','t','run')",
                (f"{event}/judge-{position}", event, name),
            )


def _placements(conn, first, last):
    """The registry's record of a person: their placed entries at the fixture's events."""
    rows = conn.execute(
        "SELECT p.event_id,p.place,e.role FROM placements p JOIN entries e "
        "ON e.entry_id IN (p.leader_entry_id,p.follower_entry_id) WHERE e.name_raw=? "
        "ORDER BY p.event_id",
        (f"{first} {last}",),
    ).fetchall()
    return [
        RawPlacement(
            str(row[2]),
            "NOV",
            str(100 + EVENTS.index(str(row[0]))),
            f"Series {EVENTS.index(str(row[0]))}",
            "January 2026",
            str(row[1]),
            3 if row[1] == 1 else 2,
            "West Coast Swing",
        )
        for row in rows
    ]


class World(SimpleNamespace):
    def registry(self, wsdc_id, **changes):
        """Accept a new registry page for one dancer and run its dancer unit."""
        person = {**self.people.get(wsdc_id, {}), **changes}
        person.setdefault("placements", _placements(self.conn, person["first"], person["last"]))
        self.people[wsdc_id] = person
        self.pages += 1
        spec = REGISTRY_SOURCE.watch(wsdc_id)
        upsert_watch(self.conn, spec, NOW)
        snapshot = f"registry-{wsdc_id}-{self.pages}"
        self.conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
            "body_bytes,content_changed,run_id,classification) "
            "VALUES (?,?,'POST',?,?,200,1,1,'run','Ok')",
            (snapshot, spec.watch_id, spec.url, NOW.isoformat()),
        )
        payload = DancerLookup(
            "dancer_lookup",
            "found",
            wsdc_id,
            wsdc_id,
            first_name=person["first"],
            last_name=person["last"],
            primary_role_raw=person.get("role", "L"),
            is_pro=person.get("pro", False),
            leader_required_raw="NOV",
            leader_allowed_raw="ADV",
            follower_required_raw="NOV",
            follower_allowed_raw="ADV",
            leader_highest_raw=person.get("highest", "NOV"),
            follower_highest_raw="NOV",
            recent_year=person.get("recent", 2026),
            placements=tuple(person["placements"]),
        )
        page = DancerPage()
        self.conn.execute(
            "DELETE FROM observations WHERE scope_kind='dancer' AND scope_id=?", (str(wsdc_id),)
        )
        self.conn.execute(
            "INSERT INTO observations VALUES (?,?,?,?,'dancer',?,0,?,?,?)",
            (
                observation_id(spec.watch_id, snapshot, payload.kind, 0),
                spec.watch_id,
                snapshot,
                payload.kind,
                str(wsdc_id),
                str(page.EXTRACT_VERSION),
                str(page.PARSER_VERSION),
                encode_payload(payload),
            ),
        )
        process_unit(
            self.db, WorkUnit("project", "dancer", str(wsdc_id)), Bundle(), self.clock, "run"
        )

    def link(self, event):
        return link_event(self.db, event, Bundle(), self.clock, "run")

    def link_all(self):
        for event in EVENTS:
            self.link(event)
        self.conn.execute("DELETE FROM pending_work WHERE stage='link'")
        assert self.stale() == set()

    def stale(self):
        """The links that are not current: re-queued by their inputs."""
        return {
            event
            for event in EVENTS
            if not derivations.current(self.conn, WorkUnit("link", "event", event))
        }

    def queued(self):
        return {
            str(row[0])
            for row in self.conn.execute(
                "SELECT unit_id FROM pending_work WHERE stage='link' AND unit_kind='event'"
            )
        }

    def readers(self, key):
        return {unit.unit_id for unit in read_index.dependents(self.conn, key)}

    def output(self, event):
        return list(output_rows(self.conn, WorkUnit("link", "event", event)))

    def answer(self, event):
        """What running the link now would conclude: every subject's link row,
        candidate rows and default id, without committing.

        A link's resolution state also reads its own previous default id
        (`SubjectResolution.history_state`: a revocation reads `revoked` once,
        then `unresolved`), so a committed re-run is not a fixed point after a
        revocation. The answer leaves that state out.
        """
        from swingset.link.evidence import load_linking_snapshot
        from swingset.link.persistence import _candidates, _link
        from swingset.link.resolution import resolve_event
        from swingset.link.service import LINKER_VERSION

        snapshot = load_linking_snapshot(self.conn, event, Bundle(), None)
        resolution = resolve_event(snapshot.evidence, snapshot.rules)
        return sorted(
            (_link(item, LINKER_VERSION), tuple(_candidates(item)), item.default_wsdc_id)
            for item in resolution.subjects
        )

    def settle(self):
        """Every stale link re-queued and hinted; re-run it. Return the stale set."""
        stale = self.stale()
        assert stale <= self.queued(), sorted(stale - self.queued())
        for event in sorted(stale):
            self.link(event)
        self.conn.execute("DELETE FROM pending_work WHERE stage='link'")
        assert self.stale() == set()
        return stale


@pytest.fixture
def world(tmp_path):
    with open_database(tmp_path / "state", lock=False) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,1)", (NOW.isoformat(),)
        )
        _events(conn)
        w = World(db=db, conn=conn, clock=FakeClock(NOW), people={}, pages=0)
        for wsdc_id, (first, last) in REGISTRY.items():
            w.registry(wsdc_id, first=first, last=last, pro=wsdc_id == 12)
        # Event units own no fixture rows here; they exist so a link can be ready.
        for unit in (
            *(WorkUnit("project", "event", event) for event in EVENTS),
            WorkUnit("project", "year", "2026"),
        ):
            process_unit(db, unit, Bundle(), w.clock, "run")
        w.link_all()
        yield w


def test_a_link_records_what_it_read(world):
    w = world
    keys = read_index.keys(w.conn, WorkUnit("link", "event", "e00"))
    assert {
        "project/event/e00",
        "pool:e00",
        "content:event_row/e00",
        "content:registry_event/e00",
        "content:missing_identity/e00",
        "content:journal",
    } <= keys
    candidates = {
        f"project/dancer/{row[0]}"
        for row in w.conn.execute(
            "SELECT wsdc_id FROM link_candidates WHERE subject_id LIKE 'e00/%'"
        )
    }
    dancers = {key for key in keys if key.startswith("project/dancer/")}
    assert candidates and candidates <= dancers
    # Never the registry: only dancers it used.
    assert len(dancers) < len(REGISTRY)
    assert derivations.desired(w.conn, WorkUnit("link", "event", "e00")).reads == tuple(
        sorted(keys)
    )


def test_a_points_change_requeues_only_the_links_that_read_the_dancer(world):
    w = world
    readers = w.readers("project/dancer/3")
    person = w.people[3]
    assert person["placements"]
    placed = {EVENTS[int(item.event_id_raw) - 100] for item in person["placements"]}
    changed = [replace(person["placements"][0], points=9), *person["placements"][1:]]
    w.registry(3, placements=changed)
    assert w.settle() == readers | placed
    assert readers | placed < set(EVENTS)


def test_a_new_matching_dancer_requeues_that_event_only(world):
    w = world
    named = {
        str(row[0])
        for row in w.conn.execute("SELECT event_id FROM entries WHERE name_raw='Zoe Marsh'")
    }
    assert named
    w.registry(40, first="Zoe", last="Marsh", placements=[])
    assert w.settle() == named
    assert w.readers("project/dancer/40") == named


def test_a_surname_word_change_requeues_events_with_candidates_of_either_word(world):
    w = world

    def candidates_of(word):
        return {
            str(row[0])
            for row in w.conn.execute(
                "SELECT DISTINCT e.event_id FROM link_candidates c JOIN dancers d USING(wsdc_id) "
                "JOIN entries e ON e.entry_id=c.subject_id WHERE lower(d.last_name)=?",
                (word,),
            )
        }

    # A registry dancer no subject names: only name rarity reaches links.
    w.registry(41, first="Zed", last="Lee", placements=[])
    lee, park = candidates_of("lee"), candidates_of("park")
    assert w.settle() == lee
    w.registry(41, first="Zed", last="Park")
    assert lee and park and w.settle() == lee | park


def test_an_eligibility_change_requeues_the_events_of_matching_judges(world):
    w = world
    judged = {
        str(row[0])
        for row in w.conn.execute("SELECT event_id FROM judges WHERE name_raw='Nina Vale'")
    }
    w.registry(42, first="Nina", last="Vale", placements=[])
    # Not yet eligible to judge: no pool reads her.
    assert w.settle() == set()
    w.registry(42, pro=True)
    assert judged and w.settle() == judged


def test_a_merge_requeues_the_dancers_readers(world, monkeypatch):
    w = world
    readers = w.readers("project/dancer/2")
    assert readers
    original = process.project_dancer

    def merged(conn, scope_id, now, run_id):
        projection = original(conn, scope_id, now, run_id)
        return replace(
            projection,
            rows=tuple(
                replace(row, merged_into_wsdc_id=1)
                if isinstance(row, Dancer) and row.wsdc_id == 2
                else row
                for row in projection.rows
            ),
        )

    monkeypatch.setattr(process, "project_dancer", merged)
    w.registry(2)
    stale = w.settle()
    assert readers <= stale
    # Beyond its readers, only the events whose candidates share its surname
    # word: the merge changed their rarity count.
    assert stale - readers <= w.readers("project/dancer/1") | readers


def test_a_year_remint_leaves_links_current_unless_their_association_moved(world):
    w = world
    year = WorkUnit("project", "year", "2026")
    # A placement reported in the year for no fixture event: the year re-mints
    # and every event's association stays as it was.
    elsewhere = RawPlacement("leader", "NOV", "555", "Elsewhere Open", "January 2026", "1", 3)
    w.registry(44, first="Nobody", last="Anywhere", placements=[elsewhere])
    assert w.settle() == set()
    before = derivations.selected_generation(w.conn, year)
    process_unit(w.db, year, Bundle(), w.clock, "run")
    assert derivations.selected_generation(w.conn, year) != before
    assert w.stale() == set()
    # A calendar gives one event another series: its row changes, and the
    # year then takes its placements away. Only that event's link re-runs.
    moved = "e07"
    assert w.conn.execute("SELECT 1 FROM registry_placements WHERE event_id=?", (moved,)).fetchone()
    with w.db.transaction():
        w.conn.execute("UPDATE events SET series_id='wsdc-999' WHERE event_id=?", (moved,))
        derivations.bump_replay(w.conn, "project", "calendar")
        # The calendar unit's hint for the event whose row it changed.
        w.conn.execute("INSERT INTO pending_work VALUES ('link','event',?,'t')", (moved,))
    assert w.settle() == {moved}
    before = derivations.selected_generation(w.conn, year)
    process_unit(w.db, year, Bundle(), w.clock, "run")
    assert derivations.selected_generation(w.conn, year) != before
    assert not w.conn.execute(
        "SELECT 1 FROM registry_placements WHERE event_id=?", (moved,)
    ).fetchone()
    assert w.settle() == {moved}


def test_a_journal_change_requeues_every_link(world):
    w = world
    w.conn.execute("UPDATE revisions SET value=value+1 WHERE name='identity_decisions'")
    assert w.stale() == set(EVENTS)


@pytest.mark.parametrize("change", ["pool", "registry", "dancer"])
def test_an_input_changed_between_load_and_commit_supersedes_the_run(world, monkeypatch, change):
    from swingset.link import pool, service

    w = world
    original = service.resolve_event
    dancer = WorkUnit("project", "dancer", "3")
    generations = [derivations.selected_generation(w.conn, dancer)]
    w.registry(3, recent=2024)
    w.settle()
    generations.append(derivations.selected_generation(w.conn, dancer))
    used = [3]
    event = sorted(w.readers("project/dancer/3"))[0]
    unit = WorkUnit("link", "event", event)
    before = w.output(event)

    def resolve(evidence, rules):
        if change == "pool":
            with w.db.transaction():
                pool.bump(w.conn, [event], now=NOW.isoformat())
        elif change == "registry":
            w.conn.execute(
                "UPDATE registry_placements SET points=points+1 WHERE event_id=?", (event,)
            )
        else:
            # A dancer the run used moves to another of its generations.
            w.conn.execute(
                "UPDATE derivation_scopes SET materialized_generation_id=CASE "
                "materialized_generation_id WHEN ? THEN ? ELSE ? END "
                "WHERE stage='project' AND unit_kind='dancer' AND unit_id=?",
                (*generations, generations[0], str(used[0])),
            )
        return original(evidence, rules)

    monkeypatch.setattr(service, "resolve_event", resolve)
    generation = derivations.selected_generation(w.conn, unit)
    assert w.link(event) is False
    assert derivations.selected_generation(w.conn, unit) == generation
    assert w.output(event) == before
    with w.db.transaction():
        selection = derivations.capture(w.conn, unit, now=NOW)
    with pytest.raises(SupersededWorkError):
        link_event(w.db, event, Bundle(), w.clock, "run", selection=selection)


def test_ready_links_are_listed_without_the_dancer_cohort(world, monkeypatch):
    from swingset.schedule.fairness import next_offline_batch

    w = world
    assert not hasattr(derivation_readiness, "all_dancers_current")
    # A dancer no link read goes stale; one link's own event row moves.
    w.registry(43, first="Nobody", last="Anywhere", placements=[])
    w.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'Nobody','Somebody') "
        "WHERE scope_kind='dancer' AND scope_id='43'"
    )
    assert not derivations.current(w.conn, WorkUnit("project", "dancer", "43"))
    w.conn.execute("UPDATE events SET name='Renamed Series' WHERE event_id='e05'")

    def forbidden(*args, **kwargs):
        raise AssertionError("listing ready links read the dancer cohort")

    monkeypatch.setattr(derivation_readiness, "dancers_current", forbidden)
    batch = next_offline_batch(w.conn, now=NOW, allowed=lambda unit: unit.stage == "link", limit=50)
    assert batch == [WorkUnit("link", "event", "e05")]
    # Readiness checks the dancers e05 read, not the registry.
    checked = []
    current = derivations.current

    def counted(conn, unit, **kwargs):
        if unit.unit_kind == "dancer":
            checked.append(unit)
        return current(conn, unit, **kwargs)

    monkeypatch.setattr(derivations, "current", counted)
    with derivation_query.read_snapshot(w.conn):
        assert derivations.ready(w.conn, WorkUnit("link", "event", "e05"))
    read = {
        WorkUnit("project", "dancer", key.removeprefix("project/dancer/"))
        for key in read_index.keys(w.conn, WorkUnit("link", "event", "e05"))
        if key.startswith("project/dancer/")
    }
    assert checked and set(checked) == read


@pytest.mark.parametrize("seed", range(3))
def test_every_link_the_probe_and_index_leave_current_reruns_to_identical_output(
    world, monkeypatch, seed
):
    """The soundness check: after random dancer changes, every link still
    current concludes exactly what it concluded before (`World.answer`)."""
    w = world
    rng = random.Random(seed)
    merged: dict[int, int] = {}
    original = process.project_dancer

    def merging(conn, scope_id, now, run_id):
        projection = original(conn, scope_id, now, run_id)
        return replace(
            projection,
            rows=tuple(
                replace(row, merged_into_wsdc_id=merged[row.wsdc_id])
                if isinstance(row, Dancer) and row.wsdc_id in merged
                else row
                for row in projection.rows
            ),
        )

    monkeypatch.setattr(process, "project_dancer", merging)
    names = [name.split(" ", 1) for name in (*ENTRY_NAMES, *JUDGE_NAMES)]
    answers = {event: w.answer(event) for event in EVENTS}
    next_id = 60
    for _ in range(10):
        wsdc_id = rng.choice(sorted(w.people))
        mutation = rng.choice(
            ["rename", "surname", "points", "new", "pro", "highest", "merge", "role", "recent"]
        )
        if mutation == "rename":
            first, last = rng.choice(names)
            w.registry(wsdc_id, first=first, last=last)
        elif mutation == "surname":
            w.registry(wsdc_id, last=rng.choice(["Lee", "Park", "Stone", "Vale", "Ortega"]))
        elif mutation == "points" and w.people[wsdc_id]["placements"]:
            placements = list(w.people[wsdc_id]["placements"])
            placements[0] = replace(placements[0], points=placements[0].points + 1)
            w.registry(wsdc_id, placements=placements)
        elif mutation == "new":
            first, last = rng.choice(names)
            w.registry(next_id, first=first, last=last, placements=[])
            next_id += 1
        elif mutation == "pro":
            w.registry(wsdc_id, pro=not w.people[wsdc_id].get("pro", False))
        elif mutation == "highest":
            w.registry(wsdc_id, highest=rng.choice(["NOV", "ALS", "CHMP", "ADV"]))
        elif mutation == "merge":
            if wsdc_id in merged:
                del merged[wsdc_id]
            else:
                merged[wsdc_id] = rng.choice([other for other in w.people if other != wsdc_id])
            w.registry(wsdc_id)
        elif mutation == "role":
            w.registry(wsdc_id, role=rng.choice(["L", "F"]))
        else:
            w.registry(wsdc_id, recent=rng.choice([2019, 2024, 2026]))
        stale = w.stale()
        assert stale <= w.queued(), (mutation, sorted(stale - w.queued()))
        for event in sorted(set(EVENTS) - stale):
            assert w.answer(event) == answers[event], (mutation, wsdc_id, event)
        w.settle()
        answers.update({event: w.answer(event) for event in stale})
    assert derivation_dependencies.recorded_reads(w.conn, WorkUnit("link", "event", "e00"))


def test_every_source_reference_a_link_binds_is_a_read_key(tmp_path):
    from test_link_service import Bundle as ServiceBundle
    from test_link_service import entry, owned_source_sheet, run, seed

    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        entry(db.connection, "event/c1/L-8", "c1", "8", "Sam Other")
        owned_source_sheet(db.connection, [("7", "Alex Lee", 1), ("8", "Sam Other", None)])
        run(db, ServiceBundle())
        refs = {
            str(row[0])
            for row in db.connection.execute(
                "SELECT ref_id FROM identity_reference_bindings WHERE event_id='event'"
            )
        }
        keys = read_index.keys(db.connection, WorkUnit("link", "event", "event"))
        assert len(refs) == 2
        assert {
            key.removeprefix("content:binding_ref/") for key in keys if "binding_ref" in key
        } == refs
        # The printed id names a dancer, which the link reads.
        assert "project/dancer/1" in keys


def test_a_read_set_without_index_rows_is_read_back_from_the_manifest(tmp_path):
    """Before schema 35, or with rows lost, the generation's manifest names it."""
    from test_link_service import Bundle as ServiceBundle
    from test_link_service import entry, owned_source_sheet, run, seed

    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        owned_source_sheet(db.connection, [("7", "Alex Lee", 1)])
        run(db, ServiceBundle())
        unit = WorkUnit("link", "event", "event")
        keys = read_index.keys(db.connection, unit)
        assert any(key.startswith("content:binding_ref/") for key in keys)
        db.connection.execute("DELETE FROM derivation_reads")
        assert derivation_dependencies.recorded_reads(db.connection, unit) == keys


def test_a_printed_id_the_registry_lacks_never_blocks_its_link(tmp_path, monkeypatch):
    """F27: an entry prints WSDC 82468, which has no dancers row and no dancer
    unit. The link reads the row's absence; it stays ready after any hint, and
    the dancer's first pointer queues it again."""
    from test_link_service import entry, owned_source_sheet, run, seed

    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        seed(conn)
        entry(conn, "event/c1/L-7", "c1", "7", "Pat Unknown")
        owned_source_sheet(conn, [("7", "Pat Unknown", 82468)])
        # No helper files, so currency recomputes the same recipe (Bundle).
        run(db, Bundle())
        unit = WorkUnit("link", "event", "event")
        dancer = WorkUnit("project", "dancer", "82468")
        assert "project/dancer/82468" in read_index.keys(conn, unit)
        assert derivations.current(conn, unit)
        assert dancer not in derivation_dependencies.prerequisites(conn, unit)
        # The fixture's event and resolution units never ran; take them as
        # current, so only the dancer could hold the link.
        current = derivations.current

        def others_current(conn, target, **kwargs):
            if target.stage == "project" and target.unit_kind != "dancer":
                return True
            return current(conn, target, **kwargs)

        monkeypatch.setattr(derivations, "current", others_current)
        conn.execute("INSERT OR IGNORE INTO pending_work VALUES ('link','event','event','t')")
        with derivation_query.read_snapshot(conn):
            assert derivations.ready(conn, unit)
        conn.execute("DELETE FROM pending_work")
        monkeypatch.undo()
        # The dancer appears: its first pointer queues the link, which read
        # its absence and is no longer current.
        w = World(db=db, conn=conn, clock=FakeClock(NOW), people={}, pages=0)
        w.registry(82468, first="Pat", last="Unknown", placements=[])
        assert {
            str(row[0])
            for row in conn.execute("SELECT unit_id FROM pending_work WHERE stage='link'")
        } == {"event"}
        assert not derivations.current(conn, unit)
        assert dancer in derivation_dependencies.prerequisites(conn, unit)
        monkeypatch.setattr(derivations, "current", others_current)
        with derivation_query.read_snapshot(conn):
            assert derivations.ready(conn, unit)


def test_a_previous_default_outside_the_pool_is_read_in_the_load(tmp_path):
    """A subject's previous default can name a dancer outside the pool (a
    merged id, or one no unit holds). The load reads it in its own snapshot,
    so the read set never certifies a value read after the load."""
    from test_link_service import entry, seed

    from swingset.link.evidence import load_linking_snapshot

    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        seed(conn)
        entry(conn, "event/c1/L-7", "c1", "7", "Pat Unknown")
        conn.execute("UPDATE entries SET wsdc_id=777777 WHERE entry_id='event/c1/L-7'")
        snapshot = load_linking_snapshot(conn, "event", Bundle(), None)
        assert snapshot.reads is not None
        assert "project/dancer/777777" in snapshot.reads.unscoped


def test_a_write_during_the_load_is_never_certified_as_what_the_link_read(world, monkeypatch):
    """The values a link observed at load come from the load's own read
    transaction. A write another process commits between the evidence reads
    and the observed values used to be recorded as what the link read, so
    the commit check passed and stale output was certified current."""
    import sqlite3

    from swingset.link import evidence

    w = world
    event = "e03"
    unit = WorkUnit("link", "event", event)
    assert w.conn.execute("SELECT 1 FROM registry_placements WHERE event_id=?", (event,)).fetchone()
    original = evidence._loaded_reads
    other = sqlite3.connect(w.db.state_dir / "state.sqlite", timeout=10)

    def racing(*args, **kwargs):
        # Another process moves the event's registry rows after the evidence
        # reads and before the observed values.
        with other:
            other.execute(
                "UPDATE registry_placements SET points=points+7 WHERE event_id=?", (event,)
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence, "_loaded_reads", racing)
    w.conn.execute("INSERT OR IGNORE INTO pending_work VALUES ('link','event',?,'t')", (event,))
    w.link(event)
    other.close()
    # Whatever it committed, it stands for the rows its evidence read: the
    # moved rows leave it stale, to run again.
    assert not derivations.current(w.conn, unit)
