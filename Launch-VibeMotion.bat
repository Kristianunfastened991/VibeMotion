@echo off
setlocal

cd /d "%~dp0"
title VibeMotion v0.1.0-pre-alpha.1 Server

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "APP_PORT=8010"
set "APP_URL=http://127.0.0.1:%APP_PORT%/app/index.html?fresh=1"

echo.
echo ============================================
echo  VibeMotion v0.1.0-pre-alpha.1 first-run setup
echo ============================================
echo.
echo Checking dependencies, Figma plugin registration, and LTX model files.
echo This can take a long time on the first launch.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo VibeMotion v0.1.0-pre-alpha.1 setup failed.
  pause
  exit /b 1
)

echo.
echo ============================================
echo  VibeMotion v0.1.0-pre-alpha.1
echo ============================================
echo.
echo This terminal is the app server.
echo Close this terminal to stop VibeMotion v0.1.0-pre-alpha.1.
echo Browser tab close does NOT stop the server.
echo.
echo Hardware profile:
echo - Vision model target: qwen2.5vl:7b
echo - STT: faster-whisper CPU int8 by default
echo - Render: FFmpeg NVENC when available
echo.
echo Cleaning old VibeMotion server processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '.').Path; Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match [regex]::Escape($root) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo Starting browser when server is ready...
start "VibeMotion Browser Opener" /min powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='%APP_URL%'; $health='http://127.0.0.1:%APP_PORT%/api/projects'; for ($i=0; $i -lt 60; $i++) { try { $r=Invoke-WebRequest -UseBasicParsing $health -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process $url; exit 0 } } catch {} Start-Sleep -Milliseconds 500 }; Start-Process $url"

echo.
echo Server logs:
echo.
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT%

echo.
echo VibeMotion v0.1.0-pre-alpha.1 stopped.
pause
