import datetime,json,subprocess,sys
from pathlib import Path
number=int(sys.argv[1]); unit=f'swingset-h16-closure-replay-{number:03d}.service'
remote='''import datetime,json,pathlib,sqlite3,subprocess
number=NUMBER
unit=f"swingset-h16-closure-replay-{number:03d}.service"
p=pathlib.Path(f"/var/tmp/swingset-h16-closure-replay-{number:03d}.json")
raw=subprocess.run(["systemctl","show",unit,"--property=ActiveState","--property=SubState","--property=Result","--property=ExecMainStatus","--property=MemoryCurrent","--property=MemoryPeak","--property=CPUUsageNSec","--property=MainPID","--property=InvocationID"],capture_output=True,text=True,check=True).stdout
result={"at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"unit":unit,"service":dict(line.split("=",1) for line in raw.splitlines() if "=" in line),"receipt":json.loads(p.read_bytes()) if p.exists() else None}
c=sqlite3.connect("file:/var/tmp/swingset-h16-closure-replay/state.sqlite?mode=ro",uri=True)
try:
 result["materialized"]={f"{a}/{b}":n for a,b,n in c.execute("SELECT stage,unit_kind,count(*) FROM derivation_scopes WHERE materialized_generation_id IS NOT NULL GROUP BY stage,unit_kind")}
 result["attempt_outcomes"]={a:n for a,n in c.execute("SELECT outcome,count(*) FROM work_attempts GROUP BY outcome")}
finally:c.close()
print(json.dumps(result))
'''.replace('NUMBER',str(number))
output=subprocess.run(['orb','-m','swingset','-u','root','/var/lib/swingset/venv/bin/python','-c',remote],capture_output=True,text=True,check=True).stdout
value=json.loads(output)
p=Path('research/verification/h16-closure-replay-resource-samples-20260914.jsonl')
if p.is_symlink():raise ValueError('refuse symlink log')
with p.open('a') as stream:stream.write(json.dumps(value,sort_keys=True)+'\n')
print(json.dumps({'at':value['at'],'unit':value['unit'],'service':value['service'],'materialized':value['materialized'],'attempt_outcomes':value['attempt_outcomes'],'receipt':{k:(value['receipt'] or {}).get(k) for k in ('status','elapsed_seconds','completed','generation_count','unfinished_by_scope','error_type')}},sort_keys=True))
