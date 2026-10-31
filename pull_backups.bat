@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Скачивает новые копии базы сайта на этот ПК (%USERPROFILE%\.zelees\server-backups) и проверяет их.
rem Без аргументов — при двойном щелчке — окно ждёт нажатия клавиши, если что-то пошло не так.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if /i "%~1"=="auto" goto auto
%PY% tools\pull_backups.py %*
if errorlevel 1 if "%~1"=="" pause
exit /b

rem Запуск из Планировщика задач: окна никто не видит, поэтому всё пишется в pull.log рядом с копиями.
:auto
set "LOG=%USERPROFILE%\.zelees\server-backups"
if not exist "%LOG%" mkdir "%LOG%"
set PYTHONIOENCODING=utf-8
echo === %date% %time% >> "%LOG%\pull.log"
%PY% tools\pull_backups.py >> "%LOG%\pull.log" 2>&1
