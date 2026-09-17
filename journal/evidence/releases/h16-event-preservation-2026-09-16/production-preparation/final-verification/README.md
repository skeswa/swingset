# Independent production initialization verification

Prepared only. Root must review the script and authorize each execution. The
initializer, its source, and all earlier retained evidence remain unchanged.

`verify-initialization.py anchor` runs after actual successful preparation and
before any initializer worker. It binds the exact gate, immutable marker, and
preparation receipt by SHA256. Under the released writer lock it opens normal
read-only SQLite, sets query-only, begins a snapshot, and records exact parse
tokens and seven inherited open runs. It checks source, schema, controls,
accepted input authority, extraction cache, and public baseline against the
prepared marker. The attempt ledger must still match the verified H15
checkpoint, so an anchor captured after initialization has begun is rejected.

`verify-initialization.py verify` additionally requires the anchor, supervisor
preflight, supervisor final, and terminal raw initializer receipt with their
reviewed SHA256 values. It checks every batch's raw receipt, exit and preservation
claims through the exact reviewed supervisor validator. It independently checks
current project/link scopes, settled execution, successful new derivation
attempts, integrity, unchanged parse tokens, and unchanged inherited open runs.
The terminal generation count must match SQLite; new attempts must equal the
supervisor's completed count. No successful count or status is assumed.

Both modes run with a 600-second bound and the exact frozen source/native
environment documented in the parent preparation README. They call no migrating
database opener, input capture/acceptance, recovery, worker, fetch, build, or
publication operation. Only a fresh mode0600 receipt in the existing private
operation directory is written. Main database and WAL size/mtime must stay
unchanged. SQLite's normal read-only WAL snapshot may use shared-memory read
marks; `immutable=1` is used only for the independently hashed H15 checkpoint.

Stage the exact script in the private operation directory and create its output
parent before execution. The same script bytes must perform anchor and final
verification. In the command shapes below, `OPS` is
`/var/lib/swingset/operations/h16-event-preservation-release-20260916`; `SHA` means
an actual reviewed digest, never a placeholder supplied to execution.

```text
python OPS/verify-initialization.py anchor \
  --gate OPS/initialization-gate.json --gate-sha256 SHA \
  --marker OPS/initialization-marker.json --marker-sha256 SHA \
  --prepare OPS/prepare-001.json --prepare-sha256 SHA \
  --output OPS/prepared-verification-anchor.json

python OPS/verify-initialization.py verify \
  --gate OPS/initialization-gate.json --gate-sha256 SHA \
  --marker OPS/initialization-marker.json --marker-sha256 SHA \
  --prepare OPS/prepare-001.json --prepare-sha256 SHA \
  --anchor OPS/prepared-verification-anchor.json --anchor-sha256 SHA \
  --supervision-preflight OPS/supervision-SESSION/preflight.json --supervision-preflight-sha256 SHA \
  --supervision-final OPS/supervision-SESSION/final.json --supervision-final-sha256 SHA \
  --receipt OPS/supervision-SESSION/initializer-NNN.json --receipt-sha256 SHA \
  --output OPS/initialization-final-verification.json
```

Limits: this version supports one successful bounded supervisor session and no
control-continuation marker. A capped session followed by another session needs
a separately reviewed extension binding the full session chain. The verifier
does not establish publication acceptance or replace the independent production
candidate audit. Host guard tests exercise synthetic files/databases only;
they do not constitute production verification.
