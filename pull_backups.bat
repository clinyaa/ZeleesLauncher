@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Скачивает новые копии базы сайта на этот ПК (%USERPROFILE%\.zelees\server-backups) и проверяет их.
rem Без аргументов — при двойном щелчке — окно ждёт нажатия клавиши, если что-то пошло не так.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% tools\pull_backups.py %*
if errorlevel 1 if "%~1"=="" pause
