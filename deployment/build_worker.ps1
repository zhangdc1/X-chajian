param(
    [string]$Python = "python"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Installing worker packaging dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r "deployment/requirements-worker.txt" pyinstaller

Write-Host "Building local worker executable..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "xbot-worker" `
    --hidden-import "DrissionPage" `
    --hidden-import "Crypto" `
    "automation/worker.py"

Write-Host "Worker build finished. See dist\\xbot-worker"
Write-Host "Remember: protect/obfuscate this worker package before external distribution."

