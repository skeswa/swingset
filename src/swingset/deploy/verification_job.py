"""Supervise one private graph or rollback-only profiler without its credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .import_job import ImportJob, app_state, interruptions, private_json, require

STATISTICS_SQL = """SELECT
    coalesce(sum(xact_commit+xact_rollback),0)::bigint AS transactions,
    coalesce(sum(tup_returned+tup_fetched),0)::bigint AS tuples,
    count(*) AS databases
    FROM pg_catalog.pg_stat_database WHERE left(datname,9)='sst_copy_'"""


def root_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o444
            and info.st_size <= 128 * 1024
        )
        return stream.read(128 * 1024 + 1)


def birth(pid: int) -> str:
    value = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    require(value[0] != "Z")
    return value[19]


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def memory(pid: int) -> dict[str, Any]:
    group = Path(f"/proc/{pid}/cgroup").read_text().split("0::", 1)[1].strip()
    require(group.startswith("/") and ".." not in group.split("/"))
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    events = {
        key: int(value)
        for key, value in (
            line.split() for line in (root / "memory.events").read_text().splitlines()
        )
    }
    require(events.get("oom_kill", 0) == events.get("oom_group_kill", 0) == 0)
    return {"cgroup": group, "peak": int((root / "memory.peak").read_text()), "events": events}


class VerificationJob(ImportJob):
    """A verifier owns its new graph copies; the supplied base is retained."""

    FORMAT = "swingset-attended-verification-v1"
    DEADLINE = "verification_deadline_seconds"
    DEADLINES = frozenset({86400})
    ARGV = "verifier_argv"
    ROLE = "verifier"
    CREDENTIALS = frozenset({"worker-dsn", "control-dsn", "scratch-admin-dsn"})
    PREFIX = "verification"
    OPERATION = "held whole-graph verification supervisor"
    ALARM = "no_verification_progress_for_60_minutes"
    LIMITS = "Exit0 requires separate receipt, full unit-set and unchanged-base inspection; no base cleanup."

    def __init__(self, *args: Any, review: Path, review_sha256: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        verdict = private_json(review, review_sha256)
        self.review(verdict)
        p = self.policy
        require(
            len(p["support5"]) == 5
            and len({x["id"] for x in p["support5"]}) == 5
            and str(UUID(p["database_id"])) == p["database_id"]
            and re.fullmatch(r"sst_copy_[0-9a-f]{24}_0", p["database"])
            and p["expected_unit_count"] >= 2
            and re.fullmatch(r"[0-9a-f]{64}", p["expected_unit_set_sha256"])
            and set(p["postgres_ids"]) == {"shared", "private"}
            and set(p["hold_identity"]) == {"owner", "reason", "created_at"}
            and all(isinstance(x, str) and x for x in p["hold_identity"].values())
            and type(p["shared_database_bytes"]) is int
            and p["shared_database_bytes"] > 0
            and type(p["private_base_database_bytes"]) is int
            and p["private_base_database_bytes"] > 0
        )
        protected = {
            self.platform,
            p["native_container"],
            *[x["id"] for x in p["seven_original_apps"]],
            *[x["id"] for x in p["support5"]],
        }
        require(
            self.container not in protected
            and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in protected)
        )
        require(
            p["postgres_ids"]["shared"] in {x["id"] for x in p["seven_original_apps"]}
            and p["postgres_ids"]["private"] in {x["id"] for x in p["support5"]}
        )
        self.app = Path("/var/tmp") / p["deployment"]
        self.incarnation: tuple[int, str] | None = None
        self.identity_seen = False
        self.result["review_sha256"] = review_sha256

    def review(self, verdict: dict[str, Any]) -> None:
        require(
            verdict["verdict"] == "ACCEPT_GRAPH_AND_OWNED_CLEANUP"
            and verdict["plan_sha256"] == self.result["policy_sha256"]
            and verdict["container_id"] == self.container
            and verdict["graph_owned_cleanup_authorized"] is True
            and verdict["base_cleanup_authorized"] is False
        )

    @staticmethod
    def utility_command() -> list[str]:
        return [
            "-m",
            "swingset.deploy.utility",
            "--role",
            "verifier",
            "--",
            "verify-derivations",
            "--state",
            "/var/lib/swingset",
            "--checkpoint",
            "/var/lib/swingset",
            "--processes",
            "2",
            "--config",
            "/app/config",
            "--overrides",
            "/app/overrides",
            "--receipt",
            "/var/lib/swingset-runtime/receipt.json",
        ]

    def extra_operator_mount(self) -> dict[str, Any] | None:
        return None

    def readonly_mount_overrides(self) -> frozenset[str]:
        mount = self.extra_operator_mount()
        if mount is None:
            return frozenset()
        require(
            mount["target"] == "/run/swingset-checker"
            and mount["type"] == "bind"
            and mount["readonly"] is True
        )
        return frozenset({mount["target"]})

    def bound(self) -> dict[str, Any]:
        value = super().bound()
        require(value["RestartCount"] == 0)
        require(value["HostConfig"].get("RestartPolicy", {}).get("Name", "no") == "no")
        require(value["Config"]["Cmd"] == self.utility_command())
        host = value["HostConfig"]
        require(
            host.get("IpcMode") in {None, "", "private"}
            and host.get("CgroupnsMode") in {None, "", "private"}
            and set(host["SecurityOpt"]) == {"no-new-privileges"}
            and not host.get("Devices")
            and not host.get("DeviceRequests")
            and not host.get("Binds")
            and not host.get("Tmpfs")
        )
        mounts = self.policy["mounts"]
        state = next(x for x in mounts if x["target"] == "/var/lib/swingset")
        require(
            state["type"] == "volume"
            and re.fullmatch(
                re.escape(self.policy["deployment"]) + r"-[0-9a-f]{12}-private-state",
                state["source"],
            )
        )
        for name in self.CREDENTIALS:
            secret = next(x for x in mounts if x["target"] == "/run/secrets/" + name)
            require(
                secret
                == {
                    "type": "bind",
                    "source": "/var/lib/docker/volumes/"
                    + state["source"]
                    + "/_data/private-secrets/"
                    + name,
                    "target": "/run/secrets/" + name,
                    "readonly": True,
                }
            )
        source = next(x for x in mounts if x["target"] == "/var/lib/swingset-source")
        require(
            source
            == {
                "type": "volume",
                "source": self.policy["deployment"] + "-state",
                "target": "/var/lib/swingset-source",
                "readonly": True,
                "subpath": "blobs",
            }
        )
        operator = self.extra_operator_mount()
        for target, suffix in (
            ("/run/swingset-config", "config"),
            ("/run/swingset-authorization", "authorization"),
            ("/run/swingset-holds", "holds"),
        ):
            item = next(x for x in mounts if x["target"] == target)
            require(
                item
                == {
                    "type": "bind",
                    "source": str(self.app / suffix),
                    "target": target,
                    "readonly": True,
                }
            )
        for target, suffix in (
            ("/var/lib/swingset-spill", "verifier-spill"),
            ("/var/lib/swingset-runtime", "verifier-runtime"),
            ("/run/swingset-checker", "verifier-checker"),
        ):
            item = next(x for x in mounts if x["target"] == target)
            if operator is not None and target == operator["target"]:
                require(item == operator)
                continue
            require(
                item["type"] == "volume"
                and not item["readonly"]
                and re.fullmatch(
                    re.escape(self.policy["deployment"]) + "-[0-9a-f]{12}-" + suffix, item["source"]
                )
            )
        if operator is not None:
            require(operator in mounts)
        require(len(value["Mounts"]) == len(host["Mounts"]) == len(mounts) == 11)
        for expected_mount in mounts:
            found = next(x for x in value["Mounts"] if x["Destination"] == expected_mount["target"])
            configured = next(x for x in host["Mounts"] if x["Target"] == expected_mount["target"])
            require(
                found["Type"] == configured["Type"] == expected_mount["type"]
                and found["RW"] == (not expected_mount["readonly"])
            )
            require(
                configured["Source"] == expected_mount["source"]
                and configured.get("ReadOnly", False) == expected_mount["readonly"]
            )
            require(
                configured.get("VolumeOptions", {}).get("Subpath") == expected_mount.get("subpath")
            )
            require(
                found.get("Name") == expected_mount["source"]
                if found["Type"] == "volume"
                else found["Source"] == expected_mount["source"]
            )
        image = self.docker("image", "inspect", self.policy["runtime_image"])
        require(image.returncode == 0)
        inherited = json.loads(image.stdout)[0]["Config"]
        argv = self.policy[self.ARGV]
        environment = dict(item.split("=", 1) for item in inherited.get("Env") or [])
        environment.update(
            dict(argv[i + 1].split("=", 1) for i, item in enumerate(argv) if item == "--env")
        )
        actual = dict(item.split("=", 1) for item in value["Config"].get("Env") or [])
        require(actual == environment and actual.get("SWINGSET_ENV") == "dryrun")
        labels = dict(inherited.get("Labels") or {})
        labels.update(
            dict(argv[i + 1].split("=", 1) for i, item in enumerate(argv) if item == "--label")
        )
        require(value["Config"]["Labels"] == labels)
        forbidden = {
            "DATABASE_URL",
            "HF_TOKEN",
            "HF_REHEARSAL_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "SWINGSET_DATABASE_URL",
            "SWINGSET_CONTROL_DATABASE_URL",
            "SWINGSET_OWNER_DATABASE_URL",
            "SWINGSET_SCRATCH_ADMIN_DSN",
        }
        require(not forbidden.intersection(actual))
        if value["State"]["Running"]:
            require(type(value["State"]["Pid"]) is int and value["State"]["Pid"] > 1)
            current = (value["State"]["Pid"], birth(value["State"]["Pid"]))
            require(self.incarnation is None or current == self.incarnation)
            self.incarnation = current
        return value

    def sample(self) -> None:
        super().sample()
        p = self.policy
        require([app_state(self.inspect(x["id"])) for x in p["support5"]] == p["support5"])
        platform = self.inspect(self.platform)
        identity = p["current_platform"]
        host = platform["HostConfig"]
        require(
            identity["container_id"] == self.platform
            and platform["Name"] == "/" + identity["name"]
            and platform["State"]["Pid"] == identity["pid"]
            and platform["State"]["StartedAt"] == identity["started_at"]
            and birth(identity["pid"]) == identity["start_ticks"]
            and boot_id() == identity["boot_id"]
            and platform["RestartCount"] == 0
            and platform["Config"]["User"] == "0:0"
            and platform["Config"]["Labels"].get("io.swingset.role") == "namespace-operator"
            and not platform["Config"]["Labels"].get("io.swingset.guarded")
            and platform["Config"]["Entrypoint"] == ["python"]
            and platform["Config"]["Cmd"]
            == ["/operator/security.py", "/operator/network.json", "/authorization", "--watch"]
            and host["ReadonlyRootfs"]
            and not host["Privileged"]
            and host["NetworkMode"] == "none"
            and host["PidMode"] == "host"
            and host["CapDrop"] == ["ALL"]
            and {x.removeprefix("CAP_") for x in host["CapAdd"]}
            == {"SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"}
            and set(host["SecurityOpt"]) == {"no-new-privileges", "label=disable"}
            and host["Memory"] == host["MemorySwap"] == 268435456
            and host["NanoCpus"] == 1000000000
            and host["PidsLimit"] == 128
        )
        expected_mounts = {
            "/var/run/docker.sock": ("bind", "/var/run/docker.sock", False),
            "/operator/security.py": ("bind", str(self.app / "security.py"), False),
            "/operator/network.json": ("bind", str(self.app / "config/network.json"), False),
            "/authorization": ("bind", str(self.app / "authorization"), True),
        }
        require(
            len(platform["Mounts"]) == 4
            and {x["Destination"]: (x["Type"], x["Source"], x["RW"]) for x in platform["Mounts"]}
            == expected_mounts
        )
        require(
            hashlib.sha256(root_bytes(self.app / "security.py")).hexdigest() == p["security_sha256"]
        )
        network_bytes = root_bytes(self.app / "config/network.json")
        require(hashlib.sha256(network_bytes).hexdigest() == p["network_policy_sha256"])
        network = json.loads(network_bytes)
        require(
            network["image_id"] == p["runtime_image"] and network["deployment"] == p["deployment"]
        )
        hold = json.loads(root_bytes(self.app / "holds/stage2-import.json"))
        require(
            all(hold[k] == v for k, v in p["hold_identity"].items())
            and datetime.fromisoformat(hold["review_at"]) > datetime.now(UTC)
        )
        if not self.result["start_attempted"]:
            required = (
                8 * max(p["shared_database_bytes"], p["private_base_database_bytes"]) + 200000000000
            )
            available = min(self.free("/var/tmp"), self.free("/var/lib/docker"))
            require(available >= required)
            self.result["initial_capacity"] = {
                "required_bytes": required,
                "available_bytes": available,
            }
        native = self.inspect(p["native_container"])
        require(
            not native["State"]["Running"]
            and native["State"]["Pid"] == 0
            and native["State"]["ExitCode"] == 0
            and not native["State"]["OOMKilled"]
            and native["State"]["FinishedAt"] == p["native_finished_at"]
        )
        self.result["resources"] = {}
        for name, identifier in {"verifier": self.container, **p["postgres_ids"]}.items():
            value = self.inspect(identifier)
            require(not value["State"]["OOMKilled"])
            if name == "private":
                h = value["HostConfig"]
                require(
                    h["Memory"] == h["MemorySwap"] == 8589934592
                    and h["NanoCpus"] == 6000000000
                    and h["PidsLimit"] == 256
                )
            if not value["State"]["Running"]:
                continue
            try:
                observed = memory(value["State"]["Pid"])
            except FileNotFoundError:
                after = self.inspect(identifier)
                require(
                    name == "verifier"
                    and not after["State"]["Running"]
                    and after["State"]["ExitCode"] == 0
                )
                continue
            peaks = self.result.setdefault("maximum_observed_cgroup_charge", {})
            peaks[name] = max(peaks.get(name, 0), observed["peak"])
            self.result["resources"][name] = observed
            if name == "verifier":
                previous_group = self.result.setdefault("verifier_cgroup", observed["cgroup"])
                require(previous_group == observed["cgroup"])
        self.result["protected12_unchanged"] = True
        if self.incarnation:
            self.startup(network)

    def startup(self, network: dict[str, Any]) -> None:
        if self.identity_seen:
            return
        require(self.clock() - self.started <= 120)
        proof_path = self.app / "authorization" / (self.container + ".json")
        try:
            proof = json.loads(root_bytes(proof_path))
        except FileNotFoundError:
            require(self.clock() - self.started <= 120)
            return
        incarnation = self.incarnation
        if incarnation is None:
            raise ValueError("verifier has no running incarnation")
        require(
            proof["container_id"] == self.container
            and proof["role"] == "verifier"
            and proof["policy_sha256"]
            == hashlib.sha256(
                json.dumps(network, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            and proof["boot_id"] == self.policy["current_platform"]["boot_id"]
            and proof["process_start"] == incarnation[1]
            and proof["network_inode"] > 0
        )
        inode = self.docker(
            "exec",
            self.platform,
            "/usr/bin/stat",
            "-L",
            "--format=%i",
            "--",
            f"/proc/{incarnation[0]}/ns/net",
        )
        require(
            inode.returncode == 0
            and inode.stdout.strip().isdigit()
            and int(inode.stdout.strip()) == proof["network_inode"]
        )
        logs = self.docker("logs", "--tail", "64", self.container)
        require(logs.returncode == 0)
        for line in (logs.stdout + "\n" + logs.stderr).splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(value, dict)
                and value.get("environment") == "dev"
                and "database_id" in value
            ):
                require(
                    value["database_id"] == self.policy["database_id"]
                    and value["database"] == self.policy["database"]
                )
                current = self.bound()
                require(current["State"]["Running"] and self.clock() - self.started <= 120)
                self.identity_seen = True
                self.result["verified_startup_identity"] = {
                    key: value[key] for key in ("environment", "database_id", "database")
                }
                self.result["verifier_incarnation"] = {
                    "container_id": self.container,
                    "pid": incarnation[0],
                    "start_ticks": incarnation[1],
                    "started_at": current["State"]["StartedAt"],
                    "boot_id": proof["boot_id"],
                    "network_inode": proof["network_inode"],
                    "cgroup": self.result["verifier_cgroup"],
                }
                self.live()
                return
        require(self.clock() - self.started <= 120)

    def progress(self) -> tuple[Any, ...] | None:
        if not self.identity_seen:
            return None
        script = (
            "import json,os,stat;from pathlib import Path;from psycopg.conninfo import conninfo_to_dict,make_conninfo;"
            "from swingset.state import engine;"
            "f=os.open('/run/secrets/worker-dsn',os.O_RDONLY|os.O_NOFOLLOW);s=os.fstat(f);"
            "assert stat.S_ISREG(s.st_mode) and s.st_uid==10001 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o400 and s.st_size<65536;"
            "d=os.read(f,65536).decode().strip();os.close(f);assert d and not any(x in d for x in ('\\n','\\r','\\0'));p=conninfo_to_dict(d);"
            "assert set(p)<={'host','hostaddr','port','dbname','user','password','sslmode','connect_timeout','application_name','gssencmode','channel_binding'};"
            "assert p['host']=='172.30.240.6' and p.get('hostaddr') in {None,p['host']} and p.get('port','5432')=='5432' and p['user']=='swingset_verify_worker' and p.get('password');"
            "assert p['dbname']==" + repr(self.policy["database"]) + ";"
            "c=engine.connect(make_conninfo(d,dbname='postgres'),read_only=True,application_name='swingset_graph_progress');"
            "c.execute(\"SET statement_timeout='10s'\");r=c.execute("
            + repr(STATISTICS_SQL)
            + ").fetchone();"
            "print(json.dumps(dict(zip(r.keys(),r)),sort_keys=True));c.close()"
        )
        response = self.docker(
            "exec",
            self.container,
            "/usr/bin/timeout",
            "--kill-after=10s",
            "30s",
            "python",
            "-c",
            script,
            timeout=45,
        )
        if response.returncode:
            self.result["progress_probe_failed"] = True
            return None
        value = json.loads(response.stdout)
        require(
            set(value) == {"transactions", "tuples", "databases"}
            and all(type(x) is int and x >= 0 for x in value.values())
        )
        self.result["verification_progress"] = value
        return value["transactions"], value["tuples"], value["databases"]

    def final_check(self) -> None:
        require(self.identity_seen)


class InputProfileJob(VerificationJob):
    """Measure one owned copy without accepting a graph or writing the base."""

    FORMAT = "swingset-attended-input-profile-v1"
    DEADLINES = frozenset({3600})
    PREFIX = "profile"
    OPERATION = "rollback-only input acceptance diagnostic"
    LIMITS = "Exit0 requires a separate diagnostic receipt; not a graph proof; no base cleanup."
    STATE_READ_ONLY = True

    def review(self, verdict: dict[str, Any]) -> None:
        require(
            verdict["verdict"] == "ACCEPT_PROFILE_AND_OWNED_CLEANUP"
            and verdict["plan_sha256"] == self.result["policy_sha256"]
            and verdict["container_id"] == self.container
            and type(verdict["owned_database_limit"]) is int
            and verdict["owned_database_limit"] == 1
            and verdict["base_cleanup_authorized"] is False
            and type(self.policy["owned_database_limit"]) is int
            and self.policy["owned_database_limit"] == 1
            and self.policy["diagnostic_only"] is True
        )

    @staticmethod
    def utility_command() -> list[str]:
        return [
            "-m",
            "swingset.deploy.utility",
            "--role",
            "verifier",
            "--",
            "profile-input-acceptance",
            "--state",
            "/var/lib/swingset-runtime/capture",
            "--checkpoint",
            "/var/lib/swingset",
            "--config",
            "/app/config",
            "--overrides",
            "/app/overrides",
            "--receipt",
            "/var/lib/swingset-runtime/input-profile.json",
        ]


def arguments(parser: argparse.ArgumentParser) -> None:
    from .import_job import arguments as common

    common(parser)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--review-sha256", required=True)
    parser.add_argument(
        "--input-profile", action="store_true", help="Supervise the rollback-only diagnostic"
    )


def execute(args: argparse.Namespace) -> int:
    try:
        with interruptions():
            job = InputProfileJob if args.input_profile else VerificationJob
            result = job(
                args.plan,
                args.plan_sha256,
                args.container,
                args.platform,
                review=args.review,
                review_sha256=args.review_sha256,
            ).run()
    except BaseException as error:
        result = {
            "complete": False,
            "mutation_outcome": "unknown",
            "no_automatic_retry": True,
            "error_type": type(error).__name__,
        }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    raise SystemExit(execute(parser.parse_args()))
