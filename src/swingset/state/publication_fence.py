"""Preserve publication's semantic reservation while operator controls remain writable.

Two fences, one per publication kind (plan phase 3, WP10):

- `publication` (and the legacy `publish`): a closure-mode or settled
  candidate. Its safety check walks live tables, so every non-control table
  is fenced, and the loops pause (F6).
- `publication_narrow`: a partitioned candidate (D-0224). Its artifacts are
  immutable and named by digest, so the fence covers only what
  `publish/safety._validate_partitioned` and the correction token read
  (`NARROW`). The loops tick through it.
"""

import sqlite3

CONTROL_TABLES = frozenset(
    {
        "operator_pauses",
        "control_state",
        "control_events",
        "execution_admissions",
        "execution_dependencies",
    }
)
#: Segmented backups record what a transport holds (schema 36). A
#: publication reads neither, and a backup may run while one is active.
OPERATIONAL_TABLES = frozenset({"backup_sets", "backup_objects"})

#: Admission kinds whose fence covers every semantic table.
FULL = ("publish", "publication")
#: A partitioned publication's admission kind.
NARROW_KIND = "publication_narrow"
#: Every publication admission kind: each settles as uncertain on recovery.
PUBLICATION_KINDS = frozenset((*FULL, NARROW_KIND))


def kinds_sql(kinds: tuple[str, ...] | frozenset[str]) -> str:
    """`('a','b')` for an `action_kind IN` clause; the kinds are constants.

    A tuple keeps its order, so `FULL` gives the trigger text of schema 12.
    """
    ordered = kinds if isinstance(kinds, tuple) else tuple(sorted(kinds))
    return "(" + ",".join(f"'{kind}'" for kind in ordered) + ")"


_BUILD_ROWS = (
    "EXISTS(SELECT 1 FROM derivation_generations g "
    "WHERE g.generation_id=R.generation_id AND g.stage='build')"
)

#: What a partitioned publication's safety check reads, table by table, with
#: the rows it reads (a SQL condition over `R`, the old or new row; None is
#: every row). Derived from `_validate_partitioned`, `_validate_candidate`,
#: `build/manifest.correction_token` and `identity_policy.correction_token`;
#: `tests/test_publication_fence.py` pins it.
NARROW: dict[str, str | None] = {
    # The input bundle the candidate was built from, and the journal digest.
    "meta": "R.key IN ('input_bundle_hash','identity_journal_digest')",
    # The journal generation.
    "revisions": "R.name='identity_decisions'",
    # The identity decision journal itself.
    "identity_decisions": None,
    "identity_journal_acceptances": None,
    # The admission part of the token.
    "admission_policies": None,
    # The revocations digest.
    "source_generations": "R.state='revoked'",
    # The durable build completion (`build/generations.completed`).
    "derivation_scopes": "R.stage='build'",
    "derivation_generations": "R.stage='build'",
    "derivation_row_refs": _BUILD_ROWS,
    # A build label's eviction marker and recipe pack (schema 36), and the
    # objects a build's rows, recipe or manifest may be read from.
    "derivation_evicted": _BUILD_ROWS,
    "label_body_packs": _BUILD_ROWS,
    "objects": (
        "(R.class IN ('release_manifest','label_body') OR (R.class='derivation_output' "
        "AND EXISTS(SELECT 1 FROM derivation_generations g "
        "WHERE g.output_digest=R.digest AND g.stage='build')))"
    ),
    # A revoked generation's body pack, like the revocations themselves.
    "source_generation_packs": (
        "EXISTS(SELECT 1 FROM source_generations s "
        "WHERE s.generation_id=R.generation_id AND s.state='revoked')"
    ),
    # A payload is shared by content; only its removal can take a build's.
    "derivation_payloads": "DELETE",
    # The release manifest, its artifacts and the partitions naming them.
    "release_partitions": None,
    "release_artifacts": None,
    "release_manifests": None,
}

