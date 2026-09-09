{
  description = "swingset pipeline and NixOS service";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      forSystem = system: import nixpkgs { inherit system; };
      source = self;
    in {
      devShells = eachSystem (system:
        let pkgs = forSystem system;
        in { default = pkgs.mkShell {
          packages = with pkgs; [ python312 uv nodejs ruff mypy ];
          UV_PYTHON = "${pkgs.python312}/bin/python3.12";
          UV_PYTHON_DOWNLOADS = "never";
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];
        }; });
      packages = eachSystem (system:
        let pkgs = forSystem system;
        in { default = import ./nix/package.nix { inherit pkgs source; }; });
      nixosModules.default = import ./nix/module.nix;
      nixosConfigurations.orb = nixpkgs.lib.nixosSystem {
        system = "aarch64-linux";
        modules = [ self.nixosModules.default ./nix/hosts/orb.nix
          { services.swingset.package = self.packages.aarch64-linux.default; } ];
      };
      # Recovery installs the CLI and state owner without collection timers.
      nixosConfigurations.orb-restore = nixpkgs.lib.nixosSystem {
        system = "aarch64-linux";
        modules = [ ./nix/hosts/orb-restore.nix
          { environment.systemPackages = [ self.packages.aarch64-linux.default ]; } ];
      };
    };
}
