$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" -m ai_portfolios.run --retry claude
if ($LASTEXITCODE -ne 0) { throw '补跑未完成，请查看提示。' }
Start-Process -FilePath "$projectRoot\reports\ai_portfolios\dashboard.html"
