$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" -m ai_portfolios.run
if ($LASTEXITCODE -ne 0) { throw '研究未完成，请查看错误；旧结果没有被伪装成新结果。' }
Start-Process -FilePath "$projectRoot\reports\ai_portfolios\dashboard.html"
