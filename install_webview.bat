@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem НЕОБЯЗАТЕЛЬНО: ставит pywebview, чтобы вход Microsoft открывался во встроенном окне,
rem а не в браузере с копированием ссылки.
where py >nul 2>nul && (py -3 -m pip install --upgrade pywebview) || (python -m pip install --upgrade pywebview)
pause
