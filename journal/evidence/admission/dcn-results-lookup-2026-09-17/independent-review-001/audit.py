import json, hashlib
from pathlib import Path
from datetime import datetime, timezone
p=Path('journal/evidence/admission/dcn-results-lookup-2026-09-17')
read=lambda n:json.loads((p/n).read_bytes())
sha=lambda b:hashlib.sha256(b).hexdigest()
canon=lambda j:json.dumps(j,sort_keys=True,separators=(',',':')).encode()
r=read('quarantine/receipt.json');m=read('quarantine/manifest.json');a=read('quarantine/authorization.json');b=read('packet/build-receipt.json');g=read('packet/execution-gate.json');prep=read('packet/preparation-receipt.json');after=read('production-after.json');adm=read('execution-admissions.json')
inputs={str(f):sha(f.read_bytes()) for f in p.rglob('*') if f.is_file()}
assert b['driver_sha256']=='606b95e653e212d2ddb88afea3da77bb4da606068ce609de45d93cdce8b49d80'
assert b['helper_manifest_sha256']=='09b6ee0bf55c83bbd3ad29fb42b2d5dbee0050ce4d7cf81ce7e74b569ab92e71'
for f,h in b['files'].items(): assert sha((p/'packet'/f).read_bytes())==h,(f,h)
assert sha(canon(a))==r['authorization_sha256']
assert read('packet/authorization.json')==a
assert sha((p/'packet/authorization.json').read_bytes())==prep['authorization_sha256']
assert sha(canon(m))==r['manifest_canonical_sha256']==b['manifest_canonical_sha256']
assert sha((p/'packet/execution-gate.json').read_bytes())==prep['gate_sha256']
assert g['schema']==after['schema']==28
assert g['source_receipt_sha256']==b['runtime_receipt_sha256']=='60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6'
assert g['driver_sha256']==b['driver_sha256'] and g['helper_manifest_sha256']==b['helper_manifest_sha256']
q=m['metadata_queries'][0]
assert [x['url'] for x in r['requests']]==['https://web.archive.org/robots.txt',q['probe_url'],q['page0_url']]
assert not m['targets'] and not r['targets'] and r['production_facts_or_watches_created']==0
assert [x['http_status'] for x in r['requests']]==[404,200,200]
for x in r['requests']:
 body=(p/'quarantine/bodies'/x['body_sha256']).read_bytes()
 assert sha(body)==x['body_sha256'] and len(body)==x['body_bytes']==x['budget_charged_bytes']
 assert x['complete'] and x['body_bytes']<=m['limits']['max_received_bytes_per_response']
 assert x['request_day']=='2026-09-17' and x['spacing_basis']=='closed_exchange_completion_floor'
 assert datetime.fromisoformat(a['execution_window']['starts_at'])<=datetime.fromisoformat(x['transport_dispatched_at'])<=datetime.fromisoformat(a['execution_window']['expires_at'])
