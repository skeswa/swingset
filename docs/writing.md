# Writing and maintaining the docs

Write for someone who knows basic programming but has never worked on Swingset.
Explain the purpose before naming the machinery. Aim for a high-school reading
level, including on detailed reference pages.

## Choose a home

| Content                                             | Home                                     |
| --------------------------------------------------- | ---------------------------------------- |
| Purpose and plain-language explanation              | `docs/overview.md`, `docs/how-it-works/` |
| Steps to complete a task                            | `docs/guides/`                           |
| Exact rules, interfaces, tables, and source details | `docs/reference/`                        |
| Work still to do and acceptance criteria            | `docs/plans/`                            |
| What is implemented, tested, deployed, or published | `docs/status.md`                         |
| Evidence, findings, and dated outcomes              | `journal/investigations/`                |
| Every decision and its reasoning                    | `journal/decisions/`                     |
| Reusable research scripts                           | `journal/tools/`                         |
| Captured sources, reports, and frozen scripts       | `journal/evidence/`                      |

## Let readers choose their depth

Start with a short answer and any context needed to understand it. Then give
an example or explanation. Put exact rules, exceptions, and supporting evidence
in later sections or linked pages. A parent page must make sense on its own.

Use descriptive headings. Write “Why the build timed out” rather than “H16
acceptance.” Keep old work-package IDs as secondary labels when needed to
connect evidence. Define technical terms on first use and link to the glossary.
Use short sentences, active verbs, and tables only when they aid comparison.

Aim for 100–250 words in an index, 300–700 in an explanation, and about one page
per decision. These are guides, not quotas. Split long pages by reader question;
do not split a single procedure across files just to meet a word count.

## Give each fact one owner

The [reference index](reference/README.md) identifies the owner of each rule.
Other pages may summarize it and link there. Avoid copying exact thresholds,
schemas, command sequences, and release status between pages.

When behavior changes, update its reference and affected task guide in the
same change. Update status only from evidence. An accepted plan does not prove
that code is implemented or running. Unverified facts must say **unverified**.

## Record decisions and investigations

Record every decision, including routine implementation choices, using the
[decision process](../journal/decisions/README.md). Keep small records brief.
Use the [investigation template](../journal/investigations/TEMPLATE.md)
for research and outcomes. A finding is not automatically a decision.

Dated records preserve what was known then. Add a visible correction or
superseding link when later evidence changes the conclusion. Do not rewrite
old measurements or invent missing approval dates. Captured evidence and
fixtures remain byte-exact; old paths in them describe their original checkout.

## Check the result

Read the opening as a newcomer. Check relative links and heading links after
moves. Use `mise run fmt`, the repository's formatting entry point. Regenerate
[enum reference](reference/enums.md) with `swingset enums --write`; do not
maintain another hand-written enum list. Use `jj status` and `jj diff` to review
changes. Commit or push only when asked.
