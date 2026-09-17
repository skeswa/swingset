"""Give the backup service read access to 33 exact H16 receipts and one directory."""

import fcntl
import hashlib
import json
import os
import pwd
import stat
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/swingset")
OP = STATE / "operations/h16-event-preservation-release-20260916"
OUTPUT = STATE / "operations/v2-continuation-20260917/backup-read-access-repair.json"


def fingerprint(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0:
        raise ValueError(f"unexpected file ownership/type: {path}")
    value = dict(
        inode=info.st_ino,
        mtime_ns=info.st_mtime_ns,
        uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
        gid=info.st_gid,
    )
    if path.is_file():
        with path.open("rb") as stream:
            value["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    elif not path.is_dir():
        raise ValueError("not an ordinary receipt or directory")
    return value


def main():
    os.umask(0o077)
    paths = [STATE / "operations/h16-performance-20260913"]
    paths += [
        OP / name
        for name in ("deployment-intent.json", "deployment.json", "initialization-anchor-002.json")
    ]
    supervision = OP / "supervision-reviewed-002"
    paths += [supervision / name for name in ("preflight.json", "final.json")]
    for number in range(1, 8):
        paths += [
            supervision / name
            for name in (
                f"batch-{number:03}-start.json",
                f"batch-{number:03}-final.json",
                f"initializer-{number:03}.resources.jsonl",
                f"initializer-{number:03}.log",
            )
        ]
    if OUTPUT.exists() or not (STATE / "operator-hold").is_file():
        raise ValueError("new receipt and existing hold required")
    for kind in ("cycle", "backup", "summary"):
        for suffix in ("service", "timer"):
            if (
                subprocess.check_output(
                    [
                        "systemctl",
                        "show",
                        f"swingset-{kind}.{suffix}",
                        "-p",
                        "ActiveState",
                        "--value",
                    ],
                    text=True,
                ).strip()
                != "inactive"
            ):
                raise ValueError("ordinary unit active")
    group = pwd.getpwnam("swingset").pw_gid
    with ExitStack() as stack:
        for name in ("state.lock", "control.lock"):
            handle = stack.enter_context((STATE / name).open("a+b"))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = {str(path): fingerprint(path) for path in paths}
        for path in paths:
            value = before[str(path)]
            if value["mode"] != (0o700 if path.is_dir() else 0o600) or value["gid"] != 0:
                raise ValueError(f"unexpected original permissions: {path}")
        changes = []
        with OUTPUT.open("x") as receipt:
            try:
                for path in paths:
                    original = before[str(path)]
                    os.chown(path, -1, group)
                    os.chmod(
                        path,
                        original["mode"] | stat.S_IRGRP | (stat.S_IXGRP if path.is_dir() else 0),
                    )
                    after = fingerprint(path)
                    for field in ("inode", "mtime_ns", "uid", "sha256"):
                        if after.get(field) != original.get(field):
                            raise ValueError(f"protected receipt changed: {path}")
                    changes.append(dict(path=str(path), before=original, after=after))
            finally:
                json.dump(
                    dict(
                        at=datetime.now(UTC).isoformat(), passed=len(changes) == 34, changes=changes
                    ),
                    receipt,
                    indent=2,
                )
                receipt.write("\n")
    # This new operation receipt must itself be readable by the checkpoint owner.
    os.chown(OUTPUT, -1, group)
    os.chmod(OUTPUT, 0o640)


if __name__ == "__main__":
    main()
