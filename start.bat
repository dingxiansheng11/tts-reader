@echo off
cd /d "%~dp0"
chcp 65001 >nul
title Read Aloud
where python >nul 2>nul
if errorlevel 1 goto nopython
python app.py
goto done
:nopython
echo.
echo [ERROR] Python not found in PATH.
echo Install Python 3.10+ , or run:  py -3 app.py
:done
echo.
pause
