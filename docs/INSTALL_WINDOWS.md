# Windows installation draft — not yet publicly approved

Current targeted-fix SKY Workbench V2.0 internal candidate Setup SHA-256: `FA21A4EB43ADBD7895699D27E319BBE6356BB307035DC21137E9C43F5EA52DFB`. Stop if the file differs. This is an internal candidate only; it does not authorize public distribution.

The workbench targets Windows 11 x64. It does not bundle COMSOL or a COMSOL license; scientific execution requires a separately licensed installation. It also does not bundle Ollama model weights. The Setup includes a WebView2 Evergreen bootstrapper, which may download the runtime if absent, so a machine without WebView2 may require network access.

After installation, inspect the preflight and visible unavailable/degraded states before creating a model. A successful command is not equivalent to verified model state or passed scientific validation. Preserve diagnostic logs when requesting support, but do not post API keys, user models, or private workspaces publicly. A targeted COMSOL 6.3 heat worker solve was run separately on the new sidecar; it is not a complete desktop product science acceptance test. Uninstall should not be used as an implicit user-data deletion operation.
