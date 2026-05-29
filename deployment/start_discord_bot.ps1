param(
    [string]$Python = "python",
    [string]$Config = "discord_config.yaml"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $Python = "py"
    } else {
        Write-Host "Python was not found. Install Python 3.10+ and check 'Add Python to PATH'." -ForegroundColor Yellow
        exit 1
    }
}

& $Python -c "import yaml, discord" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Missing PyYAML or discord.py. Discord Bot cannot start." -ForegroundColor Yellow
    Write-Host "Run this command first:" -ForegroundColor Yellow
    Write-Host "python -m pip install -r deployment/requirements-controller.txt" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}

& $Python "automation/discord_bot.py" --config $Config