gaps=[y['transport_dispatched_elapsed_seconds']-x['exchange_completed_elapsed_seconds'] for x,y in zip(r['requests'],r['requests'][1:])]
assert all(x>=10 for x in gaps)
assert sum(x['body_bytes'] for x in r['requests'])==r['received_bytes']==369
assert len(adm)==3 and all(x['state']=='settled' and x['action_kind']=='request' and x['host']=='web.archive.org' for x in adm)
assert [x['outcome'] for x in adm]==['Gone','Ok','Ok']
paid=after['archive_budget'][0];old=prep['state_observation']
assert paid['requests']-old['archive_requests']==3 and paid['bytes']-old['archive_bytes']==369
assert after['hold'] and set(after['units'].values())=={'inactive'} and len(after['units'])==6
assert after['baseline']==g['baseline_path'] and after['public_acknowledgment']['commit']=='2a6c7dc744fb36eabb5163c0a527d787d3721f4f'
rows=json.loads((p/'quarantine/bodies'/r['cdx_page0_sha256']).read_bytes())
assert len(rows)==2 and rows[0]==['timestamp','original','digest','mimetype','length']
row=dict(zip(rows[0],rows[1]))
assert row=={'timestamp':'20190719204919','original':q['original_url'],'digest':'4EU7SEVHMTLTWY2WZZWXHJHUBZOO2I7T','mimetype':'text/html','length':'4805'}
assert int((p/'quarantine/bodies'/r['requests'][1]['body_sha256']).read_text())==r['cdx_pages_reported']==1
assert r['cdx_further_pages_unexamined']==0
out=p/'independent-review-001';out.mkdir()
report={'format':'dcn-results-lookup-capture-independent-review-v1','recorded_at':datetime.now(timezone.utc).isoformat(),'reviewer':'history_gates','passed':True,'files':inputs,'requests':3,'accounted_response_body_bytes':369,'completion_to_dispatch_seconds':gaps,'paid_requests_before':10,'paid_requests_after':13,'paid_bytes_before':2934701,'paid_bytes_after':2935070,'all_request_admissions_settled':True,'source_schema':28,'hold_preserved':True,'six_ordinary_units_inactive':True,'baseline_unchanged':True,'capture_rows':[row],'further_reported_cdx_pages':0,'production_facts_or_watches_created':0,'limits':['Independent audit of retained coordinator exports; no fresh live query.','Body byte metric is bytes exposed by iter_raw; transport buffering is not measured.','CDX length is metadata, not an acquired body size or semantic validation.','No results body, PDF, interpretation, year acceptance or source-kind activation follows.'],'network_requests_by_reviewer':0,'production_operations_by_reviewer':0}
(out/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
(out/'audit.py').write_bytes(Path(__file__).read_bytes())
proposal={'format':'exact-dcn-results-body-proposal-v1','status':'proposed_requires_separate_owner_decision','purpose':'Acquire one evidenced archived Riga results-tab HTML body for offline parsing and score-locator review. The contents and presence of score links remain unknown.','target':{'source':'dcn','kind':'results_fixture','original_url':row['original'],'capture':row['timestamp'],'archive_url':'https://web.archive.org/web/'+row['timestamp']+'id_/'+row['original'],'cdx_digest':row['digest'],'cdx_mimetype':row['mimetype'],'cdx_length_metadata':4805},'locator_evidence':{'path':str(p/'quarantine/bodies'/r['cdx_page0_sha256']),'sha256':r['cdx_page0_sha256'],'independent_review':str(out/'checks.json'),'independent_review_sha256':sha((out/'checks.json').read_bytes()),'metadata_only':True},'limits':{'total_http_requests_including_robots':2,'archived_body_requests':1,'robots_requests':1,'metadata_requests':0,'max_redirects':0,'pdf_requests':0,'origin_requests':0,'automatic_retries':0,'automatic_alternatives':0,'automatic_child_requests':0,'max_received_bytes_per_response':2097152,'max_total_received_bytes':4194304,'max_elapsed_seconds':900,'min_completion_to_dispatch_seconds':10,'max_in_flight':1,'shared_archive_daily_request_budget':200},'byte_measurement':'Response body bytes exposed by the bounded streaming consumer; transport buffering is not measured. Retain the full reservation for incomplete responses, no chunk slicing or extra EOF probe at capacity.','execution_gates':['Specific owner approval for this exact body; D0069 authorized metadata only.','New independently reviewed fixed-manifest driver bound to actual deployed schema/runtime, baseline and current state; prepared against frozen003 schema28 unless separately reviewed.','Coordinator serialization with ordinary hold, six ordinary units inactive, normal H13 controls, host/robots policy and shared paid budget; no initialization or relaxation of spacing authority.','Fresh actual remaining budget check; at least ten seconds from completed exchange to dispatch using monotonic time, or any stricter active rule.','New separate single-use quarantine; abort on redirect, failure or exhausted limit without retry or alternate request.'],'reference_runtime':{'source':'/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source','source_receipt_sha256':'60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6','schema':28,'published_commit':'2a6c7dc744fb36eabb5163c0a527d787d3721f4f'},'afterward':'Retain raw body and independently audit acquisition before offline parser review. Printed score or PDF locators remain evidence only: no requests to those URLs, no watches, observations, year acceptance, page-kind activation or publication. Additional fixtures need separate exact proposals.'}
pd=Path('journal/evidence/admission/dcn-results-body-proposal-2026-09-17');pd.mkdir();(pd/'proposal.json').write_text(json.dumps(proposal,indent=2)+'\n')
print('audit',out/'checks.json');print('proposal_sha256',sha((pd/'proposal.json').read_bytes()))
