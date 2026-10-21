[CmdletBinding()]
param(
    [ValidateSet('research', 'public')]
    [string]$BuildProfile = $(if ($env:WORKBENCH_BUILD_PROFILE) { $env:WORKBENCH_BUILD_PROFILE } else { 'public' }),
    [switch]$ProbeOnly,
    [string]$FrozenSidecarPath = $env:SKY_FROZEN_SIDECAR_PATH
)

$ErrorActionPreference = 'Stop'
$workbenchRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$frontendRoot = Join-Path $workbenchRoot 'frontend'
$tauriRoot = Join-Path $frontendRoot 'src-tauri'
$python = Join-Path $workbenchRoot '.venv\Scripts\python.exe'
$kbTarget = Join-Path $tauriRoot 'resources\knowledge_base'
$javaSource = Join-Path $workbenchRoot 'java_bridge'
$javaTarget = Join-Path $tauriRoot 'resources\java_bridge'
$semanticSource = Join-Path $workbenchRoot 'scripts\semantic_runtime_worker.py'
$semanticTarget = Join-Path $tauriRoot 'resources\semantic_runtime'
$binaryTarget = Join-Path $tauriRoot 'binaries\scientific-runtime-x86_64-pc-windows-msvc.exe'
$runtimeArtifact = Join-Path $workbenchRoot 'packaging\dist\scientific-runtime.exe'

