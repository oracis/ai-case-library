@echo off
REM ============================================================
REM  AI Case Library - local launcher (zero dependency)
REM  Just runs the stdlib-only Python server.
REM ============================================================
setlocal

cd /d "%~dp0"

REM 依次探测：py 启动器 -> python
set PYEXE=
where py >nul 2>nul && set PYEXE=py
if not defined PYEXE (
  where python >nul 2>nul && set PYEXE=python
)
if not defined PYEXE (
  echo [!] 没找到 Python。请安装 Python 3.9+ 后重试：
  echo     https://www.python.org/downloads/
  echo     安装时记得勾选 "Add Python to PATH"。
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
