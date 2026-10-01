@echo off
REM ============================================================
REM  重建 6 条服务端损坏的公众号草稿（2026-10-01 确认 320003 记录级损坏）
REM
REM  为什么必须重建：magicslides-app / stan / 1lookup / gojiberryai /
REM  sierra / genius-ai 在服务端**打不开编辑页**，refresh 刷不进去。
REM  唯一出路是当新草稿重发（publish --case 走的就是这条路径）。
REM
REM  用法：双击本文件，或在 cmd 里跑
REM     scripts\wechat_rebuild_broken.bat          干跑看计划
REM     scripts\wechat_rebuild_broken.bat --go     真跑
REM ============================================================
setlocal
cd /d "%~dp0\.."

REM ⚠ 代理必须清掉，否则 CDP 的 WebSocket 会被掐断（10053/10054）
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set PYTHONIOENCODING=utf-8

set GO=
if /i "%~1"=="--go" set GO=--go

echo === 0) 前置检查 ===
where chrome.exe >nul 2>&1
if exist "%LOCALAPPDATA%\Google\ChromeCDP\Default\LOCK" (
    echo ⚠ 存在残留 LOCK，Chrome 会启动即退。正在清理...
    del /f /q "%LOCALAPPDATA%\Google\ChromeCDP\Default\LOCK" 2>nul
)

REM 启动 Chrome（如果还没在跑）
curl -s --noproxy * --max-time 4 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"webSocketDebuggerUrl" >nul
if errorlevel 1 (
    echo 启动 Chrome（CDP 模式）...
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" ^
        --remote-debugging-port=9222 ^
        --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP"
    timeout /t 8 /nobreak >nul
)

curl -s --noproxy * --max-time 5 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"Browser" >nul
if errorlevel 1 (
    echo.
    echo [X] CDP 端口 9222 不通。
    echo.
    echo     请【完全退出】所有 Chrome 窗口（任务管理器确认无 chrome.exe），
    echo     然后手动执行：
    echo.
    echo       "%ProgramFiles%\Google\Chrome\Application\chrome.exe" ^
    echo         --remote-debugging-port=9222 ^
    echo         --user-data-dir="%LOCALAPPDATA%\Google\ChromeCDP"
    echo.
    echo     启动后浏览器打开 http://127.0.0.1:9222/json/version 应返回一段JSON。
    pause
    exit /b 1
)
echo [OK] CDP 端口通

echo.
echo === 1) 登录自检 ===
python -X utf8 scripts\wechat_verify_refresh.py --list
if errorlevel 1 (
    echo.
    echo [X] 拿不到 token —— 浏览器里请扫码登录 mp.weixin.qq.com。
    echo     登录后重新运行本文件。
    pause
    exit /b 1
)

echo.
echo === 2) 服务端那6 条损坏记录的现状 ===
python -X utf8 scripts\wechat_publish.py delete ^
    --appmsgid 100000125,100000130,100000134,100000138,100000142,100000146 --dry

echo.
echo === 3) 逐条重建 ===
for %%C in (magicslides-app stan 1lookup gojiberryai sierra genius-ai) do (
    echo.
    echo -------- %%C --------
    python -X utf8 -u scripts\wechat_publish.py publish --case %%C %GO%
    if errorlevel 1 echo [warn] %%C 失败，看上面的日志
)

echo.
echo === 4) 回读对账（按篇号，别信"保存 OK"）===
python -X utf8 scripts\wechat_verify_refresh.py --all

echo.
echo === 全部结束 ===
echo 请人工确认 6 条新草稿的标题/正文/封面是否正常。
echo 确认后：
echo   1) 把新appmsgid 写回 data\wechat_published.json
echo   2) 从 data\wechat_broken_drafts.json 移除这 6 个条目
pause
endlocal