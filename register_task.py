"""Register AutonomousEarner in Windows Task Scheduler."""
import subprocess
import sys

ps_script = r"""
$ErrorActionPreference = 'Stop'
$taskName = 'AutonomousEarner'

# Delete existing task if any
try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

$vbsPath = 'C:\Users\magi1\学習\autonomous-earner\run_daemon.vbs'
$workDir = 'C:\Users\magi1\学習\autonomous-earner'

$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $vbsPath -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description 'Autonomous Earner daemon' -RunLevel Limited
Write-Host 'SUCCESS: Task registered'
"""

result = subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
import io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
print("STDOUT:", result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
print("Return code:", result.returncode)
