param(
    [Parameter(Mandatory=$true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path $Path
$bad = @()

$bad += Get-ChildItem -LiteralPath $root -Recurse -Force -File |
    Where-Object { $_.Extension -in @(".py", ".pyc") } |
    Where-Object { $_.FullName -notmatch '\\runtime\\' }
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
    $text = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue
    if (
        $text -match 'sk-[A-Za-z0-9_\-]{24,}' -or
        $text -match 'my-xbot-token-deagsgfrgsa354sfeas' -or
        $text -match 'deepseek-v4-flash'
    ) {
        $bad += $file
    }
}

if ($bad.Count -gt 0) {
    Write-Host "Release verification failed. Forbidden files or secrets found:" -ForegroundColor Red
    $bad | Select-Object -Unique FullName | Format-Table -AutoSize
    exit 1
}

Write-Host "Client release verification passed: $root" -ForegroundColor Green
