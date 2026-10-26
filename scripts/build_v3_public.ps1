[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FrozenSidecarPath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-F0-9]{64}$')][string]$ExpectedSidecarSha256,
    [Parameter(Mandatory = $true)][string]$TargetDir
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

try {
    $env:CARGO_TARGET_DIR = $target
    $env:SKY_FROZEN_SIDECAR_PATH = (Resolve-Path -LiteralPath $FrozenSidecarPath).Path
    $env:SKY_EXPECTED_SIDECAR_SHA256 = $ExpectedSidecarSha256
    $env:WORKBENCH_BUILD_PROFILE = 'public'
    # Tauri's Rust build script parses this variable as JSON. An empty value
    # can reach it through Windows child-process environment inheritance.
    # An explicit empty object is a neutral, controlled merge configuration.
    [Environment]::SetEnvironmentVariable('TAURI_CONFIG', '{}', 'Process')
    [Environment]::SetEnvironmentVariable('RUSTFLAGS', $null, 'Process')
    foreach ($name in $viteNames) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }

    # Rust panic locations otherwise retain the build user's home and source paths.
    $separator = [char]31
    $env:CARGO_ENCODED_RUSTFLAGS = @(
        "--remap-path-prefix=$($env:USERPROFILE)=/build-user",
        "--remap-path-prefix=$sourceRoot=/source"
    ) -join $separator

    Push-Location (Join-Path $sourceRoot 'frontend')
    try {
        & npm.cmd run tauri -- build --bundles nsis
        if ($LASTEXITCODE -ne 0) { throw 'Tauri/NSIS build failed.' }
    }
    finally { Pop-Location }
}
finally {
    foreach ($name in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
