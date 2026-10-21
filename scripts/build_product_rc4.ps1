$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$outputRoot = Join-Path $projectRoot "dist_rc4"
$payloadParent = Join-Path $outputRoot "installer_payload"
$payloadRoot = Join-Path $payloadParent "COMSOL Scientific Workbench"
$portableZip = Join-Path $outputRoot "COMSOL_Scientific_Workbench_RC4_Portable.zip"
$setupExe = Join-Path $outputRoot "COMSOL_Scientific_Workbench_RC4_Setup.exe"
$buildRoot = Join-Path $projectRoot "packaging\rc4_build"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$brandIcon = Join-Path $projectRoot "frontend\src-tauri\icons\icon.ico"
$ollamaRoot = Split-Path -Parent (Get-Command ollama.exe -ErrorAction Stop).Source
$modelRoot = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ollama\models' }

foreach ($target in @($outputRoot, $buildRoot)) {
    if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite existing RC4 output: $target" }
}
if (-not (Test-Path -LiteralPath $modelRoot -PathType Container)) { throw "Ollama model root not found: $modelRoot" }
if (-not (Test-Path -LiteralPath $brandIcon -PathType Leaf)) { throw "Workbench brand icon not found: $brandIcon" }
$free = (Get-PSDrive -Name ([IO.Path]::GetPathRoot($projectRoot).Substring(0,1))).Free
if ($free -lt 24GB) { throw "RC4 packaging requires at least 24 GiB free; found $([Math]::Round($free / 1GB, 2)) GiB" }

$versions = [ordered]@{
    backend = (& $python -c "from backend.app import __version__; print(__version__)" 2>$null)
    frontend = (Get-Content -LiteralPath (Join-Path $projectRoot "frontend\package.json") -Raw | ConvertFrom-Json).version
    tauri = (Get-Content -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
}
if (@($versions.Values | Where-Object { $_ -ne "1.1.0-rc4" }).Count -gt 0) { throw "Version consistency failed: $($versions | ConvertTo-Json -Compress)" }

Push-Location (Join-Path $projectRoot "frontend")
try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" } }
finally { Pop-Location }

& $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "packaging\scientific-runtime.spec") --distpath (Join-Path $buildRoot "runtime-dist") --workpath (Join-Path $buildRoot "runtime-work")
if ($LASTEXITCODE -ne 0) { throw "Scientific Runtime build failed" }
$sidecarSource = Join-Path $buildRoot "runtime-dist\scientific-runtime.exe"
$sidecarTarget = Join-Path $projectRoot "frontend\src-tauri\binaries\scientific-runtime-x86_64-pc-windows-msvc.exe"
Copy-Item -LiteralPath $sidecarSource -Destination $sidecarTarget -Force

Push-Location (Join-Path $projectRoot "frontend")
try {
    $env:WORKBENCH_BUILD_PROFILE = "public"
    & npm.cmd run tauri build -- --no-bundle
    if ($LASTEXITCODE -ne 0) { throw "Tauri production desktop build failed" }
}
finally { Pop-Location }

