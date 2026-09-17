"""Independent audit of retained exports only; no network or live state access."""
import hashlib
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path.cwd()
BASE = ROOT / 'journal/evidence/admission/dcn-results-body-2026-09-17'
OUTPUT = BASE / 'independent-review-001'
assert not OUTPUT.exists()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def read(name):
    return json.loads((BASE / name).read_bytes())

inputs = {str(p.relative_to(ROOT)): sha(p) for p in BASE.rglob('*') if p.is_file()}
receipt = read('quarantine/receipt.json')
manifest = read('quarantine/manifest.json')
authorization = read('quarantine/authorization.json')
build = read('packet/build-receipt.json')
gate = read('packet/execution-gate.json')
preparation = read('packet/preparation-receipt.json')
after = read('production-after.json')
admissions = read('execution-admissions.json')
review_path = ROOT / 'journal/evidence/admission/dcn-results-body-runner-2026-09-17/independent-review-001/checks.json'
review = json.loads(review_path.read_bytes())
assert review['passed'] and not review['remaining_blockers']
assert build['driver_sha256'] == review['driver_sha256'] == '103206f27c25f45b43807ef4ab467392b411e9ba51355e4de6ef11a1047227dd'
assert build['helper_manifest_sha256'] == review['helper_manifest_sha256'] == '20ba00fb5e54186edc83eb95a9e74f3fb57e0d1bf45804b1a84191282b132508'
for name, digest in build['files'].items():
    path = BASE / 'packet' / name
    assert not path.is_symlink() and sha(path) == digest
assert canonical(manifest) == receipt['manifest_canonical_sha256'] == build['manifest_canonical_sha256'] == authorization['manifest_canonical_sha256']
assert canonical(authorization) == receipt['authorization_sha256']
assert authorization == read('packet/authorization.json')
assert sha(BASE / 'packet/authorization.json') == preparation['authorization_sha256']
assert sha(BASE / 'packet/execution-gate.json') == preparation['gate_sha256']
assert gate['driver_sha256'] == build['driver_sha256']
assert gate['helper_manifest_sha256'] == build['helper_manifest_sha256']
assert gate['source_receipt_sha256'] == build['runtime_receipt_sha256'] == '60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6'
assert gate['schema'] == after['schema'] == preparation['state_observation']['schema'] == 28
assert gate['source'] == '/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source'
proposal_path = ROOT / 'journal/evidence/admission/dcn-results-body-proposal-2026-09-17/proposal.json'
proposal = json.loads(proposal_path.read_bytes())
assert sha(proposal_path) == build['proposal_sha256'] == manifest['proposal_sha256'] == '17dea52f53b41b04093f4582a94da0f58afbe808408304f9681e2f3750575483'
assert manifest['approval_reference'] == authorization['owner_decision_reference'] == 'journal/decisions/0073-approve-exact-riga-results-html-fixture.md'
assert authorization['approval'] == 'approved_exact_riga_results_html_only'
assert len(manifest['targets']) == len(receipt['targets']) == 1
assert not manifest['metadata_queries']
target = manifest['targets'][0]
expected = proposal['target']
assert target['original_url'] == expected['original_url']
assert target['capture_timestamp'] == expected['capture'] == '20190719204919'
assert target['replay_url'] == expected['archive_url']
assert manifest['limits']['total_http_requests_including_redirects_and_robots'] == 2
assert manifest['limits']['max_received_bytes_per_response'] == 2 * 1024**2
assert manifest['limits']['max_total_received_bytes'] == 4 * 1024**2
assert manifest['limits']['minimum_gap_seconds'] == 10
assert manifest['limits']['max_in_flight'] == 1
for field in ('max_archive_redirects_per_body','cdx_requests','source_origin_requests','pdf_requests','automatic_retries','automatic_alternate_captures','automatic_child_requests'):
    assert manifest['limits'][field] == 0
requests = receipt['requests']
assert len(requests) == 2
assert [r['url'] for r in requests] == ['https://web.archive.org/robots.txt', expected['archive_url']]
assert [r['http_status'] for r in requests] == [404, 200]
assert [r['classification'] for r in requests] == ['Gone', 'Ok']
assert receipt['status'] == 'captured_pending_independent_review'
for request in requests:
    body = BASE / 'quarantine/bodies' / request['body_sha256']
    assert sha(body) == request['body_sha256']
    assert body.stat().st_size == request['body_bytes'] == request['budget_charged_bytes']
    assert request['complete'] and 0 < request['body_bytes'] <= 2 * 1024**2
    assert request['request_day'] == '2026-09-17'
    assert request['spacing_basis'] == 'closed_exchange_completion_floor'
    dispatch = datetime.fromisoformat(request['transport_dispatched_at'])
    assert datetime.fromisoformat(authorization['execution_window']['starts_at']) <= dispatch <= datetime.fromisoformat(authorization['execution_window']['expires_at'])
    assert datetime.fromisoformat(request['issued_at']) <= dispatch <= datetime.fromisoformat(request['exchange_completed_at'])
    assert request['exchange_completed_elapsed_seconds'] >= request['transport_dispatched_elapsed_seconds']
    assert request['next_dispatch_not_before_elapsed_seconds'] - request['exchange_completed_elapsed_seconds'] >= 10
