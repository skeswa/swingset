"""Read the acknowledged release and export JesAnn Nail's recorded history."""
import csv
import hashlib
import json
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'dataset'
manifest = json.loads((BASE / '_meta/manifest.json').read_text())
verified = 0
for name, expected in manifest['files'].items():
    if name.startswith('data/'):
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == expected, name
        verified += 1
c = duckdb.connect()
c.execute("SET TimeZone='UTC'")
for p in (BASE / 'data').iterdir():
    c.execute(f"CREATE VIEW {p.name} AS SELECT * FROM read_parquet('{p}/*.parquet', hive_partitioning=false)")
def rows(sql):
    return c.execute(sql).to_arrow_table().to_pylist()
c.execute("CREATE VIEW selected_entries AS SELECT * FROM entries WHERE wsdc_id=7849 OR name_raw ILIKE '%jes%ann%nail%'")
c.execute("CREATE VIEW selected_judges AS SELECT * FROM judges WHERE wsdc_id=7849 OR name_raw ILIKE '%jes%ann%nail%'")
c.execute('''CREATE VIEW selected_placements AS SELECT p.* FROM placements p WHERE
 leader_wsdc_id=7849 OR follower_wsdc_id=7849 OR EXISTS
 (SELECT 1 FROM selected_entries e WHERE e.entry_id IN (p.leader_entry_id,p.follower_entry_id,p.couple_entry_id))''')
queries = {
 'profile': 'SELECT * FROM dancers WHERE wsdc_id=7849',
 'registry_results': '''SELECT r.*,s.url source_url FROM registry_placements r LEFT JOIN snapshots s USING(snapshot_id) WHERE wsdc_id=7849 ORDER BY event_month,series_name_raw,role,division''',
 'score_sheet_results': '''SELECT e.*,v.name event_name,v.event_month,c.name_raw contest_name,c.division,c.age_division,c.contest_type,p.place,p.placement_id,r.score_sheet_url,s.url source_url FROM selected_entries e JOIN contests c USING(contest_id) JOIN events v ON e.event_id=v.event_id LEFT JOIN selected_placements p ON e.entry_id IN(p.leader_entry_id,p.follower_entry_id,p.couple_entry_id) LEFT JOIN rounds r ON p.round_id=r.round_id LEFT JOIN snapshots s ON e.snapshot_id=s.snapshot_id ORDER BY v.event_month,e.event_id,c.name_raw''',
 'judging_appearances': '''SELECT j.*,v.name event_name,v.event_month FROM selected_judges j JOIN events v USING(event_id) ORDER BY v.event_month''',
 'callbacks': '''SELECT cb.*,r.name_raw round_name,r.score_sheet_url FROM callbacks cb JOIN selected_entries e USING(entry_id) JOIN rounds r USING(round_id) ORDER BY cb.round_id,cb.entry_id''',
 'callback_marks_received': '''SELECT m.*,j.name_raw judge_name,r.score_sheet_url FROM callback_marks m JOIN selected_entries e USING(entry_id) LEFT JOIN judges j USING(judge_id) JOIN rounds r USING(round_id) ORDER BY m.round_id,m.entry_id,m.judge_id''',
 'final_marks_received': '''SELECT m.*,j.name_raw judge_name,r.score_sheet_url FROM final_marks m JOIN selected_placements p USING(placement_id) LEFT JOIN judges j USING(judge_id) JOIN rounds r ON m.round_id=r.round_id ORDER BY m.round_id,m.placement_id,m.judge_id''',
 'heats': 'SELECT h.* FROM heats h JOIN selected_entries e USING(entry_id) ORDER BY round_id,entry_id',
 'callback_marks_given': 'SELECT m.* FROM callback_marks m JOIN selected_judges j USING(judge_id) ORDER BY round_id,entry_id',
 'final_marks_given': 'SELECT m.* FROM final_marks m JOIN selected_judges j USING(judge_id) ORDER BY round_id,placement_id',
 'identity_links': '''SELECT i.* FROM identity_links i WHERE i.wsdc_id=7849 OR subject_id IN (SELECT entry_id FROM selected_entries UNION ALL SELECT judge_id FROM selected_judges)''',
 'link_candidates': '''SELECT l.* FROM link_candidates l WHERE subject_id IN (SELECT entry_id FROM selected_entries UNION ALL SELECT judge_id FROM selected_judges)''',
}
data={}
for name,sql in queries.items():
    data[name]=rows(sql)
    (ROOT / f'{name}.json').write_text(json.dumps(data[name],default=str,indent=2)+'\n')
    with (ROOT / f'{name}.csv').open('w',newline='') as f:
        columns = list(data[name][0]) if data[name] else [x[0] for x in c.execute(sql).description]
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(data[name])
summary=rows('''SELECT role,division,count(*) results,sum(points)::INTEGER points,min(event_month) earliest,max(event_month) latest FROM registry_placements WHERE wsdc_id=7849 GROUP BY ALL ORDER BY role,earliest''')
judging=rows('''SELECT j.event_id,count(DISTINCT x.round_id) rounds,count(*) marks FROM selected_judges j JOIN (SELECT judge_id,round_id FROM callback_marks UNION ALL SELECT judge_id,round_id FROM final_marks) x USING(judge_id) GROUP BY j.event_id ORDER BY j.event_id''')
profile=data['profile'][0]
def cell(value):
    return str(value if value is not None else '—').replace('|','\\|').replace('\n',' ')