foreach ($item in @(
    @{Name="START_RC4"; Script="scripts\rc4_launcher.py"},
    @{Name="COMSOL_Scientific_Workbench_RC4_Setup"; Script="scripts\rc4_setup.py"}
)) {
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name $item.Name `
        --icon $brandIcon `
        --distpath (Join-Path $buildRoot "bootstrap-dist") `
        --workpath (Join-Path $buildRoot ("bootstrap-work-" + $item.Name)) `
        --specpath (Join-Path $buildRoot "spec") `
        (Join-Path $projectRoot $item.Script)
    if ($LASTEXITCODE -ne 0) { throw "$($item.Name) build failed" }
}

foreach ($directory in @(
    $payloadRoot,
    (Join-Path $payloadRoot "runtime\ollama"),
    (Join-Path $payloadRoot "models\ollama\blobs"),
    (Join-Path $payloadRoot "models\ollama\manifests\registry.ollama.ai\library\qwen3"),
    (Join-Path $payloadRoot "models\qwen3-8b"),
    (Join-Path $payloadRoot "models\qwen3-4b"),
    (Join-Path $payloadRoot "resources\knowledge_base"),
    (Join-Path $payloadRoot "resources\java_bridge"),
    (Join-Path $payloadRoot "resources\semantic_runtime"),
    (Join-Path $payloadRoot "LICENSES"),
    (Join-Path $payloadRoot "examples"),
    (Join-Path $payloadRoot "diagnostics"),
    (Join-Path $payloadRoot "logs"),
    (Join-Path $payloadRoot "workspace")
)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }

Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\target\release\comsol-scientific-workbench.exe") -Destination (Join-Path $payloadRoot "Workbench.exe")
Copy-Item -LiteralPath $sidecarSource -Destination (Join-Path $payloadRoot "scientific-runtime.exe")
Copy-Item -LiteralPath (Join-Path $buildRoot "bootstrap-dist\START_RC4.exe") -Destination (Join-Path $payloadRoot "START_RC4.exe")
Copy-Item -LiteralPath (Join-Path $buildRoot "bootstrap-dist\COMSOL_Scientific_Workbench_RC4_Setup.exe") -Destination $setupExe
Copy-Item -LiteralPath (Join-Path $ollamaRoot "ollama.exe") -Destination (Join-Path $payloadRoot "runtime\ollama\ollama.exe")
Copy-Item -LiteralPath (Join-Path $ollamaRoot "lib") -Destination (Join-Path $payloadRoot "runtime\ollama\lib") -Recurse

$manifestSources = [ordered]@{}
foreach ($tag in @("4b", "8b")) {
    $source = Join-Path $modelRoot ("manifests\registry.ollama.ai\library\qwen3\" + $tag)
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required model manifest missing: $source" }
    $manifestSources[$tag] = $source
    Copy-Item -LiteralPath $source -Destination (Join-Path $payloadRoot ("models\ollama\manifests\registry.ollama.ai\library\qwen3\" + $tag))
}
$digests = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($source in $manifestSources.Values) {
    $manifest = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
    [void]$digests.Add($manifest.config.digest)
    foreach ($layer in $manifest.layers) { [void]$digests.Add($layer.digest) }
}
foreach ($digest in $digests) {
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Unsafe model digest: $digest" }
    $blob = $digest.Replace(':','-')
    $source = Join-Path $modelRoot ("blobs\" + $blob)
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Required model blob missing: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $payloadRoot ("models\ollama\blobs\" + $blob))
}

Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\resources\knowledge_base\build_profile.json") -Destination (Join-Path $payloadRoot "resources\knowledge_base\build_profile.json")
$bridgeSource = Join-Path $projectRoot "frontend\src-tauri\resources\java_bridge\src"
$bridgeDestination = Join-Path $payloadRoot "resources\java_bridge\src"
New-Item -ItemType Directory -Force -Path $bridgeDestination | Out-Null
Get-ChildItem -LiteralPath $bridgeSource | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $bridgeDestination -Recurse
}
Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\resources\semantic_runtime\semantic_runtime_worker.py") -Destination (Join-Path $payloadRoot "resources\semantic_runtime\semantic_runtime_worker.py")

$documentCopies = @{
    "packaging\rc4\README_START_HERE.md" = "README_START_HERE.md"
    "packaging\rc4\TROUBLESHOOTING.md" = "TROUBLESHOOTING.md"
    "packaging\rc4\MODEL_ATTRIBUTION.md" = "MODEL_ATTRIBUTION.md"
    "packaging\rc4\DSH_AUDIT_INSTRUCTIONS.md" = "DSH_AUDIT_INSTRUCTIONS.md"
    "packaging\rc4\config.template.json" = "config.template.json"
    "packaging\rc2\licenses\WORKBENCH_LICENSE.txt" = "LICENSES\WORKBENCH_LICENSE.txt"
    "packaging\rc2\licenses\OLLAMA_LICENSE.txt" = "LICENSES\OLLAMA_LICENSE.txt"
    "THIRD_PARTY_NOTICES.md" = "LICENSES\THIRD_PARTY_NOTICES.md"
}
foreach ($sourceName in $documentCopies.Keys) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $sourceName) -Destination (Join-Path $payloadRoot $documentCopies[$sourceName])
}
$qwenLicense = Join-Path $modelRoot "blobs\sha256-d18a5cc71b84bc4af394a31116bd3932b42241de70c77d2b76d69a314ec8aa12"
if (-not (Test-Path -LiteralPath $qwenLicense -PathType Leaf)) { throw "Qwen license blob missing" }
Copy-Item -LiteralPath $qwenLicense -Destination (Join-Path $payloadRoot "LICENSES\QWEN3_LICENSE.txt")

foreach ($example in @("heat_request.txt", "tds_request.txt", "solid_request.txt", "electrochem_request.txt")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ("examples\" + $example)) -Destination (Join-Path $payloadRoot ("examples\" + $example))
}

$pointer4 = [ordered]@{ model="qwen3:4b"; digest="359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"; mode="LOW_VRAM_MODE"; ollama_manifest="../ollama/manifests/registry.ollama.ai/library/qwen3/4b"; weight_sha256="3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f" }
$pointer8 = [ordered]@{ model="qwen3:8b"; digest="500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"; mode="STANDARD"; ollama_manifest="../ollama/manifests/registry.ollama.ai/library/qwen3/8b"; weight_sha256="a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f" }
[IO.File]::WriteAllText((Join-Path $payloadRoot "models\qwen3-4b\MODEL_POINTER.json"), ($pointer4 | ConvertTo-Json) + "`n", [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $payloadRoot "models\qwen3-8b\MODEL_POINTER.json"), ($pointer8 | ConvertTo-Json) + "`n", [Text.UTF8Encoding]::new($false))
foreach ($keep in @("diagnostics\.keep", "logs\.keep", "workspace\.keep")) { [IO.File]::WriteAllText((Join-Path $payloadRoot $keep), "", [Text.UTF8Encoding]::new($false)) }

$forbiddenExtensions = @(".jar", ".mph", ".mphbin", ".pdf", ".lic", ".license")
$forbiddenNames = @(".env", "license.dat", "license.lic")
$forbidden = @(Get-ChildItem -LiteralPath $payloadRoot -File -Recurse | Where-Object { $forbiddenExtensions -contains $_.Extension.ToLowerInvariant() -or $forbiddenNames -contains $_.Name.ToLowerInvariant() })
if ($forbidden.Count -gt 0) { throw "Proprietary-content scan failed: $($forbidden.FullName -join ', ')" }
$secretHits = [Collections.Generic.List[string]]::new()
$absoluteHits = [Collections.Generic.List[string]]::new()
foreach ($file in Get-ChildItem -LiteralPath $payloadRoot -File -Recurse | Where-Object { $_.Length -lt 5MB -and $_.Extension -in @('.md','.txt','.json','.py','.java') }) {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    if ($content -match '(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*["''][A-Za-z0-9_\-]{16,}') { $secretHits.Add($file.FullName) }
    if ($content -match '(?i)(?:[A-Z]:\\Users\\|E:\\comsol_better\\|E:\\comsoldshnew\\)') { $absoluteHits.Add($file.FullName) }
}
if ($secretHits.Count -or $absoluteHits.Count) { throw "Payload safety scan failed: secrets=$($secretHits.Count), private_paths=$($absoluteHits.Count)" }

$entries = @(Get-ChildItem -LiteralPath $payloadRoot -File -Recurse | Where-Object { $_.Name -ne 'RC4_PAYLOAD_MANIFEST.json' } | Sort-Object FullName | ForEach-Object {
    [ordered]@{ path=$_.FullName.Substring($payloadRoot.Length + 1).Replace('\','/'); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$manifest = [ordered]@{
    schema_version="1.1"; product="COMSOL Scientific Workbench RC4"; version="1.1.0-rc4"; platform="windows-x86_64"; generated_at_utc=[DateTime]::UtcNow.ToString('o')
    setup_payload_external=$true; setup_contains_model_bytes=$false; proprietary_comsol_content_included=$false; private_kb_included=$false; secrets_included=$false
    model_policy=[ordered]@{ standard="qwen3:8b"; low_vram="qwen3:4b"; maximum="8b"; fallback_allowed=$false; standard_vram_threshold_mib=7168 }
    versions=$versions; entries=$entries
}
[IO.File]::WriteAllText((Join-Path $payloadRoot "RC4_PAYLOAD_MANIFEST.json"), ($manifest | ConvertTo-Json -Depth 7) + "`n", [Text.UTF8Encoding]::new($false))

& $python (Join-Path $projectRoot "scripts\create_rc4_zip.py") $payloadRoot $portableZip
if ($LASTEXITCODE -ne 0) { throw "Portable ZIP creation or integrity check failed" }
if ((Get-Item -LiteralPath $setupExe).Length -ge 50MB) { throw "Setup executable unexpectedly contains a large embedded payload" }

$result = [ordered]@{
    marker="RC4_ARTIFACTS_BUILT"; version="1.1.0-rc4"
    setup=$setupExe; setup_bytes=(Get-Item -LiteralPath $setupExe).Length; setup_sha256=(Get-FileHash -LiteralPath $setupExe -Algorithm SHA256).Hash.ToLowerInvariant()
    payload=$payloadRoot; payload_bytes=($entries | ForEach-Object { $_['bytes'] } | Measure-Object -Sum).Sum; payload_files=$entries.Count
    portable_zip=$portableZip; portable_zip_bytes=(Get-Item -LiteralPath $portableZip).Length; portable_zip_sha256=(Get-FileHash -LiteralPath $portableZip -Algorithm SHA256).Hash.ToLowerInvariant()
    proprietary_hits=0; secret_hits=0; private_absolute_path_hits=0; versions=$versions
}
[IO.File]::WriteAllText((Join-Path $outputRoot "RC4_BUILD_RESULT.json"), ($result | ConvertTo-Json -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
$result | ConvertTo-Json -Depth 5
