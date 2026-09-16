"""Finite local orchestration of already-authorized frozen scratch-only workers."""
import json,subprocess,time
from pathlib import Path
PYTHON=str(Path('.venv/bin/python').resolve())
operation='/tmp/h16_closure_replay_operation_20260914.py'
monitor='/tmp/monitor_h16_closure_replay_20260914.py'
log=Path('research/verification/h16-closure-replay-resource-samples-20260914.jsonl')
expected_bundle='558bb5f4e88b47fe80a691254d3b21ac9e491296e80bb18f4599b64a88bfe9c0'
last_report=0.0
for number in range(8,13):
 while True:
  summary=subprocess.check_output([PYTHON,monitor,str(number)],text=True)
  sample=json.loads(log.read_text().splitlines()[-1]); receipt=sample['receipt']; service=sample['service']
  if time.monotonic()-last_report>=45:
   print(summary.strip(),flush=True);last_report=time.monotonic()
  if receipt and receipt.get('input_bundle_hash',expected_bundle)!=expected_bundle:raise RuntimeError('bundle changed')
  if any(key not in {'running','succeeded'} for key in sample['attempt_outcomes']):raise RuntimeError('non-successful work attempt')
  if service['SubState']=='exited' and service['MainPID']=='0':
   if service['Result']!='success' or service['ExecMainStatus']!='0':raise RuntimeError('worker failed')
   if sample['attempt_outcomes'].get('running',0):raise RuntimeError('running attempt after exit')
   if receipt['status'] not in {'bounded_stop','current'}:raise RuntimeError('worker did not progress safely')
   subprocess.run([PYTHON,operation,'retain',str(number)],check=True)
   print('RETAINED '+str(number)+' '+receipt['status'],flush=True)
   if receipt['status']=='current':
    if receipt.get('unfinished_by_scope'):raise RuntimeError('current receipt with unfinished work')
    print('CURRENT; no further invocation',flush=True)
    raise SystemExit(0)
   if number==12:raise RuntimeError('finite supervisor limit reached without current proof')
   subprocess.run([PYTHON,operation,'launch',str(number+1)],check=True)
   break
  if service['ActiveState'] in {'failed','inactive'} or receipt.get('status') in {'interrupted','no_runnable_progress'}:raise RuntimeError('worker requires review')
  time.sleep(20)
