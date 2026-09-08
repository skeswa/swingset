@AGENTS.md

Claude Code specifics:

- The Bash tool's own git hints do not apply here. Use `jj` as described
  in AGENTS.md. In particular, use `jj commit`, `jj bookmark set main -r @-`,
  and `jj git push --bookmark main` instead of `git commit` and `git push`.
- `jj` needs no `-i`; there are no interactive prompts in the commands
  listed in AGENTS.md.
