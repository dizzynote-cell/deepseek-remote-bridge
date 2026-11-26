# Install DeepSeek Remote Bridge

When the user requests installation, check Windows, Python 3.11+, the signed-in DeepSeek Harness installation, and port availability. Run `install.ps1` for a fresh install or add `-PairWithCodex` to use the local Codex switcher on port 8766. Existing installations should preserve `config/.env`, state, and shortcut settings. Ask the user to complete any sign-in and enter their secrets locally.

For Antigravity, follow [the setup guide](docs/antigravity.md). Test the local health endpoint, model menu, and switching in both directions. Use health and model calls for installation verification. For public access, use the documented owner-authenticated HTTPS relay.

Configure additional model APIs in DeepSeek Harness. After the native app can select the model, reload the DSH bridge page. Keep credentials, logs, and conversation data out of Git.
