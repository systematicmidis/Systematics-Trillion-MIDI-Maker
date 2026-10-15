@echo off
setlocal EnableExtensions
title Systematics TRILLION MIDI maker - MSI Builder
cd /d "%~dp0"

rem ==================================================================
rem  Systematics TRILLION MIDI maker - MSI Builder
rem
rem  Wraps a finished EXE into a real Windows Installer (.msi) with a
rem  full setup wizard:
rem
rem      dist\systematics-v1.6.0.0.msi
rem
rem  The installer shows these screens (WixUI_Advanced, the stock
rem  light wizard from the WixToolset.UI.wixext extension):
rem    - Welcome + license agreement
rem    - Install for everyone (needs admin)  OR  just for me (no admin)
rem    - Choose the install folder (Browse...)
rem    - Feature tree: the app (always) and the desktop shortcut
rem      (optional, on by default - untick to skip)
rem    - Ready / progress / finish
rem
rem  MSI installs upgrade over older ones; downgrades are blocked.
rem  Silent install for rollout:
rem      msiexec /i systematics-v1.6.0.0.msi /qn
rem
rem  Run it from the app folder (the one holding trillion.py and
rem  CORE_FILES): the version wrapped into the MSI comes from
rem  CORE_FILES\__init__.py, the icon from CORE_FILES\icon.ico, and the
rem  EXE from dist\ (run build_exe.bat first). Force a specific version
rem  with:  set VERSION=1.2.5.0
rem
rem  WiX Toolset v7 is installed on first use into .tools\wix - no
rem  admin needed, nothing installed globally. The WixToolset.UI.wixext
rem  extension (the stock light wizard) is added to the tool's cache on
rem  first use. WiX v7 requires accepting the OSMF EULA; it is free for
rem  users with annual gross revenue under US$10,000 (see
rem  https://wixtoolset.org/osmf/). The batch writes the acceptance
rem  marker into %%USERPROFILE%%\.wix on first run.
rem
rem  Shortcuts:
rem      build_msi.bat              - build, then pause
rem      build_msi.bat nopause      - build, no pause
rem      set VERSION=1.2.5.0        - wrap a specific version's EXE
rem      set ADD_DESKTOP=0          - desktop shortcut off by default
rem ==================================================================

rem >>>>>> Fixed GUIDs. Keep these forever - they make upgrades work. <<<<<<
set "UPGRADE_GUID=FAE0ACA9-B140-4A9A-82AE-69A7F3ECBB6A"
set "EXE_COMPONENT_GUID=7DD93C42-FDC4-427F-A1A2-4BD47B7CDBA7"
set "DESKTOP_COMPONENT_GUID=A7B2C3D4-E5F6-4A5B-9C8D-1E2F3A4B5C6D"
rem >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

set "BUILD_RESULT=1"

rem ---- The version and the icon come from the CORE_FILES folder that sits
rem      next to this script. ----
set "SRC=%~dp0"
if not exist "%SRC%CORE_FILES\__init__.py" (
    echo [ERROR] CORE_FILES\__init__.py was not found next to this script.
    echo Run build_msi.bat from the Systematics TRILLION MIDI maker folder.
    goto :done
)

rem ---- Find the version from the source's CORE_FILES\__init__.py ----
set "VER="
for /f "usebackq tokens=2 delims== " %%V in (`findstr /c:"__version__" "%SRC%CORE_FILES\__init__.py"`) do set "VER=%%~V"
if not defined VER (
    echo [ERROR] Could not read __version__ from "%SRC%CORE_FILES\__init__.py"
    goto :done
)
rem MSI versions only have 3 parts (major.minor.build)
set "MSIVER="
for /f "tokens=1,2,3 delims=." %%A in ("%VER%") do set "MSIVER=%%A.%%B.%%C"

rem ---- Pick the EXE to wrap: the requested version, else the newest in dist\ ----
set "CHOSEN="
if defined VERSION set "CHOSEN=%VERSION%"
rem Strip any accidental whitespace from a manually-set version.
rem (tokens=1 with the default space/tab delimiters trims both sides.)
if defined CHOSEN for /f "tokens=1" %%T in ("%CHOSEN%") do set "CHOSEN=%%T"
if not defined CHOSEN goto :choose_local

echo Wrapping version %CHOSEN% into the MSI.
set "VER=%CHOSEN%"
set "MSIVER="
for /f "tokens=1,2,3 delims=." %%A in ("%CHOSEN%") do set "MSIVER=%%A.%%B.%%C"
set "EXE=dist\systematics-v%CHOSEN%.exe"
if exist "%EXE%" goto :have_exe
echo [ERROR] %EXE% was not found. Build it with build_exe.bat first.
goto :done

:choose_local
set "EXE="
if exist "dist\Systematics TRILLION MIDI maker.exe" set "EXE=dist\Systematics TRILLION MIDI maker.exe"
if not defined EXE (
    for /f "delims=" %%F in ('dir /b /o-d "dist\systematics-v*.exe" 2^>nul') do if not defined EXE set "EXE=dist\%%F"
)
if not defined EXE (
    echo [ERROR] No EXE found in dist\. Run build_exe.bat first.
    goto :done
)

:have_exe
for %%I in ("%EXE%") do set "EXE_ABS=%%~fI"
set "ICON=%SRC%CORE_FILES\icon.ico"
if not exist "%ICON%" (
    echo [ERROR] Missing %SRC%CORE_FILES\icon.ico
    goto :done
)

echo ============================================================
echo   Systematics TRILLION MIDI maker - MSI Builder
echo   Version:  %VER%   (msi %MSIVER%)
echo   EXE:      %EXE_ABS%
echo ============================================================
echo.

rem ---- Ensure WiX Toolset v7 (installed into .tools\wix) ----
set "WIXEXE=%~dp0.tools\wix\wix.exe"
set "WIXMAJOR="
if exist "%WIXEXE%" (
    for /f "tokens=1 delims=." %%V in ('"%WIXEXE%" --version 2^>nul') do set "WIXMAJOR=%%V"
)
if not "%WIXMAJOR%"=="7" (
    where dotnet >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] .NET SDK was not found. Install it from:
        echo         https://dotnet.microsoft.com/download
        goto :done
    )
    if exist "%~dp0.tools\wix" rmdir /s /q "%~dp0.tools\wix"
    echo Installing WiX Toolset v7 into .tools\wix ...
    dotnet tool install wix --tool-path "%~dp0.tools\wix" --version "7.*"
    if errorlevel 1 (
        echo [ERROR] Could not install WiX. Check your internet connection.
        goto :done
    )
)
rem WiX v7 OSMF EULA acceptance marker (free: revenue under US$10,000/yr).
rem See https://wixtoolset.org/osmf/ - the marker just records acceptance.
if not exist "%USERPROFILE%\.wix\wix7-osmf-eula.txt" (
    mkdir "%USERPROFILE%\.wix" 2>nul
    echo WiX v7 OSMF EULA accepted. Free for users with annual gross revenue under US$10,000. See https://wixtoolset.org/osmf/ > "%USERPROFILE%\.wix\wix7-osmf-eula.txt"
)
rem The stock light wizard (WixUI_Advanced) comes from the
rem WixToolset.UI.wixext extension, loaded with -ext below. Make sure it
rem is in the tool's cache (needs internet on first run only).
"%WIXEXE%" extension list 2>nul | findstr /c:"WixToolset.UI.wixext" >nul
if errorlevel 1 (
    echo Installing the WixToolset.UI.wixext extension ...
    "%WIXEXE%" extension add WixToolset.UI.wixext/7.0.0
    if errorlevel 1 (
        echo [ERROR] Could not install the WiX UI extension. Check your internet connection.
        goto :done
    )
)

