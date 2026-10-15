@echo off
setlocal EnableExtensions
title Systematics TRILLION MIDI maker - Dependency Installer
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem  Systematics TRILLION MIDI maker - dependency installer
rem
rem  This app has NO third-party (pip) packages. Every import is from
rem  Python's standard library (tkinter, lzma, json, urllib, ...), so
rem  the only thing to install is Python itself.
rem
rem  IMPORTANT: use the FULL installer below. The "embeddable" builds
rem  do NOT include tkinter, which this app needs.
rem ------------------------------------------------------------------

rem ---- Config (bump PYTHON_VERSION to a newer release when needed) ----
set "PYTHON_VERSION=3.12.12"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\python-%PYTHON_VERSION%-amd64.exe"

echo ============================================================
echo   Systematics TRILLION MIDI maker - Dependency Installer
echo ============================================================
echo.

rem ---- 1. Look for an existing Python that actually has tkinter ----
call :find_python
if defined PY goto :verify

rem ---- None found on PATH -> offer to download the full installer ----
echo No working Python (with tkinter) was found on your PATH.
echo This app needs the full Python installer, not the embeddable build.
echo.
choice /C YN /M "Download and install Python %PYTHON_VERSION% now"
if errorlevel 2 goto :abort

call :download
if errorlevel 1 goto :abort

echo.
echo Running the Python installer. Please follow its prompts.
echo ^> TICK "Add python.exe to PATH" so this and the launcher can find it.
echo.
start /wait "" "%PYTHON_INSTALLER%"

rem ---- Re-check after the install ----
call :find_python
if defined PY goto :verify

echo.
echo Python was installed, but it was not added to PATH or tkinter
echo is missing. Re-run the installer and tick "Add python.exe to PATH".
goto :abort

:verify
echo Checking tkinter on: %PY%
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] tkinter is missing from that Python installation.
    echo The embeddable / minimal Python builds do NOT include tkinter.
    echo Install the full Python from https://www.python.org/downloads/
    goto :abort
)
echo OK - Python %PYTHON_VERSION% (or newer) and tkinter are ready.
echo.
echo The app uses only Python's standard library, so there are no
echo pip packages to install. Nothing else needs to be downloaded.
echo.
echo You can now run:  "Launch Systematics TRILLION MIDI maker.bat"
echo.
pause
exit /b 0

:abort
echo.
echo Installation cancelled or failed. Nothing was changed.
pause
exit /b 1

rem ------------------------------------------------------------------
rem  Helpers
rem ------------------------------------------------------------------
:find_python
    set "PY="
    where python >nul 2>nul && set "PY=python"
    if not defined PY where py >nul 2>nul && set "PY=py -3"
    rem Only keep it if tkinter actually imports (filters out the
    rem Microsoft Store stub and embeddable/minimal builds).
    if defined PY (
        %PY% -c "import tkinter" >nul 2>nul || set "PY="
    )
    exit /b 0

:download
    echo.
    echo Downloading Python %PYTHON_VERSION% from python.org ...
    echo   %PYTHON_URL%
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%' -UseBasicParsing } catch { Write-Error $_; exit 1 }"
    if errorlevel 1 (
        echo.
        echo [ERROR] Download failed. Check your internet connection.
        exit /b 1
    )
    if not exist "%PYTHON_INSTALLER%" (
        echo [ERROR] The installer file was not created.
        exit /b 1
    )
    echo Downloaded to: %PYTHON_INSTALLER%
    exit /b 0
