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
  # Local-only backups need room for the checkpoint only, uploads for the
  # checkpoint and its transport archive (D-0203).
  copies = if cfg.backupUpload then "2" else "1";
  backupMode = if cfg.backupUpload then "upload" else "local only";
  args = "--state ${lib.escapeShellArg cfg.stateDir} --config ${source}/config --overrides ${lib.escapeShellArg cfg.overridesDir}";
  # A hung notifier must not hold the watchdog unit: the timer does not start
  # a unit that is still running.
  alertPipe =
    if cfg.alertCommand != null then
      "printf '%s\\n' \"$alert\" | ${pkgs.coreutils}/bin/timeout 60 ${pkgs.bash}/bin/bash -c ${lib.escapeShellArg cfg.alertCommand} || true"
    else
      "";
  # Silence from the watchdog reads the same as health, so with an
  # alertCommand it sends one [watchdog-alive] line a day; a missing one means
  # the timer or the notifier stopped. The line skips ALERTS.log, which holds
  # problems only, and the stamp lives in /run, so a reboot sends one early.
  heartbeat = lib.optionalString (cfg.alertCommand != null) ''
    alive=/run/swingset-watchdog-alive
    if [ -z "$(${pkgs.findutils}/bin/find "$alive" -mmin -1440 2>/dev/null)" ]; then
      alert="[watchdog-alive] swingset-watchdog ran at $(${pkgs.coreutils}/bin/date -u +%FT%TZ); no line within a day means it stopped"
      ${alertPipe}
      ${pkgs.coreutils}/bin/touch "$alive"
    fi
  '';
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
      default = 7200;
      description = ''
        The watchdog alerts when the newest request is older than this while
        the worker is unheld, unless every host that fetched today has spent
        its budget. A gate that skips every request is not a failure, so
        nothing else reports it (D-0190, D-0202).
      '';
    };
    staleProgressSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 7200;
      description = ''
        The watchdog alerts when a stage has ready units and neither its
        newest derivation generation nor its newest succeeded work attempt
        is newer than this while the worker is unheld (D-0202).
      '';
    };
    watchdogRepeatSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 7200;
      description = ''
        An alert key already in ALERTS.log within this many seconds is not
        logged or sent again (D-0202).
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
    backupUpload = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Upload each timer checkpoint to the archive. False writes a local
        checkpoint only (`swingset backup --local`), needs room for one copy
        instead of two, and makes the watchdog report the pause and a stale
        local checkpoint (D-0203).
      '';
    };
    backupMaxAgeSeconds = lib.mkOption {
      type = lib.types.ints.positive;
      default = 129600;
      description = ''
        With backupUpload false, the watchdog alerts when the newest complete
        local timer checkpoint is older than this, or there is none (D-0203).
      '';
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
    # run records and an immutable view of the database, so it costs little
    # and needs no lock; it runs on the timer and immediately after a cycle
    # fails (D-0188). The checks live in watchdog.py so tests can run them;
    # since D-0202 they also watch progress and the silent fetch gates.
    systemd.services.swingset-watchdog = {
      description = "Alert on failing cycles, stalled fetching or derive, silent gates, or low disk";
      serviceConfig = {
        Type = "oneshot";
        # Longer than any healthy run by far; a hang must end so the next
        # timer tick can run.
        TimeoutStartSec = "5min";
        ExecStart = pkgs.writeShellScript "swingset-watchdog" ''
          set -eu
          state=${lib.escapeShellArg cfg.stateDir}
          systemctl=${config.systemd.package}/bin/systemctl
          disk=$(${diskQuota}/bin/swingset-disk --raw || true)
          if [ "$($systemctl is-active swingset-cycle.timer || true)" = active ]; then
            timer=1
          else
            timer=0
          fi
          # A failed backup (room check refused, copy or verification error)
          # stays failed until the next run succeeds (D-0203).
          if $systemctl is-failed --quiet swingset-backup.service; then
            backup_failed=1
          else
            backup_failed=0
          fi
          # The daily digest runs near its memory cap; a failed one stays
          # failed until the next day's run succeeds.
          if $systemctl is-failed --quiet swingset-summary.service; then
            summary_failed=1
          else
            summary_failed=0
          fi
          # A running backup holds two checkpoints for a while (D-0203). A
          # oneshot unit is "activating" while its command runs.
          case "$($systemctl is-active swingset-backup.service || true)" in
            active | activating) backup_active=1 ;;
            *) backup_active=0 ;;
          esac
          # A watchdog that cannot run must say so, not exit silent. Its
          # stderr goes to the journal, never into an alert line.
          alert=$(${pkgs.python3}/bin/python3 ${./watchdog.py} \
            --state "$state" \
            --config ${source}/config \
            --quiet ${toString cfg.watchdogQuietSeconds} \
            --stale-fetch ${toString cfg.staleFetchSeconds} \
            --stale-progress ${toString cfg.staleProgressSeconds} \
            --repeat ${toString cfg.watchdogRepeatSeconds} \
            --disk "$disk" \
            --disk-min-free ${toString cfg.diskMinFreeBytes} \
            --timer-active "$timer" \
            --backup-failed "$backup_failed" \
            --summary-failed "$summary_failed" \
            --backup-active "$backup_active"${
              lib.optionalString (
                !cfg.backupUpload
              ) " --backup-upload 0 --backup-max-age ${toString cfg.backupMaxAgeSeconds}"
            }) || {
            code=$?
            alert="[watchdog-error] watchdog.py exited $code; read journalctl -u swingset-watchdog"
            # watchdog.py de-duplicates its own keys; this one it cannot, so
            # hold it back while ALERTS.log has one within the repeat window.
            last=$(${pkgs.coreutils}/bin/tail -c 65536 "$state/ALERTS.log" 2>/dev/null \
              | ${pkgs.gnugrep}/bin/grep ' \[watchdog-error\]' | ${pkgs.coreutils}/bin/tail -n 1 \
              | ${pkgs.coreutils}/bin/cut -d ' ' -f 1) || last=""
            if [ -n "$last" ]; then
              then_s=$(${pkgs.coreutils}/bin/date -u -d "$last" +%s 2>/dev/null || echo 0)
              if [ $(( $(${pkgs.coreutils}/bin/date -u +%s) - then_s )) -lt ${toString cfg.watchdogRepeatSeconds} ]; then
                alert=""
              fi
            fi
          }
          if [ -n "$alert" ]; then
            # Send first: on a full disk, the case [disk-low] exists for, the
            # append below fails. A failed append only means the next run
            # sends the line again.
            ${pkgs.util-linux}/bin/logger -t swingset-watchdog -p user.err "$alert"
            ${alertPipe}
            # Stamp every line: the next run reads the stamps to skip keys
            # it already reported.
            stamp=$(date -u +%FT%TZ)
            printf '%s\n' "$alert" | while IFS= read -r line; do
              printf '%s %s\n' "$stamp" "$line"
            done >> "$state/ALERTS.log" || true
            # Keep the last 1 MiB, far more than the 64 KiB de-duplication
            # reads. Rewriting in place keeps the file's owner and mode.
            log="$state/ALERTS.log"
            if [ "$(${pkgs.coreutils}/bin/stat -c %s "$log" 2>/dev/null || echo 0)" -gt 1048576 ]; then
              ${pkgs.coreutils}/bin/tail -c 1048576 "$log" > "$log.tmp" \
                && cat "$log.tmp" > "$log" \
                && rm -f "$log.tmp" || true
            fi
          fi
          ${heartbeat}
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
        # archive is a second one; a local-only backup writes no archive.
        # Refuse to start when the disk quota cannot hold them with headroom,
        # so a backup can never take the space the pipeline runs on (D-0192,
        # D-0203).
        ExecStartPre =
          "+"
          + pkgs.writeShellScript "swingset-backup-room" ''
            set -eu
            state=${lib.escapeShellArg cfg.stateDir}
            read -r used cap _ < <(${diskQuota}/bin/swingset-disk --raw) || exit 0
            need=$(( ${copies} * $(${pkgs.coreutils}/bin/du -sb "$state" --exclude=checkpoints | ${pkgs.coreutils}/bin/cut -f1) + 20000000000 ))
            free=$(( cap - used ))
            if [ "$free" -lt "$need" ]; then
              echo "backup refused (${backupMode}): $((free / 1000000000)) GB free of quota, $((need / 1000000000)) GB needed for ${copies} ${
                if cfg.backupUpload then "copies" else "copy"
              } plus 20 GB headroom" >&2
              exit 1
            fi
          '';
        # Upload mode prunes only after the upload succeeds. Local mode
        # prunes by the [retention] knobs after the checkpoint is written,
        # and from the phase 1 package on only after its read-back passes.
        ExecStart = "${cfg.package}/bin/swingset backup ${args}${
          lib.optionalString (!cfg.backupUpload) " --local"
        }";
        # A local backup (copy, read-back and two writer-lock waits) took
        # about 10 minutes on the Mac. Six hours ends a hung one, so the
        # watchdog's lower [disk-low] floor during a backup cannot last, and
        # the unit fails and alerts. An interrupted copy leaves a temporary
        # directory the next prune removes. Upload time is unmeasured, so
        # upload mode keeps no limit.
        TimeoutStartSec = if cfg.backupUpload then "infinity" else "6h";
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
      unitConfig = {
        ConditionPathExists = "!${cfg.stateDir}/operator-hold";
        # Killed at its memory cap, the digest would stop without a word
        # (`[summary-failed]`).
        OnFailure = [ "swingset-watchdog.service" ];
      };
      environment = env;
      serviceConfig = common // {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/swingset summary ${args}";
        # The digest shares the 8 GB machine with the cycle (up to about
        # 6.3 GB with a build). Without the retention walk it peaked at
        # 4.5 GB on the 35 GB copy, 4.4 GB of it the publication report's
        # closure check; the walk alone took 13 GB. Cap it, and let the
        # kernel pick it over the cycle when memory runs out.
        MemoryMax = "5G";
        OOMScoreAdjust = 800;
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
