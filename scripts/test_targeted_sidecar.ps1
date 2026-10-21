param(
  [Parameter(Mandatory = $true)][string]$Sidecar,
  [Parameter(Mandatory = $true)][string]$Workspace
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath($Workspace)
$expectedRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $workspaceRoot.StartsWith($expectedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'The test workspace must be inside the isolated candidate root.'
}
if (Test-Path -LiteralPath $workspaceRoot) { throw 'Use a new empty test workspace.' }
New-Item -ItemType Directory -Path $workspaceRoot | Out-Null

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$tokenBytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
$token = [BitConverter]::ToString($tokenBytes).Replace('-', '').ToLowerInvariant()
$priorToken = [Environment]::GetEnvironmentVariable('COMSOL_WORKBENCH_SESSION_TOKEN', 'Process')
$process = $null
try {
  [Environment]::SetEnvironmentVariable('COMSOL_WORKBENCH_SESSION_TOKEN', $token, 'Process')
  $process = Start-Process -FilePath $Sidecar -ArgumentList @('--desktop', '--host', '127.0.0.1', '--port', $port, '--workspace-dir', $workspaceRoot, '--app-data-dir', $workspaceRoot) -PassThru -WindowStyle Hidden
} finally {
  [Environment]::SetEnvironmentVariable('COMSOL_WORKBENCH_SESSION_TOKEN', $priorToken, 'Process')
}

$base = "http://127.0.0.1:$port"
$ready = $false
try {
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($process.HasExited) { throw "Sidecar exited before health: $($process.ExitCode)" }
    try {
      $health = Invoke-WebRequest -UseBasicParsing -Uri "$base/api/health" -Headers @{'X-Workbench-Token' = $token} -TimeoutSec 2
      if ($health.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $ready) { throw 'Authenticated health did not return HTTP 200.' }
  $unauthorizedStatus = 0
  try { Invoke-WebRequest -UseBasicParsing -Uri "$base/api/health" -TimeoutSec 3 | Out-Null }
  catch { $unauthorizedStatus = [int]$_.Exception.Response.StatusCode }
  if ($unauthorizedStatus -ne 401) { throw "Unauthenticated health returned $unauthorizedStatus instead of 401." }
  $shutdown = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/api/desktop/shutdown" -Headers @{'X-Workbench-Token' = $token} -TimeoutSec 5
  if ($shutdown.StatusCode -ne 200) { throw 'Authorized shutdown failed.' }
  if (-not $process.WaitForExit(15000)) { throw 'Sidecar did not exit after authorized shutdown.' }
  [pscustomobject]@{health=200; unauthenticated=401; shutdown=200; exit_code=$process.ExitCode; pid=$process.Id; workspace=$workspaceRoot} | ConvertTo-Json
} finally {
  if ($process -and -not $process.HasExited) {
    # Only the exact child created by this test is a permissible termination target.
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    $process.WaitForExit(5000) | Out-Null
  }
}
