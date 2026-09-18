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
    environment.systemPackages = [ cfg.package ];
    systemd.services.swingset-cycle = {
      description = "Collect and materialize swingset evidence";
      unitConfig.ConditionPathExists = "!${cfg.stateDir}/operator-hold";
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
    systemd.services.swingset-backup = {
      description = "Checkpoint complete swingset state";
      unitConfig.ConditionPathExists = "!${cfg.stateDir}/operator-hold";
      # Checkpoint transport archives can be larger than the worker's RAM.
      # PrivateTmp also isolates /var/tmp, while retaining its disk backing.
      environment = env // {
        TMPDIR = "/var/tmp";
      };
      serviceConfig = common // {
        Type = "oneshot";
        ExecStart = "${cfg.package}/bin/swingset backup ${args}";
        Restart = "on-failure";
        RestartSec = 60;
        TimeoutStartSec = "infinity";
      };
      unitConfig.StartLimitIntervalSec = 0;
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
