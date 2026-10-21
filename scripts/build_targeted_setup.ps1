param([Parameter(Mandatory = $true)][string]$Sidecar)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$candidateRoot = [IO.Path]::GetFullPath((Join-Path $root '..'))
$expected = '89ED868FFB28D2EBF54F5F4D33CF902BF8C50D20CE76629D85203DF4451D2FEF'
if ((Get-FileHash -LiteralPath $Sidecar -Algorithm SHA256).Hash -cne $expected) {
  throw 'The isolated sidecar differs from the tested build input.'
}
$existingMapping = @(cmd /c subst | Where-Object { $_ -like 'R:\:*' })
if ($existingMapping.Count -gt 0 -and $existingMapping[0] -ne "R:\: => $candidateRoot") {
  throw 'R: is mapped to an unrelated target.'
}
$createdMapping = $false
if ($existingMapping.Count -eq 0) {
  & cmd /c subst R: $candidateRoot
  if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated R: path mapping.' }
  $createdMapping = $true
}
if (Test-Path -LiteralPath 'R:\cargo_target_official_ssl') { throw 'Use a fresh, empty isolated Cargo target.' }
New-Item -ItemType Directory -Path 'R:\cargo_target_official_ssl' | Out-Null

# Child build processes receive no inherited secret-bearing desktop variables.
Get-ChildItem Env: | Where-Object { $_.Name -match '^(VITE_|TAURI_CONFIG$|.*(TOKEN|SECRET|PRIVATE_KEY|API_KEY|PASSWORD).*)' } |
  ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }
$env:SKY_FROZEN_SIDECAR_PATH = $Sidecar
$env:WORKBENCH_BUILD_PROFILE = 'public'
$env:CARGO_TARGET_DIR = 'R:\cargo_target_official_ssl'
$env:CARGO_INCREMENTAL = '0'
# Rust dependencies otherwise embed the builder's user-profile path in the
# production PE. Remap only debug/source locations, not Runtime behavior.
$profilePath = [Environment]::GetFolderPath('UserProfile')
$env:RUSTFLAGS = "--remap-path-prefix=$profilePath=R:\rust-deps --remap-path-prefix=$root=R:\source"

Push-Location (Join-Path $root 'frontend')
try {
  & npm.cmd run tauri -- build --bundles nsis
  if ($LASTEXITCODE -ne 0) { throw "Tauri/NSIS build failed with exit code $LASTEXITCODE" }
} finally {
  Pop-Location
  if ($createdMapping) {
    $currentMapping = @(cmd /c subst | Where-Object { $_ -like 'R:\:*' })
    if ($currentMapping.Count -eq 1 -and $currentMapping[0] -eq "R:\: => $candidateRoot") {
      & cmd /c subst R: /D | Out-Null
    }
  }
}
