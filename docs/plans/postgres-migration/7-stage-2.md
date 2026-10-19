# 7. Stage 2: dry run on the server

Read [6. Stage 1](6-stage-1.md) first; next is [8. Stage 3](8-stage-3.md).

**Goal:** prove the port on full-size data and the server's own CPU,
without touching other sites or the published dataset. Production keeps
running on the Mac.

**The proof is one full re-derivation.** The server imports a production
checkpoint and re-derives every unit with the ported code. With cutoff on,
a re-run that gives the same bytes records an equivalence; anything else
becomes a new generation. So every unit whose output changed because of the
port, the engine or the CPU shows up by itself, through all 809 triggers.
Expected: about 0.02% new generations, each inspected (estimate from the
re-derivation history).

**Before it starts:** gate 1 has passed. Swingset uses
Rhuidean's shared Postgres 17 ([4. Hosting](4-hosting.md#which-postgres)).

## Steps

1. **Provision** with Rhuidean's tooling, in that repo: a Swingset project
   (`mise run app:new`), the `swingset` database in the C locale, its extra
   roles (a change to Rhuidean's onboarding, recorded there), backup coverage (`mise run infra:backups`), the services, secrets
   and alert channels ([4. Hosting](4-hosting.md)). Add Swingset to
   Rhuidean's inventory.
   Record each Dokploy fact on that page's checklist as verified or
   unavailable.
2. **Make fetching and publishing impossible,** not just switched off:
   - the only Hugging Face token writes to a separate rehearsal dataset;
   - the network allows only the bucket, Hugging Face, the alert channel and
     the dead-man's switch, not the sites Swingset collects from;
   - the dry run uses a scratch Postgres 17 container, not the shared
     server, so the re-derivation never touches other apps;
   - the out-of-band hold is set during the import.
3. **Freeze production's code.** From this checkpoint until the switch,
   production takes no deploys, so what the dry run proves is what the
   switch replaces.
4. **Transfer and import** a production checkpoint (39 GB, plus about
   2.5 GB of small files and input bundles) with the stage 1 importer, and
   audit it. Record the transfer, import and audit times; the switch
   repeats them.
5. **Re-derive everything** with fetching and publishing impossible. Count
   new generations against equivalences per kind, and inspect every new
   generation. A transform whose output really changed (for example a
   rounding difference on the new CPU type) gets a version bump in the port
   before the switch. Record the time; it is the re-derivation the switch
   avoids.
6. **Compare releases.** Build one release on the server and one with
   today's code on the worker VM, from the same checkpoint. Every published
   table must be byte-identical, except a named list of columns such as run
   ids. Any other difference blocks the switch unless an independent check
   accepts it.
7. **Rehearse the switch's checks** on this import: a verify run of the
   ported code and a re-key, timed.
8. **Test the cases that matter most:** kill a service mid-tick; drop its
   database connection; a hold and resume with both the normal hold and the
   out-of-band flag; the backup job once, and its check; an alert reaching the owner and the
   dead-man's switch firing. Each must show no lost commit, no repeated
   source request and no hold cleared silently.
9. **Egress check** (a decision record first): the network lets the server
   reach one site, which production pauses; the server sends one
   conditional request for a page production already stored, with the same
   User-Agent.

## Gate 2

- the import's audit passes;
- every new generation from the re-derivation is explained, and each real
  change is bumped;
- the release comparison is clean;
- the verify run and re-key rehearsal pass, with times recorded;
- the tests in step 8 pass, and the backup's size and time are recorded;
- the egress check is clean, or a fix is agreed;
- peak memory per service and for Postgres is recorded, with no
  out-of-memory kill.

| Step                                      | Review                  |
| ----------------------------------------- | ----------------------- |
| step 9, the egress check, contacts a site | a decision record first |
| the rest                                  | tests and receipts      |

**Rollback:** delete the server's services. Production never changed.

Next: [8. Stage 3](8-stage-3.md).
