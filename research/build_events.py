import re,glob,csv,html,unicodedata,datetime as dt,collections
MON={m:i for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
TODAY=dt.date(2026,9,4); SINCE=dt.date(2025,9,4)
def parse_date(d):
    d=d.strip()
    m=re.fullmatch(r'([A-Z][a-z]{2}) (\d+) - (\d+), (\d{4})',d)
    if m: y=int(m[4]); return dt.date(y,MON[m[1]],int(m[2])),dt.date(y,MON[m[1]],int(m[3]))
    m=re.fullmatch(r'([A-Z][a-z]{2}) (\d+) - ([A-Z][a-z]{2}) (\d+), (\d{4})',d)
    if m: y=int(m[5]); return dt.date(y,MON[m[1]],int(m[2])),dt.date(y,MON[m[3]],int(m[4]))
    m=re.fullmatch(r'([A-Z][a-z]{2}) (\d+) (\d{4}) - ([A-Z][a-z]{2}) (\d+) (\d{4})',d)
    if m: return dt.date(int(m[3]),MON[m[1]],int(m[2])),dt.date(int(m[6]),MON[m[4]],int(m[5]))
    raise ValueError(d)
def slug(name):
    s=unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().lower()
    s=re.sub(r'\(?\bon hiatus\b\)?|\(hiatus\)|\bhiatus\b',' ',s)
    s=re.sub(r'\b(20\d\d|\d{1,2}(st|nd|rd|th)|[ivx]+)\b',' ',s)   # drop years, ordinals, roman numerals
    s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
    return s
ROW=re.compile(r'<tr class="([^"]*)"><td>([^<]*)</td><td><div class="event_name">(?:<a href="([^"]*)"[^>]*>)?([^<]*)(?:</a>)?</div><div class="event_type">([^<]*)</div></td><td>([^<]*)</td><td><a href="\?country=([^"]*)"')
events={}
for f in sorted(glob.glob('snaps/*.html')):
    snap=re.search(r'(\d{8})',f)[1]
    s=open(f,encoding='utf-8',errors='replace').read()
    for cls,date,url,name,etype,loc,cc in ROW.findall(s):
        name=html.unescape(name).strip(); loc=html.unescape(loc).strip()
        start,end=parse_date(html.unescape(date))
        key=f"{end:%Y-%m}-{slug(name)}"
        e=events.setdefault(key,dict(event_key=key,series_slug=slug(name),first_seen=snap,seen_count=0,names=collections.Counter()))
        e.update(name=name,start_date=start,end_date=end,website=html.unescape(url or ''),event_type=etype.strip(),
                 location=loc,country_code=cc,flags=cls.strip(),last_seen=snap)
        e['seen_count']+=1; e['names'][name]+=1
rows=[]
for e in events.values():
    if not (SINCE<=e['end_date'] and e['start_date']<=TODAY): continue
    parts=[p.strip() for p in e['location'].split(',')]
    city=parts[0] if parts else ''; country=parts[-1] if len(parts)>1 else ''
    region=', '.join(parts[1:-1]) if len(parts)>2 else ''
    status='in_progress' if e['end_date']>=TODAY else 'ended'
    if 'event-canceled' in e['flags']: status='canceled'
    if 'hiatus' in e['name'].lower(): status='hiatus'
    rows.append(dict(event_key=e['event_key'],name=e['name'],series_slug=e['series_slug'],
        start_date=e['start_date'].isoformat(),end_date=e['end_date'].isoformat(),city=city,region=region,country=country,
        country_code=e['country_code'],event_type=e['event_type'],flags=e['flags'],status=status,website=e['website'],
        first_seen_snapshot=e['first_seen'],last_seen_snapshot=e['last_seen'],snapshots_seen=e['seen_count']))
rows.sort(key=lambda r:(r['start_date'],r['event_key']))
with open('events.csv','w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(len(rows),'events'); 
print(collections.Counter(r['event_type'] for r in rows)); print(collections.Counter(r['status'] for r in rows)); print(collections.Counter(r['flags'] for r in rows))
# duplicate slug check: same series twice in window
c=collections.Counter(r['series_slug'] for r in rows); print('series with >1 rows:',{k:v for k,v in c.items() if v>1})
# keys colliding across names
