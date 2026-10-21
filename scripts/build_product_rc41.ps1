$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$outputRoot = Join-Path $projectRoot "dist_rc41"
$payloadParent = Join-Path $outputRoot "installer_payload"
$payloadRoot = Join-Path $payloadParent "COMSOL Scientific Workbench"
$portableZip = Join-Path $outputRoot "COMSOL_Scientific_Workbench_RC4.1_Portable.zip"
$setupExe = Join-Path $outputRoot "COMSOL_Scientific_Workbench_RC4.1_Setup.exe"
$buildRoot = Join-Path $projectRoot "packaging\rc41_build"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$brandIcon = Join-Path $projectRoot "frontend\src-tauri\icons\icon.ico"

foreach ($target in @($outputRoot, $buildRoot)) {
    if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite existing RC4.1 output: $target" }
}
if (-not (Test-Path -LiteralPath $brandIcon -PathType Leaf)) { throw "Workbench brand icon not found: $brandIcon" }

$cargoText = Get-Content -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\Cargo.toml") -Raw
$versions = [ordered]@{
    backend = (& $python -c "from backend.app import __version__; print(__version__)" 2>$null)
    frontend = (Get-Content -LiteralPath (Join-Path $projectRoot "frontend\package.json") -Raw | ConvertFrom-Json).version
    tauri = (Get-Content -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
    rust = [regex]::Match($cargoText, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
}
if (@($versions.Values | Where-Object { $_ -ne "1.1.0-rc4.1" }).Count -gt 0) { throw "Version consistency failed: $($versions | ConvertTo-Json -Compress)" }

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
    @{Name="START_RC41"; Script="scripts\rc41_launcher.py"},
    @{Name="COMSOL_Scientific_Workbench_RC41_Setup"; Script="scripts\rc41_setup.py"}
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
    (Join-Path $payloadRoot "resources\knowledge_base"),
    (Join-Path $payloadRoot "resources\java_bridge\src"),
    (Join-Path $payloadRoot "resources\semantic_runtime"),
    (Join-Path $payloadRoot "LICENSES"),
    (Join-Path $payloadRoot "examples")
)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }

Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\target\release\comsol-scientific-workbench.exe") -Destination (Join-Path $payloadRoot "Workbench.exe")
Copy-Item -LiteralPath $sidecarSource -Destination (Join-Path $payloadRoot "scientific-runtime.exe")
Copy-Item -LiteralPath (Join-Path $buildRoot "bootstrap-dist\START_RC41.exe") -Destination (Join-Path $payloadRoot "START_RC41.exe")
Copy-Item -LiteralPath (Join-Path $buildRoot "bootstrap-dist\COMSOL_Scientific_Workbench_RC41_Setup.exe") -Destination $setupExe

Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\resources\knowledge_base\build_profile.json") -Destination (Join-Path $payloadRoot "resources\knowledge_base\build_profile.json")
$bridgeSource = Join-Path $projectRoot "frontend\src-tauri\resources\java_bridge\src"
$bridgeDestination = Join-Path $payloadRoot "resources\java_bridge\src"
Get-ChildItem -LiteralPath $bridgeSource | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $bridgeDestination -Recurse }
Copy-Item -LiteralPath (Join-Path $projectRoot "frontend\src-tauri\resources\semantic_runtime\semantic_runtime_worker.py") -Destination (Join-Path $payloadRoot "resources\semantic_runtime\semantic_runtime_worker.py")

$documentCopies = @{
    "packaging\rc41\README_START_HERE.md" = "README_START_HERE.md"
    "packaging\rc41\TROUBLESHOOTING.md" = "TROUBLESHOOTING.md"
    "packaging\rc41\MODEL_ATTRIBUTION.md" = "MODEL_ATTRIBUTION.md"
    "packaging\rc41\config.template.json" = "config.template.json"
    "packaging\rc2\licenses\WORKBENCH_LICENSE.txt" = "LICENSES\WORKBENCH_LICENSE.txt"
    "THIRD_PARTY_NOTICES.md" = "LICENSES\THIRD_PARTY_NOTICES.md"
}
foreach ($sourceName in $documentCopies.Keys) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $sourceName) -Destination (Join-Path $payloadRoot $documentCopies[$sourceName])
}
foreach ($example in @("heat_request.txt", "tds_request.txt", "solid_request.txt", "electrochem_request.txt")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ("examples\" + $example)) -Destination (Join-Path $payloadRoot ("examples\" + $example))
}

$forbiddenExtensions = @(".jar", ".mph", ".mphbin", ".pdf", ".lic", ".license", ".ts", ".tsx", ".rs")
$forbiddenNames = @(".env", "license.dat", "license.lic")
$forbiddenDirectories = @("node_modules", "target", ".git", "tests", "screenshots", "research", "artifacts", "logs", "cache", "__pycache__")
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -File -Recurse)
$forbidden = @($payloadFiles | Where-Object {
    $relative = $_.FullName.Substring($payloadRoot.Length + 1)
    $segments = $relative.Split([IO.Path]::DirectorySeparatorChar)
    $forbiddenExtensions -contains $_.Extension.ToLowerInvariant() -or
    $forbiddenNames -contains $_.Name.ToLowerInvariant() -or
    @($segments | Where-Object { $forbiddenDirectories -contains $_.ToLowerInvariant() }).Count -gt 0
})
if ($forbidden.Count -gt 0) { throw "Clean-payload scan failed: $($forbidden.FullName -join ', ')" }
$unexpectedSource = @($payloadFiles | Where-Object {
    $_.Extension.ToLowerInvariant() -in @('.py','.java') -and
    -not $_.FullName.StartsWith((Join-Path $payloadRoot "resources\java_bridge"), [StringComparison]::OrdinalIgnoreCase) -and
    $_.FullName -ne (Join-Path $payloadRoot "resources\semantic_runtime\semantic_runtime_worker.py")
})
if ($unexpectedSource.Count -gt 0) { throw "Unexpected source file in payload: $($unexpectedSource.FullName -join ', ')" }

