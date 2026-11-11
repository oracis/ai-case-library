#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探针：两个自动化 Chrome profile 里找 CSDN 登录态，并离线验证会话。

  1. 起无头 Chrome（chrome-debug-profile @9223，再试 ChromeCDP @9222）
  2. CDP Network.getAllCookies 过滤 csdn.net 域
  3. 有 UserName/UserToken 即视为可能已登录 → 拼完整 Cookie 串
  4. 用 _probe_csdn 已验证的签名算法 + 该 Cookie POST saveArticle 空 body：
       - 401 请登录后操作  → 该 profile 的 CSDN 会话无效/过期
       - 其他业务响应       → 会话有效（**到此为止，不存稿**）

风险控制：全程不建草稿；cookie 值只打印前 4 位。用完即删。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)

import chrome_cdp_launch as L          # noqa: E402
from juejin_publish import _CDP        # noqa: E402  stdlib CDP 客户端

sys.path.insert(0, ROOT)

LOGIN_MARKERS = ("UserName", "UserToken", "p_uid", "dc_session_id")
UATMP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")


def probe_port(port):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % port, timeout=3) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def launch(profile, port, chrome_hint=None):
    if probe_port(port):
        print("[probe] %d 已在运行，复用" % port)
        return None
    L.clear_proxy()
    L.remove_lock(profile)
    # chrome-debug-profile 是系统 Chrome 建的：Chrome for Testing 版本旧，
    # 开它会 exit 21（profile 版本更新）。有 chrome_hint 就强制用它。
    chrome, label = (chrome_hint, "system Chrome (hinted)") if chrome_hint \
        else L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    args = L.build_args(chrome, profile, port=port, headless=True)
    print("[probe] launch %s (%s, headless)" % (label, profile), flush=True)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=port):
        proc.terminate()
        return None
    return proc


def collect(port):
    cdp = _CDP(port)
    tid = None
    try:
        tid = cdp.open("about:blank")
        cdp.send("Network.enable")
        r = cdp.send("Network.getAllCookies")
        items = (r.get("result") or {}).get("cookies") or []
        return [c for c in items if "csdn.net" in (c.get("domain") or "")]
    finally:
        if tid:
            try:
                cdp.send_browser("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass
        cdp.bws.close()


def main():
    sys_chrome = os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")
    profiles = [
        (os.path.expandvars(r"%USERPROFILE%\chrome-debug-profile"), 9223, sys_chrome),
        (os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP"), 9222, None),
    ]
    found = {}
    for profile, port, hint in profiles:
        print("\n### profile: %s" % profile)
        try:
            proc = launch(profile, port, chrome_hint=hint)
        except SystemExit as e:
            print("  [skip] %s" % e)
            proc = None
        if proc is None and not probe_port(port):
            print("  [skip] CDP 起不来，跳过该 profile")
            continue
        try:
            cookies = collect(port)
            names = sorted(c["name"] for c in cookies)
            print("  csdn.net cookie 数: %d" % len(cookies))
            hit = [n for n in names if n in LOGIN_MARKERS]
            print("  登录标志命中: %s" % (hit or "无"))
            if hit:
                pair = ["%s=%s" % (c["name"], c["value"])
                        for c in cookies if not c["name"].startswith("__")]
                found[profile] = pair
        finally:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
                time.sleep(1)

    if not found:
        print("\n[结论] 两个自动化 profile 都没有 CSDN 登录态"
              "（日常浏览器里可能有，或需扫码登录一次）")
        return

    # 会话有效性离线验证（复用已验证的签名算法）
    print("\n### 会话有效性验证（空 body，不会建稿）")
    import base64, hashlib, hmac, uuid  # noqa: E401
    CA_KEY = "203803574"
    SECRET = "9znpamsyl2c7cdrr9sas0le9vbc3r6ba"
    PATH = "/blog-console-api/v3/mdeditor/saveArticle"
    URL = "https://bizapi.csdn.net" + PATH
    nonce = str(uuid.uuid4())
    to_sign = ("POST\n*/*\n\napplication/json;charset=UTF-8\n\n"
               "x-ca-key:%s\nx-ca-nonce:%s\n%s" % (CA_KEY, nonce, PATH))
    sig = base64.b64encode(hmac.new(SECRET.encode(), to_sign.encode(),
                                    hashlib.sha256).digest()).decode()
    for profile, pair in found.items():
        cookie = "; ".join(pair)
        headers = {
            "User-Agent": UATMP,
            "Accept": "*/*",
            "Content-Type": "application/json;charset=UTF-8",
            "x-ca-key": CA_KEY,
            "x-ca-nonce": nonce,
            "x-ca-signature": sig,
            "x-ca-signature-headers": "x-ca-key,x-ca-nonce",
            "Origin": "https://editor.csdn.net",
            "Referer": "https://editor.csdn.net/",
            "Cookie": cookie,
        }
        req = urllib.request.Request(URL, data=b"{}", method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with op.open(req, timeout=15) as r:
                code, body = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read().decode("utf-8", "replace")
        verdict = "❌ 无效（401）" if code == 401 else "✅ 有效（非 401）"
        print("  %s -> HTTP %s %s | %s" % (
            os.path.basename(profile), code, verdict, body[:150]))

        # 会话有效就把 cookie 存进 data/（与 juejin_cookie.json 同约定）
        if code != 401:
            out = os.path.join(ROOT, "data", "csdn_cookie.json")
            import atomicio  # noqa: E401
            atomicio.write_json(out, {"cookie_string": cookie,
                                      "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            print("  已保存 -> %s" % out)


if __name__ == "__main__":
    main()
