param(
    [string]$Version = (Get-Date -Format "yyyyMMdd_HHmmss")
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$releaseRoot = Join-Path $root "release"
$stageRoot = Join-Path $releaseRoot "xbot_release_$Version"
$controllerDir = Join-Path $stageRoot "xbot-controller"
$workerDir = Join-Path $stageRoot "xbot-worker"

if (Test-Path $stageRoot) {
    Remove-Item -LiteralPath $stageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $controllerDir, $workerDir | Out-Null

function Copy-ProjectFile {
    param(
        [string]$Source,
        [string]$DestinationRoot
    )
    $target = Join-Path $DestinationRoot $Source
    $parent = Split-Path -Parent $target
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $root $Source) -Destination $target -Force
}

function Copy-ProjectTree {
    param(
        [string]$Source,
        [string]$DestinationRoot
    )
    $target = Join-Path $DestinationRoot $Source
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $root $Source) -Recurse -File |
        Where-Object {
            $_.FullName -notmatch '\\__pycache__\\' -and
            $_.FullName -notmatch '\\logs\\' -and
            $_.FullName -notmatch '\\tasks\\' -and
            $_.FullName -notmatch '\\output\\' -and
            $_.FullName -notmatch '\\data\\' -and
            $_.Extension -ne ".pyc"
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring((Join-Path $root $Source).Length).TrimStart('\')
            $dest = Join-Path $target $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        }
}

Copy-ProjectTree "automation" $controllerDir
Copy-ProjectTree "deployment" $controllerDir
Copy-ProjectFile "账号评分提示词.txt" $controllerDir

Copy-ProjectTree "automation" $workerDir
Copy-ProjectTree "deployment" $workerDir
Copy-ProjectFile "newtkmain.py" $workerDir
Copy-ProjectFile "newkami.py" $workerDir
Copy-ProjectFile "fnkuaiyan_go_based.py" $workerDir
Copy-ProjectFile "账号评分提示词.txt" $workerDir

Copy-Item -LiteralPath "automation/discord_config_example.yaml" -Destination (Join-Path $controllerDir "discord_config.example.yaml") -Force
Copy-Item -LiteralPath "automation/scheduler_config_example.yaml" -Destination (Join-Path $controllerDir "scheduler_config.example.yaml") -Force
Copy-Item -LiteralPath "deployment/model_config.example.yaml" -Destination (Join-Path $controllerDir "model_config.example.yaml") -Force
Copy-Item -LiteralPath "automation/config_example.yaml" -Destination (Join-Path $workerDir "automation_config.example.yaml") -Force
Copy-Item -LiteralPath "deployment/model_config.example.yaml" -Destination (Join-Path $workerDir "model_config.example.yaml") -Force
Copy-Item -LiteralPath "deployment/gui_config.example.yaml" -Destination (Join-Path $workerDir "config.example.yaml") -Force

New-Item -ItemType Directory -Force -Path `
    (Join-Path $controllerDir "automation/data"), `
    (Join-Path $workerDir "automation/logs"), `
    (Join-Path $workerDir "automation/tasks"), `
    (Join-Path $workerDir "automation/output") | Out-Null

$controllerZip = Join-Path $releaseRoot "xbot-controller_$Version.zip"
$workerZip = Join-Path $releaseRoot "xbot-worker_$Version.zip"
if (Test-Path $controllerZip) { Remove-Item -LiteralPath $controllerZip -Force }
if (Test-Path $workerZip) { Remove-Item -LiteralPath $workerZip -Force }
Compress-Archive -Path (Join-Path $controllerDir "*") -DestinationPath $controllerZip -Force
Compress-Archive -Path (Join-Path $workerDir "*") -DestinationPath $workerZip -Force

Write-Host "Release package created:"
Write-Host "  Controller: $controllerZip"
Write-Host "  Worker:     $workerZip"
Write-Host "Staging dir:  $stageRoot"
