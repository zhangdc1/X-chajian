param(
    [string]$Python = "python",
    [string]$Token = "test-token",
    [int]$Port = 8766
)

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "1/4 Checking controller health..."
Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" | ConvertTo-Json -Depth 5

Write-Host "2/4 Creating a test comment draft job..."
& $Python "automation/create_job.py" `
    --central-api "http://127.0.0.1:$Port" `
    --token $Token `
    --job-type comment_draft `
    --payload-json '{\"account_id\":\"demo\",\"tweet_text\":\"AI tools are changing daily workflows.\"}'

Write-Host "3/4 Listing workers..."
$headers = @{ "X-Automation-Token" = $Token }
Invoke-RestMethod -Headers $headers -Uri "http://127.0.0.1:$Port/workers" | ConvertTo-Json -Depth 8

Write-Host "4/4 Done. If a worker is running, it should pick up the test job."

