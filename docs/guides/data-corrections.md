# Reviewing and correcting data

Review uncertain records before changing their identity or event assignment.
The [identity reference](../reference/identity-linking.md) owns the exact rules.
Read [current status](../status.md) before rebuilding or publishing production data.

Identity decisions are append-only. Follow the [decision-journal contract](../reference/recovery/identity.md#the-decision-journal)
when adding a correction; do not erase earlier reviewed decisions.

## Review and removals

Review published `review_queue` rows and add narrowly scoped alias, identity,
source URL, or suppression CSV rows. Use `NONE` for an explicitly unmatched
identity override. Rebuild and inspect the candidate before enabling publication.
Suppression runs at build and leaves raw private evidence intact. Replace an
affected committed parser fixture with another event and update its expected
observations; do not merely hide the fixture from the tests.

A correction re-runs only what reads it
([state: invalidation](../reference/state.md#invalidation)):

- `event_aliases.csv` or `source_urls.csv` queues every source's mapping
  unit; only the resolutions, events, years and links whose output changes
  mint a generation.
- `series_aliases.csv` or `finding_reviews.csv` queues the inventory and
  every year.
- `identity_overrides.csv` re-runs every link.

The next build rebuilds only the partitions whose inputs changed. A
partition that fails a check is withheld with its reason and the rest
publish; `swingset partitions --withheld` lists them
([build: partition artifacts](../reference/build.md#partition-artifacts)).

GC is manual (`swingset gc`). It must retain artifacts referenced by snapshots,
findings, accepted inputs, retained candidates, or checkpoints and remove only
unreferenced files older than one day.

## Approving a decrease

A release that makes a [gate measure](../reference/publishing.md#publication-gate)
go down is refused. The release loop's summary shows `verdict.reasons`, such
as `decrease:confirmed_links 5120->4980`. Find the cause first: a lost link
or event is usually a bug to fix, not a decrease to approve.

If the decrease is right, the owner adds one row per measure to
`overrides/release_decreases.csv` and commits it. Agents never edit this file
in the checkout: production reads `overrides/` from the Mac checkout itself
(`overridesDir` in `nix/hosts/orb.nix`), so a row written there approves a
decrease at the next capture, with no deploy or review.

```csv
measure,baseline_value,candidate_value,reason,approved_by,approved_at
confirmed_links,5120,4980,duplicate dancers merged (issue 812),owner,2026-09-29
```

- `measure` and `baseline_value` must match the reason exactly.
  `candidate_value` is a floor: the row approves any candidate value from it
  up to the baseline, so the next build, a day later with a few more rows,
  still passes. A larger decrease (below the floor) is refused again, and so
  is any decrease from another baseline.
- `reason` says why the loss is correct. `approved_by` names who approved
  it. `approved_at` is an ISO date or timestamp.
- A row with an unknown measure, an empty field, a value that is not a
  decrease, or a duplicate makes the file invalid. Loops still capture it;
  the next build is refused with `overrides_invalid:release_decreases.csv:<line>:
<error>`, which `[release-refused]` names. Fix the line.

The new file changes the input bundle, so the next build writes a new
candidate, and its verdict lists the rows it used. Old rows may stay; they
match only their own baseline value.

## Rolling back a publication

Undo a bad release by publishing an earlier one again, as a new commit on
top of the hub head ([D-0250](../../journal/decisions/0250-correct-only-current-baselines-and-revert-as-a-correction.md)).
Never revert or reset the hub by hand: the local baseline would no longer
match the hub head, and the next publication fails with `public head changed`.

1. Stop publication first, or the next build publishes the bad content
   again: set `services.swingset.dryRun = true;` and rebuild the worker. Wait
   until `systemctl list-units 'swingset-release*'` shows no active tick.
2. Pick the target, a former baseline that still has its candidate
   directory. Each `candidates/<id>/PUBLISHED` names its `commit` and
   `verified_at`; `readlink /var/lib/swingset/baseline` is the bad one.
   `gc --apply` can remove old candidates, so roll back before running it.
3. Build it as a dry run:
   `sudo -u swingset swingset build --revert-to <id> --lock-timeout 3600`.
   It prints the new candidate. Its `_meta/manifest.json` shows
   `release_policy.mode` `correction_only` and `reverted_from`; its changelog
   lists what the revert undoes. It runs today's identity policy and
   suppressions, so it never restores a join or a row they withdraw.
4. Publish it with the token, as a transient unit:
   `sudo systemd-run --wait --pipe -p User=swingset -p EnvironmentFile=/etc/swingset.env swingset publish --revert-to <id> --lock-timeout 3600`.
   It reconciles, builds again and commits on top of the current head. A
   revert is a correction, so the gate does not refuse it. The output's
   `commit` is the new hub head, and `baseline/PUBLISHED` names it too.
5. Fix the cause, then set `dryRun = false` again. The next normal build is
   gated against the reverted release.
