@echo off
setlocal EnableExtensions
title Systematics TRILLION MIDI maker - Portable Zip Builder
cd /d "%~dp0"

rem ==================================================================
rem  Systematics TRILLION MIDI maker - Portable Zip Builder
rem
rem  Packages the finished EXE (from dist\) into a single zip you
rem  can carry on a USB stick and run anywhere, with no
rem  installation:
rem
rem      dist\systematics-v1.5.0.0-portable.zip
rem
rem  Usage:
rem      make_portable.bat              - build, then pause
rem      make_portable.bat nopause      - build, no pause
rem  ==================================================================

rem ---- Find the version from CORE_FILES\__init__.py ----
set "VER="
for /f "usebackq tokens=2 delims== " %%V in (`findstr /c:"__version__" "CORE_FILES\__init__.py"`) do set "VER=%%~V"
if not defined VER (
    echo [ERROR] Could not read __version__ from CORE_FILES\__init__.py
    goto :done
)

rem ---- Find the EXE in dist\ ----
set "EXE="
if exist "dist\Systematics TRILLION MIDI maker.exe" set "EXE=dist\Systematics TRILLION MIDI maker.exe"
if not defined EXE (
    for /f "delims=" %%F in ('dir /b /o-d "dist\systematics-v*.exe" 2^>nul') do if not defined EXE set "EXE=dist\%%F"
)
if not defined EXE (
    echo [ERROR] No EXE found in dist\. Run build_exe.bat first.
    goto :done
)
for %%I in ("%EXE%") do set "EXE_ABS=%%~fI"

echo ============================================================
echo   Systematics TRILLION MIDI maker - Portable Zip Builder
echo   Version:  %VER%
echo   EXE:      %EXE_ABS%
echo ============================================================
echo.

rem ---- Stage the files ----
set "STAGE=%TEMP%\systematics_portable_%RANDOM%"
set "APP=%STAGE%\Systematics TRILLION MIDI maker"
mkdir "%APP%" 2>nul
copy /y "%EXE_ABS%" "%APP%\" >nul

>  "%APP%\README.txt" echo Systematics TRILLION MIDI maker  v%VER%  (portable)
>> "%APP%\README.txt" echo.
>> "%APP%\README.txt" echo How to use:
>> "%APP%\README.txt" echo   1. Copy this whole folder anywhere - a USB stick, a
>> "%APP%\README.txt" echo      school or library PC, a PC you do not own.
>> "%APP%\README.txt" echo   2. Double-click "Systematics TRILLION MIDI maker.exe".
>> "%APP%\README.txt" echo      No installation, no admin, no registry.
>> "%APP%\README.txt" echo.
>> "%APP%\README.txt" echo To remove it: delete this folder. That is all.

rem ---- Zip it ----
set "OUT=%~dp0dist\systematics-v%VER%-portable.zip"
echo Building %OUT% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%APP%' -DestinationPath '%OUT%' -CompressionLevel Optimal -Force"
set "BUILD_RESULT=%errorlevel%"

rem ---- Cleanup ----
rmdir /s /q "%STAGE%" 2>nul

:done
echo.
if "%BUILD_RESULT%"=="0" (
    echo ============================================================
    echo   Done! Your portable zip is here:
    echo(
    echo       dist\systematics-v%VER%-portable.zip
    echo(
    echo   Unzip it onto a USB stick and run the EXE inside.
    echo ============================================================
) else (
    echo [ERROR] The zip build failed. See the messages above.
)
if /i not "%~1"=="nopause" pause
endlocal
