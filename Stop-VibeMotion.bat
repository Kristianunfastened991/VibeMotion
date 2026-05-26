@echo off
setlocal

echo Stopping VibeMotion server processes...
taskkill /FI "WINDOWTITLE eq VibeMotion v0.1.0-pre-alpha.1 Server*" /T /F >nul 2>nul
taskkill /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq *uvicorn*" /T /F >nul 2>nul

for /f "tokens=2" %%a in ('tasklist /v /fo csv ^| findstr /i "uvicorn app.main:app"') do (
  taskkill /PID %%~a /T /F >nul 2>nul
)

echo Done.
endlocal
