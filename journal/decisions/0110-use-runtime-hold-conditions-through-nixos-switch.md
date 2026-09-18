# D-0110: Use runtime hold conditions through the NixOS switch

Recorded: 2026-09-17  
Decided by: agent  
Topic: Held production deployment  
Supersedes: —  
Superseded by: —

## Decision

Keep the operator hold and add runtime `ConditionPathExists` drop-ins to all six
ordinary services and timers through the candidate-006 NixOS switch. Retain the
requested runtime masks too, but verify inactivity directly because the existing
NixOS links take unit-file precedence over those masks.

## Why

`systemctl mask --runtime` created `/run` masks, while systemd continued loading
the `/etc/systemd/system` NixOS links. The hold conditions applied to the loaded
units and timers and kept all six inactive during activation. Direct post-switch
checks confirmed the hold and inactive states before migration.

## Links

- [Earlier mask proposal](0109-mask-ordinary-units-through-the-schema29-switch.md)
- [Production outcome](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md#candidate-006-production-outcome)
