# D-0183: Agents also decide deployments and publication

Recorded: 2026-09-21  
Decided by: owner, 2026-09-21; source: "make the Step Right and the DCN PDF parser from main deployment decision on your own; to re-iterate, until further notice, the owner is not needed for decision making (D-0172)"  
Topic: Documentation  
Supersedes: the clause of [D-0172](0172-delegate-all-project-decisions-to-agents-until-revoked.md) that kept deployment and publication outside delegated authority  
Superseded by: —

## Decision

Until the owner revokes it, agents decide and carry out deployments and
publications as well, under the production tier in `AGENTS.md`: a preflight
receipt and a post-check receipt for a switch, a checkpoint before a schema
migration, and the full suite before a change that touches migrations. The
first such decision is deploying `main` as candidate 010 after the parse
backlog drains, which migrates the database from schema 29 to 32.

## Why

The owner was asked whether to deploy `main` with its migrations and answered
that the owner is not needed for that class of decision. The measured cost
of the migration, 92 seconds plus a 34-second reclaim on a copy, and the
existing checkpoint and preflight procedure make it a routine operation.

## Links

- [D-0172](0172-delegate-all-project-decisions-to-agents-until-revoked.md)
- [AGENTS.md](../../AGENTS.md), how much checking a change gets
