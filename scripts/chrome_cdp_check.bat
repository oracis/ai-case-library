@echo off
REM ============================================================
REM  Chrome CDP 登录态自检（诊断「登录后一会就掉」）
REM
REM  流程：清 LOCK → 启动 Chrome for Testing → 检查 CDP → 体检登录态
REM
REM  ⚠ Chrome 必须在【本机】跑，agent 环境拉不起 GUI 进程。
REM     直接双击本文件即可。
REM
REM  ⚠ 判据已修正（2026-10-02）：不要再看 Cookies 文件大小！
REM     实测日常 Chrome 的 cookie 库 1.4MB 但微信 cookie 只有 8 条，
REM     CDP profile 只有 32KB 却是 12 条且能正常登录。
REM     文件大小取决于 SQLite 页/WAL 残留膨胀，与登录态无正相关。
REM     唯一可靠判据 =能不能拿到 token（脚本最后一步会实拉）。
REM ============================================================
setlocal
cd /d "%~dp0\.."

set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set PYTHONIOENCODING=utf-8

set CFT=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe
set PROF=%LOCALAPPDATA%\Google\ChromeCDP

if not exist "%CFT%" (
    echo [X] 没找到 Chrome for Testing：%CFT%
    echo     请先下载 Chrome for Testing（官方针对CDP 场景的推荐浏览器）。
    pause
    exit /b 1
)

echo === 0) 清残留 LOCK（Chrome 启动即退的元凶）===
if exist "%PROF%\Default\LOCK" (
    echo 发现 LOCK，正在删除...
    del /f /q "%PROF%\Default\LOCK" 2>nul
) else (
    echo 无 LOCK
)

echo.
echo === 1) 启动 Chrome（Chrome for Testing + 独立 profile）===
start "" "%CFT%" --remote-debugging-port=9222 --user-data-dir="%PROF%" --no-first-run --no-default-browser-check
timeout /t 12 /nobreak >nul

curl -s --noproxy * --max-time 5 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"Browser" >nul
if errorlevel 1 (
    echo [X] CDP 端口不通。常见原因：Chrome 启动即退。
    echo     - 看任务管理器有没有 chrome.exe 闪一下就没了
    echo     - 那就是 LOCK 没清干净，关掉所有 Chrome 窗口后重跑本文件
    pause
    exit /b 1
)
echo [OK] CDP 端口通

echo.
echo === 2) 登录态体检 ===
python -X utf8 scripts\chrome_login_state.py
echo.
pause
endlocal