# Architecture

```
                 ┌──────────────┐
  WSDC calendar  │   discover   │  finds events and the URLs to watch
  EEPro index    │              │
  scoring.dance  └──────┬───────┘
  DCN lists             │ watches (url, policy, parser)
                        ▼
                 ┌──────────────┐
                 │   schedule   │  decides which watches are due now
                 └──────┬───────┘
                        │ due watches
                        ▼
                 ┌──────────────┐   conditional GET, robots, per-host
                 │    fetch     │   delay, backoff, budget
                 └──────┬───────┘
                        │ new bodies only
                        ▼
                 ┌──────────────┐   content-addressed blobs +
                 │   archive    │   snapshot rows in SQLite
                 └──────┬───────┘
                        │ snapshots
                        ▼
                 ┌──────────────┐   pure functions: bytes -> records
                 │    parse     │   one parser per (source, page kind)
                 └──────┬───────┘
                        │ raw records (source vocabulary)
                        ▼
                 ┌──────────────┐   canonical ids, enums, name forms,
                 │  normalize   │   event/contest matching across sources
                 └──────┬───────┘
                        │ canonical records
                        ▼
                 ┌──────────────┐   bib -> name -> wsdc_id with
                 │     link     │   confidence; registry confirmation
                 └──────┬───────┘
                        │ linked records
                        ▼
                 ┌──────────────┐   Parquet tables, changelog, manifest
                 │    build     │
                 └──────┬───────┘
                        │ diff vs last publish
                        ▼
                 ┌──────────────┐   one atomic HF commit per change
                 │   publish    │
                 └──────────────┘
```

Every stage is a CLI subcommand. Every stage is idempotent. State lives
in one SQLite file. Raw bodies live in a content-addressed blob store.
Both live on the local disk of the box that runs the pipeline and are
backed up to a private Hugging Face dataset repo ([operations](operations.md#backup-and-restore)).

The stages after `archive` never touch the network. They can be re-run
from the archive at any time, for example after a parser fix.
