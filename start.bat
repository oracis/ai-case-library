@echo off
REM ============================================================
REM  AI Case Library - local launcher (zero dependency)
REM  Just runs the stdlib-only Python server.
REM
REM  NOTE: keep this file pure ASCII. cmd.exe reads .bat using the
REM  OEM codepage (GBK on this machine); Chinese bytes in comments
REM  or echo lines corrupt parsing and can silently drop the
REM  commands that follow.
REM ============================================================
setlocal EnableExtensions

cd /d "%~dp0"

REM Probe for an interpreter: py launcher -> python
set PYEXE=
where py >nul 2>nul && set PYEXE=py
if not defined PYEXE (
  where python >nul 2>nul && set PYEXE=python
)
if not defined PYEXE (
  if exist "%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set PYEXE=%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe
  )
)
if not defined PYEXE (
  echo [!] No Python interpreter found. Install Python 3.9+ and retry:
  echohttps://www.python.org/downloads/
  echo     Tick "Add Python to PATH" during setup.
  pause
  exit /b 1
)

echo.
echo   Starting AI Case Library ...
echo   Browser will open at http://127.0.0.1:5052/
echo   Press Ctrl+C in this window to stop.
echo.

%PYEXE% server.py

echo.
echo Server stopped.
pause
endlocal