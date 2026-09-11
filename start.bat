@echo off
REM ============================================================
REM  AI Case Library - local launcher (zero dependency)
REM  Just runs the stdlib-only Python server.
REM ============================================================
setlocal

cd /d "%~dp0"

set PY=
if exist "C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
  set PY=C:\Users\DELL\.workbuddy\binaries\python\versions\3.13.12\python.exe
)
if "%PY%"=="" (
  where python >nul 2>nul && set PY=python
)
if "%PY%"=="" (
  echo [!] Python not found. Install Python 3.9+ and retry.
  pause
  exit /b 1
)

echo.
echo   Starting AI Case Library ...
echo   Browser will open at http://127.0.0.1:5052/
echo   Press Ctrl+C in this window to stop.
echo.

"%PY%" server.py

echo.
echo Server stopped.
pause
endlocal
