# Using the Codex CLI for scripted web lookups

Date: 2026-09-22  
Type: Research  
Topic: Tooling for review agents (D-0180)

## Question

Review agents under D-0180 need a few web lookups per finding, and this
session's search budget ran out. Can the installed Codex CLI (0.155.1) do a
lookup non-interactively and return a machine-readable answer?

## Answer

Yes. One run answered the New Year's Swing Fling question with 32 web
searches in 2 minutes 45 seconds and returned JSON that matched the schema
exactly.

```sh
codex exec --ephemeral --skip-git-repo-check -s read-only --json \
  --output-schema schema.json -o answer.json "<question>" < /dev/null
```

- `--ephemeral` keeps no session on disk; `--skip-git-repo-check` lets it
  run outside a checkout; `-s read-only` forbids writes.
- `--output-schema` makes the last message conform to a JSON Schema; `-o`
  writes that message to a file. The schema used: event, held (yes, no,
  unknown), dates, sources (URLs), reasoning.
- `--json` streams events to standard output; counting `"type":"web_search"`
  lines shows how many searches ran.
- `< /dev/null` is required. With standard input piped, `codex exec` waits
  for it ("Reading additional input from stdin...") and never starts; the
  first probe hung for twenty minutes that way.
- It prints an unrelated OAuth error for a Notion MCP server on standard
  error; harmless here, but a wrapper should not treat standard error as a
  failure.

It shares no budget with this session's `WebSearch`. It uses the owner's
Codex login and quota.

## What a maintained tool would look like

A `journal/tools/review/lookup.py` that takes a question and a schema name,
runs the command above with a per-run timeout, validates the JSON, and prints
it. A review agent then calls it once per finding and pastes the answer and
the source URLs into its report. The tool never fetches the project's source
sites directly; it only asks Codex, whose searches read public pages the way
any search engine would. Not built yet: this note records the working
invocation so the next session can decide.
