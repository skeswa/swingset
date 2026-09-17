"""Activate the reviewed H16 system after actual scratch acceptance, retaining holds."""
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path('/nix/store/z689qy41inndill3d92ym8im852x3649-source')
OLD = Path('/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8')
NEW = Path('/nix/store/sx7lpr80cx0n9vzsi3cwz9nxawqc4p1i-nixos-system-swingset-lxc-25.11.20260630.b6018f8')
OPS = Path('/var/lib/swingset/operations/h16-event-preservation-release-20260916')
UNITS = [f'swingset-{name}.{kind}' for name in ('cycle', 'backup', 'summary') for kind in ('service', 'timer')]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def states():
    return {unit: subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'], capture_output=True, text=True, check=True).stdout.strip() for unit in UNITS}

def write_new(path, value):
    with path.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())

assert Path('/run/current-system').resolve() == OLD
assert Path('/nix/var/nix/profiles/system').resolve() == OLD
assert Path('/var/lib/swingset/operator-hold').is_file()
assert all(state == 'inactive' for state in states().values())
assert sha(SOURCE / 'h16-source.json') == '0c3a391aca5c32381ab10daa22adfdde44cda76ab26b68df002f9822ad4945fb'
assert str(SOURCE) in (NEW / 'etc/systemd/system/swingset-cycle.service').read_text()
preflight = json.loads((OPS / 'preflight.json').read_bytes())
assert preflight['preflight_passed'] is True
assert preflight['gate_sha256'] == sha(OPS / 'gate.json')
assert preflight['gate']['source_receipt_sha256'] == sha(SOURCE / 'h16-source.json')
build = json.loads((OPS / 'scratch-build.json').read_bytes())
audit = json.loads((OPS / 'scratch-audit.json').read_bytes())
assert build['passed'] and build['semantic_publication_preflight'] and build['source'] == str(SOURCE)
assert audit['passed'] and audit['checks'] and all(value is True for value in audit['checks'].values())
assert (audit['state'], audit['candidate'], audit['candidate_id'], audit['manifest_hash']) == (build['state'], build['candidate'], build['candidate_id'], build['manifest_hash'])
assert not (OPS / 'deployment.json').exists()
report = {'source': str(SOURCE), 'source_receipt_sha256': sha(SOURCE / 'h16-source.json'), 'previous_system': str(OLD), 'system': str(NEW), 'started_at': datetime.now(UTC).isoformat(), 'passed': False, 'evidence': {name: sha(OPS / name) for name in ('preflight.json', 'gate.json', 'scratch-build.json', 'scratch-audit.json')}, 'driver_sha256': sha(Path(__file__))}
write_new(OPS / 'deployment-intent.json', report)
activation_succeeded = False
try:
    subprocess.run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', str(NEW)], check=True)
    subprocess.run([str(NEW / 'bin/switch-to-configuration'), 'switch'], check=True)
    activation_succeeded = True
except BaseException as error:
    report['activation_error'] = type(error).__name__
    raise
finally:
    try:
        subprocess.run(['systemctl', 'stop', *UNITS], check=True)
        report.update(active_system=str(Path('/run/current-system').resolve()), persistent_system=str(Path('/nix/var/nix/profiles/system').resolve()), unit_states=states())
        report['passed'] = activation_succeeded and report['active_system'] == report['persistent_system'] == str(NEW) and all(state == 'inactive' for state in report['unit_states'].values()) and Path('/var/lib/swingset/operator-hold').is_file()
    except BaseException as error:
        report['hold_or_verification_error'] = type(error).__name__
        report['passed'] = False
        raise
    finally:
        report['finished_at'] = datetime.now(UTC).isoformat()
        report['activation_commands_succeeded'] = activation_succeeded
        write_new(OPS / 'deployment.json', report)
assert report['passed']
print(json.dumps(report, indent=2))
