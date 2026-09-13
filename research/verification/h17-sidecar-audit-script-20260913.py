import json,hashlib,re
from pathlib import Path
from collections import Counter
P=Path('/tmp/swingset-v2-phase1-check/h17-review');O=Path('research/workflow-output/h17-proposals-20260913')
D=json.loads((P/'sample.json').read_text());samples={s['sample_id']:s for s in D['samples']}; streams={k:sorted((s for s in samples.values() if s['stream']==k),key=lambda s:s['sample_id']) for k in ('accepted','unresolved')}
errors=[];seen=Counter();artifacts=set();locators=0;files={};counts=Counter()
def check(ok,code,ctx):
 if not ok:errors.append({'code':code,'context':ctx})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def at(value,pointer):
 for part in pointer.strip('/').split('/'):
  part=part.replace('~1','/').replace('~0','~');value=value[int(part)] if isinstance(value,list) else value[part]
 return value
for p in sorted(O.glob('proposals*.json')):
 d=json.loads(p.read_text());files[p.name]=sha(p)
 for key in ('sample_digest','population_digest','seed','cohort','cutoff'):check(d['packet'][key]==D[key],'packet_'+key,p.name)
 for key in ('sample','provenance'):check(d['packet'][key+'_file_sha256']==sha(P/(key+'.json')),'packet_file',p.name)
 check(d['human_adjudications_supplied']==0 and d['gold_labels_created']==0,'gold_header',p.name)
 check('HUMAN REVIEW REQUIRED' in d['label'],'review_label',p.name)
 for name,a in d['artifact_hashes_verified'].items():
  path=Path(a.get('path',P/'evidence'/name)); b=path.read_bytes();artifacts.add(str(path));check(sha(path)==a['portable_file_sha256'],'portable_hash',name)
  if name.startswith('extract-'):b=json.dumps(json.loads(b),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
  check(hashlib.sha256(b).hexdigest()==a['archive_sha256'],'archive_hash',name)
 for r in d['proposals']:
  sid=r['sample_id'];seen[sid]+=1;s=samples[sid];ctx=[p.name,sid]
  for key in ('sample_fingerprint','subject_id','name_raw','role','source_reference'):check(r[key]==s[key],'subject_'+key,ctx)
  check(r['packet_candidate_ids']==s['candidate_ids'],'candidate_list',ctx)
  check(r['packet_accepted_wsdc_id']==s['accepted_wsdc_id'],'frozen_id',ctx)
  check(r['packet_method']==s['link_method'],'frozen_method',ctx)
  off=r.get('offset',r.get('ordinal',0)-1);check(streams[s['stream']][off]['sample_id']==sid,'sorted_offset',ctx)
  check((s['stream']=='unresolved')==('unresolved' in p.name),'stream',ctx)
  check(r['human_review_required'] and r['verified_identity'] is False and r['adjudication'] is None and r['gold_label'] is None,'gold_row',ctx)
  chosen=r['chosen_wsdc_id'];check((chosen is not None)==(r['proposal_kind']=='proposal'),'proposal_kind',ctx)
  counts[(s['stream'],r['proposal_kind'])]+=1
  if chosen is not None:
   check(chosen in s['candidate_ids'],'chosen_candidate',ctx)
   check(chosen in [a['wsdc_id'] for a in s['registry_evidence']],'chosen_packet_evidence',ctx)
   check(chosen in [a.get('wsdc_id',a.get('id')) for a in r['registry_evidence']],'chosen_sidecar_evidence',ctx)
  if not s['name_raw'] or s['role']=='couple' or r.get('source_masked'):check(chosen is None,'masked_couple_assignment',ctx)
  e=r['scoring_evidence'];check(e['snapshot_id']==s['evidence']['snapshot_id'],'source_snapshot',ctx)
  for kind in ('body','extract'):
   a=e[kind];check(a.get('archive_sha256',a.get('sha256'))==s['evidence'][kind+'_sha256'],'source_'+kind,ctx)
  body=Path(e['body']['path']).read_bytes();ext=json.loads(Path(e['extract']['path']).read_bytes())
  rows=e['inspected_subject_rows'];check(bool(rows),'empty_source_rows',ctx)
  for row in rows:
   pointer=row.get('locator',row.get('extract_json_pointer',row.get('raw_json_pointer',row.get('body_json_pointer'))))
   if not pointer:check(False,'missing_locator',ctx);continue
   source=json.loads(body) if pointer.startswith('/data/') else ext
   try:
    actual=at(source,pointer);cells=row.get('cells',row.get('first_cells'))
    if cells and isinstance(cells[0],dict):check(actual[:len(cells)]==cells,'source_cells',ctx+[pointer])
    else:
     vals=[c.get('v') if 'v' in c else c.get('text') for c in actual]
     check(vals[:len(cells)]==cells,'source_cells',ctx+[pointer])
    locators+=1
   except (KeyError,ValueError,TypeError,IndexError) as exc:check(False,'bad_locator',ctx+[pointer,str(exc)])
   if r.get('source_masked'):check('***' in row['cells'],'unmasked_row_leak',ctx)
  for a in r['registry_evidence']:
   wid=a.get('wsdc_id',a.get('id')); packet=next((z for z in s['registry_evidence'] if z['wsdc_id']==wid),None)
   check(packet is not None,'unlisted_registry_evidence',ctx)
   if packet:
    check(a['snapshot_id']==packet['snapshot']['snapshot_id'],'registry_snapshot',ctx)
    for kind in ('body','extract'):check(a[kind].get('archive_sha256',a[kind].get('sha256'))==packet['snapshot'][kind+'_sha256'],'registry_'+kind,ctx)
  check(bool(r['rationale']) and bool(r['human_next_check']),'reason',ctx)
 check(d['proposal_count']==sum(r['chosen_wsdc_id'] is not None for r in d['proposals']),'proposal_total',p.name)
 check(d['abstention_count']==sum(r['chosen_wsdc_id'] is None for r in d['proposals']),'abstention_total',p.name)
for sid,n in seen.items():check(n==1,'duplicate_subject',sid)
coverage={k:sorted(i for i,s in enumerate(ss) if s['sample_id'] in seen) for k,ss in streams.items()}
for k,ix in coverage.items():check(ix==list(range(len(ix))),'coverage_gap',k)
report={'files_sha256':files,'subjects':len(seen),'coverage':{k:{'count':len(v),'first':v[0],'last':v[-1]} for k,v in coverage.items()},'counts':{str(k):v for k,v in counts.items()},'unique_artifacts':len(artifacts),'source_locators_verified':locators,'errors':errors}
Path('/tmp/h17-sidecar-audit.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
