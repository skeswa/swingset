# D-0273: Capture build time and run as rendering inputs

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to implement stage 1  
Topic: Build identity and recovery  
Supersedes: —  
Superseded by: —

## Decision

Before rendering a partitioned or whole release, capture the actual cutoff and
run id in the immutable build
recipe context and candidate policy. A retry with the same complete build
context, recipe and dependencies uses those captured values for coverage and changelog
rendering, including the public local-page verification time. Changed inputs
capture a fresh cutoff and run. Actual retry attempts
still keep their own run and generation completion receipts. Health and its
daily trigger always read the actual attempt clock before this choice.
Closure and correction builds keep their existing context rules.

Candidate policy also preserves these inputs when files became durable before
generation completion. A legacy unpublished candidate without them gets a fresh
render; recovery never guesses its original clock. Published baselines can still
be reused after their ordinary byte checks.

## Why

The empty-restore test reproduced every domain file for an unpublished candidate,
but the retry clock changed coverage and changelog bytes under the same build
trigger. Time and run are real rendering inputs. Capturing them before the first
attempt makes a rebuild reproducible without changing public-health cadence,
rounding time, or rewriting historical provenance.

## Links

[Stage 1 recovery checks](../../docs/plans/postgres-migration/6-stage-1.md#gate-1).
