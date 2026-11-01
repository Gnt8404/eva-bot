# Ева: автозапуск на Windows через Планировщик задач.
#
# Запуск (PowerShell от имени пользователя):
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
#
# Что делает:
#   1. проверяет python;
#   2. создаёт задачу "EvaBot", которая при входе в Windows запускает
#      deploy\windows\start-eva.bat (а тот сам перезапускает бота при падении);
#   3. дополнительно ставит проверку раз в 5 минут: если процесса нет — поднять.
#
# Удалить автозапуск:  Unregister-ScheduledTask -TaskName EvaBot -Confirm:$false
$ErrorActionPreference = "Stop"

$botDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$bat = Join-Path $PSScriptRoot "start-eva.bat"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python не найден в PATH. Установи Python 3.10+ с https://python.org и повтори."
    exit 1
}
if (-not (Test-Path $bat)) {
    Write-Host "Не найден $bat"
    exit 1
}
Write-Host "Папка бота: $botDir"

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $botDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName "EvaBot" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Готово: задача EvaBot создана и будет запускать Еву при входе в систему."
Write-Host "Проверить:  Get-ScheduledTask -TaskName EvaBot"
Write-Host "Запустить сейчас:  Start-ScheduledTask -TaskName EvaBot"