rem ---- Generate the license RTF ----
set "WORK=%TEMP%\systematics_msi_%RANDOM%"
mkdir "%WORK%" 2>nul
set "LICENSE=%WORK%\license.rtf"
rem The RTF is plain black text - the stock wizard background is light.
rem It must also be long enough (a few paragraphs) that MSI stores it in
rem the Text table - a ScrollableText control reads its content from
rem there, and a short inline text renders as an empty box.
>  "%LICENSE%" echo {\rtf1\ansi\deff0{\fonttbl{\f0\fnil Segoe UI;}}\f0\fs20
>> "%LICENSE%" echo Systematics TRILLION MIDI maker - License Agreement\par
>> "%LICENSE%" echo \par
>> "%LICENSE%" echo By installing this software you agree to use it for its intended purpose. This software is provided as-is, without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose and non-infringement.\par
>> "%LICENSE%" echo \par
>> "%LICENSE%" echo You may use, copy and distribute this software freely, provided that you do not modify it and do not remove this license notice. This license notice applies to all copies and substantial portions of the software.\par
>> "%LICENSE%" echo \par
>> "%LICENSE%" echo In no event shall the authors or copyright holders be liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from, out of or in connection with the software or the use or other dealings in the software.\par
>> "%LICENSE%" echo }

