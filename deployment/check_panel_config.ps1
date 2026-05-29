param(
    [string]$Python = "python"
)

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $Python = "py"
    } else {
        Write-Host "Python was not found." -ForegroundColor Yellow
        exit 1
    }
}

& $Python -c "import yaml, json, pathlib; p=pathlib.Path('config.yaml'); print('config.yaml exists:', p.exists()); cfg=yaml.safe_load(p.read_text(encoding='utf-8')) if p.exists() else {}; keys=['RUN_MODE','BIT_API_URL','GROUP_ID','WINDOW_COUNT','FARMING_CONFIG','TARGET_BOOST_CONFIG','POST_CONFIG','COMMENT_TEXTS']; print(json.dumps({k: cfg.get(k) for k in keys}, ensure_ascii=False, indent=2))"
