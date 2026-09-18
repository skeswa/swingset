# D-0116: Refresh Archive robots separately

Recorded: 2026-09-17  
Decided by: agent  
Topic: Fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Refresh stale `web.archive.org` robots evidence with a separate sealed
one-request operation before the Step Right body acquisition. Keep the body
runner unable to refresh robots, and require its later gate to bind the newly
retained status, body digest, timestamp and parsed policy.

## Why

The first live packet build stopped because the retained robots record was 4.9
days old. Separating that request preserves exact request accounting and keeps
the body operation limited to its one reviewed event-page request.

This proposal records an implementation choice under D-0087. It does not grant
source admission, historical-year acceptance or publication.

## Links

- [Step Right runner design](../investigations/2026/stepright-body-runner-design-2026-09-17.md)
- [Step Right body runner decision](0108-build-a-separate-step-right-body-runner.md)
