@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Портативная версия (один exe, без установки). Игры и настройки хранятся в папке data рядом с exe.
rem Для обычной раздачи используйте build_release.bat — он делает установщик и обновления.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ZeleesLauncher --icon ZeleesLauncher.ico ^
    --distpath dist\portable --collect-submodules launcher --add-data "launcher\assets.zip;launcher" main.py
if errorlevel 1 goto fail
echo portable> dist\portable\portable.txt
echo.
echo Готово: dist\portable\ZeleesLauncher.exe (держите рядом файл portable.txt)
pause
exit /b 0
:fail
echo Сборка не удалась.
pause
exit /b 1
