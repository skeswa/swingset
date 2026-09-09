{ lib, ... }:
{
  imports = [ /etc/nixos/configuration.nix ];
  time.timeZone = lib.mkForce "UTC";
  users.groups.swingset = {};
  users.users.swingset = {
    isSystemUser = true;
    group = "swingset";
    home = "/var/lib/swingset";
  };
  systemd.tmpfiles.rules = [ "d /var/lib/swingset 0700 swingset swingset -" ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
}
