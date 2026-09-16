"""Summarize the 2026-09-11 Wayback CDX coverage checks. Offline; stdlib only.

Reads the CDX JSON saved under verification/2026-09-11/ and prints the
tables quoted in wayback-coverage-2026-09-11.md. Rerun after replacing
those files with newer CDX output to refresh the numbers.

    python3 journal/tools/collection/wayback_coverage.py
"""

import collections
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parents[2] / "evidence/collection/wayback-2026-09-11"


def rows(name):
    data = json.loads((HERE / name).read_text())
    return data[1:] if data else []


def calendar_months():
    by = collections.defaultdict(set)
    for (ts,) in rows("cdx_worldsdc_events_months_2016_on.json"):
        by[ts[:4]].add(ts[4:6])
    print("worldsdc.com/events/ months with a 200 capture, per year")
    for y in range(2016, 2027):
        print(f"  {y}: {len(by.get(str(y), ()))}")


def eepro():
    slugs = collections.Counter()
    years = collections.Counter()
    kinds = collections.Counter()
    for ts, url in rows("cdx_eepro_results_all.json"):
        m = re.search(r"/results/([^/?]+)/?([^/?]*)", url)
        if not m or m.group(1).endswith(".php"):
            kinds["php or other"] += 1
            continue
        slug, leaf = m.groups()
        slugs[slug] += 1
        years[ts[:4]] += 1
        kinds["directory" if not leaf else leaf.rsplit(".", 1)[-1]] += 1
    slug_years = collections.Counter(
        (re.search(r"20\d\d", s) or re.search(r"$", s)).group(0) or "undated" for s in slugs
    )
    print("eepro.com/results/*: distinct 200 URLs", sum(slugs.values()) + kinds["php or other"])
    print("  by file kind", dict(kinds))
    print("  captures by year", dict(sorted(years.items())))
    print("  slugs", len(slugs), "by year in slug", dict(sorted(slug_years.items())))


def step_right():
    events = {}
    for ts, url in rows("cdx_steprightsolutions_html_all.json"):
        m = re.search(r"/events/([^/?#]+)(/round/(\d+))?", url)
        if not m:
            continue
        e = events.setdefault(m.group(1), {"rounds": 0, "first": ts})
        e["first"] = min(e["first"], ts)
        if m.group(2):
            e["rounds"] += 1
    by_year = collections.Counter()
    with_rounds = collections.Counter()
    round_pages = collections.Counter()
    for slug, e in events.items():
        y = (re.search(r"20\d\d", slug) or re.search(r"$", slug)).group(0) or "undated"
        by_year[y] += 1
        if e["rounds"]:
            with_rounds[y] += 1
            round_pages[y] += e["rounds"]
    print(
        "steprightsolutions.com: event slugs", len(events), "round pages", sum(round_pages.values())
    )
    print("  year: slugs / slugs with round pages / round pages")
    for y in sorted(by_year):
        print(f"  {y}: {by_year[y]} / {with_rounds[y]} / {round_pages[y]}")


if __name__ == "__main__":
    calendar_months()
    eepro()
    step_right()