$secretHits = [Collections.Generic.List[string]]::new()
$absoluteHits = [Collections.Generic.List[string]]::new()
foreach ($file in $payloadFiles | Where-Object { $_.Length -lt 5MB -and $_.Extension -in @('.md','.txt','.json','.py','.java') }) {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    if ($content -match '(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*["''][A-Za-z0-9_\-]{16,}') { $secretHits.Add($file.FullName) }
    if ($content -match '(?i)(?:[A-Z]:\\Users\\|E:\\comsol_better\\|E:\\comsoldshnew\\|E:\\\u6570\u636e\u96c6\\)') { $absoluteHits.Add($file.FullName) }
}
if ($secretHits.Count -or $absoluteHits.Count) { throw "Payload safety scan failed: secrets=$($secretHits.Count), private_paths=$($absoluteHits.Count)" }

$entries = @($payloadFiles | Sort-Object FullName | ForEach-Object {
    [ordered]@{ path=$_.FullName.Substring($payloadRoot.Length + 1).Replace('\','/'); bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$manifest = [ordered]@{
    schema_version="1.2"; product="COMSOL Scientific Workbench RC4.1"; version="1.1.0-rc4.1"; distribution="lite"; platform="windows-x86_64"; generated_at_utc=[DateTime]::UtcNow.ToString('o')
    setup_payload_external=$true; model_weights_included=$false; ollama_runtime_included=$false; proprietary_comsol_content_included=$false; private_kb_included=$false; secrets_included=$false
    external_dependencies=[ordered]@{ comsol="6.3 installation and valid license for RUN"; ai_provider="Ollama, LM Studio, or configured cloud provider"; local_models=@("qwen3:8b", "qwen3:4b") }
    model_policy=[ordered]@{ standard="qwen3:8b"; low_vram="qwen3:4b"; maximum="8b"; fallback_allowed=$false; silent_download_allowed=$false; standard_vram_threshold_mib=7168; models_root_env="OLLAMA_MODELS" }
    versions=$versions; entries=$entries
}
[IO.File]::WriteAllText((Join-Path $payloadRoot "RC41_PAYLOAD_MANIFEST.json"), ($manifest | ConvertTo-Json -Depth 7) + "`n", [Text.UTF8Encoding]::new($false))

& $python (Join-Path $projectRoot "scripts\create_rc41_zip.py") $payloadRoot $portableZip
if ($LASTEXITCODE -ne 0) { throw "Portable ZIP creation or integrity check failed" }
if ((Get-Item -LiteralPath $setupExe).Length -ge 50MB) { throw "Setup executable unexpectedly contains a large embedded payload" }

$result = [ordered]@{
    marker="RC41_ARTIFACTS_BUILT"; version="1.1.0-rc4.1"; distribution="lite"
    setup=$setupExe; setup_bytes=(Get-Item -LiteralPath $setupExe).Length; setup_sha256=(Get-FileHash -LiteralPath $setupExe -Algorithm SHA256).Hash.ToLowerInvariant()
    payload=$payloadRoot; payload_bytes=($entries | ForEach-Object { $_['bytes'] } | Measure-Object -Sum).Sum; payload_files=$entries.Count
    portable_zip=$portableZip; portable_zip_bytes=(Get-Item -LiteralPath $portableZip).Length; portable_zip_sha256=(Get-FileHash -LiteralPath $portableZip -Algorithm SHA256).Hash.ToLowerInvariant()
    model_weight_bytes=0; ollama_runtime_bytes=0; proprietary_hits=0; secret_hits=0; private_absolute_path_hits=0; versions=$versions
}
[IO.File]::WriteAllText((Join-Path $outputRoot "RC41_BUILD_RESULT.json"), ($result | ConvertTo-Json -Depth 5) + "`n", [Text.UTF8Encoding]::new($false))
$result | ConvertTo-Json -Depth 5
