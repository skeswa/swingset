"""Prepare exact score-PDF CDX query proposal from retained audited HTML only."""
from pathlib import Path
from urllib.parse import urlencode, urljoin, parse_qs, urlsplit
from datetime import UTC, datetime
import hashlib
import json
from selectolax.parser import HTMLParser

root=Path.cwd()
out=root/'journal/evidence/admission/dcn-score-pdf-lookup-proposal-2026-09-17'
assert not out.exists()
body_path=Path('journal/evidence/admission/dcn-results-body-2026-09-17/quarantine/bodies/487b38c2ff264e62bf2e6de8e2b24cd5aff2ceab40856a56c1ac8539a4aad573')
audit_path=Path('journal/evidence/admission/dcn-results-body-2026-09-17/independent-review-001/checks.json')
receipt_path=Path('journal/evidence/admission/dcn-results-body-2026-09-17/quarantine/receipt.json')
state_path=Path('journal/evidence/admission/dcn-results-body-2026-09-17/production-after.json')
def sha(path):return hashlib.sha256((root/path).read_bytes()).hexdigest()
audit=json.loads((root/audit_path).read_bytes());receipt=json.loads((root/receipt_path).read_bytes());state=json.loads((root/state_path).read_bytes())
assert audit['passed'] and audit['body_sha256']==sha(body_path)
body=(root/body_path).read_bytes();tree=HTMLParser(body)
original=receipt['targets'][0]['original_url']
canonical=tree.css_first('link[rel="canonical"]').attributes['href']
heading=tree.css_first('#resultsDownloadZone h3').text(strip=True)
assert heading=="Jack'n'Jill Newcomer"
links=[]
for a in tree.css('a[href]'):
 href=a.attributes['href']
 if '/roundscores/' not in href:continue
 parent=a.parent
 while parent is not None and 'row' not in parent.attributes.get('class','').split():parent=parent.parent
 assert parent is not None
 tables=[]
 for table in parent.css('table'):
  # HTML5 foster parenting moves the source's invalid table/legend nesting.
  legend=table.parent.css_first('legend')
  assert legend is not None
  tables.append(dict(legend=legend.text(strip=True),headers=[th.text(strip=True) for th in table.css('th')],data_rows=len(table.css('tr'))-1))
 links.append(dict(printed_href=href,original_url=urljoin(original,href),link_text=a.text(strip=True),container_tables=tables))
assert [(x['printed_href'],[t['legend'] for t in x['container_tables']]) for x in links]==[('/eventdirector/en/roundscores/3451330.pdf',['Finals']),('/eventdirector/en/roundscores/3451331.pdf',['Prelims - leaders','Prelims - followers'])]
context=dict(format='exact-dcn-score-pdf-locator-context-v1',recorded_at=datetime.now(UTC).isoformat(),body_path=str(body_path),body_sha256=sha(body_path),independent_acquisition_review=str(audit_path),independent_acquisition_review_sha256=sha(audit_path),acquisition_receipt=str(receipt_path),acquisition_receipt_sha256=sha(receipt_path),observed_memento=receipt['targets'][0]['captured_at'],requested_original_url=original,printed_canonical_url=canonical,printed_title=tree.css_first('title').text(strip=True),printed_desktop_heading=' '.join(tree.css_first('h1').text(separator=' ',strip=True).split()),selected_contest=heading,locators=links,limits=['The source prints these two URLs under the selected Newcomer contest. Other contests are only selectors, not captured results.','Finals has9 rows; prelim leaders table is empty and followers table has8 rows. These HTML counts do not prove complete participation, marks, promotions, or PDF semantics.','Canonical URL adds the source-printed2018 slug; no alias or alternate query is authorized.','This is offline locator evidence, not PDF capture evidence, source-kind admission, or year acceptance.'],network_requests=0)
queries=[]
for identifier,link in zip(('dcn-riga-newcomer-finals-3451330','dcn-riga-newcomer-prelims-3451331'),links,strict=True):
 params=[('url',link['original_url']),('matchType','exact'),('from','2018'),('to','2026'),('filter','statuscode:200'),('collapse','digest')]
 probe='https://web.archive.org/cdx/search/cdx?'+urlencode(params+[('showNumPages','true')])
 page='https://web.archive.org/cdx/search/cdx?'+urlencode(params+[('output','json'),('fl','timestamp,original,digest,mimetype,length'),('page','0')])
 queries.append(dict(id=identifier,source='dcn',original_url=link['original_url'],printed_href=link['printed_href'],source_container_tables=link['container_tables'],probe_url=probe,page0_url=page,max_pages=1,page0_condition='Only if this exact URL probe reports one or more pages; further pages remain unexamined.'))
