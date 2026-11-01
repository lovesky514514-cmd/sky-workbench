[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FrozenSidecarPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-F0-9]{64}$')][string]$ExpectedSidecarSha256,
    [Parameter(Mandatory = $true)][string]$TargetDir,
    [switch]$LabPreview
)

$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$candidateRoot = (Resolve-Path -LiteralPath (Join-Path $sourceRoot '..')).Path
$target = [IO.Path]::GetFullPath($TargetDir)
$allowedPrefix = $candidateRoot.TrimEnd('\') + '\'
if (-not $target.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The build target must stay inside the isolated candidate.'
}

# A path under a junction can escape the candidate even when its text looks safe.
$parent = Split-Path -Parent $target
while ($parent.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    if ((Test-Path -LiteralPath $parent) -and
        ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The build target has a reparse-point parent.'
    }
    $parent = Split-Path -Parent $parent
}
if ((Test-Path -LiteralPath $target) -and
    ((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The build target is a reparse point.'
}
if ((Test-Path -LiteralPath $target) -and @(Get-ChildItem -LiteralPath $target -Force).Count -gt 0) {
    throw 'The build target is not empty.'
}
if (-not (Test-Path -LiteralPath $FrozenSidecarPath -PathType Leaf)) {
    throw 'The frozen sidecar is missing.'
}
$actualHash = (Get-FileHash -LiteralPath $FrozenSidecarPath -Algorithm SHA256).Hash
if ($actualHash -cne $ExpectedSidecarSha256) {
    throw 'The frozen sidecar hash does not match.'
}

$saved = @{}
foreach ($name in @('CARGO_TARGET_DIR', 'CARGO_ENCODED_RUSTFLAGS', 'RUSTFLAGS',
                    'SKY_FROZEN_SIDECAR_PATH', 'SKY_EXPECTED_SIDECAR_SHA256',
                    'WORKBENCH_BUILD_PROFILE', 'TAURI_CONFIG')) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$viteNames = @(Get-ChildItem Env:VITE_* -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
foreach ($name in $viteNames) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$sensitiveNames = @(Get-ChildItem Env: | Where-Object { $_.Name -match 'TOKEN|SECRET|API_KEY|PASSWORD' } | Select-Object -ExpandProperty Name)
foreach ($name in $sensitiveNames) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

try {
    $env:CARGO_TARGET_DIR = $target
    $env:SKY_FROZEN_SIDECAR_PATH = (Resolve-Path -LiteralPath $FrozenSidecarPath).Path
    $env:SKY_EXPECTED_SIDECAR_SHA256 = $ExpectedSidecarSha256
    $env:WORKBENCH_BUILD_PROFILE = 'public'
    # Tauri's Rust build script parses this variable as JSON. An empty value
    # can reach it through Windows child-process environment inheritance.
    # Both the desktop title and a fresh provider profile belong to this build;
    # the installer hook preserves any settings already saved by the user.
    $base = Get-Content -LiteralPath (Join-Path $sourceRoot 'frontend/src-tauri/tauri.conf.json') -Raw | ConvertFrom-Json
    $base.app.windows[0].title = 'SKY Workbench 3.1'
    $overlay = @{
        app = @{ windows = @($base.app.windows) }
        bundle = @{ windows = @{ nsis = @{
            installerHooks = (Join-Path $sourceRoot 'packaging/provider_defaults.nsh')
        } } }
    }
    if ($LabPreview) {
        # A separate identifier gives the lab build its own default data path.
        # Its product/shortcut names also remain separate from the regular app.
        $base = Get-Content -LiteralPath (Join-Path $sourceRoot 'frontend/src-tauri/tauri.conf.json') -Raw | ConvertFrom-Json
        $base.app.windows[0].title = 'SKY Workbench 3.1 Lab'
        $overlay = @{
            productName = 'SKY Workbench 3.1 Lab'
            identifier = 'io.scientificworkbench.sky.lab31'
            app = @{ windows = @($base.app.windows) }
            bundle = @{ windows = @{ nsis = @{
                startMenuFolder = 'SKY Workbench 3.1 Lab'
                installerHooks = (Join-Path $sourceRoot 'packaging/lab_defaults.nsh')
            } } }
        }
    }
    $overlayJson = $overlay | ConvertTo-Json -Depth 20 -Compress
    [Environment]::SetEnvironmentVariable('TAURI_CONFIG', $overlayJson, 'Process')
    [Environment]::SetEnvironmentVariable('RUSTFLAGS', $null, 'Process')
    foreach ($name in $viteNames) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    foreach ($name in $sensitiveNames) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    # The CLI bundler and Rust codegen must consume the same overlay. The
    # environment variable alone is only authoritative for Rust codegen.
    $overlayPath = Join-Path $target 'tauri.build.config.json'
    [IO.File]::WriteAllText($overlayPath, $overlayJson, (New-Object Text.UTF8Encoding($false)))
    $buildInputs = @{
        profile = $(if ($LabPreview) { 'lab-preview' } else { 'public-release' })
        sidecar_sha256 = $actualHash
        tauri_config_sha256 = (Get-FileHash -LiteralPath (Join-Path $sourceRoot 'frontend/src-tauri/tauri.conf.json') -Algorithm SHA256).Hash
        configuration_overlay = $overlay
    } | ConvertTo-Json -Depth 25
    [IO.File]::WriteAllText((Join-Path $target 'INSTALLER_BUILD_INPUTS.json'), $buildInputs, (New-Object Text.UTF8Encoding($false)))

    # Rust panic locations otherwise retain the build user's home and source paths.
    $separator = [char]31
    $env:CARGO_ENCODED_RUSTFLAGS = @(
        "--remap-path-prefix=$($env:USERPROFILE)=/build-user",
        "--remap-path-prefix=$sourceRoot=/source"
    ) -join $separator

    Push-Location (Join-Path $sourceRoot 'frontend')
    try {
        & npm.cmd run tauri -- build --bundles nsis --config $overlayPath
        if ($LASTEXITCODE -ne 0) { throw 'Tauri/NSIS build failed.' }
    }
    finally { Pop-Location }
}
finally {
    foreach ($name in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
