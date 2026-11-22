@echo off
REM Launch a headed Chrome for the two clouds (Aliyun/TencentCloud, port 9228,
REM profile chrome-yun-profile) in YOUR OWN desktop session.
REM
REM DOUBLE-CLICK this file from Explorer (NOT via the agent sandbox). Only then
REM does the Chrome window belong to your session: you can SEE it, drag the
REM Aliyun captcha slider, and it is NOT reclaimed when an agent command ends.
REM Leave the window open, then tell the agent to continue the publish.
setlocal
set "CFT=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe"
set "PROF=%USERPROFILE%\chrome-yun-profile"
if not exist "%CFT%" set "CFT=C:\Program Files\Google\Chrome\Application\chrome.exe"
echo Starting Chrome on port 9228 (profile chrome-yun-profile)...
if exist "%PROF%\Default\LOCK" del /f /q "%PROF%\Default\LOCK" 2>nul
start "Yun-Chrome" "%CFT%" --remote-debugging-port=9228 --user-data-dir="%PROF%" --no-first-run --no-default-browser-check --no-sandbox --remote-allow-origins=* about:blank
echo Done. Leave the Chrome window open, then tell the agent to continue.
pause
