@echo off
cd /d "%~dp0"
chcp 65001 >nul
title 长文朗读器 ReadAloud
where python >nul 2>nul
if errorlevel 1 goto trypy
python app.py
goto done
:trypy
where py >nul 2>nul
if errorlevel 1 goto nopython
py -3 app.py
goto done
:nopython
echo.
echo [缺少 Python] 这台电脑还没装 Python，装上就能用了。
echo.
echo   下载地址：https://www.python.org/downloads/
echo   安装时务必勾选 "Add python.exe to PATH"
echo   装完重新双击本文件即可。
echo.
:done
echo.
pause
