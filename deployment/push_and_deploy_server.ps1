param(
    [string]$CommitMessage = "update xbot server and client fixes",
    [string]$Branch = "main",
    [string]$Server = "root@你的服务器IP",
    [string]$ServerDir = "/opt/xbot",
    [switch]$SkipPush,
    [switch]$SkipServerRestart
)

$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "== Local repo ==" -ForegroundColor Cyan
git status --short

if (-not $SkipPush) {
    Write-Host "== Commit and push ==" -ForegroundColor Cyan
    git add automation deployment newtkmain.py

    $changes = git diff --cached --name-only
    if ($changes) {
        git commit -m $CommitMessage
    } else {
        Write-Host "No staged changes to commit." -ForegroundColor Yellow
    }

    git push origin $Branch
}

if ($SkipServerRestart) {
    Write-Host "Skip server update/restart." -ForegroundColor Yellow
    exit 0
}

Write-Host "== Server pull and restart ==" -ForegroundColor Cyan
$remoteScript = @"
set -e
cd '$ServerDir'
echo '--- backup ---'
mkdir -p backups
tar -czf backups/controller_db_`$(date +%Y%m%d_%H%M%S).tar.gz automation/data discord_config.yaml 2>/dev/null || true
echo '--- git pull ---'
git pull origin '$Branch'
echo '--- install deps ---'
python3 -m pip install -r deployment/requirements-controller.txt
echo '--- restart controller ---'
systemctl restart xbot-controller
echo '--- restart discord bot ---'
pkill -f 'automation/discord_bot.py' || true
if systemctl list-unit-files | grep -q '^xbot-discord.service'; then
  systemctl restart xbot-discord
else
  nohup python3 automation/discord_bot.py --config discord_config.yaml > discord_bot.log 2>&1 &
fi
echo '--- status ---'
systemctl status xbot-controller --no-pager || true
if systemctl list-unit-files | grep -q '^xbot-discord.service'; then
  systemctl status xbot-discord --no-pager || true
else
  tail -n 30 discord_bot.log || true
fi
curl -fsS http://127.0.0.1:8766/health || true
echo
echo 'server update done'
"@

ssh $Server $remoteScript

