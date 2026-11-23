{
  description = "Noctalia local/agent-status bar indicator and lifecycle hook bridge";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      helper = pkgs.writeShellApplication {
        name = "noctalia-agent-status";
        runtimeInputs = [ pkgs.python3 ];
        text = ''
          exec python3 ${./helper/agent-status.py} "$@"
        '';
      };
      hooks = (pkgs.formats.json { }).generate "noctalia-agent-hooks.json" {
        noctalia-desktop-status =
          let
            handler = event: {
              type = "command";
              command = "${pkgs.lib.getExe helper} hook ${event}";
              timeout = 2;
            };
          in {
            SessionStart = [ (handler "SessionStart") ];
            PreInvocation = [ (handler "PreInvocation") ];
            PreToolUse = [ { matcher = ".*"; hooks = [ (handler "PreToolUse") ]; } ];
            PostToolUse = [ { matcher = ".*"; hooks = [ (handler "PostToolUse") ]; } ];
            Stop = [ (handler "Stop") ];
          };
      };
      plugin = pkgs.runCommand "noctalia-plugin-agent-status" { } ''
        cp -r ${./agent-status} $out
      '';
    in {
      lib = {
        pluginDir = ./agent-status;
        helperScript = ./helper/agent-status.py;
      };
      packages.${system} = {
        inherit helper hooks plugin;
        default = helper;
      };
      checks.${system} = {
        helper = pkgs.runCommand "noctalia-agent-status-checked" {
          nativeBuildInputs = [ pkgs.python3 ];
        } ''
          python3 ${./tests/test-agent-status.py} ${./helper/agent-status.py}
          touch $out
        '';
      };
    };
}
