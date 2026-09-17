# JesAnn Nail’s history page

Build an offline, searchable page from the saved September 16 dataset extract.
It opens directly in a browser and contains its own data, styles, and scripts.

```sh
.venv/bin/python journal/tools/quality/person-history/build.py \
  journal/evidence/quality/jesann-nail-2026-09-17/exports \
  tmp/jesann-nail-history-2026-09-17/index.html \
  --jes-test tmp/jesann-nail-history-2026-09-17/jes-test
open tmp/jesann-nail-history-2026-09-17/index.html
```

The default view includes every recorded competition participation in the
extract, including prelims, semifinals, Strictly, and Pro-Am. Search events or
partners; filter by year, division, role, wins, podiums, absence of a registry
match, or a best recorded preliminary/semifinal round. Expand entries for
partners, source links, callbacks, and judge marks. Download the filtered view
as CSV with identity and dataset provenance.

Source percentages use 100 contest-and-role appearances as their denominator:
15 have only detailed result data, 19 have both kinds of evidence, and 66 have
only a registry summary. Thus 34% have detailed results and 66% are registry-only.
The six judging appearances are separate. This is recorded coverage, not a
claim about every real-world appearance.

The required [Jes Test report](../jes_test/README.md) provides
placement-independent registry/sheet correspondence. A result disagreement
therefore remains one appearance with both source claims visible. Ambiguous
matches remain separate. Each sheet remains an unconfirmed name match. No
registry match means points are unknown, not zero. The page does not write
identity decisions or change the dataset. Counts and percentages are computed
from the embedded records.

`build.py` embeds the JSON exports into `page.html`, `style.css`, and `app.js`.
It requires Python’s standard library and is scoped to WSDC #7849. Regenerate
from a fresh reviewed extract to update the page; it does not fetch data.

[Source evidence](../../../evidence/quality/jesann-nail-2026-09-17/README.md) ·
[Validation outcome](../../../investigations/2026/jesann-history-page-2026-09-17.md) ·
[Decision](../../../decisions/0064-present-all-participation-with-source-overlap.md)
