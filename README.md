# Swingset

Swingset collects public West Coast Swing competition results into a
[dataset on Hugging Face](https://huggingface.co/datasets/skeswa/swingset).
It saves copies of its sources so results can be checked and rebuilt when
pages change or mistakes are found.

The data includes events, contests, rounds, competitors, judges, scores, and
World Swing Dance Council (WSDC) registry records. Coverage and identity matches
are still being checked. A missing result does not mean someone did not compete.

## Start here

1. [What the project does](docs/overview.md): purpose, scope, and a small example.
2. [How it works](docs/how-it-works/README.md): follow a result from a source page to the dataset.
3. [Current status](docs/status.md): what is published, what is blocked, and the evidence behind it.

For a specific task, use the [documentation guide](docs/README.md).
For code, start with the [code map](docs/how-it-works/code-map.md) and
[development guide](docs/guides/development.md).
Research findings and formal decisions live in the [project journal](journal/README.md).

## Collection and personal data

Swingset identifies itself, follows robots.txt, limits requests, and pauses
when a site blocks access. The [collection rules](docs/reference/fetching.md)
and [source guides](docs/reference/sources/README.md) give the exact settings.

Site operators can [open an issue](https://github.com/skeswa/swingset/issues/new/choose)
to ask us to slow down or stop. Use the same link to request removal of personal
data. Identify the affected event or public result page; do not post identity
documents or private information. Mention affected test fixtures too.

Code is MIT licensed. The code license does not relicense third-party results;
see [data use and privacy](docs/reference/ethics-and-legal.md).
