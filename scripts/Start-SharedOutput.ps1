$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "项目虚拟环境不存在：$python。先按 README 安装依赖。"
}

$url = 'http://127.0.0.1:8789/api/teams'
try {
    $response = Invoke-WebRequest -Uri $url -TimeoutSec 2 -SkipHttpErrorCheck
    if ($response.StatusCode -eq 200) {
        Write-Host '合并研究与 Paper 结果控制台已经运行： http://127.0.0.1:8789/'
        return
    }
} catch {}

$listener = Get-NetTCPConnection -State Listen -LocalPort 8789 -ErrorAction SilentlyContinue
if ($listener) {
    $owners = ($listener.OwningProcess | Sort-Object -Unique) -join ', '
    throw "8789 上有旧服务（PID $owners），但没有合并面板 API。为避免误停其他进程，未自动覆盖；请先在任务管理器结束该 PID，再运行此脚本。"
}

Start-Process -FilePath $python -ArgumentList '-m ai_portfolios.console_server --port 8789' `
    -WorkingDirectory $repoRoot -WindowStyle Hidden
Start-Sleep -Seconds 2
$response = Invoke-WebRequest -Uri $url -TimeoutSec 5 -SkipHttpErrorCheck
if ($response.StatusCode -ne 200) {
    throw "合并控制台未就绪，HTTP $($response.StatusCode)"
}
Write-Host '合并研究与 Paper 结果控制台已启动： http://127.0.0.1:8789/'
