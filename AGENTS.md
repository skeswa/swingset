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

| Task | Command |
|---|---|
| See what changed | `jj status` and `jj diff` |
| See history | `jj log` |
| Describe the current change | `jj describe -m "<message>"` |
| Finish the current change and start a new empty one | `jj commit -m "<message>"` |
| Move `main` to the finished change | `jj bookmark set main -r @-` |
| Push `main` | `jj git push --bookmark main` |
| Fetch | `jj git fetch` |
| Undo the last jj operation | `jj undo` |
| Throw away the working-copy change | `jj abandon @` |

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
- Commit messages: short summary line, blank line, body in plain
  sentences. End with the attribution trailers the session asks for.
- The remote is `origin` (`git@github.com:skeswa/swingset.git`); the
  default branch is `main`, and `trunk()` resolves to `main@origin`.

## Repository layout

- `design/` holds the design documents. `design/README.md` is the index.
  Keep them in the style described there: terse, exhaustive, high-school
  readable, with unverified facts marked.
- `research/` holds one-off research artifacts and the scripts that
  rebuild them. Do not edit generated CSVs by hand; see
  `research/README.md`.
- `src/swingset/` holds the pipeline; `tests/` holds offline tests.
- `nix/` holds the service module and VM configuration. `docs/runbook.md`
  covers operation; `docs/implementation-status.md` records acceptance gaps.

## Scraping etiquette

Any code or research that touches third-party sites must follow
`design/fetching.md`: one request in flight per host, a floor of five
seconds between requests to the same host, conditional requests where
the server supports them, and the project User-Agent
`swingset/<version> (+https://github.com/skeswa/swingset)`.
