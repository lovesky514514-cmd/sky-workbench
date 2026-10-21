# Windows installation / Windows 安装

**Distribution status:** These instructions describe a V2.0 **internal candidate**, not a public release. No public download is authorized by this document.

## Requirements / 前提条件

- Windows 11 x64 for the tested desktop target.
- A separate, valid COMSOL 6.3 installation and license for real COMSOL model execution. SKY Workbench does not include COMSOL software, license files, or a license bypass.
- A locally available Ollama service and selected model if you want local-model assistance. Model weights are not bundled with this source snapshot; the application shows unavailable models explicitly.
- WebView2. The Setup includes an Evergreen bootstrapper; if WebView2 is absent, installation may require network access to obtain its runtime.

## Install the approved internal candidate / 安装内部候选

1. Obtain the Setup only through an approved internal channel. Do not rename an older installer as V2.0.
2. Verify the actual Setup file with PowerShell:

   ```powershell
   Get-FileHash -Algorithm SHA256 -LiteralPath 'path\to\SKY Workbench_2.0.0_x64-setup.exe'
   ```

   The current targeted-fix internal candidate SHA-256 is `FA21A4EB43ADBD7895699D27E319BBE6356BB307035DC21137E9C43F5EA52DFB`. Stop if it differs; the hash is an identity check, not proof of an authorized public release.
3. Run the Setup, launch SKY Workbench, and review the first-run environment states. **Ready**, **Degraded**, **Blocked**, and **Unavailable** must reflect the local machine. Do not assume that successful installation means COMSOL or a model is connected.
4. Open a task, review its scientific plan, and approve execution only when the assumptions, units, boundaries, and intended checks are correct. See the [user guide](USER_GUIDE.md).

## Failure and recovery / 故障与恢复

Use the in-app help and **Open Logs** for diagnosis. If the scientific runtime is unavailable, use the visible restart action; an interrupted run is not completed or certified. If COMSOL is unavailable, the interface may still open, but real COMSOL execution is unavailable. Consult [help and troubleshooting](TROUBLESHOOTING.md) for specific symptoms.

Do not post API keys, license material, user models, private workspaces, or unredacted local paths in an issue. Keep a backup of your workspace before manual repair or uninstall; uninstall is not an implicit request to delete user data.

## Acceptance boundary / 验收边界

A successful command is distinct from verified model state and passed scientific validation. A targeted COMSOL 6.3 heat worker solve was run separately on the new sidecar; it is **not** a complete desktop science acceptance test or evidence that every workflow works on a new user's machine. See [build limitations](../BUILDING.md) for the source/binary boundary.
