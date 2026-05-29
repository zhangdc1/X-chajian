param(
    [string]$Python = "python",
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8766,
    [string]$Token = "my-xbot-token-2026-change-this",
    [string]$DbPath = "automation/data/controller.db"
)

Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path "automation/data" | Out-Null

& $Python "automation/central_server.py" `
    --host $HostName `
    --port $Port `
    --token $Token `
    --db $DbPath
