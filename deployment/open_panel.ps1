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

Write-Host "Opening parameter panel..."
& $Python "newtkmain.py"
