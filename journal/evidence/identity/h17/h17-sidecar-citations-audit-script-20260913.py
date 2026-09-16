import json
from pathlib import Path
O=Path('research/workflow-output/h17-proposals-20260913');errors=[];sources=0;registry=0

def at(v,p):
 for k in p.strip('/').split('/'):
  k=k.replace('~1','/').replace('~0','~');v=v[int(k)] if isinstance(v,list) else v[k]
 return v
for file in sorted(O.glob('proposals*.json')):
 for r in json.loads(file.read_text())['proposals']:
  ev=r['scoring_evidence'];ext=json.loads(Path(ev['extract']['path']).read_text());body=Path(ev['body']['path']).read_bytes()
  for row in ev['inspected_subject_rows']:
   p=row.get('locator',row.get('extract_json_pointer',row.get('body_json_pointer')))
   if p.startswith('/data/'):
    raw=json.loads(body);parts=p.split('/');g=at(raw,'/'.join(parts[:5]));table=at(raw,'/'.join(parts[:7]));header=[c.get('v') for c in table['data'][0]]
    expected=g['roundName'];assert ext==raw['data']['scoresData']
   else:
    table=ext[int(p.split('/')[1])];expected=table['heading'];header=[c['text'] for c in table['rows'][0]]
   if row['heading']!=expected or row['header']!=header[:len(row['header'])]:errors.append([file.name,r['sample_id'],'header',p])
   sources+=1
  for ev in r['registry_evidence']:
   body=json.loads(Path(ev['body']['path']).read_text())
   for key in ('sampled_event_matches','same_month_placements','other_year_placements'):
    for ref in ev.get(key,[]):
     p=ref.get('locator',ref.get('body_json_pointer'));actual=at(body,p);div=at(body,p.rsplit('/competitions/',1)[0])['division']['name']
     expected={'role':actual['role'],'division':div,'result':actual['result'],'points':actual['points']}
     if key=='sampled_event_matches':expected['event']=actual['event']
     else:expected.update(event=actual['event']['name'],date=actual['event']['date'])
     for k,v in expected.items():
      if ref[k]!=v:errors.append([file.name,r['sample_id'],'registry',p,k])
     registry+=1
print('source headers',sources,'registry citations',registry,'errors',errors)
p=Path('/tmp/h17-sidecar-audit.json');d=json.loads(p.read_text());d.update(source_headers_verified=sources,registry_citations_verified=registry,citation_errors=errors);p.write_text(json.dumps(d,indent=2))
