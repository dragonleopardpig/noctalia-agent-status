# noctalia-agent-status

Noctalia bar indicator (`local/agent-status`), lifecycle hook helper (`noctalia-agent-status`), and generated hook configuration (`packages.x86_64-linux.hooks`).

## Usage

- **Noctalia plugin source**: add this repository as a Noctalia plugin source (`catalog.toml` + `agent-status/`).
- **Nix flake**: provides `packages.x86_64-linux.{helper,hooks,plugin}`, `lib.pluginDir`, and `checks.x86_64-linux.helper`.