elapsed_gap = requests[1]['transport_dispatched_elapsed_seconds'] - requests[0]['exchange_completed_elapsed_seconds']
wall_gap = (datetime.fromisoformat(requests[1]['transport_dispatched_at']) - datetime.fromisoformat(requests[0]['exchange_completed_at'])).total_seconds()
assert elapsed_gap >= 10 and wall_gap >= 10
assert requests[1]['transport_dispatched_elapsed_seconds'] >= requests[0]['next_dispatch_not_before_elapsed_seconds']
assert (datetime.fromisoformat(receipt['finished_at']) - datetime.fromisoformat(receipt['started_at'])).total_seconds() <= 900
assert sum(r['body_bytes'] for r in requests) == receipt['received_bytes'] == 16839
assert requests[1]['body_bytes'] == 16693
observed = parsedate_to_datetime(requests[1]['headers']['memento-datetime']).astimezone(UTC)
assert observed == datetime.strptime(expected['capture'], '%Y%m%d%H%M%S').replace(tzinfo=UTC)
assert receipt['targets'][0] == dict(archive_url=expected['archive_url'],body_sha256=requests[1]['body_sha256'],captured_at=observed.isoformat(),id=target['id'],interpretation='unreviewed_quarantine_body',original_url=expected['original_url'],requested_archive_url=expected['archive_url'])
assert requests[1]['body_sha256'] == '487b38c2ff264e62bf2e6de8e2b24cd5aff2ceab40856a56c1ac8539a4aad573'
assert len(admissions) == 4 and len({a['action_id'] for a in admissions}) == 4
assert all(a['state'] == 'settled' and a['host'] == 'web.archive.org' for a in admissions)
logical = [a for a in admissions if a['action_kind'] == 'fetch']
paid_admissions = [a for a in admissions if a['action_kind'] == 'request']
assert len(logical) == 2 and all(a['outcome'] == 'completed' for a in logical)
assert len(paid_admissions) == 2
for admission, request in zip(paid_admissions, requests, strict=True):
    assert admission['state'] == 'settled' and admission['action_kind'] == 'request'
    assert admission['host'] == 'web.archive.org' and admission['outcome'] == request['classification']
    assert admission['control_revision'] == 2
    assert datetime.fromisoformat(admission['admitted_at']) <= datetime.fromisoformat(request['transport_dispatched_at']) <= datetime.fromisoformat(admission['settled_at'])
old = preparation['state_observation']
paid = next(row for row in after['archive_budget'] if row['day'] == old['archive_day'])
assert old['archive_requests'] == 13 and paid['requests'] == 15
assert paid['bytes'] - old['archive_bytes'] == receipt['received_bytes']
assert paid['requests'] <= manifest['limits']['shared_archive_daily_request_budget'] == 200
assert after['hold_sha256'] == '965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638'
assert after['active_system'] == after['persistent_system'] == '/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8'
prior_export = ROOT / 'journal/evidence/admission/dcn-results-lookup-2026-09-17/production-after.json'
assert after['public_acknowledgment'] == json.loads(prior_export.read_bytes())['public_acknowledgment']
assert after['hold'] and len(after['units']) == 6 and set(after['units'].values()) == {'inactive'}
assert after['baseline'] == gate['baseline_path'] == old['baseline_path']
assert after['public_acknowledgment']['commit'] == '2a6c7dc744fb36eabb5163c0a527d787d3721f4f'
assert after['public_acknowledgment']['candidate_id'] == 'cand_8f31cad7226643ae'
assert receipt['production_facts_or_watches_created'] == 0
assert inputs == {str(p.relative_to(ROOT)):sha(p) for p in BASE.rglob('*') if p.is_file()}
OUTPUT.mkdir()
report = dict(format='dcn-results-body-capture-independent-review-v1',recorded_at=datetime.now(UTC).isoformat(),reviewer='eligible_time',passed=True,files=inputs,runner_review_sha256=sha(review_path),proposal_sha256=sha(proposal_path),request_count=2,body_bytes=16693,accounted_response_body_bytes=16839,observed_memento=observed.isoformat(),body_sha256=requests[1]['body_sha256'],completion_to_dispatch_seconds=elapsed_gap,completion_to_dispatch_wall_seconds=wall_gap,paid_requests_before=13,paid_requests_after=15,paid_bytes_before=old['archive_bytes'],paid_bytes_after=paid['bytes'],all_admissions_settled=True,logical_fetch_admissions=2,paid_request_admissions=2,source_schema=28,hold_preserved=True,six_ordinary_units_inactive=True,baseline_unchanged=True,production_facts_or_watches_created=0,limits=['Audit of retained coordinator exports; no fresh live query.','Byte metric is exposed response-body bytes; transport buffering is not measured.','Spacing measured at wrapper dispatch/completion boundaries; socket timestamps are not claimed.','Quarantined HTML is not a parsed/accepted source kind or year, score-PDF authority, watch, or public publication.'],network_requests_by_reviewer=0,production_operations_by_reviewer=0)
(OUTPUT/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
(OUTPUT/'audit.py').write_bytes(Path(__file__).read_bytes())
print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
