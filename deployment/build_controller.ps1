param(
    [string]$Python = "python"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Installing controller packaging dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r "deployment/requirements-controller.txt" pyinstaller

Write-Host "Building controller executables..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "xbot-controller" `
    "automation/central_server.py"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "xbot-discord-bot" `
    "automation/discord_bot.py"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "xbot-scheduler" `
    "automation/scheduler.py"

Write-Host "Controller build finished. See dist\\"

