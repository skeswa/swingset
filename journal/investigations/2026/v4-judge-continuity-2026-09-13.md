# Keeping named judges when their person ID is unknown (V4)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Reviewer: Codex coordinator, 2026-09-13 UTC. Source review and independent
projection comparison: history inventory agent. No source requests ran.

Projector18 incorrectly omitted eleven named WDR judge records when their
contests became unsupported. All eleven still had retained source bindings.
The production replay was stopped before linking, building or publishing.
Its completed source admissions and pending derivations remain durable; V3
is still the acknowledged public baseline.

Projector19 collects named judge headers independently of scoring support.
It emits no results for unsupported contests and invents no anonymous panel
members. Every judge ID remains null unless the separate identity policy
permits a supported link. A missing WSDC number is not a missing judge.

The independent comparison projected all 178 retained result events. It
retained all 4,919 V3 judge records without changing their names, including
the eleven WDR records. It recovered twelve additional EEPro names previously
omitted with numeric contests. All twelve are named, nonanonymous and have
null WSDC IDs. Their 104 source bindings were checked directly against raw
HTML header cells. Counts and full-record hashes for all seven other
canonical record types are identical to projector18.

The full suite passed 599 tests; Ruff and mypy passed. An additional focused
anonymous-header control passes beside the six complete retained WDR
fixtures and supported ordinal/numeric controls. The reviewed projector
module SHA-256 is
`f65e87516828d9caad581e7dbd80550344cd7b570bfd6ceb4fbff87ffdaab0c3`.
It matches source `/nix/store/lz99diyqwf6f95i8ggvx3ji0lvrjpfxd-source`.

The coordinator approves this pinned projector19 correction for production
replay under the existing reviewed admission policies. The final candidate
still requires independent integrity, identity, history and admission checks
before publication. The earlier 67 unsupported contests and 683 invalid
ordinal final marks remain withheld; no scoring guard is relaxed.

The [machine receipt](../../evidence/releases/v4/v4-judge-continuity-20260913.json) retains
the original failure, complete removed/restored/added record lists, raw-header
proof and full non-judge comparison hashes.
