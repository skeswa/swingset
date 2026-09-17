from pathlib import Path
import hashlib,json,sqlite3,subprocess,datetime
source=Path('/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source')
r=json.loads((source/'extension-source.json').read_bytes())
actual={str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() and p!=source/'extension-source.json'}
assert actual==set(r['files']), (len(actual),len(r['files']))
for n,h in r['files'].items(): assert hashlib.sha256((source/n).read_bytes()).hexdigest()==h,n
c=sqlite3.connect('file:/var/lib/swingset/state.sqlite?mode=ro',uri=True)
tables=[x[0] for x in c.execute("select name from sqlite_master where type='table' and name like '%schema%'")]
units=['swingset-'+x+'.'+y for x in ['cycle','backup','summary'] for y in ['service','timer']]
print(json.dumps(dict(checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_verified=True,source_files=len(r['files']),source_receipt_sha256=hashlib.sha256((source/'extension-source.json').read_bytes()).hexdigest(),active_system=str(Path('/run/current-system').resolve()),persistent_system=str(Path('/nix/var/nix/profiles/system').resolve()),hold_exists=Path('/var/lib/swingset/operator-hold').is_file(),hold_sha256=hashlib.sha256(Path('/var/lib/swingset/operator-hold').read_bytes()).hexdigest(),units={u:subprocess.run(['systemctl','is-active',u],capture_output=True,text=True).stdout.strip() for u in units},schema_tables={t:[list(row) for row in c.execute('select * from '+t)] for t in tables})))
