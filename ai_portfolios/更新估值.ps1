$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" -m ai_portfolios.run --refresh-only
if ($LASTEXITCODE -ne 0) { throw '估值更新失败。' }
Start-Process -FilePath "$projectRoot\reports\ai_portfolios\dashboard.html"
