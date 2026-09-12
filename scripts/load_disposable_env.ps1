# Load DATABASE_URL from app/config.env.disposable into the current PowerShell session.
# Usage: . .\scripts\load_disposable_env.ps1
$envFile = Join-Path $PSScriptRoot "..\app\config.env.disposable"
if (-not (Test-Path $envFile)) {
    Write-Error "Missing $envFile — copy from app/config.env.disposable.example"
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { return }
    $name = $Matches[1]
    $value = $Matches[2].Trim()
    Set-Item -Path "env:$name" -Value $value
}
$env:MUSICBOT_DOTENV_OVERRIDE = "false"
Write-Host "Loaded disposable DB env from app/config.env.disposable (DATABASE_URL set; dotenv override disabled)."
