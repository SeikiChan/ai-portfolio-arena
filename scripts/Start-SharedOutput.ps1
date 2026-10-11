$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "项目虚拟环境不存在：$python。先按 README 安装依赖。"
}

$url = 'http://127.0.0.1:8790/api/teams'
try {
    $response = Invoke-WebRequest -Uri $url -TimeoutSec 2 -SkipHttpErrorCheck
    if ($response.StatusCode -eq 200) {
        Write-Host '共享只读结果页已经运行： http://127.0.0.1:8790/'
        return
    }
} catch {}

$listener = Get-NetTCPConnection -State Listen -LocalPort 8790 -ErrorAction SilentlyContinue
if ($listener) {
    throw '本机 8790 端口已被其他服务占用；未停止或覆盖该服务。'
}

Start-Process -FilePath $python -ArgumentList '-m ai_portfolios.shared_output_server --port 8790' `
    -WorkingDirectory $repoRoot -WindowStyle Hidden
Start-Sleep -Seconds 2
$response = Invoke-WebRequest -Uri $url -TimeoutSec 5 -SkipHttpErrorCheck
if ($response.StatusCode -ne 200) {
    throw "共享结果服务未就绪，HTTP $($response.StatusCode)"
}
Write-Host '共享只读结果页已启动： http://127.0.0.1:8790/'
