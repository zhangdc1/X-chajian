param(
    [string]$Python = "python",
    [string]$Version = (Get-Date -Format "yyyyMMdd_HHmmss"),
    [switch]$UsePyArmor
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing build dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r "deployment/requirements-worker.txt" pyinstaller
if ($UsePyArmor) {
    & $Python -m pip install pyarmor
}

$buildSource = $root
if ($UsePyArmor) {
    $obfRoot = Join-Path $root "build\obf_client_src"
    if (Test-Path $obfRoot) {
        Remove-Item -LiteralPath $obfRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $obfRoot | Out-Null
    Write-Host "Obfuscating Python sources with PyArmor..."
    $pyarmor = (Get-Command pyarmor -ErrorAction SilentlyContinue)
    if (-not $pyarmor) {
        $pythonDir = Split-Path -Parent (Resolve-Path $Python)
        $candidate = Join-Path $pythonDir "Scripts\pyarmor.exe"
        if (Test-Path $candidate) {
            $pyarmor = Get-Item $candidate
        }
    }
    if (-not $pyarmor) {
        throw "pyarmor command not found after installation"
    }
    & $pyarmor.Source gen -O $obfRoot `
        "automation\worker.py" `
        "automation\supervisor.py" `
        "automation\launcher.py" `
        "automation\panel_entry.py" `
        "automation\license_guard.py" `
        "automation\bit_browser.py" `
        "automation\grok_adapter.py" `
        "automation\legacy_runner.py" `
        "automation\model_client.py" `
        "automation\score_plan_parser.py" `
        "automation\smart_comment.py" `
        "automation\task_audit.py" `
        "automation\job_types.py" `
        "newtkmain.py" `
        "fnkuaiyan_go_based.py"
    New-Item -ItemType Directory -Force -Path (Join-Path $obfRoot "automation") | Out-Null
    Copy-Item -LiteralPath "automation\__init__.py" -Destination (Join-Path $obfRoot "automation\__init__.py") -Force
    $buildSource = $obfRoot
}

$distRoot = Join-Path $root "dist_client_build"
if (Test-Path $distRoot) {
    Remove-Item -LiteralPath $distRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

function Build-Exe {
    param(
        [string]$Name,
        [string]$Entry,
        [switch]$Windowed
    )
    $args = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--distpath", $distRoot,
        "--workpath", (Join-Path $root "build\pyinstaller_$Name"),
        "--specpath", (Join-Path $root "build"),
        "--name", $Name,
        "--hidden-import", "DrissionPage",
        "--hidden-import", "Crypto",
        "--hidden-import", "yaml",
        "--hidden-import", "pystray",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL.ImageDraw"
    )
    if ($Windowed) {
        $args += "--windowed"
    }
    $args += (Join-Path $buildSource $Entry)
    & $Python @args
}

Build-Exe -Name "XBotWorker" -Entry "automation\worker.py"
Build-Exe -Name "XBotSupervisor" -Entry "automation\supervisor.py" -Windowed
Build-Exe -Name "XBotLegacyRunner" -Entry "automation\legacy_runner.py"
Build-Exe -Name "XBotGuiLogViewer" -Entry "automation\gui_log_viewer.py" -Windowed
Build-Exe -Name "XBotLauncher" -Entry "automation\launcher.py" -Windowed
Build-Exe -Name "XBotPanel" -Entry "automation\panel_entry.py" -Windowed

$releaseRoot = Join-Path $root "release"
$clientDir = Join-Path $releaseRoot "xbot-client_$Version"
if (Test-Path $clientDir) {
    Remove-Item -LiteralPath $clientDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $clientDir | Out-Null

foreach ($name in @("XBotWorker", "XBotSupervisor", "XBotLegacyRunner", "XBotGuiLogViewer", "XBotLauncher", "XBotPanel")) {
    Copy-Item -LiteralPath (Join-Path $distRoot $name) -Destination (Join-Path $clientDir "runtime\$name") -Recurse -Force
}

Copy-Item -LiteralPath "automation\config_example.yaml" -Destination (Join-Path $clientDir "automation_config.yaml") -Force
Copy-Item -LiteralPath "deployment\gui_config.example.yaml" -Destination (Join-Path $clientDir "config.yaml") -Force
Copy-Item -LiteralPath "deployment\model_config.example.yaml" -Destination (Join-Path $clientDir "model_config.yaml") -Force
Copy-Item -LiteralPath "deployment\start_worker.bat" -Destination (Join-Path $clientDir "start_worker.bat") -Force
Copy-Item -LiteralPath "deployment\open_panel.bat" -Destination (Join-Path $clientDir "open_panel.bat") -Force
Copy-Item -LiteralPath "deployment\launch_xbot.bat" -Destination (Join-Path $clientDir "launch_xbot.bat") -Force

New-Item -ItemType Directory -Force -Path `
    (Join-Path $clientDir "logs"), `
    (Join-Path $clientDir "tasks"), `
    (Join-Path $clientDir "output"), `
    (Join-Path $clientDir "automation\data") | Out-Null

(Get-Content -LiteralPath (Join-Path $clientDir "automation_config.yaml") -Raw -Encoding UTF8) `
    -replace 'require_license: false', 'require_license: true' `
    -replace 'license_heartbeat_enabled: true', 'license_heartbeat_enabled: false' `
    -replace 'log_dir: automation/logs', 'log_dir: logs' `
    -replace 'draft_output_path: automation/output/comment_drafts.jsonl', 'draft_output_path: output/comment_drafts.jsonl' `
    -replace 'lock_dir: automation/local_locks', 'lock_dir: tasks/local_locks' |
    Set-Content -LiteralPath (Join-Path $clientDir "automation_config.yaml") -Encoding UTF8

& powershell -ExecutionPolicy Bypass -File "deployment\verify_client_release.ps1" -Path $clientDir -Python $Python

$zipPath = Join-Path $releaseRoot "xbot-client_$Version.zip"
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $clientDir "*") -DestinationPath $zipPath -Force

Write-Host "Client EXE release created:"
Write-Host "  Folder: $clientDir"
Write-Host "  Zip:    $zipPath"
