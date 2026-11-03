@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo Downloader - Windows setup
where py >nul 2>nul
if not errorlevel 1 (
  set "PYTHON=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Install Python 3.10+ from python.org, then open this file again.
    echo Or: winget install --id Python.Python.3.13 --exact
    pause
    exit /b 1
  )
  set "PYTHON=python"
)
%PYTHON% --version
if errorlevel 1 (
  echo Python could not start. Install Python from python.org and try again.
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 goto tools
where ffprobe >nul 2>nul
if errorlevel 1 goto tools
where deno >nul 2>nul
if errorlevel 1 goto tools
goto install
:tools
echo FFmpeg/ffprobe and Deno are required. Open a NEW terminal after installing.
echo   winget install --id Gyan.FFmpeg --exact
echo   winget install --id DenoLand.Deno --exact
echo If already installed, this terminal may need reopening to refresh PATH.
echo See README.md for official download links.
:install
%PYTHON% setup.py
set "SETUP_STATUS=%ERRORLEVEL%"
if not "%SETUP_STATUS%"=="0" (
  echo Setup needs attention. Follow the messages above, then rerun Setup.cmd.
) else (
  echo Ready. Double-click Start.cmd, or open a NEW terminal and type yt.
)
pause
exit /b %SETUP_STATUS%
