# Object storage on Dokploy

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Reviewed 2026-09-14 America/Denver (2026-09-15 UTC). Documentation and source
review only; no deployment, compatibility test, or performance benchmark.
Recommendations below are engineering judgments. This note does not amend the
[accepted migration plan](../../../docs/plans/postgres-migration/README.md).

## Recommendation

For Swingset, prefer a **managed private S3-compatible bucket, initially
Cloudflare R2 Standard, with a local artifact cache** if application object
storage is the goal. It avoids operating another storage server and separates
the remote archive from the Dokploy host. Keep PostgreSQL, locks, working files,
and build scratch space on their planned local volumes.

For the immediate migration, the existing **named artifact volume plus complete
offsite checkpoints** remains the smallest change. Adding S3 storage is a
separate application change; installing Dokploy does not convert our archive.

If self-hosting is a requirement, evaluate **SeaweedFS for the single-server
deployment** and **Garage for a future replicated deployment**. Both have
Dokploy templates. A same-server object store still shares that server's disk
and failure risks; independent recovery copies remain necessary.

## The three normal integration patterns

| Pattern                      | How it fits Dokploy                                                                                                         | Main tradeoff                                                                  |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Managed bucket               | Worker uses an S3 SDK with endpoint, bucket, region, and credentials.                                                       | Least storage administration; remote reads and writes depend on the network.   |
| Self-hosted S3 service       | Deploy a template or reviewed Compose service with persistent named volumes. Worker connects over a private Docker network. | Local access and control; we own upgrades, capacity, durability, and recovery. |
| Local volume with S3 backups | Application keeps using files; Dokploy or our checkpoint uploader sends backups to a bucket.                                | Easiest migration; does not give the application an object-storage API.        |

