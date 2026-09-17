# D-0088: Scope origin score-PDF controls after empty Archive lookups

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: DCN preliminary and final PDF fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Prepare one separate origin fixture operation for the two exact PDF URLs printed
in the audited Riga Newcomer results HTML. The completed Archive lookups returned
no capture rows for either exact URL. Retain that bounded negative evidence;
do not infer that the PDFs were never archived.

Permit at most four HTTP requests: origin robots, at most one redirect from
`/robots.txt` to the exact same-host HTTPS `/eventdirector/robots.txt`, and one
request for each printed PDF. Permit no PDF redirect, retry, alternate, child,
additional metadata or other origin URL. Keep a 2 MiB response-body bound,
8 MiB aggregate bound and 15-minute window, plus existing stricter host rules.
Standing owner authority is D-0087; this record does not create new authority.

## Why

Real preliminary and final score controls remain necessary for DCN parsing.
The source prints round IDs `3451330` and `3451331`; no ID guessing is needed.
The exact Archive responses supply no usable capture. A distinct origin runner
must honor origin policy, rather than relabeling the Archive transport or its
robots behavior.

## Consequences

Require an independently reviewed source-bound origin runner and fresh gates.
Preserve the operator hold, H13 controls, writer serialization, one request in
flight, 10-second completion-to-dispatch monotonic floor, stricter robots gaps,
shared 200-request/300 MB daily accounting and the one-event/day DCN origin
limit. The packet cannot initialize unknown legacy host-spacing authority.

Resolve the fixture source-policy binding before execution. The maintained
source configuration omits DCN and defaults to disabled. Do not silently treat
that absence as enabled or activate an ordinary page kind. Preserve any explicit
source kill switch. The coordinator must record the concrete reviewed choice
and actual current gate state under D-0087.

Origin requests use the project User-Agent, gzip, conditional validators when
a matching retained representation supports them, and no cookie storage or
sending. Keep bounded decoded-body accounting and conservative incomplete
reservations; transport/decompressor buffering is not measured. Reuse robots
only with verified fresh policy evidence, and never refetch inside its 24-hour
window merely to bypass a refusal.

Retain actual origin observation timestamps. Do not assign the old event date
or archived HTML capture time to a PDF fetched now. All bodies remain in a fresh
single-use quarantine for independent audit and offline interpretation. This
operation creates no watches, observations, accepted year, activated kind or
publication. Missing, blocked or changed bodies remain explicit findings.

## Links

- [Standing authorization](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Exact origin scope and evidence](../investigations/2026/dcn-origin-score-pdf-proposal-2026-09-17.md)
- [Origin gap rules](../../docs/reference/backfill.md#origin-fallback-for-gaps)
- [DCN source contract](../../docs/reference/sources/danceconvention.md)