def table(headers, values):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(cell(v) for v in row)+' |' for row in values])
def title(s):return {'allstar':'All-Star'}.get(s,s.title())
report=['# JesAnn Nail: full recorded history','',
'WSDC ID **7849**. This report uses the current acknowledged Swingset release, published September 16, 2026, commit `2a6c7dc744fb36eabb5163c0a527d787d3721f4f`. The release evidence cutoff is September 16 at 22:25:41 UTC. Her registry profile was fetched September 11, 2026 at 17:10:26 UTC. All '+str(verified)+' copied Parquet files match the release manifest hashes.','',
'The registry contains **85 results at 76 event editions**, September 2010–August 2026: **12 wins, 35 top-three results, and 307 recorded points across all categories and roles**. Points across categories are not a single division balance. The profile lists her primary role as follower and her required/allowed follower division as All-Star. Its highest recorded leader division is Intermediate (23 points), and highest follower division is All-Star (134 points). Leader required/allowed fields are `none`; no eligibility is inferred from them.','',
'The 34 score-sheet entries and six judging appearances below match the printed names JesAnn Nail, Jes Ann Nail, or Jess Ann Nail. **All have null WSDC IDs in this release.** They are useful name-based evidence, but are not confirmed identity joins to 7849. Source spelling, entry roles, identity status and provenance are preserved in the exports.','',
'**Coverage limits:** The dataset starts at 2010 and historical score sheets remain incomplete. Missing results do not establish nonparticipation. There are no registry results for 2021 in this extract. Registry months are reported months, not exact event dates. `F` means finalist with no exact place in the registry. The score-sheet and registry tables overlap; do not add their row counts as unique competitions. An entry’s best recorded round does not by itself establish elimination.','',
'**Points by role and division**','',table(['Role','Division','Results','Points','First result','Latest result'],[[title(x['role']),title(x['division']),x['results'],x['points'],str(x['earliest'])[:7],str(x['latest'])[:7]] for x in summary]),'',
'**Every registry result, oldest first**','',table(['Month','Event','Division','Role','Result','Points'],[[str(x['event_month'])[:7],x['series_name_raw'],title(x['division']),title(x['role']),x['result'],x['points']] for x in data['registry_results']]),'',
'**Every matching score-sheet entry**','',
'The result is the published final place when available; otherwise it is the furthest recorded round. Couples retain the full printed entry name. Contest labels are abbreviated from structured fields; raw labels are in the exports.','']
sheetrows=[]
for x in data['score_sheet_results']:
    label=(title(x['age_division']) if x['age_division']!='none' else title(x['division']))+' '+{'jack_and_jill':'J&J','strictly':'Strictly'}.get(x['contest_type'],title(x['contest_type']))
    if 'ProAm' in x['contest_name'] or 'Ugly Sweater' in x['contest_name']: label=x['contest_name']
    sheetrows.append([x['event_month'],x['event_name'],label,x['role'],x['place'] if x['place'] is not None else x['best_round'],x['partner_name_raw'] or (x['name_raw'] if x['role']=='couple' else None),x['link_status']])
report += [table(['Month','Event','Contest','Role','Result / best round','Partner / printed couple','Identity status'],sheetrows),'','**Every matching judging appearance**','']
jcounts={x['event_id']:x for x in judging}
report += [table(['Month','Event','Printed name','Rounds with marks','Marks recorded'],[[x['event_month'],x['event_name'],x['name_raw'],jcounts.get(x['event_id'],{}).get('rounds',0),jcounts.get(x['event_id'],{}).get('marks',0)] for x in data['judging_appearances']]),'',
'**Detailed exports**','',
'CSV and JSON exports preserve all columns, including source snapshot IDs and timestamps. The query script is [extract.py](extract.py); it reads only the pinned local release copy.','']
for name in queries: report.append(f'- [{name}.csv]({name}.csv): {len(data[name])} rows; [JSON]({name}.json).')
report += ['', 'Registry source: '+data['registry_results'][0]['source_url']+'.', '', 'Published release: https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f', '']
(ROOT/'JesAnn-Nail-full-history.md').write_text('\n'.join(report))
print(json.dumps({'verified_files':verified,'counts':{k:len(v) for k,v in data.items()},'judging':judging,'report':str(ROOT/'JesAnn-Nail-full-history.md')},indent=2))
