# Source interpretation checks tools

Review a parser’s interpretation before it can replace saved evidence.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/admission/README.md)

| Script                                                                     | Purpose                                                                             |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| [assess_source_admission.py](assess_source_admission.py)                   | Run H6 source admission contracts over retained archives, entirely offline.         |
| [assess_unsupported_scoring.py](assess_unsupported_scoring.py)             | Read-only projected impact of explicitly unsupported numeric/Solo scoring.          |
| [bootstrap_parse.py](bootstrap_parse.py)                                   | Offline V4 parsing while new sheet acquisition remains gated.                       |
| [bootstrap_v4.py](bootstrap_v4.py)                                         | Apply reviewed V4 admission and phase1 evidence with acquisition held.              |
| [compare_admission_replay.py](compare_admission_replay.py)                 | Explain exact payload changes after an isolated H7 rehearsal, read-only.            |
| [export_phase1_year_review.py](export_phase1_year_review.py)               | Export a fresh offline year review from the exact completed parser-8 scratch.       |
| [fixture_exception.py](fixture_exception.py)                               | Exact, operator-authorized new-source fixture quarantine; no admission activation.  |
| [fixture_transport.py](fixture_transport.py)                               | Bounded streaming transport for the one reviewed fixture manifest, without watches. |
| [rehearse_source_admission.py](rehearse_source_admission.py)               | Replay explicitly reviewed admission contracts on a new disposable SQLite copy.     |
| [replay_phase1_newsletter_parser8.py](replay_phase1_newsletter_parser8.py) | Replay the exact 28 retained newsletters on a sealed disposable schema-29 state.    |
| [refresh_archive_robots.py](refresh_archive_robots.py)                     | Refresh one exact Archive robots record through a sealed accounting-only operation. |
| [stepright_body_runner.py](stepright_body_runner.py)                       | Acquire one exact Step Right event body into a sealed quarantine.                   |

The [DCN origin fixture builder](build_dcn_origin_fixture.py) seals the two exact
Riga score-PDF locators with source and accounting controls. The
[origin runner](dcn_origin_fixture_runner.py) checks the reviewed gate, uses
shared budgets and completion-based spacing, and records one event per UTC day
before dispatch. Captured bodies stay in quarantine. See the
[operation record](../../investigations/2026/dcn-origin-runner-2026-09-17.md);
the runner's schema-28 scope is not a generic driver for later schemas.

The Step Right runner is bound to one Asian Open 2015 replay URL and schema 29.
Its final packet passed independent audit and retained the body in quarantine;
it does not enable the ordinary source, admit a page kind or create watches.
The separate robots helper exists so body operations cannot refresh policy
inside their own request scope. See the
[operation record](../../investigations/2026/stepright-body-runner-design-2026-09-17.md).
