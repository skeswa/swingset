# Event accounting after checkpoint activation

Five offline cases in [test_event_accounting_restore.py](../../../tests/test_event_accounting_restore.py) exercise actual disposable checkpoint installation and activation at schema 24. They cover state introduced in schemas 21–24; they do not test migration from historical schema 21, 22, or 23 checkpoints.

The fixture combines actual successful fetch/parse/accept operations, a retained unavailable-origin response, and an admitted parent replacement that retires one page. Activation preserves every saved progress operation, accounting and retirement receipt, and sampled observation. Its epoch change makes the saved observations stale. Reporting preserves historical receipts without inventing new transitions.

After fresh observation, the activated copy agrees with an uninterrupted copy. A later successful response replaces the unavailable outcome and produces exactly the real acquisition and interpretation progress. Rechecking the old retirement does not add a duplicate receipt. Checkpoint bytes remain unchanged.

Four negative cases revoke parent support, revoke result support, change the unavailable response classification, or remove its body. Neither branch revives an accounted flag or claims successful progress. For body loss, the uninterrupted branch can retain fresh parent samples while activation has invalidated the restored branch's samples. Both remain unassessed; restoring the body and observing again makes their accounting agree without creating success or retirement receipts.

The focused run passed 5 cases in 1.32 seconds. The related restore, gap, and retirement run passed 80 cases in 6.74 seconds. Exact logs and hashes are retained in the [validation receipt](../../evidence/runtime/event-completion-2026-09-16/schema24-activated-accounting-validation-20260916.json). Its original hash-timing label is corrected by the [timing correction](../../evidence/runtime/event-completion-2026-09-16/schema24-activated-accounting-hash-timing-correction-20260916.json): logs precede formatting, while captured file hashes follow it. The coordinator retains independent validation of the formatted files separately.

This checks checkpoint activation, observer refresh, and mocked local fetch/parse/accept primitives. It does not establish whole-cycle, linking, build, publication, production recovery, remote Hub, or network performance acceptance. Accounting covers the known enumeration; it does not establish permanent absence or whole-event completeness. No runtime changes were needed.
