# D-0280: Allow only the resolver's bind capability

Recorded: 2026-11-05  
Decided by: agent, 2026-11-05; source: stage 2 implementation under D-0172  
Topic: Server network policy  
Supersedes: —  
Superseded by: —

## Decision

The pinned CoreDNS service drops all capabilities, then adds only
`NET_BIND_SERVICE`. It keeps its nonroot user, read-only filesystem,
`no-new-privileges`, and default AppArmor and seccomp profiles. Application
workers keep every capability dropped.

## Why

The official image's executable carries an effective file capability for
`NET_BIND_SERVICE` alone. With that capability excluded from the bounding set,
the real server refused execution before reading DNS configuration. Changing
the listening port would not remove the executable's file capability.

## Alternatives and consequences

A custom image could strip the file capability, but would add another image
build and provenance chain. Retaining the one bind capability lets us use the
pinned official image. It permits binding low ports only within the resolver's
private network namespace.

## Links

[Server setup and file-capability receipt](../evidence/runtime/postgres-stage2-2026-11-05/network-setup-first.json).
