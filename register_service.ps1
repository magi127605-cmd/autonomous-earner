# Autonomous Earner — Windows Task Scheduler 登録スクリプト
# 管理者権限で実行すること
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File register_service.ps1
#
# 削除:
#   schtasks /Delete /TN "AutonomousEarner" /F

$ErrorActionPreference = "Stop"

$taskName = "AutonomousEarner"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "pythonw"  # pythonw = コンソールウィンドウなしで実行
$scriptPath = Join-Path $projectDir "daemon.py"

Write-Host "================================"
Write-Host " Autonomous Earner - Service Registration"
Write-Host "================================"
Write-Host ""
Write-Host "Project: $projectDir"
Write-Host "Script:  $scriptPath"
Write-Host ""

# 既存タスクがあれば削除
$existingTask = schtasks /Query /TN $taskName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "既存タスクを削除中..."
    schtasks /Delete /TN $taskName /F
}

# VBSラッパー作成（非表示で実行するため）
$vbsPath = Join-Path $projectDir "run_daemon.vbs"
$vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "$pythonExe ""$scriptPath""", 0, False
"@
Set-Content -Path $vbsPath -Value $vbsContent -Encoding ASCII

# タスクスケジューラに登録（ログオン時に起動）
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument """$vbsPath""" -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Autonomous Earner: AI-driven autonomous revenue generation system" `
    -RunLevel Limited

Write-Host ""
Write-Host "[OK] タスク '$taskName' を登録しました"
Write-Host ""
Write-Host "操作方法:"
Write-Host "  開始: schtasks /Run /TN `"$taskName`""
Write-Host "  停止: .env の ENABLED=false に変更"
Write-Host "  削除: schtasks /Delete /TN `"$taskName`" /F"
Write-Host ""
Write-Host "今すぐ開始しますか？ (Y/N)"
$response = Read-Host
if ($response -eq "Y" -or $response -eq "y") {
    schtasks /Run /TN $taskName
    Write-Host "[OK] デーモンを開始しました"
}
