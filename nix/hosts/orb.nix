{ lib, ... }:
{
  # OrbStack's generated configuration also owns container boot, networking,
  # the host integration user and certificates. Importing orbstack.nix alone
  # drops those settings. This path is intentionally evaluated with --impure.
  imports = [ /etc/nixos/configuration.nix ];
  time.timeZone = lib.mkForce "UTC";
  services.swingset = {
    enable = true;
    dryRun = false;
    environmentFile = "/etc/swingset.env";
    overridesDir = "/Users/skeswa/repos/skeswa/swingset/overrides";
  };
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
}