proposal=dict(format='exact-dcn-score-pdf-cdx-proposal-v1',status='proposed_requires_separate_owner_decision',purpose='Locate Archive capture metadata for the two score-PDF URLs printed in the independently audited Riga Newcomer results HTML. No PDF or archived body is requested.',locator_evidence=dict(path=str(body_path),sha256=sha(body_path),independent_review=str(audit_path),independent_review_sha256=sha(audit_path),acquisition_receipt=str(receipt_path),acquisition_receipt_sha256=sha(receipt_path),context_path=str(out.relative_to(root)/'locator-context.json')),metadata_queries=queries,query_semantics='Exact original URLs only, captures2018–2026, successful HTTP200 captures collapsed by digest. No MIME filter: retain actual CDX mimetype for independent review instead of assuming the unacquired PDF response type. No wildcard, alternate scheme/host/slug, session suffix, range expansion or additional query.',limits=dict(metadata_requests=4,metadata_queries=2,max_pages_per_query=1,total_http_requests_including_robots=5,robots_requests=1,max_redirects=0,archived_body_requests=0,pdf_requests=0,origin_requests=0,additional_queries=0,automatic_retries=0,automatic_alternatives=0,automatic_child_requests=0,max_received_bytes_per_response=2097152,max_total_received_bytes=8388608,max_elapsed_seconds=900,min_completion_to_dispatch_seconds=10,max_in_flight=1,shared_archive_daily_request_budget=200),byte_measurement='Response body bytes exposed by the bounded streaming consumer; transport buffering is not measured. Total8MiB is a stricter aggregate ceiling than five full2MiB responses; stop when remaining capacity cannot fund a request. Incomplete responses keep their reservation; no sliced chunks or extra EOF probe.',execution_gates=['Specific owner decision for this exact proposal; D0069 authorized a different completed metadata lookup and D0073 authorized only the acquired Riga HTML body.','New independently reviewed fixed-manifest two-query runner and fresh single-use metadata quarantine; existing one-query runner cannot execute this proposal unchanged.','Bind exact then-current source/runtime/schema/system/public baseline and retained locator/audit hashes; prepare against frozen003 schema28 unless a later runtime is separately reviewed.','Coordinator serialization under writer/control gates with operator hold and six ordinary units inactive. Preserve H13 controls, source/host gates, robots, cooldowns, one in flight, project User-Agent, conditional requests where supported, and at least10seconds from completed exchange to next dispatch using monotonic time or any stricter active rule.','Recheck and debit actual shared Archive UTC-day capacity at execution. No budget increase, refund, spacing reset or production initialization.','Stop without retries, redirects or alternative queries on unexpected response, hold/control/host refusal, malformed page count, response/aggregate/time capacity or robots denial. An empty probe skips only its own page0; the other exact approved query may proceed through ordinary gates.'],reference_runtime=dict(source='/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source',source_receipt_sha256='60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6',schema=28,published_commit='2a6c7dc744fb36eabb5163c0a527d787d3721f4f'),last_observed_budget=dict(path=str(state_path),sha256=sha(state_path),observed_at=state['observed_at'],requests_used=15,request_budget=200,remaining_at_observation=185,capacity_reserved=0),afterward='Independently audit retained metadata. Any PDF acquisition requires a separate exact capture proposal and owner decision. Missing rows or unexamined pages remain gaps; no guessed IDs, observations, watches, source-kind activation, historical-year acceptance or public publication follows.',network_requests_during_preparation=0)
out.mkdir(parents=True)
(out/'locator-context.json').write_text(json.dumps(context,indent=2)+'\n')
proposal['locator_evidence']['context_sha256']=sha(out.relative_to(root)/'locator-context.json')
(out/'proposal.json').write_text(json.dumps(proposal,indent=2)+'\n')
(out/'prepare.py').write_bytes(Path(__file__).read_bytes())
assert len(queries)==2
for q in queries:
 for field in ('probe_url','page0_url'):
  parsed=parse_qs(urlsplit(q[field]).query)
  assert parsed['url']==[q['original_url']] and parsed['matchType']==['exact']
  assert parsed['filter']==['statuscode:200']
report=dict(format='exact-dcn-score-pdf-proposal-preparation-v1',passed=True,body_sha256=sha(body_path),proposal_sha256=sha(out.relative_to(root)/'proposal.json'),context_sha256=sha(out.relative_to(root)/'locator-context.json'),script_sha256=sha(out.relative_to(root)/'prepare.py'),printed_locator_count=2,metadata_url_count=4,network_requests=0,production_operations=0)
(out/'preparation-checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
