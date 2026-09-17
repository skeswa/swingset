# Event-extension operating handoff, 2026-09-17

The reviewed event-completion runtime is deployed under hold. Its live migration
passed at 16:59:15 UTC. Ordinary input acceptance, service activation, measured
operating acceptance and a subsequent publication remain unfinished. This
handoff supersedes the older H16 runtime pins for current operation; it does not
replace the retained H16 release receipts or public baseline.

## Exact current pins

- Worker: OrbStack machine `swingset`; service account `swingset`.
- State: `/var/lib/swingset`; database schema 28.
- Active and persistent system: `/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
- Source: `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`.
- Source receipt: `60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
- Package: `/nix/store/dx0f5m41y5x05sp6vq116vxb8yfr5cxh-swingset`.
- Runtime Python: `/var/lib/swingset/venv/bin/python`. The editable installation
  was rebound offline to source 003 at 17:31 UTC. Import checks without
  `PYTHONPATH` passed with the service library path; see the
  [binding receipt](../../evidence/runtime/event-extension-2026-09-17/venv-binding-001/receipt.json).
  Do not mutate the shared environment while helpers run.
- Config: the source's `config` directory. External overrides:
  `/Users/skeswa/repos/skeswa/swingset/overrides`. Exact files match the freeze;
  production input acceptance is still pending.
- Hold: `/var/lib/swingset/operator-hold`, SHA-256
  `965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638`.
  All six ordinary cycle, backup and summary services/timers remain inactive.
- Public candidate: `cand_8f31cad7226643ae`; acknowledged public commit:
  `2a6c7dc744fb36eabb5163c0a527d787d3721f4f`. No new publication.

The [frozen validation](../../evidence/runtime/event-extension-2026-09-17/validation-003/)
passed 2,239 tests, Ruff and mypy over 217 source files. Later DCN parser and
operating-helper edits have separate checks and are outside that source.
The [live migration](../../evidence/runtime/event-extension-2026-09-17/live-migration-001/)
preserved all 71 predecessor tables and added 45 tables. Its exact executed
helper is retained beside its gate and receipt. Do not substitute newly formatted
helper bytes under the old hash.

## Checkpoint and recovery

Latest verified current-schema checkpoint:
`/var/lib/swingset/checkpoints/extension28-held-20260917-002`.
Manifest SHA-256:
`4929859092cbd3e15122f3598b5da477a65498976cc744c52cf2b142d1cebea3`.
Private archive acknowledgment: `d71060d4f77b6073f797bd7232bf2aa3694ba685`.
It contains 68,812 files and 8,767,609,759 bytes, including the 15 paid Archive
requests recorded today. The schema-28 backup passed at 17:56:52 UTC with zero
live database changes and the old accepted input bundle preserved. Its
[receipt](../../evidence/runtime/schema28-checkpoint-2026-09-17/production-002/receipt.json)
is separate from restore validation of this checkpoint, which remains pending.
The first attempt and its exact two-file permission repair are retained.

Retained pre-migration rollback checkpoint:
`/var/lib/swingset/checkpoints/h16-before-extension-20260917-003`.
Manifest SHA-256:
`22102b3cc07b07b49800c702396a748bbbe733e148bbca9b426bff370792223d`.
Private archive acknowledgment: `da38556935ee18ee26a93e121ef461514ac2f423`.
It contains 68,688 files and includes all ten paid fixture requests before rollout.
The checkpoint is schema 14. The corresponding actual restore through schema 28
passed; [restore evidence](../../evidence/runtime/event-extension-2026-09-17/restore-002/)
includes both remote restore checks, final baseline verification and unchanged
predecessor tables. Frozen schema-14 backup and WP16 helpers are not generic
schema-28 operation drivers.

## Active disposable rehearsal

Scratch state: `/var/tmp/swingset-extension-input-20260917-001`.
Receipts: `/var/tmp/swingset-extension-input-receipts-20260917-001`.
Exact staged helper:
`/var/lib/swingset/operations/v2-continuation-20260917/input-rehearsal-001.py`,
SHA-256 `7bc742cdfbdf8e38085d7affb1c82df8cbdca655b637a7764b46f58d98087e1b`.
Marker SHA-256:
`8c9ad75dbfb7b25d3b99e655e17de203af05185299daeaa8448ba8e204aa862e`.

Preparation and scratch input acceptance passed. Accepted bundle:
`27528a47da5f702a4ff2ea68c1bfe2a2ce608989600fd9bc7e6734b4c74a0c95`.
All 4,931 named judges were unchanged. The first bounded drain finished at 17:15:26 UTC with preservation checks
passing. Its 550.008-second selection window completed one parse and three
projections, then stopped at the selection time limit. It left 31,820 parse
hints and two projection hints. This is insufficient operating throughput;
read-only profiles found repeated mapping and dancer-readiness checks. The reviewed fixes selected a parse in 9.09 seconds in the fourth
[comparison](../../evidence/runtime/offline-selector-profile-2026-09-17/comparison-004/report.json).
This read-only instrumented result is not sustained operating throughput;
actual replay under the new runtime remains pending.
The 600-second selection budget is not a hard total wall deadline. No
network, production input acceptance or publication is authorized by that helper.
Read the [closed receipt](../../evidence/runtime/extension-input-rehearsal-2026-09-17/production-copy-001/drain-001.json) before launching another turn. An empty queue cannot
establish complete fleet derivation history.

## Remaining operating gates

The ten paid Archive fixture requests support a reviewed ten-second legacy
spacing baseline. Its prepared packet is at
`/var/lib/swingset/operations/v2-continuation-20260917/legacy-spacing-001`.
The Archive baseline was applied at 17:07:15 UTC without a request, budget
change or hold removal. Its [receipt](../../evidence/runtime/legacy-spacing-baseline-2026-09-17/production-apply-001/application-001.json)
records baseline `baseline_d6003a4b6acc4a5e9f846f164120010e`.
Six other paid hosts remain unknown; their
last-request effective policy is not established by retained evidence. Do not
infer a five-second baseline or clear their gate from elapsed calendar time.

Complete scratch replay and preservation review before live input acceptance.
The installed environment now binds source 003. Recheck effective services, control
state and publication behavior before resuming ordinary work. Existing owner
approval covers deployment and publication after those concrete gates; the
hold remains a runtime interlock, not a new owner decision.

Source-kind activation and each historical year's acceptance retain their
separate gates. D-0087 supplies standing authority for necessary fixture
acquisition; exact scopes and reviewed runners remain required. The exact Riga metadata lookup is
approved in D-0069 and finished at 17:17:13 UTC: three HTTP requests, 369
accounted response body bytes and one exact capture row. Independent capture
audit passed; this metadata run fetched no results body or PDF. The separately
approved D-0073 body capture completed at 17:43 UTC and passed independent
audit: two requests and 16,839 accounted response-body bytes. Shared Archive
usage reached 15 requests and 2,951,909 bytes at that cutoff. The later
two-URL PDF metadata lookup finished at 18:10 UTC using five requests and 156
bytes; both exact lookups returned empty capture rows. Current retained usage
is 20 requests and 2,952,065 bytes. The verified checkpoint above predates
these five debits. No PDF or origin request was made; HTML remains quarantined. No automatic repair
kind has been activated. See [current status](../../../docs/status.md) and the
[continuation record](v2-continuation-2026-09-17.md).
