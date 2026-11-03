@echo off
setlocal
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run py setup.py first. See README.md.
  exit /b 1
)
set "PATH=%~dp0.venv\Scripts;%PATH%"
"%~dp0.venv\Scripts\python.exe" "%~dp0downloader.py" %*
