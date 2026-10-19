@echo off
REM ============================================================
REM  Chrome CDP login-state self-check
REM
REM  NOTE: This file MUST stay pure ASCII. cmd.exe reads .bat as
REM  the OEM codepage (GBK on this machine); any Chinese byte in a
REM  REM/comment/echo line corrupts parsing and silently skips the
REM  following commands (that is how "cd" got dropped and the path
REM  became scripts\scripts\...).
REM
REM  Flow: clear LOCK -> start Chrome for Testing -> check CDP ->
REM        live-probe the real login state (token probe)
REM
REM  IMPORTANT: --no-sandbox is REQUIRED on this machine.
REM  Without it Chrome starts, loads policy/variations services, then
REM  dies within ~2s with exit code 3 (RESULT_CODE_KILLED_BAD_MESSAGE)
REM  and the DevTools port never opens. Verified by flag-matrix test:/nREM    baseline              -> FAIL (exit 3)
REM    --disable-gpu         -> FAIL (exit 3)
REM    --no-sandbox          -> CDP OK
REM    --no-sandbox + gpu off-> CDP OK
REM  Root cause: the sandbox blocks renderer/GPU child-process
REM  creation in this environment. The flag is safe here because we
REM  only automate our own local WeChat session.
REM
REM  Run this on the LOCAL machine (double-click). The agent sandbox
REM  cannot keep a GUI process alive.
REM
REM  Do NOT judge by Cookies file size. See chrome_cdp_setup.md.
REM ============================================================
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROF=%LOCALAPPDATA%\Google\ChromeCDP"
set "CFT=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe"

set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set PYTHONIOENCODING=utf-8

REM ---- resolve a python interpreter -------------------------------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  if exist "%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  )
)
if not defined PY (
  echo [X] No python interpreter found on PATH.
  pause
  exit /b 1
)

if not exist "%CFT%" (
  echo [X] Chrome for Testing not found:
  echo     %CFT%
  echo     Download Chrome for Testing first.
  pause
  exit /b 1
)

echo === 0) Clear stale LOCK (cause of instant-exit) ===
if exist "%PROF%\Default\LOCK" (
  echo   found LOCK, deleting...
  del /f /q "%PROF%\Default\LOCK" 2>nul
) else (
  echo   no LOCK
)

echo.
echo === 1) Start Chrome for Testing ===
start "" "%CFT%" --remote-debugging-port=9222 --user-data-dir="%PROF%" --no-first-run --no-default-browser-check --no-sandbox
REM "timeout" is unreliable here: Git Bash injects GNU coreutils
REM timeout into PATH, which shadows the cmd.exe builtin.
REM "ping -n" sleeps reliably in BOTH environments.
ping -n 13 127.0.0.1 >nul

curl -s --noproxy * --max-time 5 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"Browser" >nul
if errorlevel 1 (
  echo [X] CDP port 9222 not reachable. Chrome likely exited instantly.
  echo     - check Task Manager for chrome.exe flashing and vanishing
  echo     - close ALL Chrome windows, then re-run this file
  pause
  exit /b 1
)
echo   [OK] CDP port is up

echo.
echo === 2) Login state probe ===
"%PY%" -X utf8 "%SCRIPT_DIR%chrome_login_state.py"
echo.
pause
endlocal