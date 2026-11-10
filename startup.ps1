# Local startup script - starts both fin_ver and noreset services
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

function Write-Step($msg) { Write-Host "  $msg" -ForegroundColor Gray }
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [ERROR] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  BullySim - Setup & Launch" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# 1. Check Python
# ============================================================
Write-Step "Checking Python..."
try {
    $pyVer = python --version 2>&1
    Write-OK "Python: $pyVer"
} catch {
    Write-Err "Python not found"
    Write-Host "  Please install Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Yellow
    pause; exit 1
}

# ============================================================
# 2. Check .env files
# ============================================================
Write-Step "Checking config files..."

$finEnv = Join-Path $ROOT "fin_ver\.env"
$finExample = Join-Path $ROOT "fin_ver\.env.example"
$noresetEnv = Join-Path $ROOT "noreset\.env"
$noresetExample = Join-Path $ROOT "noreset\.env.example"
$apiKey = ""

# fin_ver .env
if (-not (Test-Path $finEnv)) {
    if (Test-Path $finExample) {
        Write-Warn "fin_ver\.env not found, creating..."
        Copy-Item $finExample $finEnv
        $apiKey = Read-Host "Enter DeepSeek API Key"
        $content = (Get-Content $finEnv -Raw) -replace 'your_deepseek_api_key_here', $apiKey
        Set-Content $finEnv $content -NoNewline
        Write-OK "fin_ver API Key configured"
    } else {
        Write-Err "fin_ver\.env.example not found"
        pause; exit 1
    }
} else {
    Write-OK "fin_ver\.env exists"
}

# noreset .env
if (-not (Test-Path $noresetEnv)) {
    if (Test-Path $noresetExample) {
        Write-Warn "noreset\.env not found, creating..."
        $content = (Get-Content $noresetExample -Raw) -replace 'your_deepseek_api_key_here', $apiKey
        Set-Content $noresetEnv $content -NoNewline
        Write-OK "noreset API Key configured"
    } else {
        Write-Warn "noreset\.env.example not found, noreset will not start"
    }
} else {
    Write-OK "noreset\.env exists"
}

# ============================================================
# 3. Install dependencies
# ============================================================
Write-Step "Installing Python dependencies..."

$logDir = Join-Path $ROOT "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Step "  fin_ver dependencies..."
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$result = cmd /c "pip install -r `"$ROOT\fin_ver\requirements.txt`" -q 2>&1"
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Some deps failed to install (see logs\pip_fin.log)"
    $result | Out-File "$logDir\pip_fin.log"
}
Write-OK "fin_ver deps ready"

Write-Step "  noreset dependencies..."
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$result = cmd /c "pip install -r `"$ROOT\noreset\requirements.txt`" -q 2>&1"
$ErrorActionPreference = $prev
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Some deps failed to install (see logs\pip_noreset.log)"
    $result | Out-File "$logDir\pip_noreset.log"
}
Write-OK "noreset deps ready"

# ============================================================
# 4. Kill old processes
# ============================================================
Write-Step "Stopping old instances..."
Get-Process -Name "python" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1
Write-OK "Old processes cleaned"

# ============================================================
# 5. Start services
# ============================================================
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Starting services" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$finDir = Join-Path $ROOT "fin_ver"
$noresetDir = Join-Path $ROOT "noreset"

Write-Step "Starting fin_ver (port 5000)..."
$finProc = Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory $finDir -PassThru -WindowStyle Minimized
Write-OK "fin_ver PID: $($finProc.Id)"

Start-Sleep 3

Write-Step "Starting noreset (port 5001)..."
$noresetProc = Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory $noresetDir -PassThru -WindowStyle Minimized
Write-OK "noreset PID: $($noresetProc.Id)"

# ============================================================
# 6. Wait for readiness
# ============================================================
Write-Step "Waiting for services..."
$ready = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5000" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $ready = $true
        break
    } catch {}
    Start-Sleep 2
}

if (-not $ready) {
    Write-Err "fin_ver startup timed out. Check: cd fin_ver && python run.py"
    Stop-Process -Id $finProc.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $noresetProc.Id -Force -ErrorAction SilentlyContinue
    pause; exit 1
}

$readyNoreset = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:5001" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $readyNoreset = $true
        break
    } catch {}
    Start-Sleep 2
}

if (-not $readyNoreset) {
    Write-Err "noreset startup timed out. Check: cd noreset && python run.py"
    Stop-Process -Id $finProc.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $noresetProc.Id -Force -ErrorAction SilentlyContinue
    pause; exit 1
}
Write-OK "Services are ready"

# ============================================================
# 7. Open browser
# ============================================================
Start-Process "http://localhost:5000"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  fin_ver  : http://localhost:5000" -ForegroundColor Yellow
Write-Host "  noreset  : http://localhost:5001" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Browser opened. Press ESC to stop." -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
while ($true) {
    $key = [Console]::ReadKey($true)
    if ($key.Key -eq [ConsoleKey]::Escape) { break }
}

# ============================================================
# 8. Cleanup
# ============================================================
Write-Host ""
Write-Step "Stopping services..."
Stop-Process -Id $finProc.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $noresetProc.Id -Force -ErrorAction SilentlyContinue
Write-OK "All services stopped"
Start-Sleep 1