# Compute the same SHA-256 in Windows PowerShell 5.1 without relying on
# Microsoft.PowerShell.Utility command discovery in the nested Tauri/npm shell.
function Get-DesktopFileSha256 {
  param([Parameter(Mandatory = $true)][string]$LiteralPath)
  $algorithm = [System.Security.Cryptography.SHA256]::Create()
  try {
    $stream = [System.IO.File]::Open($LiteralPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try { return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose() }
  }
  finally { $algorithm.Dispose() }
}

if ($ProbeOnly) {
  # This probe follows the npm script into Windows PowerShell, but stops before
  # frontend compilation, PyInstaller, copying, or any release output mutation.
  $module = Get-Module -ListAvailable Microsoft.PowerShell.Utility | Select-Object -First 1
  $command = Get-Command Get-FileHash -ErrorAction SilentlyContinue
  $systemModulePath = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility'
  $modulePaths = @($env:PSModulePath -split ';' | Where-Object { $_ })
  $loadedBefore = [bool](Get-Module Microsoft.PowerShell.Utility)
  $importWorks = $false
  try {
    Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
    $importWorks = [bool](Get-Command Get-FileHash -ErrorAction SilentlyContinue)
  }
  catch { $importWorks = $false }
  $loadedModule = Get-Module Microsoft.PowerShell.Utility
  Write-Output "RC52_CHILD_PROBE version=$($PSVersionTable.PSVersion) executable=$((Get-Process -Id $PID).Path) utility_module_available=$([bool]$module) utility_loaded_before=$loadedBefore get_file_hash_before=$([bool]$command) autoload_preference=$PSModuleAutoLoadingPreference import_restores_command=$importWorks module_version=$($loadedModule.Version) module_path=$($loadedModule.Path) module_exports_hash=$($loadedModule.ExportedCommands.ContainsKey('Get-FileHash')) module_path_entries=$($modulePaths.Count) system_module_path_present=$([bool](Test-Path -LiteralPath $systemModulePath))"
  Write-Output "RC52_CHILD_MODULE_PATHS=$($modulePaths -join '|')"
  return
}

if ($BuildProfile -ne 'public') {
  # Private research material must never pass through the desktop installer path.
  throw 'Desktop packaging is public-only. Use the separate research harness for private Knowledge Base work.'
}

& npm.cmd run build --prefix $frontendRoot
if ($LASTEXITCODE -ne 0) { throw 'React production build failed.' }

New-Item -ItemType Directory -Force -Path $kbTarget, $javaTarget, $semanticTarget, (Split-Path -Parent $binaryTarget) | Out-Null
$resolvedKbTarget = [System.IO.Path]::GetFullPath($kbTarget)
$resolvedTauriRoot = [System.IO.Path]::GetFullPath($tauriRoot)
if (-not $resolvedKbTarget.StartsWith($resolvedTauriRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Refusing to clean Knowledge Base staging outside the Tauri project: $resolvedKbTarget"
}
$unexpectedKb = @(Get-ChildItem -LiteralPath $resolvedKbTarget -Force | Where-Object { $_.Name -notin @('.gitkeep', 'build_profile.json') })
if ($unexpectedKb.Count -ne 0) {
  # Preserve unexpected material as audit evidence; do not delete it during a build.
  throw 'Knowledge Base staging contains non-public files. Quarantine them through an explicit audited procedure before packaging.'
}
$profilePayload = [ordered]@{
  profile = 'public'
  version = '2.0.0'
  runtime_version = '1.1.0-rc5.2'
  proprietary_kb_included = $false
  policy = 'public-safe-no-comsol-derived-kb'
}


Copy-Item -LiteralPath (Join-Path $javaSource 'src') -Destination $javaTarget -Recurse -Force
Copy-Item -LiteralPath $semanticSource -Destination (Join-Path $semanticTarget 'semantic_runtime_worker.py') -Force
# This isolated candidate accepts only the exact OpenSSL-updated sidecar tested
# for local Runtime health and authentication; public approval remains separate.
if ($FrozenSidecarPath) {
  $expectedFrozenHash = 'CE8E1412E791F68198D3426C7685C27D2208F8AB387534374A21478302DFC183'
  if (-not (Test-Path -LiteralPath $FrozenSidecarPath -PathType Leaf)) {
    throw 'SKY_FROZEN_SIDECAR_PATH does not identify a file.'
  }
  $builtHash = Get-DesktopFileSha256 -LiteralPath $FrozenSidecarPath
  if ($builtHash -cne $expectedFrozenHash) {
    throw 'OpenSSL-updated sidecar hash does not match the pinned isolated build input.'
  }
  Copy-Item -LiteralPath $FrozenSidecarPath -Destination $binaryTarget -Force
  $sidecarHash = Get-DesktopFileSha256 -LiteralPath $binaryTarget
  if ($sidecarHash -cne $expectedFrozenHash) { throw 'Staged sidecar bytes differ from frozen input.' }
} else {
  # Source-build mode is separate; its result must be re-audited for release.
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Candidate .venv Python is unavailable. Install packaging/requirements-build.txt in an independent candidate virtual environment.'
  }
  if ((Get-Item -LiteralPath (Join-Path $workbenchRoot '.venv')).LinkType) {
    throw 'Candidate .venv must be an independent directory, not a junction to another Workbench.'
  }
  & $python -m PyInstaller --version 2>$null
  if ($LASTEXITCODE -ne 0) { throw 'PyInstaller is required in the independent candidate .venv; install packaging/requirements-build.txt.' }
  & $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $workbenchRoot 'packaging\dist') --workpath (Join-Path $workbenchRoot 'packaging\build') (Join-Path $workbenchRoot 'packaging\scientific-runtime.spec')
  if ($LASTEXITCODE -ne 0) { throw 'Scientific Runtime PyInstaller build failed.' }
  if (-not (Test-Path -LiteralPath $runtimeArtifact -PathType Leaf) -or (Get-Item -LiteralPath $runtimeArtifact).Length -eq 0) {
    throw 'Candidate PyInstaller did not produce a nonempty scientific Runtime executable.'
  }
  Copy-Item -LiteralPath $runtimeArtifact -Destination $binaryTarget -Force
  $builtHash = Get-DesktopFileSha256 -LiteralPath $runtimeArtifact
  $sidecarHash = Get-DesktopFileSha256 -LiteralPath $binaryTarget
  if ($builtHash -cne $sidecarHash) { throw 'Tauri sidecar bytes differ from candidate PyInstaller output.' }
}

$profilePayload['runtime_binary_sha256'] = $sidecarHash
$profilePayload | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $kbTarget 'build_profile.json') -Encoding utf8

Write-Host "Desktop resources ready: $tauriRoot ($BuildProfile profile); sidecar SHA-256: $sidecarHash"
