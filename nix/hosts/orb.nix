{ lib, ... }:
{
  # OrbStack's generated configuration also owns container boot, networking,
  # the host integration user and certificates. Importing orbstack.nix alone
  # drops those settings. This path is intentionally evaluated with --impure.
  imports = [ /etc/nixos/configuration.nix ];
  time.timeZone = lib.mkForce "UTC";
  services.swingset = {
    enable = true;
    # Release ticks publish (D-0252); they were dry runs from 2026-09-21 to
    # 2026-10-13 while identity output was recovered (D-0189).
    dryRun = false;
    environmentFile = "/etc/swingset.env";
    overridesDir = "/Users/skeswa/repos/skeswa/swingset/overrides";
    # The archive transport refuses a file over 20 GiB and a checkpoint is
    # about 36 GB, so no upload can succeed. Back up locally, keep one timer
    # checkpoint, and keep the Mac byte copy current by hand until segmented
    # backups land (D-0203).
    backupUpload = false;
    # Phase 4 step 8: segmented local backup sets and the weekly restore drill
    # (D-0232).
    backupSegmented = true;
  };
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
}