Dokploy documents **S3 Destinations as backup connections** to existing buckets.
Configuring one does not configure the worker's SDK. Its volume-backup feature
supports Docker named volumes, including Compose services, but excludes bind
mounts such as `../files`. [Destination settings](https://docs.dokploy.com/docs/core/actions),
[volume backups](https://docs.dokploy.com/docs/core/volume-backups).

## Managed storage

R2 has an S3-compatible HTTPS endpoint usable from ordinary S3 SDKs, so Swingset
can stay in Dokploy; Cloudflare Workers are unnecessary. Compatibility is a
documented subset, so validate the actual operations and SDK options we use.
Dokploy separately documents R2 as a backup destination.
[R2 APIs](https://developers.cloudflare.com/r2/api/),
[compatibility](https://developers.cloudflare.com/r2/api/s3/api/),
[Dokploy R2 setup](https://docs.dokploy.com/docs/core/cloudflare-r2).

At review time, R2 Standard lists $0.015/GB-month, $4.50/million Class A
operations, and $0.36/million Class B operations, with free allowances of
10 GB-month, one million Class A operations, and ten million Class B operations.
Direct R2 egress is free. Thus 100 GB retained for a month is approximately
$1.35 in storage after the allowance, before request charges and taxes; this
is an illustration, not a measured Swingset bill.
[R2 pricing](https://developers.cloudflare.com/r2/pricing/).

I favor Standard initially because replay and verification access patterns are
not yet measured. Infrequent Access has retrieval charges and a 30-day minimum
storage duration. [Storage classes](https://developers.cloudflare.com/r2/buckets/storage-classes/).

AWS S3 is also a documented Dokploy destination and a sensible choice when an
AWS account or a specific AWS feature drives the decision. This review does
not establish R2 as cheapest across all providers or all access patterns.
[Dokploy AWS setup](https://docs.dokploy.com/docs/core/aws-s3).

## Self-hosted choices

| Service                    | Verified facts                                                                                                                                                                     | Assessment for Swingset                                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| SeaweedFS                  | Dokploy template exists. Upstream offers a single-process `weed mini` mode and describes it as suitable for single-node production.                                                | First single-server candidate to test; broader capabilities than our blob archive requires.                                 |
| Garage                     | Dokploy templates exist, including a UI variant. Supports core object operations and multipart uploads; its compatibility table excludes bucket versioning and AWS-style policies. | Good candidate for a focused object archive when its permission model and replication design fit.                           |
| RustFS                     | Dokploy template exists. Upstream README currently demonstrates `1.0.0-rc.6`.                                                                                                      | Evaluate later; a release-candidate example and template availability do not establish readiness for our only archive copy. |
| PGSTY Silo / MinIO lineage | Dokploy's MinIO template uses `pgsty/minio`; that project has since become `pgsty/silo`. Upstream `minio/minio` is archived.                                                       | Consider only after reviewing the maintained fork and a pinned release; avoid copying old MinIO recipes unchanged.          |

Sources: [SeaweedFS template](https://docs.dokploy.com/docs/templates/seaweedfs),
[SeaweedFS upstream](https://github.com/seaweedfs/seaweedfs),
[Garage template](https://dokploy.com/templates/garage),
[Garage UI template](https://dokploy.com/templates/garage-with-ui),
[Garage compatibility](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/),
[RustFS template](https://dokploy.com/templates/rustfs),
[RustFS upstream](https://github.com/rustfs/rustfs),
[MinIO template](https://dokploy.com/templates/minio),
[MinIO upstream](https://github.com/minio/minio),
[Silo upstream](https://github.com/pgsty/silo).

Garage's template configures replication factor one. Its own quick-start guide
warns against using the single-node setup in production because it provides no
redundancy. Multiple containers on sandile.dev would still share one host;
replication needs independent failure domains to address host loss.
[Garage quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/).

There is visible template drift: Dokploy's MinIO description says upstream was
archived in February 2026, while GitHub records April 25, 2026. Silo records its
rename on August 6. Use the upstream record for those facts and review the
actual image before deployment. This is why a template is a starting point,
not release qualification. [Dokploy template](https://dokploy.com/templates/minio),
[upstream archive](https://github.com/minio/minio),
[fork rename](https://github.com/pgsty/silo).

For either self-hosted shortlist candidate, use a pinned image, persistent named
volumes, a private service endpoint, and separate application credentials. Only
route a public TLS endpoint if external clients need it. Keep administration
private and test restore from an independent copy. These are proposed deployment
choices, not claims that every template supplies those defaults.

## What would change in Swingset

The [current archive](../../../src/swingset/fetch/archive.py) already hashes original
response bytes and stores deterministic gzip files under
`blobs/sha256/<aa>/<bb>/<hash>`. Extracts have separate content hashes. Those
make natural object keys without changing snapshot identity.

A proposed implementation would:

1. Introduce an object-storage backend around body/extract reads and writes,
   retaining a local implementation and a verified local cache.
2. Preserve the rule that an artifact is durable before database state refers
   to it. If the remote bucket becomes authoritative, a successful local write
   alone cannot satisfy that rule. An asynchronous mirror instead needs an
   explicit pending-upload state and a stated recovery lag.
3. Preserve gzip bytes and verify the original content SHA on reads. Do not
   substitute an object's ETag for our content hash.
4. Keep mutable control files, file locks, baseline links, and build temporary
   files local. The existing archive uses file fsync and atomic replacement;
   mounting the entire state directory through an S3 filesystem adapter is
   outside the proven storage contract.
5. Adapt checkpoint creation, restoration, artifact recovery, and garbage
   collection, which also depend on local paths. This is more than supplying
   an endpoint environment variable.
6. Preserve a checkpoint manifest tying a consistent database snapshot to every
   required artifact. Independent database and bucket backup schedules do not
   establish that relationship. Do not expire referenced historical bodies by
   age. The [migration plan](../../../docs/plans/postgres-migration/README.md) already requires
   complete database-plus-artifact recovery.

A warm cache lets replay reuse local blobs. A cold cache introduces remote
requests and transfer time; free egress does not remove latency. Actual impact
is **unverified** until measured from sandile.dev. A managed bucket would make
network relevant during new durable writes and cache misses, while an
asynchronous backup-only bucket would chiefly need it during backup and restore.

Before selecting the backend, measure compressed bytes, object count, object
size distribution, and replay cache misses. Then test interrupted uploads,
retry behavior, immediate reads after writes, hash verification, cold-cache
replay, and a complete restore using the intended SDK and pinned versions.
No provider or template has passed those Swingset-specific checks in this review.
