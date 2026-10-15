@echo off
setlocal
title Systematics TRILLION MIDI maker
cd /d "%~dp0"

rem Find a usable Python interpreter (python first, then the py launcher).
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)

if not defined PY (
    echo(
    echo [ERROR] Python was not found on your PATH.
    echo Install Python 3 from https://www.python.org/downloads/ and run this again.
    echo(
    pause
    exit /b 1
)

%PY% trillion.py
if errorlevel 1 (
    echo(
    echo The app closed with an error. See the messages above.
    pause
)

endlocal
