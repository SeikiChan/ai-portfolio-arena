$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "项目虚拟环境不存在：$python。先按 README 安装依赖。"
}

$taskName = 'AI Portfolio Arena - Alpaca Paper Auto Trader'
$action = New-ScheduledTaskAction -Execute $python `
    -Argument '-m ai_portfolios.auto_trader --once' `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At '7:00AM'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'Runs Claude and Codex once after the US market opens; Paper accounts only.' -Force | Out-Null
Write-Host "已安装每日任务：$taskName"
Write-Host '任务按本机时间每天 7:00 启动；程序在 10:00 America/New_York 触发并先检查 Alpaca Paper 交易时段。'
Write-Host '结果保存在 reports/auto_trader 和 reports/engines；共享网页只读发布结果。'
