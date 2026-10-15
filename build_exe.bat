@echo off
setlocal EnableExtensions
title Systematics TRILLION MIDI maker - EXE Builder
cd /d "%~dp0"

rem ==================================================================
rem  Systematics TRILLION MIDI maker - EXE Builder
rem
rem  Builds the app into a single EXE with PyInstaller:
rem
rem      dist\Systematics TRILLION MIDI maker.exe
rem
rem  Run it from the app folder (the one holding trillion.py and
rem  CORE_FILES). The build uses these local files only - nothing is
rem  downloaded and nothing outside this folder is touched.
rem
rem  Shortcuts:
rem      build_exe.bat              - build, then pause
rem      build_exe.bat nopause      - build, no pause
rem ==================================================================

set "BUILD_RESULT=1"
set "WORK="

rem ---- Find a usable Python interpreter ----
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] Python was not found on your PATH.
    echo Install Python 3 from https://www.python.org/downloads/
    goto :done
)

rem ---- Install PyInstaller if missing ----
%PY% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller ...
    %PY% -m pip install --disable-pip-version-check pyinstaller
    if errorlevel 1 (
        echo [ERROR] Could not install PyInstaller.
        goto :done
    )
)

rem ---- Build temp folder (keeps PyInstaller's work files out of the project) ----
set "WORK=%TEMP%\systematics_build_%RANDOM%"
mkdir "%WORK%" 2>nul

echo ============================================================
echo   Systematics TRILLION MIDI maker - EXE Builder
echo ============================================================
echo.

%PY% -m PyInstaller ^
    --onefile ^
    --clean ^
    --noconfirm ^
    --name "Systematics TRILLION MIDI maker" ^
    --icon "%~dp0CORE_FILES\icon.ico" ^
    --distpath "%~dp0dist" ^
    --workpath "%WORK%\pybuild" ^
    --specpath "%WORK%" ^
    --add-data "%~dp0CORE_FILES\assets;CORE_FILES\assets" ^
    --add-data "%~dp0CORE_FILES\lang;CORE_FILES\lang" ^
    --add-data "%~dp0CORE_FILES\icon.png;CORE_FILES" ^
    --add-data "%~dp0CORE_FILES\icon.ico;CORE_FILES" ^
    trillion.py

set "BUILD_RESULT=%errorlevel%"

rem ---- Clean up the temp work folder ----
if defined WORK (
    if exist "%WORK%" (
        rem Retry a few times in case Windows briefly locks the folder
        rem (Defender scan, closing handles) before giving up.
        for /l %%N in (1,1,5) do (
            rmdir /s /q "%WORK%" 2>nul
            if not exist "%WORK%" goto :done
            ping -n 2 127.0.0.1 >nul
        )
        echo [WARN] Could not fully remove "%WORK%" - delete it manually.
    )
)

:done
echo.
if "%BUILD_RESULT%"=="0" (
    echo ============================================================
    echo   Done! Your EXE is here:
    echo(
    echo       dist\Systematics TRILLION MIDI maker.exe
    echo ============================================================
    rem Refresh the Windows icon cache so Explorer stops showing the
    rem old icon for the overwritten EXE. Harmless if it fails.
    if exist "%SystemRoot%\System32\ie4uinit.exe" (
        "%SystemRoot%\System32\ie4uinit.exe" -show >nul 2>nul
    )
    echo If the old icon still shows, right-click the EXE -^> "Open with",
    echo or delete %%LocalAppData%%\IconCache.db and restart Explorer.
) else (
    echo [ERROR] The build failed. See the messages above.
)
if /i not "%~1"=="nopause" pause
endlocal
