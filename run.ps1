# ------------------------------------------------------------------
# Listing Scraper Prototype - one-command setup & run (PowerShell)
#
#   cd $HOME\Desktop\listing-scraper
#   .\run.ps1
#
# If scripts are blocked, first run:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# ------------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 1. Virtual environment
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}
& ".\.venv\Scripts\Activate.ps1"

# 2. Dependencies
Write-Host "Installing dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# 3. Stealth browser (one-time download, needed for live Airbnb pricing)
if (-not (Test-Path ".browser_installed")) {
    Write-Host "Installing Scrapling stealth browser (one-time, may take a few minutes)..." -ForegroundColor Cyan
    scrapling install
    New-Item -ItemType File ".browser_installed" | Out-Null
}

# 4. Run (app code lives in service/)
Write-Host ""
Write-Host "  Server starting -> http://localhost:8000" -ForegroundColor Green
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
Set-Location service
uvicorn app:app --port 8000
