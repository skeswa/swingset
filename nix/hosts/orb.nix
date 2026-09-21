{ lib, ... }:
{
  # OrbStack's generated configuration also owns container boot, networking,
  # the host integration user and certificates. Importing orbstack.nix alone
  # drops those settings. This path is intentionally evaluated with --impure.
  imports = [ /etc/nixos/configuration.nix ];
  time.timeZone = lib.mkForce "UTC";
  services.swingset = {
    enable = true;
    # Cycles build candidates but do not publish until the identity output
    # regression of 2026-09-21 is repaired (D-0189).
    dryRun = true;
    environmentFile = "/etc/swingset.env";
    overridesDir = "/Users/skeswa/repos/skeswa/swingset/overrides";
    # A longer cycle keeps the fixed per-cycle work (reconciliation, event
    # bookkeeping) small next to the offline phase that drains backlogs; the
    # 15-minute timer simply skips ticks while a cycle runs (D-0185).
    cycleBudget = "40m";
  };
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
}
