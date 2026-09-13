# WP16 source preparation before H16 publication

No assembly, Nix build, state mutation, input acceptance, source request,
activation or publication was executed by this preparation.

There is no contract requiring H16 publication before preparing or building the
future WP16 source. design/implementation-plan-v2.md lines64–68 make V5 depend
on V2/V3/V4, which already published; lines85–87 explicitly allow V5 alongside
V6. G2/G3 at lines100–101 guard actual phase-two watches, not source packaging.
research/wp16-deployment-preparation.md requires the actual H16 release and a
new verified schema14 backup before assembling an execution gate. That remains
unchanged. Earlier coordinator sequencing did not add a human approval gate.

Prepared files:

- /tmp/wp16-assembly-plan.json: exact current reviewed overlay hashes, base
  identity, removed-file set (empty), resources and validation references.
- /tmp/prepare_wp16_assembly.py: default read-only inventory verification;
  --assemble is an explicit later coordinator action. It does not run Nix.
- /tmp/wp16-base-inventory.json: read-only extraction of the existing base's
  file inventory, retained only as preparation evidence.

Base: /nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source, h16-source.json SHA256
3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92.
The actual base receipt moves unchanged to provenance/h16-before-wp16.json in
the new bundle. Existing provenance remains retained.

The plan overlays all clean current src and tests files, current copies of
already-retained research Python helpers, and the exact WP16/fixture helper and
resource additions. There are 539 overlay files, 18,856,082 bytes. The expected
source has 652 inventoried files plus wp16-source.json itself. This includes
18 runtime paths changed/added against093, listed exactly in the JSON plan.
Configuration, overrides, Nix/package dependencies and every existing migration
must remain byte-equal to093; migration0015 is the only new migration. No source
configuration enabling event_sites is added. All Python/test caches are excluded.

Validation authority is the actual 1396-test worktree receipt
research/verification/h16-changelog-worktree-validation-20260913.json, SHA256
223c0cfc2690f2aebad3e262a67bc3bd0f06860a59b8044651f38fa5913b732f.
The script verifies every runtime hash against it. The earlier1388-test executor
receipt is retained as historical evidence, not relabeled. No test result for
the not-yet-assembled frozen bundle is asserted.

The helper closure explicitly adds research/accept_wp16.py and keeps accept_h11;
adds original research/fixture_exception.py and fixture_transport.py plus the
proposal, runner notes and exact machine manifest; includes existing tests and
source fixture bytes. Seven exact extra retained resources close the tests and
fixture-manifest validation:

- 2026-09-11/cdx_dcn_eventpage_2013_2018_first50.json
- 2026-09-11/cdx_steprightsolutions_html_all.json
- 2026-09-11/wayback_id_steprt_events_index.hdr
- 2026-09-11/wayback_id_steprt_round_507.hdr
- 2026-09-11/wayback_id_steprt_round_508.hdr
- 2026-09-12/event-list/asp/r00_web_archive_org.json
- 2026-09-12/event-list/asp3/r05_web_archive_org.json

All seven live under research/verification in the source. No unverified actual
SRS/DCN body is invented. The reviewed /tmp H13 fixture adapter stays a separately
hashed operational helper; its imported original fixture modules can now bind
to this source without test-only supplementation.

After review, root can copy the script/plan into operations or VM /tmp and first
run default verification with their actual hashes. Explicit assembly would be:

    PYTHON /OPS/prepare_wp16_assembly.py --plan /OPS/wp16-assembly-plan.json --plan-sha256 EXACT_PLAN_HASH --assemble /var/tmp/swingset-wp16-release-source

The script requires a new dedicated path and writes an exhaustive
wp16-source.json with format wp16-reviewed-source-v1, schema15,
acquisition_enabled=false and repairs_activated=false. It explicitly records
frozen_validation_pending=true and deployment_executed=false. Receipt creation
is not evidence that later gates passed.

Run the frozen full suite in a disposable writable mirror without adding any
source files, then record that exact receipt. A Nix build may run independently:

    nix build path:/var/tmp/swingset-wp16-release-source#nixosConfigurations.orb.config.system.build.toplevel --no-link

Building does not switch the system or invoke the pipeline. Source freshness
checks must reject any changed worktree file before copying; if central format
or another reviewed edit changes an overlay hash, regenerate and review the plan.
Keep testing and Nix writable caches outside the frozen source, and do not write
post-test results back into its already hashed inventory.

Actual deployment/migration waits for the verified H16 closure publication,
latest private schema14 checkpoint, copied accept_wp16 driver hash and all real
operational gate receipts. No owner year acceptance, new-source fixture approval,
source-kind enforcement, or acquisition permission follows from this preparation.
