# biliTickerBuy 监控面板 - 启动脚本
# 在新 PowerShell 窗口中启动，自动置顶

$root = Split-Path $MyInvocation.MyCommand.Path -Parent
$venvPython = Join-Path $root "venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
    Write-Warning "venv python not found, using system python"
}

Write-Host "Starting biliTickerBuy Monitor..." -ForegroundColor Cyan
Start-Process -WindowStyle Normal -FilePath powershell.exe -ArgumentList @"
-NoExit -Command "& { `"$venvPython`" `"$(Join-Path $root status_ui.py)`" }"
"@

Write-Host "Monitor started in new window. The window will auto-set topmost." -ForegroundColor Green
