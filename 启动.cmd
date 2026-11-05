@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title MHGU Installer Tool

set "PY="
set "PY=%~dp0runtime\python\python.exe"
if exist "%PY%" goto found
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if exist "%PY%" goto found
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
if exist "%PY%" goto found
goto try_path

:found
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto try_path
goto run

:try_path
set "PY="
for /f "delims=" %%I in ('where python 2^>nul') do call :pick "%%I"
if not defined PY goto no_python
"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 goto no_python
goto run

:pick
echo %~1 | find /i "WindowsApps" >nul
if not errorlevel 1 goto :eof
if defined PY goto :eof
set "PY=%~1"
goto :eof

:run
echo.
echo   MHGU Installer Tool  ^|  official + touch branches
echo   Starting local server, the browser will open itself...
echo.
"%PY%" "%~dp0app.py" %*
echo.
echo   Stopped. Press any key to close.
pause >nul
exit /b 0

:no_python
echo.
echo   [ERROR] Python 3 not found.
echo.
echo   Put a portable Python at:  runtime\python\python.exe
echo   Or install Python 3.10+ from python.org and tick "Add python.exe to PATH".
echo.
echo   Manual start:
echo     python app.py
echo.
pause
exit /b 1
