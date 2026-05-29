param(
    [string]$Config = "automation_config.yaml"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path $Config)) {
    Write-Host "找不到配置文件: $Config" -ForegroundColor Red
    exit 1
}

$raw = Get-Content -Path $Config -Encoding UTF8
$centralApi = (($raw | Select-String -Pattern '^\s*central_api:\s*(.+)\s*$').Matches.Groups[1].Value).Trim()
$centralToken = (($raw | Select-String -Pattern '^\s*central_token:\s*(.+)\s*$').Matches.Groups[1].Value).Trim().Trim('"')
$bitApi = (($raw | Select-String -Pattern '^\s*bit_api_url:\s*(.+)\s*$').Matches.Groups[1].Value).Trim()

Write-Host "Worker 配置检查" -ForegroundColor Cyan
Write-Host "central_api: $centralApi"
Write-Host "bit_api_url: $bitApi"

try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($centralToken))
    $fingerprint = ([BitConverter]::ToString($hash) -replace '-', '').Substring(0, 12).ToLower()
    Write-Host "central_token 指纹: $fingerprint"
} catch {
    Write-Host "central_token 指纹计算失败: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "检查中央控制器 /health ..."
try {
    $headers = @{ "X-Automation-Token" = $centralToken }
    $health = Invoke-RestMethod -Uri "$centralApi/health" -Headers $headers -TimeoutSec 5
    $health | ConvertTo-Json -Depth 5
} catch {
    Write-Host "中央控制器连接失败或 token 不匹配: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "检查比特浏览器本地 API ..."
try {
    $uri = "$bitApi/browser/list"
    $body = @{ page = 0; pageSize = 1 } | ConvertTo-Json
    $res = Invoke-RestMethod -Method Post -Uri $uri -Body $body -ContentType "application/json" -TimeoutSec 5
    $res | ConvertTo-Json -Depth 5
} catch {
    Write-Host "比特浏览器 API 连接失败: $_" -ForegroundColor Red
    Write-Host "请确认：1）比特浏览器客户端已打开；2）本地 API 已启用；3）端口是否仍是 54345。" -ForegroundColor Yellow
}
