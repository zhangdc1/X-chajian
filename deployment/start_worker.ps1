param(
    [string]$Python = "python",
    [string]$Config = "automation_config.yaml"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $Python = "py"
    } else {
        Write-Host ""
        Write-Host "Python was not found." -ForegroundColor Yellow
        Write-Host "Install Python 3.10+ and check 'Add Python to PATH', then reopen PowerShell." -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
}

& $Python -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Missing PyYAML. Worker cannot read automation_config.yaml." -ForegroundColor Yellow
    Write-Host "Run this command first:" -ForegroundColor Yellow
    Write-Host "powershell -ExecutionPolicy Bypass -File deployment/install_worker_deps.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

if (-not (Test-Path $Config)) {
    Write-Host ""
    Write-Host "Config file not found: $Config" -ForegroundColor Yellow
    Write-Host "Copy the example config first:" -ForegroundColor Yellow
    Write-Host "Copy-Item automation/config_example.yaml automation_config.yaml" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

& $Python "automation/worker.py" --config $Config
