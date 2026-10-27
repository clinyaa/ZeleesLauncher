@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- ищем Python (py-лаунчер или python в PATH) ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo В установленном Python нет tkinter.
    echo Переустановите Python с python.org и оставьте галочку "tcl/tk and IDLE".
    pause
    exit /b 1
)

rem --- запускаем без чёрного окна консоли ---
if "%PY%"=="py -3" (
    start "" pyw -3 main.py
) else (
    start "" pythonw main.py
)
exit /b 0

:nopython
echo Python 3 не найден.
echo.
echo Установите его одним из способов:
echo   1) winget install -e --id Python.Python.3.12
echo   2) https://www.python.org/downloads/  (отметьте "Add python.exe to PATH")
echo.
echo После установки снова запустите start.bat
pause
exit /b 1
