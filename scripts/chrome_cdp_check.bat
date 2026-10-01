@echo off
REM ============================================================
REM  Chrome CDP 登录态自检（诊断「登录后一会就掉」）
REM
REM  根因参考 docs：scripts\chrome_cdp_setup.md
REM  本脚本：清 LOCK → 启动 Chrome → 检查 CDP → 看 Cookies 大小
REM
REM  ⚠ Chrome 必须在【本机】跑，agent 环境拉不起GUI 进程。
REM     直接双击本文件即可。
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
    echo     装一次即可（官方针对CDP 场景的推荐浏览器）。
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
start "" "%CFT%" --remote-debugging-port=9222 --user-data-dir="%PROF%"
timeout /t 10 /nobreak >nul

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
echo === 2) Cookies 文件大小（登录态是否真在盘上）===
for %%F in ("%PROF%\Default\Network\Cookies" "%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies") do (
    if exist %%F (
        for %%S in (%%~zF) do (
            echo   %%~nxF  = %%S 字节
        )
    ) else (
        echo   %%~nxF  不存在
    )
)
echo   判据：CDP profile 的 Cookies 应 ^>100KB。若只有 32KB 左右= 登录态又丢了。

echo.
echo === 3) 登录自检 ===
python -X utf8 scripts\wechat_verify_refresh.py --list
if errorlevel 1 (
    echo.
    echo [X] 拿不到 token —— 请在浏览器里扫码登录 mp.weixin.qq.com
    echo     登录后再跑一次本文件，确认 Cookies 是否涨到 100KB 以上。
    pause
    exit /b 1
)
echo [OK] 登录态可用

echo.
echo 全部通过。登录态持久性由Cookies 大小判断，详见 scripts\chrome_cdp_setup.md
pause
endlocal