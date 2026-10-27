@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Запуск с консолью — видно все ошибки Python
where py >nul 2>nul
if errorlevel 1 (
    python main.py
) else (
    py -3 main.py
)
pause
