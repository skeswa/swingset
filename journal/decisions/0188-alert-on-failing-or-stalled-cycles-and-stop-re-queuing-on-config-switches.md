# D-0188: Alert on failing or stalled cycles, and stop re-queuing work on config-only switches

Recorded: 2026-09-21  
Decided by: owner, 2026-09-21; source: "shall we institute a timeout to prevent this from happening in the future? 9 hours of inactivity is a lot to tolerate"  
Topic: Operations  
Supersedes: —  
Superseded by: —

## Decision

Three guards, one per layer:

- The service module gains a `swingset-watchdog` unit that runs every 30
  minutes and immediately after a cycle fails. It reads the retained run
  records and alerts when three consecutive cycles failed or when no cycle
  has finished for `watchdogQuietSeconds` (two hours) while unheld with the
  timer active. An alert is appended to `ALERTS.log` in the state
  directory, logged at error level, and piped to `alertCommand` when set.
- `AGENTS.md` requires an unattended agent watch to fire on failure
  signals and on 60 minutes without progress, and to report within two
  hours.
- The repository identity ignores the `uncommitted:<store name>` marker a
  path-flake deployment exports and uses the digest of the Python sources
  instead, so a config-only or nix-only switch no longer re-queues every
  parse and projection unit.

## Why

On 2026-09-21 the drain finished at 10:59 UTC and the next seventeen cycles
failed in the build for four hours before anyone looked. The agent's watch
was waiting for queue counts to reach zero; the failures left the counts
unchanged, so it never fired, and nothing on the worker escalates a run of
failures. Separately, three of the day's switches changed no parser byte and
each re-queued 31,821 parse units because the fingerprint carried the store
path.

## Links

- [Operation guide](../../docs/guides/operation.md)
- [Candidate 011 receipt](../evidence/runtime/candidate-011-2026-09-21/receipt.json)
