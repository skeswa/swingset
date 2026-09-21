# Recover published identities and fill event metadata

This release work targets dataset `138352ef5e9ec25e12b60dc6acb85db808c5e77f`.
The [2026-09-21 investigation](../../journal/investigations/2026/published-field-gaps-2026-09-21.md)
and its receipt establish the baseline. Investigation is complete; repair
and publication remain open.

1. **Recover identity output first.** The [diagnosis](../../journal/investigations/2026/published-field-gaps-2026-09-21.md#trigger-the-history-snapshot-advanced-before-linking-caught-up)
   reproduced the incompatible-generation failure. Keep ordinary releases
   from replacing populated output while replacement links are pending;
   preserve urgent suppression and revocation handling. Test a populated
   release followed by a new history generation and incomplete linking.
   Resolve how previously supported IDs can return under the baseline-only
   accuracy policy; its null-baseline rejection is now reproduced. Record
   any policy change separately. Restore supported coverage for the 435
   withheld old events through accepted source evidence or valid retained
   output; keep the intentional Monterey alias consolidation. Compare
   matched IDs, candidate evidence, points, and result rows against both
   `138352e` and `0923f27`, explaining remaining losses. Successful build
   validation alone does not establish improved data quality.
2. **Preserve available locations.** Carry supported historical location
   evidence into events, parse two-part locations correctly, and handle
   country normalization without guessing. Start with the 913 measured
   name/date matches; report how many yield supported improvements and why
   others remain null. Test conflicting sources and preservation of existing
   metadata.
3. **Improve date coverage without inventing precision.** Review the 254
   month-only events by source and reconciliation state. Upgrade dates only
   from supported listings; report remaining month-only counts separately
   from parser or mapping failures.
4. **Make heat support explicit.** Correct the generated card's explanation
   of the empty table. M5 heat acquisition and any decision to remove the
   reserved table are separate work; an empty schema is not completed coverage.

Done means a newer published dataset with supported identity/points recovery
and measured metadata improvements, with unexplained losses resolved. Follow
the repository's independent review and receipt requirements when publishing.
