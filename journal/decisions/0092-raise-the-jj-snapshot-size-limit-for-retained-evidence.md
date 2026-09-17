# D-0092: Raise the jj snapshot size limit for retained evidence

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Repository tooling  
Supersedes: —  
Superseded by: —

## Decision

Set `snapshot.max-new-file-size` to `16MiB` in this repository's jj config
(`jj config set --repo snapshot.max-new-file-size 16MiB`). Every clone sets
it once; `AGENTS.md` lists the command. Keep files under that size unless a
retention rule requires more, and prefer the existing `.gitignore` rules for
regenerable output.

## Why

jj refuses to snapshot new files over 1 MiB and prints a warning on every
command until the file is ignored or the limit is raised. The 2026-09-17
retained evidence added eight tracked files over that limit (the largest is a
14 MB rehearsal marker) plus a 2.8 MB DanceConvention fixture. Previous
sessions worked around the warning with a one-off `--config` flag, which does
not persist and left later evidence files unsnapshotted. Retained evidence is
immutable by rule (`journal/evidence/README.md`), so shrinking those files is
not an option here.

## Alternatives

- Keep the default and pass `--config` per command: fragile, and silently
  drops new files from the working copy until someone notices the warning.
- Ignore or compress the large files: conflicts with the retention rule for
  evidence bundles and fixtures. Compression stays open for future bundles.
- A tracked jj config file: jj does not read tracked config for security
  reasons, so the setting must be applied per clone.

## Consequences

`jj status` no longer warns, and new evidence up to 16 MiB is tracked without
a flag. Accidental large files under 16 MiB are no longer caught by jj; review
`jj status` before committing. Repository size grows with retained evidence.

## Links

- [Working in this repo](../../AGENTS.md)
- [Evidence retention rules](../evidence/README.md)
