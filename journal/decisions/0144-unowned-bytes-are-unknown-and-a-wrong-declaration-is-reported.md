# D-0144: Bytes no label owns are unknown, and a declaration that names a missing file is reported

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

The planner splits every distinct payload's bytes three ways, not two:

- **local** if any generation that names it is on the local list, because
  archiving the others would free nothing;
- **unknown** if no reference names it at all, or if every reference that names
  it belongs to a generation with no label; and
- **archivable** otherwise.

Unknown bytes are reported under `unknown_payload_bytes` and never counted in
`archivable_payload_bytes`. The plan lists the two kinds separately, as
`orphan_payloads` and `unowned_payloads`.

Payload bytes everywhere are measured with SQLite's `octet_length`, never
`length`.

An open finding may declare a `body` or `extract` digest for a file that is not
on disk. The file closure keeps whatever is there and skips the rest, and the
planner reports the declaration under `missing_declared_references`. It is not
an error.

## Why

Plan section 7 says a reference whose label is missing is unknown, that doctor
reports it, and that nothing removes it until someone works out what it is. The
first version of the planner asked only whether a payload had any local owner,
so a payload named solely by a reference with no label was scored as bytes
archiving would give back. That is the opposite of the rule: it told the
operator, and the `gc --apply` step that reads these totals, that unexplained
bytes were eligible, and it hid them from the unknown counters. Confirmed by
experiment before the fix: a 12-byte payload named only by a reference to a
generation that does not exist raised `archivable_payload_bytes` by 12 and left
`unknown_payload_bytes` at 0.

`length` counts characters on a TEXT column. Payloads are canonical JSON written
with `ensure_ascii=False`, so a dancer named `Renée Zoë` is stored as raw UTF-8
and every accent cost a byte the count did not see. This is a west-coast-swing
dataset; accented names are ordinary, not exotic. `octet_length` is available in
SQLite 3.50 and is the same query otherwise. These are the numbers section 9
tells the operator to size the cap against, so they have to be bytes.

The declared-reference rule restores the behaviour the evidence scan had before
schema 31. That scan kept a blob or extract only if the file existed. Making the
declaration authoritative turned one wrong row into a total backup outage:
`create_checkpoint` raised "referenced artifact is missing" and no backup could
be written until someone deleted the row by hand, confirmed by experiment. A
finding is written by about twenty callers across the pipeline and is not
checked against the disk when it is written, so the declaration cannot be
treated as a promise the way a hold can. The backup path is the one thing that
has to keep working when the state directory is in a bad way.

## Alternatives

- Check body and extract declarations when `replace_findings` writes them, the
  way `add_hold` checks a held digest. Rejected for now: `replace_findings` is
  given a connection and no state directory, and threading one through every
  caller is a change to twenty modules for a check the planner already reports.
  A generation reference is checked, because `require_local` needs only the
  connection. Worth revisiting when a finding caller is next reworked.
- Count a payload as archivable if any owner is archivable. Rejected: archiving
  one owner of a shared payload frees nothing, so the number would not be bytes
  that leave the file.
- Report unknown payloads as one list. Rejected: a payload nobody references and
  a payload an unlabelled reference names have different causes, and an operator
  working out what they are needs to know which.

## Consequences

`archivable_payload_bytes` is now what it says: bytes that would leave the file.
Unknown bytes are visible in doctor and in the plan and can only shrink by
someone explaining them. Byte counts rise slightly against the old figures
wherever payloads hold non-ASCII text, which is the correction, not a
regression. A finding that declares a file which is not there costs a line in
doctor's unknown section until it is fixed.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0139](0139-findings-declare-the-support-they-rely-on.md), whose
  consequences state this rule: a declared file that is not there is reported as
  `missing_declared_references`, and the closure keeps only what is on disk
- [D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)
- [State contract](../../docs/reference/state.md)
- Renumbered: declared references are schema 30, not 31, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md), which also makes unowned payloads unnameable before schema 32.
