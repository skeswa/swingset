# Working in this repo

## Version control: use jj, not git

This is a colocated [Jujutsu](https://jj-vcs.github.io/jj/) repo (`.jj/`
and `.git/` side by side). Use `jj` for every version-control action.
Do not run `git add`, `git commit`, `git push`, `git checkout`, `git
stash`, or `git rebase`. Read-only git commands (`git log`, `git diff`,
`git show`) are tolerated when a jj equivalent is awkward, but prefer jj.

Why: jj snapshots the working copy automatically, so there is no staging
area and nothing to forget. Mixing git write commands into a colocated
repo leaves jj and git disagreeing about the working copy.

### Everyday commands

| Task                                                | Command                       |
| --------------------------------------------------- | ----------------------------- |
| See what changed                                    | `jj status` and `jj diff`     |
| See history                                         | `jj log`                      |
| Describe the current change                         | `jj describe -m "<message>"`  |
| Finish the current change and start a new empty one | `jj commit -m "<message>"`    |
| Move `main` to the finished change                  | `jj bookmark set main -r @-`  |
| Push `main`                                         | `jj git push --bookmark main` |
| Fetch                                               | `jj git fetch`                |
| Undo the last jj operation                          | `jj undo`                     |
| Throw away the working-copy change                  | `jj abandon @`                |

Typical flow after editing files:

```
jj status
jj commit -m "<message>"
jj bookmark set main -r @-
jj git push --bookmark main
```

`jj commit` closes the working-copy change with the given message and
opens a fresh empty change on top, so the working copy is always a
separate revision from what was pushed.

### Rules

- Commit or push only when asked.
- Never force-push. `jj git push` refuses non-fast-forward moves of
  `main` by default; leave it that way.
- Never edit `.jj/` or `.git/` by hand.
- Once per clone, run `jj config set --repo snapshot.max-new-file-size 16MiB`.
  Retained evidence and fixtures exceed jj's 1 MiB default, and without this
  setting jj refuses to snapshot them and warns on every command (D-0092).
- Commit messages: short summary line, blank line, body in plain
  sentences. End with the attribution trailers the session asks for.
- The remote is `origin` (`git@github.com:skeswa/swingset.git`); the
  default branch is `main`, and `trunk()` resolves to `main@origin`.

## Repository layout and documentation

- `docs/README.md` is the reader's entry point. `docs/overview.md` and
  `docs/how-it-works/` explain the project from the top down.
- `docs/guides/` holds task instructions. `docs/reference/README.md` lists
  the owner of each exact rule. `docs/plans/` holds active implementation plans.
- `docs/status.md` is the single current status summary. Distinguish implemented,
  tested, deployed, and published work; link to dated evidence.
- `journal/` holds investigations, dated outcomes, and formal project decisions.
- `journal/tools/` holds research tools grouped by purpose. `journal/evidence/`
  holds captured inputs, reports, and frozen scripts grouped by topic and run.
  Do not edit retained evidence or generated CSVs by hand; see `journal/evidence/README.md`.
  Follow its small-evidence rules for new output: reuse existing records, keep
  disposable diagnostics in scratch storage, and compress large text before
  sealing it. Run `mise run evidence-size` when adding an evidence bundle.
  Files over 1 MiB belong in a verified external archive, with a small retained
  receipt; do not add them to version control (D-0106).
- `src/swingset/` holds the pipeline; `tests/` holds offline tests.
- `nix/` holds the service module and VM configuration. Start operating work
  at `docs/guides/operation.md` and read the current status and linked handoff.

## Keep docs and decisions current

- Update affected docs in the same change, without waiting to be asked.
  Cover behavior, commands, architecture, and status; fix stale links and claims
  you encounter. Support status changes with evidence.
- Record every decision and its reason in `journal/decisions/` in the same
  change, including routine implementation choices. Use its `README.md` and
  template; keep small records brief and update the index. Chat, code comments,
  and change descriptions do not replace the log.
- Record explicit acceptance; an agent recommendation stays proposed.
  Preserve accepted reasoning; use a new record for a changed choice and link
  both records. Put research and conclusions in `journal/investigations/`.
- Write tersely at a high-school reading level for readers with minimal context.
  Explain the purpose first, then link to detail. Give each rule one home,
  mark unverified facts, and keep history in the journal. See `docs/writing.md`.

## Formatting

`mise run fmt` is the only formatting entry point: nixfmt (RFC 166) for
`.nix`, taplo for `.toml`, shfmt for `.sh`, `ruff format` for `.py`, oxfmt
for `.json`, `.md`, and `.yml`. It needs only mise, not `nix develop` or
the project venv. Run it before committing hand edits. Formatter versions
are pinned exactly in `mise.toml`; `mise install` fetches them once per
machine. Bump them deliberately and re-run fmt, and keep the ruff pin in
step with the one in `uv.lock`.

Captured and machine-written files are never reformatted: `flake.lock`,
`uv.lock`, every `fixtures/` directory, and retained `journal/evidence/` bundles
are listed in `.prettierignore`, which oxfmt reads. Add to that list rather than hand-formatting around it.

## Scraping etiquette

Any code or research that touches third-party sites must follow
`docs/reference/fetching.md`: one request in flight per host, a floor of five
seconds between requests to the same host, conditional requests where
the server supports them, and the project User-Agent
`swingset/<version> (+https://github.com/skeswa/swingset)`.
