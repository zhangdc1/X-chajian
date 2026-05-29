param(
    [string]$Python = "python",
    [string]$DbPath = "automation/data/controller.db"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

& $Python "automation/maintenance.py" cancel-score-plan-backlog --db $DbPath
