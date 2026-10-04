# D-0242: A failed robots read is retried on the host's backoff, and its host leaves the scheduler

Recorded: 2026-10-09  
Decided by: agent, 2026-10-09; source: the robots stall of 2026-10-08 and 2026-10-09  
Topic: Fetch politeness  
Supersedes: —  
Superseded by: —

## Decision

A robots.txt read that failed (status 500 or above, including 599 for no
response) still disallows the whole host. It is read again when the host's
ServerError backoff ends: 15 minutes after the read, doubling with
`hosts.pause_streak` up to a day. Every other stored read (200, 4xx, and any
other status, which still disallows all) is kept a day. There is no allow
after a long outage; the host stays fail-closed.

Until that retry, the fair scheduler leaves the host's watches out of the
candidates, as it does for a paused host. A pass in which it left out a due
watch of that host writes one host guard, `robots unavailable`, expiring an
hour after the retry, instead of one `robots disallow` row per watch. A
blocked host with nothing due writes none, so it cannot silence acquire
`[loop-idle]`.

## Why

One failed read of scoring.dance's robots.txt (599, empty body) was cached
for a day, so every scoring.dance watch was skipped for 24 hours. The
acquisition pass picked those watches first; each skip re-ran the candidate
selection over about 1,443 due watches (0.4 s each), so the 4-minute pass
ended before any registry page. Fetching nearly stopped for two days.
RFC 9309 (2.3.1.4) says to treat a 5xx as complete disallow and to retry.
The gate already pauses the host on that same backoff, so the retry needs
no new column.

## Links

[Robots stall receipt](../evidence/runtime/robots-stall-2026-10-09/receipt.json);
rules in [fetching](../../docs/reference/fetching.md#politeness-rules).
