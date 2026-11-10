# Public startup script - starts services + Cloudflare tunnels
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

$TEMP_DIR = "$env:TEMP\bullysim_tunnels"
New-Item -ItemType Directory -Force -Path $TEMP_DIR | Out-Null

function Write-Step($msg) { Write-Host "  $msg" -ForegroundColor Gray }
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [ERROR] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  BullySim - Public Launch" -ForegroundColor Green
Write-Host "  (Cloudflare Tunnel)" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. Check environment
# ============================================================
Write-Step "Checking environment..."

$pythonOk = $true
try { python --version 2>&1 | Out-Null } catch { $pythonOk = $false }
if (-not $pythonOk) {
    Write-Err "Python not found. Please install Python 3.11+"
    pause; exit 1
}
Write-OK "Python ready"

$cfOk = $true
try { cloudflared version 2>&1 | Out-Null } catch { $cfOk = $false }
if (-not $cfOk) {
    Write-Err "cloudflared not found"
    Write-Host "  Install: winget install cloudflared" -ForegroundColor Yellow
    Write-Host "  Or download: https://github.com/cloudflare/cloudflared/releases" -ForegroundColor Yellow
    pause; exit 1
}
Write-OK "cloudflared ready"

# ============================================================
# 2. Check .env (API Key)
# ============================================================
Write-Step "Checking API Key..."

$finEnv = Join-Path $ROOT "fin_ver\.env"
$noresetEnv = Join-Path $ROOT "noreset\.env"

$envOk = $true
if (-not (Test-Path $finEnv)) {
    Write-Err "fin_ver\.env not found"
    $envOk = $false
}
if (-not (Test-Path $noresetEnv)) {
    Write-Err "noreset\.env not found"
    $envOk = $false
}
if (-not $envOk) {
    Write-Host "  Run startup.bat first to configure API Key" -ForegroundColor Yellow
    pause; exit 1
}

$finMatch = Select-String -Path $finEnv -Pattern 'DEEPSEEK_API_KEY=(\S+)'
$noresetMatch = Select-String -Path $noresetEnv -Pattern 'DEEPSEEK_API_KEY=(\S+)'
$finKey = if ($finMatch) { $finMatch.Matches.Groups[1].Value } else { $null }
$noresetKey = if ($noresetMatch) { $noresetMatch.Matches.Groups[1].Value } else { $null }

if ($finKey -and $finKey -ne 'your_deepseek_api_key_here') {
    Write-OK "fin_ver API Key: $($finKey.Substring(0, [Math]::Min(4, $finKey.Length)))***"
} else {
    Write-Err "fin_ver API Key not configured"
    pause; exit 1
}
if ($noresetKey -and $noresetKey -ne 'your_deepseek_api_key_here') {
    Write-OK "noreset API Key: $($noresetKey.Substring(0, [Math]::Min(4, $noresetKey.Length)))***"
} else {
    Write-Warn "noreset API Key not configured"
}

# ============================================================
# 3. Kill old processes
# ============================================================
Write-Step "Stopping old instances..."
Get-Process -Name "python" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Write-OK "Old processes cleaned"

# ============================================================
# 4. Start local services
# ============================================================
Write-Step "Starting local services..."

$procFin = Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory (Join-Path $ROOT "fin_ver") -PassThru -WindowStyle Hidden
Write-OK "fin_ver started (PID $($procFin.Id))"

Start-Sleep 3

$procNo = Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory (Join-Path $ROOT "noreset") -PassThru -WindowStyle Hidden
Write-OK "noreset started (PID $($procNo.Id))"

Write-Step "Waiting for services..."
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $r5000 = Invoke-WebRequest -Uri "http://127.0.0.1:5000" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $r5001 = Invoke-WebRequest -Uri "http://127.0.0.1:5001" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $ready = $true
        break
    } catch {}
    Start-Sleep 2
}
if (-not $ready) {
    Write-Err "Local services timed out"
    Stop-Process -Id $procFin.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $procNo.Id -Force -ErrorAction SilentlyContinue
    pause; exit 1
}
Write-OK "Local services ready"

# ============================================================
# 5. Start Cloudflare tunnels
# ============================================================
Write-Step "Starting Cloudflare tunnels..."

Remove-Item "$TEMP_DIR\cloudflared_*" -Force -ErrorAction SilentlyContinue

$procCf5000 = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel", "--url", "http://localhost:5000", "--protocol", "http2" `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$TEMP_DIR\cloudflared_5000_stdout.log" `
    -RedirectStandardError "$TEMP_DIR\cloudflared_5000_stderr.log"
Write-OK "fin_ver tunnel started (PID $($procCf5000.Id))"

$procCf5001 = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel", "--url", "http://localhost:5001", "--protocol", "http2" `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$TEMP_DIR\cloudflared_5001_stdout.log" `
    -RedirectStandardError "$TEMP_DIR\cloudflared_5001_stderr.log"
Write-OK "noreset tunnel started (PID $($procCf5001.Id))"

# ============================================================
# 6. Get public URLs
# ============================================================
Write-Host ""
Write-Step "Obtaining public URLs..."

function Get-TunnelURL($stdoutLog, $stderrLog, $timeoutSec = 45) {
    $start = Get-Date
    while (((Get-Date) - $start).TotalSeconds -lt $timeoutSec) {
        foreach ($log in @($stdoutLog, $stderrLog)) {
            if (Test-Path $log) {
                $content = Get-Content $log -Raw -ErrorAction SilentlyContinue
                if ($content -match '(https://[a-z0-9-]+\.trycloudflare\.com)') {
                    return $Matches[1]
                }
            }
        }
        Write-Host "  ." -NoNewline -ForegroundColor DarkGray
        Start-Sleep 2
    }
    return $null
}

$url5000 = Get-TunnelURL "$TEMP_DIR\cloudflared_5000_stdout.log" "$TEMP_DIR\cloudflared_5000_stderr.log"
$url5001 = Get-TunnelURL "$TEMP_DIR\cloudflared_5001_stdout.log" "$TEMP_DIR\cloudflared_5001_stderr.log"

# ============================================================
# 7. Display results
# ============================================================
Write-Host ""
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  All services started" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Local addresses:" -ForegroundColor White
Write-Host "    fin_ver  : http://localhost:5000" -ForegroundColor Gray
Write-Host "    noreset  : http://localhost:5001" -ForegroundColor Gray
Write-Host ""

if ($url5000) {
    Write-Host "  Public addresses:" -ForegroundColor White
    Write-Host "    fin_ver  : $url5000" -ForegroundColor Yellow
} else {
    Write-Warn "fin_ver public URL timeout"
}

if ($url5001) {
    Write-Host "    noreset  : $url5001" -ForegroundColor Yellow
} else {
    Write-Warn "noreset public URL timeout"
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Press ESC to stop all services..." -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan

while ($true) {
    $key = [Console]::ReadKey($true)
    if ($key.Key -eq [ConsoleKey]::Escape) { break }
}

# ============================================================
# 8. Cleanup
# ============================================================
Write-Host ""
Write-Step "Stopping services..."

Stop-Process -Id $procCf5000.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $procCf5001.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $procFin.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $procNo.Id -Force -ErrorAction SilentlyContinue

Get-Process -Name "python" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-OK "All services stopped"
Start-Sleep 1
