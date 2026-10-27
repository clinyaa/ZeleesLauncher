@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Быстрое обновление (только код лаунчера, без переустановки у пользователей).
rem Пример:  publish_update.bat 1.0.1 "Исправлен вход" --upload
if "%~1"=="" (
    echo Укажите версию, например:  publish_update.bat 1.0.1 "Что нового" --upload
    pause
    exit /b 1
)
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% tools\publish_update.py %*
pause
