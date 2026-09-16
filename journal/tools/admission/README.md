# Source interpretation checks tools

Review a parser’s interpretation before it can replace saved evidence.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/admission/README.md)

| Script                                                         | Purpose                                                                             |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| [assess_source_admission.py](assess_source_admission.py)       | Run H6 source admission contracts over retained archives, entirely offline.         |
| [assess_unsupported_scoring.py](assess_unsupported_scoring.py) | Read-only projected impact of explicitly unsupported numeric/Solo scoring.          |
| [bootstrap_parse.py](bootstrap_parse.py)                       | Offline V4 parsing while new sheet acquisition remains gated.                       |
| [bootstrap_v4.py](bootstrap_v4.py)                             | Apply reviewed V4 admission and phase1 evidence with acquisition held.              |
| [compare_admission_replay.py](compare_admission_replay.py)     | Explain exact payload changes after an isolated H7 rehearsal, read-only.            |
| [fixture_exception.py](fixture_exception.py)                   | Exact, operator-authorized new-source fixture quarantine; no admission activation.  |
| [fixture_transport.py](fixture_transport.py)                   | Bounded streaming transport for the one reviewed fixture manifest, without watches. |
| [rehearse_source_admission.py](rehearse_source_admission.py)   | Replay explicitly reviewed admission contracts on a new disposable SQLite copy.     |
