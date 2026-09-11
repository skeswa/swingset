{
  pkgs,
  source ? ../.,
}:
let
  sourcePath =
    if builtins.isAttrs source then
      source.outPath
    else
      builtins.path {
        path = source;
        name = "swingset-source";
      };
  # A dirty checkout has no commit that identifies its contents. Keep the
  # content-addressed store identity instead of reporting the parent as exact.
  sourceIdentity =
    if builtins.isAttrs source && source ? rev then
      source.rev
    else
      "uncommitted:${builtins.baseNameOf sourcePath}";
in
(pkgs.writeShellApplication {
  name = "swingset";
  runtimeInputs = [
    pkgs.uv
    pkgs.python312
  ];
  text = ''
    export SWINGSET_REVISION=${pkgs.lib.escapeShellArg sourceIdentity}
    export UV_PYTHON=${pkgs.python312}/bin/python3.12
    export UV_PYTHON_DOWNLOADS=never
    export SWINGSET_CONFIG_DIR=${sourcePath}/config
    export SWINGSET_OVERRIDES_DIR="''${SWINGSET_OVERRIDES_DIR:-${sourcePath}/overrides}"
    export UV_PROJECT_ENVIRONMENT="''${UV_PROJECT_ENVIRONMENT:-/var/lib/swingset/venv}"
    export UV_CACHE_DIR="''${UV_CACHE_DIR:-/var/lib/swingset/uv-cache}"
    export LD_LIBRARY_PATH=${
      pkgs.lib.makeLibraryPath [
        pkgs.stdenv.cc.cc.lib
        pkgs.zlib
      ]
    }
    exec uv run --project ${sourcePath} --frozen --no-dev swingset "$@"
  '';
})
// {
  projectSource = sourcePath;
}
