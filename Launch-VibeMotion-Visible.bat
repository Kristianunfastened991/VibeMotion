@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
set "APP_PORT=8010"
set "APP_URL=http://127.0.0.1:%APP_PORT%/app/index.html?fresh=1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo VibeMotion v1.0 setup failed.
  pause
  exit /b 1
)

echo Starting VibeMotion v1.0 server in a visible window...
echo Leave this window open while the app is running.
echo.
echo Starting browser when server is ready...
start "VibeMotion Browser Opener" /min powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='%APP_URL%'; $health='http://127.0.0.1:%APP_PORT%/api/projects'; for ($i=0; $i -lt 60; $i++) { try { $r=Invoke-WebRequest -UseBasicParsing $health -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process $url; exit 0 } } catch {} Start-Sleep -Milliseconds 500 }; Start-Process $url"

"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT% --reload

endlocal
