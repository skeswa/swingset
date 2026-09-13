# G1 registry probe rehearsal

The live deployment check passed on 2026-09-13 UTC: cursor 29029 advanced to
29030 on one identical not-found lookup. See the
[operating receipt](v2-progress.md#g1-operating-receipt). The G1 source pause
is cleared; the persistent service hold still prevents scheduled publication.

This is an offline deployment prerequisite, not evidence that the writer has
been upgraded or its source pause can be cleared. The fixture uses a disposable
v1 database and the archived exact not-found response. Its cursor 29029 mirrors
the observed checkpoint shape; it is not a newly captured lookup for that ID.

Run from the checkout:

```sh
uv run pytest tests/test_registry_g1.py tests/test_registry_verification.py -q
```

The bounded G1 test performs one mocked registry lookup, plus a mocked robots
check. It proves:

- Schema migration preserves the source pause and the original observation.
- A v1 snapshot restores only its original successful fetch time. A newer
  `last_checked_at` does not satisfy the probe.
- Missing or corrupt artifacts and obsolete interpretation versions remain
  unusable with their original required digest and a stable reason.
- The source pause prevents all requests until removed in the disposable test.
- One identical successful miss creates usable verification after probe start,
  advances cursor 29029 to 29030 and miss count 0 to 1, and creates no snapshot
  or parse work. No dataset is built or published by that bounded test.
- Reopening migrated state does not duplicate historical verification.

After deployment, keep the source paused until the installed runtime and a
restorable checkpoint are confirmed. The live G1 acceptance still needs one
controlled probe attempt against the actual current cursor, with concurrent
registry collection prevented. Record the before and after doctor output,
check timestamp, unchanged body digest, interpretation snapshot, cursor and
miss count. The check must be newer than `registry_probe_started_at`. An error,
an invalid miss envelope, or an absent usable check fails this gate and keeps
the pause in place. If the lookup returns a newly issued dancer instead of a
miss, retain that evidence and test the next unresolved cursor; do not relabel
a found response as a miss.

Clearing the pause and making that live request require the deployment and
operational authorization for that step. This document and its tests execute
neither operation.

H3 remains a separate gate: the retained audit fixtures cover paired names and
unrestricted divisions. Reviewed false and true judge counterexamples have
not been supplied, so judge accuracy acceptance remains open.
