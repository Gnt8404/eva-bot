@echo off
REM Ева: запуск на Windows с автоперезапуском.
REM Запускай двойным щелчком или поставь в автозапуск (см. install-autostart.ps1).
REM Нужен установленный Python 3.10+ (python.exe в PATH).
chcp 65001 >nul
cd /d "%~dp0.."
if not exist data mkdir data
:loop
echo [%date% %time%] старт Евы>>data\bot.log
python -u bot.py >>data\bot.log 2>&1
echo [%date% %time%] бот остановился (код %errorlevel%), перезапуск через 3 сек>>data\bot.log
timeout /t 3 /nobreak >nul
goto loop
