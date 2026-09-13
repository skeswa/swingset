"""Prepare a closed WP16 source from a reviewed overlay; default inspection only.

Never migrates state, invokes pipeline code, builds Nix, fetches or activates.
Root may separately run --assemble after reviewing the exact overlay manifest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

BASE=Path('/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source')
BASE_RECEIPT_SHA='3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()

def relative(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('unsafe inventory path')
    return p

def verify(root,files):
    for name,sha in files.items():
        p=root/relative(name)
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or digest(p)!=sha:
            raise ValueError('file changed: '+name)

def assemble(plan_path,plan_sha,output=None):
    if digest(plan_path)!=plan_sha:raise ValueError('reviewed plan hash differs')
    plan=json.loads(plan_path.read_bytes());repo=Path(plan['repository']).resolve(strict=True)
    if plan['format']!='wp16-source-assembly-proposal-v1' or plan['base']!=str(BASE) or plan['base_receipt_sha256']!=BASE_RECEIPT_SHA:
        raise ValueError('wrong assembly basis')
    if digest(BASE/'h16-source.json')!=BASE_RECEIPT_SHA:raise ValueError('base receipt differs')
    base=json.loads((BASE/'h16-source.json').read_bytes())
    verify(BASE,base['files']);verify(repo,plan['overlay'])
    validation=repo/plan['validation_receipt']
    if digest(validation)!=plan['validation_receipt_sha256']:raise ValueError('validation receipt differs')
    tested=json.loads(validation.read_bytes())
    if tested['tests']['passed']!=1396:raise ValueError('expected reviewed1396-test receipt')
    if any(plan['overlay'].get(k)!=v for k,v in tested['runtime_files'].items()):raise ValueError('runtime differs from reviewed tested tree')
    if any(any(part in {'__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'} for part in Path(k).parts) or Path(k).suffix=='.pyc' for k in plan['overlay']):raise ValueError('cache in reviewed overlay')
    actual={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual!=set(base['files'])|{'h16-source.json'}:raise ValueError('base file closure differs')
    expected={**base['files'],**plan['overlay']}
    for name in plan['remove']:
        relative(name)
        if not name.startswith(('src/','tests/')):raise ValueError('unexpected removal')
        expected.pop(name,None)
    expected['provenance/h16-before-wp16.json']=BASE_RECEIPT_SHA
    # Configuration/overrides/build dependencies are inherited unchanged: no
    # source switch, reviewed alias, activation or dependency drift is introduced.
    for name,sha in base['files'].items():
        if name.startswith(('config/','overrides/','nix/','src/swingset/state/migrations/')) or name in {'flake.nix','flake.lock','pyproject.toml','uv.lock','uv.toml'}:
            if expected.get(name)!=sha:raise ValueError('protected configuration/build file differs: '+name)
    if output is None:return {'verified_only':True,'files_planned':len(expected),'output_created':False}
    output=output.absolute()
    if output.exists() or output.is_symlink() or output.parent.resolve()!=output.parent or not str(output).startswith('/var/tmp/swingset-wp16-'):
        raise ValueError('new dedicated /var/tmp WP16 assembly required')
    output.mkdir(mode=0o700)
    for name,sha in expected.items():
        dest=output/relative(name);dest.parent.mkdir(parents=True,exist_ok=True)
        src=BASE/'h16-source.json' if name=='provenance/h16-before-wp16.json' else (repo/name if name in plan['overlay'] else BASE/name)
        shutil.copyfile(src,dest)
        if digest(dest)!=sha:raise ValueError('copy changed: '+name)
        dest.chmod(0o755 if os.access(src,os.X_OK) else 0o644)
    verify(output,expected)
    actual={p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    if actual!=set(expected):raise ValueError('assembled closure differs')
    source={'format':'wp16-reviewed-source-v1','schema':15,'acquisition_enabled':False,'repairs_activated':False,'base':str(BASE),'base_receipt_sha256':BASE_RECEIPT_SHA,'reviewed':str(repo),'assembly_plan_sha256':plan_sha,'files':dict(sorted(expected.items())),'frozen_validation_pending':True,'deployment_executed':False}
    body=json.dumps(source,sort_keys=True,separators=(',',':')).encode()
    with (output/'wp16-source.json').open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
    return {'assembled':str(output),'files':len(expected),'source_receipt_sha256':hashlib.sha256(body).hexdigest(),'frozen_validation_pending':True,'deployment_executed':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--plan-sha256',required=True)
    p.add_argument('--assemble',type=Path)
    a=p.parse_args();print(json.dumps(assemble(a.plan,a.plan_sha256,a.assemble),sort_keys=True))

if __name__=='__main__':main()
