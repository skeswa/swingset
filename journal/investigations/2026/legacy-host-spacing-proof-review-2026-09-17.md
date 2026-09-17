# Legacy host spacing proof review, 2026-09-17

None of the six non-Archive paid hosts has enough evidence in the reviewed export
to establish its last request's original effective spacing. Keep those hosts
unknown and blocked. This is a read-only evidence finding, not a recommendation
to probe the hosts or change baseline authority. The separate exact Archive
fixture proof remains eligible for its existing guarded coordinator procedure.

## Evidence reviewed

The coordinator's [read-only export](../../evidence/runtime/legacy-host-proof-review-2026-09-17/read-only-export.json)
was observed at `2026-09-17T16:43:37.725840+00:00` on schema 14. Its SHA-256 is
`e80cc543060f245c97735ad4b0293a0b15ef717a8d32885953dae0315c468d9d`.
It contains host metadata, the latest paid day, up to three retained request
admissions, and the latest snapshot/run selected for each host.

All six unique exported robots bodies were independently hashed against their
content-addressed filenames. The retained bodies yield no `swingset` crawl delay
under the reviewed robots interpretation: HTTP 404/403 bodies add no delay;
the HTTP 200 bodies have no matching delay. The ten- and thirty-second rules in
the WSDC body apply to other named agents. This does not prove which body or
policy governed a missing historical request, and the exported caches have
expired by the observation date.

The original H16 `hosts.toml` was read from the independently verified local
mirror `/Users/skeswa/.cache/swingset-h16-proof-tests-20260916`. Its SHA-256 is
`6c0b8d7b34e1c4a068374c59d0f41d52459f0113883b9868a6750c2753123122`.
The current file matches frozen runtime 003's inventory pin:
`a32134c7dd6891b8a2f7856cf6851651d5617af937b8c45cb63b9541e8be74d1`.
Their configured host gaps agree: five seconds for EEPro, scoring.dance, WDR and
worldsdc.com; two seconds for the registry configuration, with the existing
five-second ordinary floor and two-second sweep exception; ten for Archive.
`www.worldsdc.com` uses the default five-second policy. These two known policy
files do not themselves bind the requests made before the H16 freeze.

## Host findings

| Host                            | Latest paid day/count | Latest exported snapshot              | Missing proof                                                                                                                                                            |
| ------------------------------- | --------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `eepro.com`                     | September 13 / 2      | Origin, September 12 at 16:32:51 UTC  | Later paid requests have no exported request admissions or matching last-request receipt.                                                                                |
| `points.worldsdc.com`           | September 13 / 6      | Origin, September 12 at 13:09:40 UTC  | Later request identity and ordinary-versus-sweep mode are unknown; robots were refreshed later than that snapshot.                                                       |
| `scores.worlddanceregistry.com` | September 12 / 3      | Origin, September 9 at 15:07:24 UTC   | The snapshot predates the paid ledger and September 12 robots refresh.                                                                                                   |
| `scoring.dance`                 | September 13 / 725    | Origin, September 13 at 04:15:52 UTC  | This is the closest candidate, but no admission identifies the final paid request or binds it to its effective config/robots policy.                                     |
| `worldsdc.com`                  | September 13 / 3      | Origin, September 9 at 04:00:29 UTC   | Later paid requests are unaccounted for by the selected snapshot/run.                                                                                                    |
| `www.worldsdc.com`              | September 13 / 57     | Wayback, September 13 at 05:54:42 UTC | The original URL host is not the request host for a Wayback exchange. This snapshot cannot establish the final origin request; its run is also unfinished in the export. |

All six have an empty `last_requests` list. Empty lists establish absence in this
export, not absence of paid requests. The Archive host instead has settled
fixture request admissions and separately retained exact execution receipts;
this review does not expand or duplicate that proof.

## Why the deadline is insufficient

The older host gate stored `next_allowed_at = grant_time + effective_gap`.
That row stores neither the grant timestamp nor its selected gap. Subtracting a
snapshot's later `fetched_at` does not recover the gap: reservation, HTTP and
response processing take time, and robots, redirects or failed requests may not
produce the selected snapshot. A finished nearby run and several quiet calendar
days do not supply the missing original-policy binding. No inferred five-second
baseline is authorized by this review.

## Exact next evidence needed

For any non-Archive candidate, retain a request-level ledger or equivalent
source-bound execution receipt identifying the last actual paid request,
its effective host policy and request mode, and the robots response/policy used.
Account for later paid requests, redirects, robots fetches and failures, and bind
the ledger to the retained daily usage without refunds. Then the coordinator
can review the concrete original-gap proof, stopped-worker observation, existing
deadline and conservative new wait using the established baseline procedure.

For scoring.dance, locating a retained source/config binding and complete
request accounting for `run_20260913T041530Z` is the narrowest next investigation.
For the other hosts, first identify the later paid exchanges rather than relying
on the older selected snapshot. This may require additional read-only retained
state or journal evidence. No network request, production mutation, baseline
application, test-suite acceptance or service activation was performed here.

See [the baseline helper's separate scope](legacy-spacing-baseline-preparation-2026-09-17.md)
and [D-0056](../../decisions/0056-anchor-host-spacing-to-request-completion.md).
