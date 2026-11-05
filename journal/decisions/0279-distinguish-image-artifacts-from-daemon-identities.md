# D-0279: Distinguish image artifacts from daemon identities

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to start stage 2  
Topic: Container identity  
Supersedes: The assumption that a local Docker image ID is portable between daemons  
Superseded by: —

## Decision

Keep the OCI artifact digest, selected platform manifest, configuration digest
and target daemon's image ID as separate facts. Verify their content-addressed
chain, architecture and source binding before starting containers. The network
policy pins the identity the target daemon reports for the actual container.
No tag or identifier copied from another daemon substitutes for this check.

## Why

The local containerd-backed daemon reports manifest or index identities where
the target's classic image store expects a configuration identity. Official
gateway indexes also selected ARM64 locally while the server needs AMD64.
Treating those identifiers as interchangeable would reject the intended image
or make an incorrect provenance claim. The same bytes must be proved across
both stores before their container identities authorize any database access.

## Links

[Image and deployment rules](../../docs/plans/postgres-migration/4-hosting.md#images).
