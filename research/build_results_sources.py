"""Turn the wcs-results-sources workflow output into research/results-sources.csv.

Usage: python3 build_results_sources.py <run1.json> [<run2.json> ...]

Each JSON is a workflow's return value: {"results": [...], "missing": [...], "counts": {...}}.
Later files override earlier ones for the same event_key. Rows are joined to
events.csv on event_key so every event appears once, including those no
workflow answered. Then results-sources.overrides.csv is applied on top.
"""
import csv, json, sys, pathlib, html, re

HERE = pathlib.Path(__file__).parent
COLS = ['event_key', 'name', 'end_date', 'platform', 'secondary_platforms', 'results_url', 'scores_url',
        'callbacks_url', 'heat_sheets_url', 'has_results', 'has_scores', 'has_callbacks', 'has_heat_sheets',
        'bibs_visible', 'wsdc_ids_visible', 'confidence', 'passes', 'first_pass_platform', 'evidence',
        'urls_checked', 'notes', 'requests_made', 'results_host', 'url_in_index', 'edition_held', 'source_run']

WDR_HOSTS = {'scores.worlddanceregistry.com', 'www.worlddanceregistry.com', 'worlddanceregistry.com'}

def load_index_ids():
    idx = HERE / 'aggregators'
    sd = {r['event_id'] for r in csv.DictReader(open(idx / 'scoringdance_events.tsv'), delimiter='\t')}
    ee = {r['slug'] for r in csv.DictReader(open(idx / 'eepro_events.tsv'), delimiter='\t')}
    dcn = {r['event_id'] for r in csv.DictReader(open(idx / 'dcn_events.tsv'), delimiter='\t')}
    return sd, ee, dcn

def check_index(platform, url, sd, ee, dcn):
    """Does the results URL point at an id or slug that exists in the captured index?"""
    if platform == 'scoring.dance':
        m = re.search(r'/events/(\d+)', url); return 'yes' if m and m[1] in sd else 'no'
    if platform == 'eepro':
        m = re.search(r'event=([^&/]+)|/results/([^/]+)/', url)
        slug = m and (m[1] or m[2]); return 'yes' if slug in ee else 'no'
    if platform == 'danceconvention.net':
        m = re.search(r'/eventpage/(\d+)', url); return 'yes' if m and m[1] in dcn else 'no'
    return 'n/a'

def main(paths):
    by_key = {}
    for path in paths:
        out = json.load(open(path))
        for r in out.get('results', []):
            prev = by_key.get(r['event_key'])
            r = dict(r, source_run=pathlib.Path(path).stem)
            if prev is not None:
                r['first_pass_platform'] = prev.get('platform', '')
                r['passes'] = str(prev.get('passes', 1)) + '+retry'
            by_key[r['event_key']] = r
    events = list(csv.DictReader(open(HERE / 'events.csv')))
    unknown = set(by_key) - {e['event_key'] for e in events}
    if unknown:
        print('WARNING: result keys not in events.csv:', sorted(unknown), file=sys.stderr)
    sd, ee, dcn = load_index_ids()
    rows = []
    for e in events:
        r = by_key.get(e['event_key'])
        row = {c: '' for c in COLS}
        row.update(event_key=e['event_key'], name=e['name'], end_date=e['end_date'])
        if r is None:
            row.update(platform='not_researched', confidence='', notes='workflow agent returned nothing')
        else:
            for c in COLS:
                if c in r and r[c] is not None:
                    v = r[c]
                    row[c] = str(v).lower() if isinstance(v, bool) else html.unescape(str(v))
            m = re.search(r'https?://([^/\s]+)', row['results_url'])
            row['results_host'] = m[1].lower() if m else ''
            if row['results_host'] in WDR_HOSTS:
                row['platform'] = 'worlddanceregistry'
            if row['edition_held'] == 'no':
                row['platform'] = 'not_held'   # edition cancelled or on hiatus; nothing to find
            row['url_in_index'] = check_index(row['platform'], row['results_url'], sd, ee, dcn)
        rows.append(row)
    # Manual corrections. Any non-empty cell in the overrides file replaces the agent's value.
    ov_path = HERE / 'results-sources.overrides.csv'
    if ov_path.exists():
        overrides = {o['event_key']: o for o in csv.DictReader(open(ov_path))}
        for row in rows:
            o = overrides.get(row['event_key'])
            if not o:
                continue
            for c, v in o.items():
                if c != 'event_key' and v != '' and c in row:
                    row[c] = v
            if o.get('platform') == 'not_found':
                for c in ('results_url', 'scores_url', 'callbacks_url', 'heat_sheets_url', 'secondary_platforms'):
                    row[c] = ''   # an override to not_found retracts the agent's URLs
            if row['secondary_platforms'].strip() == row['platform']:
                row['secondary_platforms'] = ''   # the agent's guess became the primary platform
            row['confidence'] = 'high'
            row['passes'] = 'manual'
            m = re.search(r'https?://([^/\s]+)', row['results_url'])
            row['results_host'] = m[1].lower() if m else ''
            row['url_in_index'] = check_index(row['platform'], row['results_url'], sd, ee, dcn)
        unknown_ov = set(overrides) - {r['event_key'] for r in rows}
        if unknown_ov:
            print('WARNING: override keys not in events.csv:', sorted(unknown_ov), file=sys.stderr)
    with open(HERE / 'results-sources.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)
    import collections
    print(len(rows), 'rows;', dict(collections.Counter(r['platform'] for r in rows)))

if __name__ == '__main__':
    main(sys.argv[1:])
