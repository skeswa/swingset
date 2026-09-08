# Technology

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | best library coverage for scraping, Parquet, and HF |
| Packaging | `flake.nix` devshell providing Python 3.12, `uv`, `node`; `pyproject.toml` + `uv.lock` for Python deps | nix owns the toolchain, uv owns Python deps. Avoids uv2nix overrides for native wheels. The dev Mac has only system Python 3.9 today. |
| HTTP | `httpx` | HTTP/2, timeouts, easy to wrap. Conditional GET handled by us, not a cache library, so behavior is explicit and logged. |
| robots.txt | `Protego` | Google-compatible, supports `Crawl-delay` |
| HTML | `selectolax` | fast, lenient |
| PDF | `pdfplumber` | table extraction; MIT. PyMuPDF is faster but AGPL |
| JS payloads | `node` from nixpkgs as a sandboxed subprocess | evaluates DCN's `__NUXT__`; reproducible under nix, trivially timed out |
| State | SQLite via stdlib `sqlite3`, WAL mode, on local disk | one file, transactional, backed up to HF |
| Analytics inside pipeline | DuckDB | joins over Parquet and SQLite for build and linking |
| Fuzzy matching | `RapidFuzz` | Jaro-Winkler, token ratios |
| Probabilistic linking | `Splink` | Fellegi-Sunter with term frequency; used to fit weights, not required at runtime |
| Assignment | `scipy` | `linear_sum_assignment` |
| Name parsing | `nameparser` | splits suffixes and particles |
| Parquet | `pyarrow` >= 21 | CDC and page index flags |
| Hub | `huggingface_hub` | `create_commit`, `hf_hub_download` |
| Orchestration | systemd timers from a NixOS module | exact cadence, local state, no round-trip. See [operations](operations.md). |
| CI | GitHub Actions | tests and lint on pull requests only; never runs the pipeline |
| Tests | `pytest`, `respx` for httpx, fixtures from the archive | golden files; no network in tests |
| Lint and types | `ruff`, `mypy --strict` | |
