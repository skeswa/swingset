# Frozen proof-source mirror preparation

Executed on 2026-09-16. No tests, source assembly, Nix build, worker jobs,
deployment, initialization, or publication were run by this verification.

The initial command used `orb -m swingset python3` and failed with exit 127:
`python3` is not on the VM login PATH. It did not execute the script or copy files.
The explicit frozen Python path below succeeded:

```sh
orb -m swingset \
  /nix/store/s5rij9y3vb4gxbh0iqz3k0ndmsac7h02-python3-3.12.13/bin/python3.12 \
  /Users/skeswa/repos/skeswa/swingset/journal/evidence/releases/h16-validation-2026-09-16/verify-frozen-and-mirror.py
```

The script verifies source and service bindings before calling:

```python
shutil.copytree(SOURCE, MIRROR, copy_function=shutil.copyfile)
```

`SOURCE` is `/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source`.
`MIRROR` is `/Users/skeswa/.cache/swingset-h16-proof-tests-20260916`, a shared
host path. Existing output is refused. Directories receive mode 0755 and files
0644 so tests can use a writable mirror. All 636 file hashes match the immutable
source; no files or caches were excluded. A second read from host Python verified
all 636 hashes against the VM verification receipt. Tests had not started during
either comparison.

- VM verification: `frozen-source-and-mirror-verification.json`, SHA-256
  `89ac578930ca6822a1c4ad828b91d726db1f2f72e2e276742e8138e47c995111`.
- Host verification: `host-mirror-verification.json`, SHA-256
  `05613f27673eb021c2652f5c2fb3187c81f265936c507b94c10418b1a246bdba`.

After tests, compare every source file again and exclude only explicitly named
generated cache paths from the extra-file inventory. The mirror currently has
no virtual environment; use the coordinator's existing verified interpreter.
