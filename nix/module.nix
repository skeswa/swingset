{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.swingset;
  source = cfg.package.projectSource or ../.;
  env = {
    UV_PYTHON = "${pkgs.python312}/bin/python3.12";
    UV_PYTHON_DOWNLOADS = "never";
    # Wheel installation can exceed uv's 30-second default on a cold VM.
    UV_HTTP_TIMEOUT = "120";
    HF_HUB_DISABLE_PROGRESS_BARS = "1";
    UV_PROJECT_ENVIRONMENT = "${cfg.stateDir}/venv";
    UV_CACHE_DIR = "${cfg.stateDir}/uv-cache";
    LD_LIBRARY_PATH = lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
      pkgs.zlib
    ];
  };
  common = {
    User = "swingset";
    Group = "swingset";
    DynamicUser = false;
    WorkingDirectory = "${source}";
    EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
    ExecStartPre = "${pkgs.uv}/bin/uv sync --project ${source} --frozen --no-dev";
    ProtectSystem = "strict";
    ProtectHome = "read-only";
    ReadWritePaths = [ cfg.stateDir ];
    PrivateTmp = true;
    NoNewPrivileges = true;
    KillMode = "mixed";
    TimeoutStopSec = 45;
    UMask = "0077";
  };
  args = "--state ${lib.escapeShellArg cfg.stateDir} --config ${source}/config --overrides ${lib.escapeShellArg cfg.overridesDir}";
  alertPipe =
    if cfg.alertCommand != null then "printf '%s\\n' \"$alert\" | ${cfg.alertCommand} || true" else "";
  # A btrfs quota caps this machine's disk (OrbStack sets one), and statfs
  # ignores quotas, so df reports the host volume instead. This reads the
  # quota itself. Only root may query it, so root's result is cached in /run
  # for everyone else; the watchdog timer refreshes it every half hour.
  diskQuota = pkgs.writeShellScriptBin "swingset-disk" ''
    cache=/run/swingset-disk
    if [ "$(${pkgs.coreutils}/bin/id -u)" = 0 ]; then
      line=$(${pkgs.btrfs-progs}/bin/btrfs qgroup show -reF --raw / 2>/dev/null \
        | ${pkgs.gawk}/bin/awk '$1 ~ /^0\// && $4 ~ /^[0-9]+$/ { print $2, $4; exit }') || line=""
      if [ -n "$line" ]; then
        printf '%s %s\n' "$line" "$(${pkgs.coreutils}/bin/date -u +%FT%TZ)" > "$cache.tmp"
        ${pkgs.coreutils}/bin/chmod 0644 "$cache.tmp"
        ${pkgs.coreutils}/bin/mv "$cache.tmp" "$cache"
      else
        ${pkgs.coreutils}/bin/rm -f "$cache"
      fi
    fi
    [ -r "$cache" ] || exit 0
    read -r used cap at < "$cache"
    if [ "''${1:-}" = "--raw" ]; then
      printf '%s %s %s\n' "$used" "$cap" "$at"
    else
      ${pkgs.gawk}/bin/awk -v u="$used" -v c="$cap" -v at="$at" 'BEGIN {
        printf "disk quota: %.1f GB used of %.1f GB, %.1f GB free (btrfs qgroup, read %s)\n", u/1e9, c/1e9, (c-u)/1e9, at }'
    fi
  '';
  quotaAwareDf = pkgs.writeShellScriptBin "df" ''
    ${pkgs.coreutils}/bin/df "$@"
    status=$?
    note=$(${diskQuota}/bin/swingset-disk 2>/dev/null) || note=""
    if [ -n "$note" ]; then
      echo "df cannot see this machine's quota. $note" >&2
    fi
    exit $status
  '';
