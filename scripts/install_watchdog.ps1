$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonConsole = (& python -c 'import sys; print(sys.executable)').Trim()
$pythonWindowless = Join-Path (Split-Path -Parent $pythonConsole) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'pythonw.exe not found' }
$clientScript = Join-Path $projectRoot 'scripts\isolated_client.py'
$taskArguments = '"' + $clientScript + '" run --scheduled'
$taskName = 'NetworkGate-Residential-Watchdog'
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and (($existingTask.Actions.Execute -ne $pythonWindowless) -or ($existingTask.Actions.Arguments -ne $taskArguments))) {
    throw 'Refusing to replace a task with a different executable or arguments'
}
$taskAction = New-ScheduledTaskAction -Execute $pythonWindowless -Argument $taskArguments -WorkingDirectory $projectRoot
$accountName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$onLogon = New-ScheduledTaskTrigger -AtLogOn -User $accountName
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $accountName -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($onLogon, $periodic) -Settings $taskSettings -Principal $taskPrincipal -Description 'Only supervises the isolated residential-candidate proxy on 127.0.0.1:17898. Manual Stop disables automatic restart.' -Force | Select-Object TaskName,State
