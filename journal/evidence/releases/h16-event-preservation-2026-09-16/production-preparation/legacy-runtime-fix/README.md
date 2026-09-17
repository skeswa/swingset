# Legacy runtime-recipe preparation fix

Prepared offline on 2026-09-16 after the original production initializer stopped before creating its marker or accepting inputs. The legacy schema-14 database did not have an accepted `pipeline/recipe/runtime` row, and the driver indexed a missing query result.

`initialize-002.py` differs from the untouched `b54c02a1` driver only by reading that row first and assigning `prior_recipe = None` when it is absent. Unknown prior identity therefore differs from the captured runtime recipe. The existing marker records `extract_cache_invalidated = true`; ordinary `inputs.accept()` clears extractor cache labels, enqueues its normal invalidation work, and records the accepted runtime identity. This preparation never runs parsing.

All source, policy, control, protected-state, input-authority and marker checks remain byte-identical. `initializer-002.diff` records the complete change. The new driver SHA-256 is `c3259a023436e2cc3ee56e1ad83f3170b455f42187ff71421eeac25387bf96d7`. Downstream operational gates and reviewed helper pins must bind that new hash before use; this packet does not alter them.

`offline-checks-001.json` records seven passing checks: five new regressions plus the frozen runtime's two input tests. The tests explicitly import the frozen schema-14 host mirror, capture actual input bundles, and run actual acceptance against disposable databases. They cover an absent legacy recipe, an existing matching recipe, marker-preserving rollback/retry after interruption, refusal of policy changes, and the exact two-line driver delta. The external production preflight is mocked; no production state or VM workload was accessed. Pyflakes checks also pass.

Tests pin both the frozen pytest configuration and absolute frozen import paths. Invoking pytest with the current repository's default configuration would load schema 21 and is not evidence for this operational fix. No successful production preparation or acceptance is claimed here.
