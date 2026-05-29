param(
    [string]$Token = "my-xbot-token-2026-change-this",
    [int]$Port = 8766
)

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "Checking central controller on 127.0.0.1:$Port ..."
$tcp = Test-NetConnection 127.0.0.1 -Port $Port
$tcp | Select-Object ComputerName,RemotePort,TcpTestSucceeded | Format-List

if ($tcp.TcpTestSucceeded) {
    try {
        Write-Host "Calling /health ..."
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" | ConvertTo-Json -Depth 5

        Write-Host "Calling /workers ..."
        $headers = @{ "X-Automation-Token" = $Token }
        Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:$Port/workers" | ConvertTo-Json -Depth 8
    } catch {
        Write-Host "HTTP check failed:" -ForegroundColor Yellow
        Write-Host $_
    }
} else {
    Write-Host "Central controller is not listening. Start it first:" -ForegroundColor Yellow
    Write-Host "powershell -ExecutionPolicy Bypass -File deployment/start_controller.ps1 -Token `"$Token`"" -ForegroundColor Cyan
}
