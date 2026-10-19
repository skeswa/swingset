# D-0263: Postgres holds all state, and the move happens in four stages

Recorded: 2026-10-19  
Decided by: owner, 2026-10-19  
Source: "our postgres (replacing sqlite) should maintain all of our state including overrides, exceptions, approvals etc"; the owner also set the stage order  
Topic: Hosting  
Supersedes: in part, [D-0004](0004-postgres-migration.md), which covered only a database port and a transfer  
Superseded by: amended by [D-0264](0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md) (the consequences below: the environment fingerprint no longer triggers re-derivation, and a stage 0 precedes the four stages)

## Decision

- Postgres becomes the one home of everything Swingset knows and everything
  people or agents decide: pipeline data, overrides, exceptions, approvals,
  holds, settings and releases. Each decision is a row with who proposed it,
  who approved it, when and why.
- The database is built to be queried. Published facts keep stable ids, a
  release and redirects, so it can back an API later.
- The release builder keeps writing the Hugging Face files, so releases stay
  exactly as complete as today.
- Large files that never change, and backups, move to object storage off the
  machine.
- The move has four stages, each ending with a gate: refactor and check
  locally; move files to the cloud; a dry run on Dokploy that cannot fetch
  or publish; the switch. Decisions move into Postgres after the switch, one
  kind at a time.

## Why

The Mac sleeping has stopped production for hours to days. There is no
backup off the machine. Decisions are spread across files, git and free
text, where nobody can query them. The owner wants to build on the data.

## Consequences

As recorded on 2026-10-19: the move changes every recipe scope. Production
re-derives everything once, in one batched deploy in stage 1. From then on
the environment fingerprint hashes the lockfile, the Python version and the
CPU type, so new images for the same CPU do not re-derive. Production runs
on ARM64 and the server is most likely AMD64 (an Intel Xeon; stage 1's
server check confirms it), so the server re-derives once in the dry run and
once at the switch, which pauses releases for one to three days.

Amended the same day by D-0264: the environment identity stays exact
rather than simplified, is recorded for audit and triggers nothing; a stage 0 makes declared transform
versions and differential proofs decide re-derivation, so the three
re-derivations above become proofs and the switch pauses releases only for
the import and its audit. Plan: [Postgres and Dokploy](../../docs/plans/postgres-migration/README.md).

Plan edit the same day, with the owner: stage 1's full-size checks (the
import, the whole-graph proof, the release comparison) run on the server in
an isolated stack, because the Mac lacks the disk; code and tests stay on
the Mac. The plan also has no dates or waiting periods, so it can run in
one session.

Plan edit later the same day, with the owner: the lighter plan shape ("B").
Production does not change before the switch. The order becomes: port
(with stage 0), a dry run on the server whose one full re-derivation is the
proof, the switch, and then files into object storage, `gov` and the
catalog.

Also with the owner: publishing on a daily cadence is not a requirement.
Postgres, and later an API, becomes the main way to reach the data, and
Hugging Face one export. The scraping and safety rules become approved
rows in `gov` that change without a deploy; loosening one needs a second
approver.
