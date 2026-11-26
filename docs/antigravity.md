# Antigravity setup on Windows

Antigravity runs inside this DeepSeek Remote Bridge host. Install the official [Antigravity CLI](https://antigravity.google/docs/getting-started?tab=cli) on the same Windows account and sign in. Run `agy models` to check that CLI model discovery works, then open Antigravity from the bridge's top-left menu. When paired with Codex, the direct local page is `http://127.0.0.1:8766/dsh/antigravity.html`.

You can set `ANTIGRAVITY_BRIDGE_PROJECTS_ROOT` and `ANTIGRAVITY_BRIDGE_STANDALONE_DIR` in the local bridge configuration. These directories are separate from DeepSeek Harness projects. CLI-origin sessions can change model in the bridge. Quota and available models come from your own account. Attachments are staged on the host and passed as local file paths.

## Optional desktop session delivery

Desktop history remains read-only until the app's sidecar transport is configured. CLI sessions work without a sidecar or an open desktop app.

On Antigravity versions that support desktop sidecars, create `sidecar.json` inside your user profile's `.gemini/config/sidecars/antigravity-remote-bridge/` directory. Replace each example path with the absolute path on your own computer. In particular, the state directory must match `DSH_BRIDGE_HOME` in the bridge configuration.

```json
{
  "display_name": "Antigravity Remote Bridge",
  "command": "C:/path/to/pythonw.exe",
  "args": [
    "C:/path/to/deepseek-remote-bridge/app/antigravity_desktop.py",
    "C:/path/to/deepseek-remote-bridge/state",
    "C:/path/to/antigravity/resources/bin/language_server.exe"
  ],
  "restart_policy": "on-failure"
}
```

Back up your desktop app config. Enable the sidecar named `antigravity-remote-bridge` and associate it with your own desktop project in the app's sidecar settings. On versions with JSON configuration, the entry belongs inside the existing `sidecars` object, with `enabled: true`, your own `projectId`, and the app-supported `schemaVersion`. Reopen the desktop app when it is idle. The desktop project must be within the configured Antigravity projects root. If the app does not support this transport, use CLI sessions for sending. Desktop session models are changed in the desktop app.
