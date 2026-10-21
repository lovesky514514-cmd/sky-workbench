# Windows installation draft — not yet publicly approved

Current OpenSSL-updated SKY Workbench V2.0 internal candidate Setup SHA-256: `7C4F1886D4A57C3F9A4DDF7D2F706506C28F19EB975B9477B205CDF1158E8A58`. Stop if the file differs. This is an internal candidate only; it does not authorize public distribution.

The workbench targets Windows 11 x64. It does not bundle COMSOL or a COMSOL license; scientific execution requires a separately licensed installation. It also does not bundle Ollama model weights. The Setup includes a WebView2 Evergreen bootstrapper, which may download the runtime if absent, so a machine without WebView2 may require network access.

After installation, inspect the preflight and visible unavailable/degraded states before creating a model. A successful command is not equivalent to verified model state or passed scientific validation. Preserve diagnostic logs when requesting support, but do not post API keys, user models, or private workspaces publicly. A targeted COMSOL 6.3 heat worker solve was run separately on the new sidecar; it is not a complete desktop product science acceptance test. Uninstall should not be used as an implicit user-data deletion operation.
