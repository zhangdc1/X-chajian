param(
    [string]$Python = "python"
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

Write-Host "Installing worker dependencies with $Python..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r "deployment/requirements-worker.txt"

Write-Host "Worker dependencies installed."
