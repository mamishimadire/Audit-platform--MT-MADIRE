<#
  Starts the MT AUDIT backend (FastAPI/uvicorn) and frontend (Vite) if they
  aren't already running, then opens the app in your default browser.

  Usage (from a PowerShell terminal, at the project root):
    .\start-app.ps1

  Each service gets its own visible PowerShell window so you can see its
  logs and stop it by closing that window (or Ctrl+C inside it). Running
  this script again is safe - it skips anything already running.

  NOTE: port 5173 (Vite's default) collides with another project on this
  machine ("phone/frontend/web"). This project's frontend is pinned to
  port 5180 with strictPort in vite.config.ts specifically so it never
  silently lands on a random free port and gets confused with that other
  app - always use 5180 for this project, not 5173.
#>

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$frontendPort = 5180

function Test-Port($port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

function Test-OurFrontend($port) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:$port/" -UseBasicParsing -TimeoutSec 3
        return $resp.Content -match "MT AUDIT"
    } catch {
        return $false
    }
}

function Wait-ForPort($port, $label, $timeoutSeconds = 60) {
    Write-Host "Waiting for $label on port $port..." -NoNewline
    $elapsed = 0
    while (-not (Test-Port $port)) {
        if ($elapsed -ge $timeoutSeconds) {
            Write-Host ""
            Write-Warning "$label did not come up on port $port within $timeoutSeconds seconds - check its terminal window for errors."
            return $false
        }
        Start-Sleep -Seconds 1
        $elapsed += 1
        Write-Host "." -NoNewline
    }
    Write-Host " ready."
    return $true
}

# --- Backend (FastAPI, port 8001) ---
if (Test-Port 8001) {
    Write-Host "Backend already running on port 8001 - leaving it as is."
} else {
    Write-Host "Starting backend..."
    $backendPath = Join-Path $root "backend"
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "Set-Location '$backendPath'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001"
    )
    Wait-ForPort 8001 "Backend" | Out-Null
}

# --- Frontend (Vite, pinned to port 5180 - see note above) ---
if (Test-Port $frontendPort) {
    Start-Sleep -Seconds 1
    if (Test-OurFrontend $frontendPort) {
        Write-Host "Frontend already running on port $frontendPort - leaving it as is."
    } else {
        Write-Warning "Something else is already listening on port $frontendPort and it isn't MT AUDIT. Stop that process first, then re-run this script."
        exit 1
    }
} else {
    Write-Host "Starting frontend..."
    $frontendPath = Join-Path $root "frontend"
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-Command",
        "Set-Location '$frontendPath'; npm run dev"
    )
    Wait-ForPort $frontendPort "Frontend" | Out-Null
}

Start-Sleep -Seconds 1
Write-Host "Opening http://localhost:$frontendPort/ ..."
Start-Process "http://localhost:$frontendPort/"