#: A table's key columns, for "an insert that adds no new row changes nothing".
_KEYS = {
    "meta": ("key",),
    "revisions": ("name",),
    "identity_decisions": ("decision_id",),
    "identity_journal_acceptances": ("digest",),
    "admission_policies": ("page_kind",),
    "source_generations": ("generation_id",),
    "derivation_scopes": ("stage", "unit_kind", "unit_id"),
    "derivation_generations": ("generation_id",),
    "derivation_row_refs": ("generation_id", "ordinal"),
    "derivation_evicted": ("generation_id",),
    "label_body_packs": ("generation_id",),
    "objects": ("class", "digest"),
    "source_generation_packs": ("generation_id",),
    "release_partitions": ("partition",),
    "release_artifacts": ("artifact_digest",),
    "release_manifests": ("manifest_digest",),
}
#: Tables whose update fences only a changed value (a counter, a setting).
_VALUE = {"meta": "value", "revisions": "value"}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def narrow_condition(table: str, action: str) -> str | None:
    """The row condition of `table`'s narrow trigger for `action`, or None for no trigger.

    An insert fences only a new key: `INSERT ... ON CONFLICT DO NOTHING` and
    `INSERT OR IGNORE` on a present row change nothing the check reads (parse
    upserts `admission_policies`, derive re-accepts an unchanged bundle), and
    `DO UPDATE` fires the update trigger. `INSERT OR REPLACE` of a present key
    would pass, so no writer of these tables uses it (a test checks). An
    update of `meta` or `revisions` fences only a changed value.
    """
    rows = NARROW[table]
    if rows == "DELETE":
        return "1" if action == "DELETE" else None
    parts: list[str] = []
    if action == "INSERT":
        if rows is not None:
            parts.append(rows.replace("R.", "NEW."))
        keys = _KEYS[table]
        match = " AND ".join(f"k.{_quote(key)}=NEW.{_quote(key)}" for key in keys)
        parts.append(f"NOT EXISTS(SELECT 1 FROM {_quote(table)} k WHERE {match})")
    elif action == "UPDATE":
        if rows is not None:
            parts.append(
                "(" + rows.replace("R.", "OLD.") + " OR " + rows.replace("R.", "NEW.") + ")"
            )
        if table in _VALUE:
            column = _quote(_VALUE[table])
            parts.append(f"NEW.{column} IS NOT OLD.{column}")
    elif rows is not None:
        parts.append(rows.replace("R.", "OLD."))
    return " AND ".join(parts) or "1"


def install_publication_fences(conn: sqlite3.Connection) -> None:
    """Refresh coverage after every migration, including newly introduced tables."""
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        if table in CONTROL_TABLES or table in OPERATIONAL_TABLES:
            continue
        quoted_table = _quote(table)
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = _quote(f"publication_fence_{table}_{action.lower()}")
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {quoted_table} "
                "WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' "
                f"AND action_kind IN {kinds_sql(FULL)}) "
                "BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END"
            )
        if table not in NARROW:
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            condition = narrow_condition(table, action)
            if condition is None:
                continue
            name = _quote(f"publication_narrow_fence_{table}_{action.lower()}")
            # The admission test comes first, so the row condition is read
            # only while a narrow publication is active.
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {quoted_table} "
                "WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' "
                f"AND action_kind='{NARROW_KIND}') AND ({condition}) "
                "BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END"
            )


def narrow_triggers(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """The narrow fence as installed: table -> sorted actions (for tests and doctor)."""
    found: dict[str, list[str]] = {}
    prefix = "publication_narrow_fence_"
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ?", (prefix + "%",)
    ):
        table, _, action = str(name).removeprefix(prefix).rpartition("_")
        found.setdefault(table, []).append(action)
    return {table: sorted(actions) for table, actions in sorted(found.items())}


def expected_narrow(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """The narrow triggers this schema should carry: `NARROW` over its tables."""
    present = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        table: sorted(
            action.lower()
            for action in ("INSERT", "UPDATE", "DELETE")
            if narrow_condition(table, action) is not None
        )
        for table in sorted(NARROW)
        if table in present
    }


def admission_kind(conn: sqlite3.Connection, *, narrow: bool) -> str:
    """The kind a publication is admitted as: narrow only when its fence is whole.

    A database whose narrow triggers are missing (installed by migration 35
    onward) takes the full fence, so a narrow publication never runs unfenced.
    """
    if narrow and narrow_triggers(conn) == expected_narrow(conn):
        return NARROW_KIND
    return "publication"
