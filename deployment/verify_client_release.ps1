param(
    [Parameter(Mandatory=$true)]
    [string]$Path,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path $Path
$bad = @()

$bad += Get-ChildItem -LiteralPath $root -Recurse -Force -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".py", ".pyc") }
$bad += Get-ChildItem -LiteralPath $root -Recurse -Force -Directory |
    Where-Object { $_.Name -eq "__pycache__" }
$bad += Get-ChildItem -LiteralPath $root -Recurse -Force -File |
    Where-Object {
        $_.Name -match 'controller\.db|controller\.db-wal|controller\.db-shm|task_audit\.jsonl|current_task\.json|comment_drafts\.jsonl' -or
        $_.FullName -match '\\logs\\.+\.txt$'
    }

$configFiles = Get-ChildItem -LiteralPath $root -Recurse -Force -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in @(".yaml", ".yml", ".json", ".txt", ".bat", ".cmd") }
foreach ($file in $configFiles) {
    $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (
        $text -match 'sk-[A-Za-z0-9_\-]{24,}' -or
        $text -match 'my-xbot-token-deagsgfrgsa354sfeas' -or
        $text -match 'deepseek-v4-flash'
    ) {
        $bad += $file
    }
}

$yamlFiles = @("automation_config.yaml", "config.yaml", "model_config.yaml")
foreach ($name in $yamlFiles) {
    $yamlPath = Join-Path $root $name
    if (-not (Test-Path $yamlPath)) {
        Write-Host "Release verification failed. Missing YAML: $name" -ForegroundColor Red
        exit 1
    }
    & $Python -c "import sys, yaml; yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print('yaml ok:', sys.argv[1])" $yamlPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Release verification failed. Invalid YAML: $name" -ForegroundColor Red
        exit 1
    }
}

if ($bad.Count -gt 0) {
    Write-Host "Release verification failed. Forbidden files or secrets found:" -ForegroundColor Red
    $bad | Select-Object -Unique FullName | Format-Table -AutoSize
    exit 1
}

Write-Host "Client release verification passed: $root" -ForegroundColor Green
