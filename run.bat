@echo off
chcp 65001 >nul
title CppShot - 懒人 C++ 截图器
cd /d "%~dp0"
if exist "CppShot.exe" (
    start "" "CppShot.exe"
    exit /b
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "app.py"
) else (
    python "app.py"
)
