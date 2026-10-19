# Glossary

Words used across the [migration plan](README.md). Pages put a word in
**bold** where it first matters.

| Word                          | Meaning                                                                                                                                                                           |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gov`                         | The tables that will hold decisions: overrides, approvals, holds, settings                                                                                                        |
| Audit digest                  | The code or environment fingerprint recorded at each deploy for the record, but not part of any recipe                                                                            |
| Catalog                       | The published releases, stored in Postgres so they are easy to query                                                                                                              |
| Checkpoint                    | A saved copy of production's data                                                                                                                                                 |
| Clone                         | A copy-on-write copy of a database file or a checkpoint (`cp --reflink` on btrfs or APFS) that costs nothing until written                                                        |
| Code freeze                   | From the dry run's checkpoint to the switch, production takes no deploys, so what the dry run proved is what the switch replaces                                                  |
| Collation                     | The rule a database uses to sort text                                                                                                                                             |
| Conditional request           | Asking a site to send a page only if it changed since a given copy                                                                                                                |
| Connection pooler             | A middleman that shares database connections between programs                                                                                                                     |
| Current unit                  | A unit whose stored result is up to date with its inputs; only current units are compared in a verify run                                                                         |
| Cutoff                        | Keeping an existing generation when a re-run's output is identical, instead of writing a new one (D-0205)                                                                         |
| Dead-man's switch             | An outside service that raises an alarm when regular pings stop                                                                                                                   |
| Declared version              | A hand-written version constant on a transform; after stage 0 it is what makes that transform's results out of date                                                               |
| Dependency image              | The server's container image without Swingset's own code; its digest is the server's runtime identity                                                                             |
| Docker, Docker Compose        | Docker packages a program with everything it needs; Compose starts several of these together                                                                                      |
| Dokploy                       | A tool that runs Docker apps on your own server, with a web page to manage them                                                                                                   |
| Drain                         | Run until the work queue is empty                                                                                                                                                 |
| Egress check                  | A small test of how other sites treat requests from the server's address                                                                                                          |
| Engine core                   | The small part of the code (`state/engine/`) that connects to Postgres, runs transactions, takes locks and converts values                                                        |
| Equivalence                   | A row saying that a new input fingerprint produced exactly an existing generation's output (migration 33)                                                                         |
| Gate                          | A written check that must pass before the next stage starts                                                                                                                       |
| Hash, digest, sha256          | A short fingerprint of some content; any change to the content changes it. sha256 is the method used                                                                              |
| Hold                          | A switch that stops the worker's loops from starting new work                                                                                                                     |
| Irreplaceable, rebuildable    | The two labels every table carries: irreplaceable tables are backed up; rebuildable ones are rebuilt by re-derivation                                                             |
| Lock, advisory lock           | A claim that stops two jobs from writing at once; an advisory lock is one the program asks Postgres for by name                                                                   |
| Migration                     | A numbered script that changes the database's tables                                                                                                                              |
| MinIO                         | Object storage you can run yourself; used here as a local stand-in                                                                                                                |
| Namespace                     | A group of tables in Postgres (Postgres calls it a "schema")                                                                                                                      |
| Object storage, bucket        | A service that stores files by name, such as S3, R2 or B2; a bucket is one storage area                                                                                           |
| Out-of-band hold              | A hold kept outside the database, so it works while Postgres is down                                                                                                              |
| Parquet                       | The column-based file format the dataset is published in                                                                                                                          |
| psycopg                       | The Python library that talks to Postgres                                                                                                                                         |
| Re-derivation                 | Re-running the derive loop over every unit when its inputs or code change; 10 to 14 hours on today's VM                                                                           |
| Re-key                        | Recording equivalences so existing results count as current under new recipe values, without re-running them                                                                      |
| Rebuild                       | Recovering from a backup: restore the irreplaceable tables and files into a fresh database, then re-derive everything                                                             |
| Recipe, recipe scope          | A unit's recipe is the map of named inputs and digests its identity is hashed from; a scope is the code the recipe names                                                          |
| Recovery point, recovery time | How much recent data a failure may lose (up to about 3½ days, with backups twice a week), and how long until things run again (about a day, to rebuild)                           |
| Redirect                      | A record that an old id now lives under a new one                                                                                                                                 |
| Runtime identity              | The exact fingerprint of the software code runs in: the interpreter and wheels on the worker, the dependency image on the server                                                  |
| Supersede                     | Replace a decision by adding a newer row, never by editing the old one                                                                                                            |
| Tick                          | One run of one loop                                                                                                                                                               |
| Transform                     | The code that turns a unit's inputs into its output: a page kind's extractor, parser or contract, a projector, the linker, the build                                              |
| Trigger                       | Database code that runs by itself when a row changes                                                                                                                              |
| Unit                          | One piece of derived work: one page's parse, one dancer's or event's projection, one event's links, one build                                                                     |
| User-Agent                    | The name a program gives a website when it asks for a page                                                                                                                        |
| Verify run                    | Running the new code over every current unit of the changed transforms on a copy, and comparing each output and dependency set with what is stored; releases wait for a clean one |