rem ---- Desktop shortcut default level (1 = on, 2 = off by default) ----
set "DESKTOP_LEVEL=1"
if /i "%ADD_DESKTOP%"=="0" set "DESKTOP_LEVEL=2"

rem ---- Generate the .wxs manifest (menu installer) ----
>  "%WORK%\app.wxs" echo ^<?xml version="1.0" encoding="UTF-8"?^>
>> "%WORK%\app.wxs" echo ^<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
>> "%WORK%\app.wxs" echo      xmlns:ui="http://wixtoolset.org/schemas/v4/wxs/ui"^>
>> "%WORK%\app.wxs" echo   ^<Package Name="Systematics TRILLION MIDI maker"
>> "%WORK%\app.wxs" echo            Manufacturer="SystematicMidis"
>> "%WORK%\app.wxs" echo            Version="%MSIVER%"
>> "%WORK%\app.wxs" echo            UpgradeCode="%UPGRADE_GUID%"
>> "%WORK%\app.wxs" echo            Language="1033"^>
>> "%WORK%\app.wxs" echo     ^<MajorUpgrade DowngradeErrorMessage="A newer version of [ProductName] is already installed."^/^>
>> "%WORK%\app.wxs" echo     ^<MediaTemplate EmbedCab="yes"^/^>
>> "%WORK%\app.wxs" echo     ^<Icon Id="AppIcon" SourceFile="%ICON%"^/^>
>> "%WORK%\app.wxs" echo     ^<Property Id="ARPPRODUCTICON" Value="AppIcon"^/^>
>> "%WORK%\app.wxs" echo     ^<Property Id="ApplicationFolderName" Value="Systematics TRILLION MIDI maker"^/^>
>> "%WORK%\app.wxs" echo     ^<Property Id="WixAppFolder" Value="WixPerMachineFolder"^/^>
>> "%WORK%\app.wxs" echo     ^<Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="Launch Systematics TRILLION MIDI maker"^/^>
>> "%WORK%\app.wxs" echo     ^<WixVariable Id="WixUILicenseRtf" Value="%LICENSE%"^/^>
>> "%WORK%\app.wxs" echo     ^<UIRef Id="WixUI_Advanced_X64"^/^>
>> "%WORK%\app.wxs" echo     ^<StandardDirectory Id="ProgramFiles64Folder"^>
>> "%WORK%\app.wxs" echo       ^<Directory Id="APPLICATIONFOLDER" Name="Systematics TRILLION MIDI maker"^>
>> "%WORK%\app.wxs" echo         ^<Component Id="AppExe" Guid="%EXE_COMPONENT_GUID%"^>
>> "%WORK%\app.wxs" echo           ^<File Id="AppExeFile" Source="%EXE_ABS%" Name="Systematics TRILLION MIDI maker.exe" KeyPath="yes"^>
>> "%WORK%\app.wxs" echo             ^<Shortcut Id="StartMenuShortcut" Name="Systematics TRILLION MIDI maker" Directory="ProgramMenuFolder" Icon="AppIcon" WorkingDirectory="APPLICATIONFOLDER"^/^>
>> "%WORK%\app.wxs" echo           ^</File^>
>> "%WORK%\app.wxs" echo         ^</Component^>
>> "%WORK%\app.wxs" echo         ^<Component Id="DesktopShortcutCmp" Guid="%DESKTOP_COMPONENT_GUID%"^>
>> "%WORK%\app.wxs" echo           ^<Shortcut Id="DesktopShortcut" Name="Systematics TRILLION MIDI maker" Directory="DesktopFolder" Target="[#AppExeFile]" Icon="AppIcon" WorkingDirectory="APPLICATIONFOLDER"^/^>
>> "%WORK%\app.wxs" echo           ^<RegistryValue Root="HKCU" Key="Software\SystematicMidis\STMM" Name="DesktopShortcut" Type="integer" Value="1" KeyPath="yes"^/^>
>> "%WORK%\app.wxs" echo         ^</Component^>
>> "%WORK%\app.wxs" echo       ^</Directory^>
>> "%WORK%\app.wxs" echo     ^</StandardDirectory^>
>> "%WORK%\app.wxs" echo     ^<StandardDirectory Id="ProgramMenuFolder"^/^>
>> "%WORK%\app.wxs" echo     ^<StandardDirectory Id="DesktopFolder"^/^>
>> "%WORK%\app.wxs" echo     ^<Feature Id="MainFeature" Title="Systematics TRILLION MIDI maker" Description="The app itself." Level="1"^>
>> "%WORK%\app.wxs" echo       ^<ComponentRef Id="AppExe"^/^>
>> "%WORK%\app.wxs" echo       ^<Feature Id="DesktopFeature" Title="Desktop shortcut" Description="Add a shortcut to your desktop." Level="%DESKTOP_LEVEL%"^>
>> "%WORK%\app.wxs" echo         ^<ComponentRef Id="DesktopShortcutCmp"^/^>
>> "%WORK%\app.wxs" echo       ^</Feature^>
>> "%WORK%\app.wxs" echo     ^</Feature^>
>> "%WORK%\app.wxs" echo     ^<CustomAction Id="LaunchApp" FileRef="AppExeFile" ExeCommand="" Execute="immediate" Impersonate="yes" Return="asyncNoWait"^/^>
>> "%WORK%\app.wxs" echo     ^<UI^>
>> "%WORK%\app.wxs" echo       ^<Publish Dialog="ExitDialog" Control="Finish" Event="DoAction" Value="LaunchApp" Order="1" Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed"^/^>
>> "%WORK%\app.wxs" echo     ^</UI^>
>> "%WORK%\app.wxs" echo   ^</Package^>
>> "%WORK%\app.wxs" echo ^</Wix^>

rem ---- Build the MSI ----
rem The stock light wizard (WixUI_Advanced) is supplied by the
rem WixToolset.UI.wixext extension.
set "OUT=%~dp0dist\systematics-v%VER%.msi"
echo Building %OUT% ...
"%WIXEXE%" build "%WORK%\app.wxs" -o "%OUT%" -arch x64 -pdbtype none -ext WixToolset.UI.wixext
set "BUILD_RESULT=%errorlevel%"

rem ---- Cleanup ----
rmdir /s /q "%WORK%" 2>nul

:done
echo.
if "%BUILD_RESULT%"=="0" (
    echo ============================================================
    echo   Done! Your MSI is here:
    echo(
    echo       dist\systematics-v%VER%.msi
    echo(
    echo   Install for everyone:   double-click it as admin, or:
    echo       msiexec /i systematics-v%VER%.msi /qn
    echo ============================================================
) else (
    echo [ERROR] The MSI build failed. See the messages above.
)
if /i not "%~1"=="nopause" pause
endlocal