in
{
  options.services.swingset = {
    enable = lib.mkEnableOption "swingset evidence pipeline";
    package = lib.mkOption {
      type = lib.types.package;
      default = import ./package.nix { inherit pkgs; };
      description = "swingset CLI package from the flake.";
    };
    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/swingset";
    };
    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
    };
    overridesDir = lib.mkOption {
      type = lib.types.str;
      default = "${source}/overrides";
    };
    cycleBudget = lib.mkOption {
      type = lib.types.str;
      default = "12m";
    };
    alertCommand = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Shell command the watchdog runs after logging an alert, with the
        alert text on standard input; for example a curl to a notification
        topic. Unset means log only.
      '';
    };
    diskMinFreeBytes = lib.mkOption {
      type = lib.types.ints.positive;
      default = 100000000000;
      description = ''
        The watchdog alerts when the machine's btrfs quota has less than this
        many bytes free. The default is the 100 GB trigger in AGENTS.md. No
        quota, no alert.
      '';
    };
    staleFetchSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 172800;
      description = ''
        The watchdog alerts when the newest fetched snapshot is older than
        this while the worker is unheld. A gate that skips every request is
        not a failure, so nothing else reports it (D-0190).
      '';
    };
    watchdogQuietSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 7200;
      description = "Seconds without a finished cycle, while unheld, that count as a stall.";
    };
    dryRun = lib.mkOption {
      type = lib.types.bool;
      default = true;
    };
    scratchMaxAgeDays = lib.mkOption {
      type = lib.types.ints.positive;
      default = 3;
      description = ''
        Rehearsal scratch directories under /var/tmp/swingset-* are removed
        once nothing inside them has changed for this many days. A KEEP file
        at the top of a directory exempts it.
      '';
    };
  };
  config = lib.mkIf cfg.enable {
    users.groups.swingset = { };
    users.users.swingset = {
      isSystemUser = true;
      group = "swingset";
      home = cfg.stateDir;
    };
    systemd.tmpfiles.rules = [ "d ${cfg.stateDir} 0700 swingset swingset -" ];
    environment.systemPackages = [
      cfg.package
      diskQuota
      # Shadows coreutils df: same output, plus one stderr line with the quota.
      (lib.hiPrio quotaAwareDf)
    ];
    systemd.services.swingset-cycle = {
      description = "Collect and materialize swingset evidence";
      unitConfig = {
        ConditionPathExists = "!${cfg.stateDir}/operator-hold";
        OnFailure = [ "swingset-watchdog.service" ];
      };
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment = env;
      serviceConfig = common // {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/swingset cycle ${args} --budget ${cfg.cycleBudget} --timer ${
          if cfg.dryRun then "--dry-run" else "--publish"
        }";
      };
    };
    systemd.timers.swingset-cycle = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*:0/15";
        RandomizedDelaySec = 120;
        Persistent = true;
      };
    };
    # Nine hours of failing cycles went unnoticed on 2026-09-21 because
    # nothing escalated a run of failures. The watchdog reads the retained
    # run records, not the database, so it costs nothing and needs no lock;
    # it runs on the timer and immediately after a cycle fails (D-0188).
    systemd.services.swingset-watchdog = {
      description = "Alert on repeated swingset cycle failures or a stalled pipeline";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "swingset-watchdog" ''
          set -eu
          state=${lib.escapeShellArg cfg.stateDir}
          disk=$(${diskQuota}/bin/swingset-disk --raw || true)
          alert=$(DISK="$disk" DISK_MIN_FREE=${toString cfg.diskMinFreeBytes} STALE_FETCH=${toString cfg.staleFetchSeconds} ${pkgs.python3}/bin/python3 - "$state" ${toString cfg.watchdogQuietSeconds} <<'PY'
          import glob, json, os, subprocess, sys, time
          from datetime import datetime, timezone
          state, quiet = sys.argv[1], int(sys.argv[2])
          runs = sorted(glob.glob(os.path.join(state, "runs", "run_*.json")), key=os.path.getmtime)[-3:]
          records = []
          for path in runs:
              try:
                  records.append(json.load(open(path)))
              except Exception:
                  continue
          messages = []
          if len(records) == 3 and all(r.get("failed") or r.get("error") for r in records):
              last = records[-1]
              messages.append(
                  "three consecutive cycles failed; latest %s: %s"
                  % (last.get("run_id"), (last.get("error") or "unit failures")[:200])
              )
          held = os.path.exists(os.path.join(state, "operator-hold"))
          timer = subprocess.run(["systemctl", "is-active", "swingset-cycle.timer"], capture_output=True, text=True).stdout.strip()
          if records and not held and timer == "active":
              finished = records[-1].get("finished_at")
              if finished:
                  age = time.time() - datetime.fromisoformat(finished).timestamp()
                  if age > quiet:
                      messages.append("no cycle has finished for %d minutes while unheld" % (age // 60))
          if not held and timer == "active":
              try:
                  import sqlite3
                  # immutable=1 reads the main file only and creates nothing,
                  # so root never leaves files in the service's state directory.
                  db = sqlite3.connect("file:%s/state.sqlite?immutable=1" % state, uri=True)
                  newest = db.execute("SELECT max(fetched_at) FROM snapshots").fetchone()[0]
                  db.close()
                  if newest:
                      age = time.time() - datetime.fromisoformat(newest).timestamp()
                      if age > int(os.environ["STALE_FETCH"]):
                          messages.append("no page has been fetched for %d hours while unheld" % (age // 3600))
              except Exception:
                  pass
          disk = os.environ.get("DISK", "").split()
          if len(disk) >= 2:
              free = int(disk[1]) - int(disk[0])
              if free < int(os.environ["DISK_MIN_FREE"]):
                  messages.append(
                      "disk quota has %.1f GB free of %.1f GB; df does not show this"
                      % (free / 1e9, int(disk[1]) / 1e9)
                  )
          print("\n".join(messages))
          PY
          )
          if [ -n "$alert" ]; then
            stamp=$(date -u +%FT%TZ)
            printf '%s %s\n' "$stamp" "$alert" >> "$state/ALERTS.log"
            ${pkgs.util-linux}/bin/logger -t swingset-watchdog -p user.err "$alert"
            ${alertPipe}
          fi
        '';
      };
    };
    systemd.timers.swingset-watchdog = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*:5/30";
        Persistent = true;
      };
    };
    systemd.services.swingset-backup = {
      description = "Checkpoint complete swingset state";
      unitConfig = {
        ConditionPathExists = "!${cfg.stateDir}/operator-hold";
        # A backup writes a full local copy before it can fail on transport,
        # so a restart loop fills the disk with copies: six in forty minutes
        # on 2026-09-22 (D-0192). A failed backup alerts and waits for the
        # next timer run instead.
        OnFailure = [ "swingset-watchdog.service" ];
      };
      # Checkpoint transport archives can be larger than the worker's RAM.
      # PrivateTmp also isolates /var/tmp, while retaining its disk backing.
      environment = env // {
        TMPDIR = "/var/tmp";
      };
      serviceConfig = common // {
        Type = "oneshot";
        # A checkpoint is a full copy of the state directory and the transport
        # archive is a second one. Refuse to start when the disk quota cannot
        # hold both with headroom, so a backup can never take the space the
        # pipeline runs on (D-0192).
        ExecStartPre =
          "+"
          + pkgs.writeShellScript "swingset-backup-room" ''
            set -eu
            state=${lib.escapeShellArg cfg.stateDir}
            read -r used cap _ < <(${diskQuota}/bin/swingset-disk --raw) || exit 0
            need=$(( 2 * $(${pkgs.coreutils}/bin/du -sb "$state" --exclude=checkpoints | ${pkgs.coreutils}/bin/cut -f1) + 20000000000 ))
            free=$(( cap - used ))
            if [ "$free" -lt "$need" ]; then
              echo "backup refused: $((free / 1000000000)) GB free of quota, $((need / 1000000000)) GB needed for two copies plus 20 GB headroom" >&2
              exit 1
            fi
          '';
        ExecStart = "${cfg.package}/bin/swingset backup ${args}";
        TimeoutStartSec = "infinity";
      };
    };
    systemd.timers.swingset-backup = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = [
          "Mon..Thu *-*-* 04:00:00"
          "Fri..Sun *-*-* 04,12,20:00:00"
        ];
        Persistent = true;
      };
    };
    systemd.services.swingset-summary = {
      description = "swingset daily journal digest";
      unitConfig.ConditionPathExists = "!${cfg.stateDir}/operator-hold";
      environment = env;
      serviceConfig = common // {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/swingset summary ${args}";
      };
    };
    systemd.timers.swingset-summary = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*-*-* 08:00:00";
        Persistent = true;
      };
    };
    # Rehearsals copy the whole state directory into /var/tmp/swingset-<name>.
    # Each copy is several gigabytes and nothing removed them before this
    # timer existed. Runs as root because operators create them as root, and
    # without PrivateTmp so it sees the real /var/tmp.
    systemd.services.swingset-scratch-clean = {
      description = "Remove idle swingset rehearsal scratch under /var/tmp";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "swingset-scratch-clean" ''
          set -eu
          age=${toString cfg.scratchMaxAgeDays}
          for dir in /var/tmp/swingset-*/; do
            [ -d "$dir" ] || continue
            dir="''${dir%/}"
            if [ -e "$dir/KEEP" ]; then
              echo "kept (KEEP marker): $dir"
              continue
            fi
            if [ -n "$(${pkgs.findutils}/bin/find "$dir" -newermt "$age days ago" -print -quit)" ]; then
              continue
            fi
            echo "removing rehearsal scratch idle for $age days: $dir"
            rm -rf -- "$dir"
          done
        '';
      };
    };
    systemd.timers.swingset-scratch-clean = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*-*-* 05:00:00";
        Persistent = true;
      };
    };
  };
}
