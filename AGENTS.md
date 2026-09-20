# Working in this repo

## The goal

Swingset exists to publish a better dataset every week. Work is either
release work, which names the dataset commit it will improve and how, or
maintenance, which gets one session a week. Done means a published dataset
commit newer than the current baseline that is strictly better; see
`docs/plans/milestones.md`. Side quests go to the maintenance queue in
`docs/plans/README.md`, not into the current session.

Nothing sits "deployed under hold" for more than a day. A hold names its
owner, its reason and a review date (`docs/guides/operation.md`, holds), and
a hold older than 48 hours is the first line of `docs/status.md`.

## One session at a time

Run `mise run session-start -- --label <you>` before touching the checkout
and `mise run session-end` when you stop. It refuses while another label
holds a lock younger than six hours. Parallel subagents inside one session
are fine when each owns distinct files. Do not write transfer prompts for a
successor: a new session starts from this file and `docs/status.md`, so keep
both current instead.

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
  sentences. Routine implementation choices are explained here, not in the
  decision log. End with the attribution trailers the session asks for.
- The remote is `origin` (`git@github.com:skeswa/swingset.git`); the
  default branch is `main`, and `trunk()` resolves to `main@origin`.

## Repository layout and documentation

- `docs/README.md` is the reader's entry point. `docs/overview.md` and
  `docs/how-it-works/` explain the project from the top down.
- `docs/guides/` holds task instructions. `docs/reference/README.md` lists
  the owner of each exact rule. `docs/plans/` holds active implementation plans
  and the maintenance queue.
- `docs/status.md` is a dashboard of at most 60 lines: published, running,
  held, next release and what blocks it, known limits. Each line links to one
  receipt. History goes to the journal, never to this page. The candidate
  history table keeps the implemented, tested, deployed and published
  columns; do not repeat that distinction in prose.
- `journal/` holds investigations, dated outcomes, and decisions.
  `journal/tools/` holds maintained research tools with a README entry.
  `journal/evidence/` holds compact receipts and the captured inputs they
  need. Never edit retained evidence by hand; see `journal/evidence/README.md`.
- `src/swingset/` holds the pipeline; `tests/` holds offline tests.
- `nix/` holds the service module and VM configuration. Start operating work
  at `docs/guides/operation.md` and `docs/status.md`.
- Update affected docs in the same change, without waiting to be asked. Fix
  stale links and claims you encounter. Write tersely at a high-school
  reading level; see `docs/writing.md`.

## Decisions

- Record a decision when a future reader could want to reverse it: the data
  model, an identity rule, a retention limit, a source or host policy,
  anything that touches production or a third party, or a reversal of an
  earlier record. Routine implementation choices go in the commit message
  (D-0175, replacing the "every decision" rule of D-0006 and D-0169).
- A record fits on one screen: decision, why, what it replaces, one link.
  Add alternatives and consequences only when a reasonable engineer would
  have chosen differently. Use `journal/decisions/TEMPLATE.md` and update
  the index in the same change.
- A choice about plan direction is a plan edit in `docs/plans/`, not a record.
- Decide yourself under D-0172; the record's existence is its acceptance.
  Research and conclusions go in `journal/investigations/`.

## Evidence

- One production operation, one compact JSON receipt under
  `journal/evidence/`: commands, hashes, exit code, counts, timestamp.
  Logs, packets, frozen helper scripts, comparison reports and rehearsal
  output stay in scratch and are not retained. A rehearsal on a copy proves
  the change to the person running it; the production receipt is the evidence.
- A change may add at most 200 KiB under `journal/evidence/`, and no file may
  exceed 1 MiB. `mise run evidence-size` enforces both; run it before a
  commit that touches evidence.
- A script that runs once lives in scratch. A script that runs twice becomes
  a `swingset` subcommand with a test.

## How much checking a change gets

| Change                                                 | Before it lands                                                                   |
| ------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Offline code, tests, docs                              | Core tests pass and `mise run fmt` ran                                            |
| A production switch or migration                       | One preflight receipt and one post-check receipt                                  |
| Anything that deletes data, publishes, or spends money | One independent check by a second agent with a written verdict, plus the receipts |

No change gets more than that. One reviewer per change; reviewers are not
reviewed. Optimization work of any kind starts only from a measurement
receipt and a trigger written in its plan. For state size the trigger is a
live database over 20 GB or a worker disk under 100 GB free.

## Tests

Run the core suite, `nix develop -c uv run pytest -q`, and the test files you
changed while iterating. Run `--full-suite` only as a final or important check:
before a commit or handoff, or when a change touches crash recovery,
migrations, backup and restore, publication, or anything else the extended
suite covers (see `docs/guides/testing.md`). A full run takes about ten
minutes and writes about 15 GiB of temp; a core run takes about three minutes
and must stay under that. Put a new test in the existing file for its area;
start a new file only for a new module. The dev shell pins `TMPDIR=/tmp` so
pytest prunes to its last three runs; never point `TMPDIR` at a directory
nothing prunes. On the worker, `/tmp` is a small tmpfs: use `--basetemp`
under `/var/tmp` for a full run there.

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
