# Historical SQLite upgrade assertions

The two `source-*.zip` parts contain byte-for-byte source captured from revision `461906f9`.
`manifest.json` pins both parts and every member with SHA-256. The capture
follows selected tests' static imports and named dynamic-module imports. It
contains 277 runtime modules, 33 test/helper modules, 37 SQL migrations, and
only the original configuration, overrides and captured pages those tests need.
The parts total 1,436,072 bytes; each is below 1 MiB.

Obsolete schema upgrades, old restore activation, interning and backfill
assertions run here unchanged. The exact H16 SQLite scratch-replay research
contract is archived here too; current replay uses `mise dev-replay` and
its native Postgres tests. Current partition, payload, storage, changelog,
retention, doctor, backup and restore behavior runs against Postgres. SQLite 37
import tests check the boundary between the archived format and Postgres.

`tests/legacy_sqlite.py` verifies every captured byte, extracts beneath pytest's
temporary directory, and runs one isolated subprocess per historical file per
session. Each public case checks its archived setup, assertions and teardown.
The subprocess gets no ambient credentials, refuses network connections, and
uses its own configuration and empty Hugging Face cache. The runtime image
contains no test fixtures.

## SQL migrations

The original files keep their names beneath `src/swingset/state/migrations/`
inside the two archive parts. Read their SHA-256 hashes in `manifest.json`, or extract
both parts with Python's standard archive tool:

```
python -m zipfile -e tests/fixtures/legacy_sqlite37/source-1.zip /tmp/swingset-historical
python -m zipfile -e tests/fixtures/legacy_sqlite37/source-2.zip /tmp/swingset-historical
```

## Historical Python helpers

The original `state/derivation_payload_migration.py` and
`state/finding_reference_migration.py` are also retained inside the two archive parts.
They run only in historical tests; the live worker cannot select a SQLite
backend or execute those upgrades.

Do not edit archived source. A changed historical assertion needs a new capture
and manifest, alongside its reason.

The retired, fixed-manifest fixture-quarantine research contract also runs
here with its original SQLite authorizer, file lock and all 27 case nodes. Its
reviewed manifest/CDX/header inputs are archived byte for byte; current native
acquisition and role safety remain in the ordinary Postgres tests.
