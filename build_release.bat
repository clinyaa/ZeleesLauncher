@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Сборка ZeleesLauncher
rem Дважды щёлкните этот файл: он спросит номер версии и соберёт установщик в папку release.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python не найден. Установите его: winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)

%PY% tools\build_release.py %*
if errorlevel 1 (
    echo.
    echo Сборка не удалась — подробности выше. Скопируйте текст ошибки и пришлите его.
)
echo.
pause
