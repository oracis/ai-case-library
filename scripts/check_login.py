#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 CDP 在**已开的调试 Chrome** 里逐个平台检查登录态（只读，不点任何按钮）。

为什么需要这个：`xhs_publish drafts` / `toutiao_publish login` 这些子命令
在登录态失效时只报一句「打不开草稿箱抽屉」，看不出到底是「没登录」还是
「选择器失配」。这里直接读页面 URL + 关键 DOM 特征，给出可判定的结论。

⚠ 只做 GET 导航 +读 DOM，绝不点登录/发布类按钮。
"""
import json
import re
import sys
import time
import urllib.request

CDP = "http://127.0.0.1:9222"

PLATFORMS = [
    ("公众号", "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN",
     [" domesticMsg", "weui-desktop", " mp-common-mask"], ["/cgi-bin/loginpage", "/login"]),
    ("今日头条", "https://mp.toutiao.com/profile_v4/graphic/publish",
     ["mp-editor", "publish-title", "ProseMirror"], ["/auth/page/login", "login"]),
    ("小红书", "https://creator.xiaohongshu.com/publish/publish",
     ["creator-editor", "note-editor", "publish-title"], ["/login"]),
    ("B站", "https://member.bilibili.com/read/home",
     ["bcc-read", "member-panel", " up"], ["/platform/login"]),
]


def cdp_get(path):
    with urllib.request.urlopen(CDP + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def new_tab(url):
    """开一个新标签页，返回 targetId。"""
    req = urllib.request.Request(
        CDP + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def close_tab(tid):
    try:
        urllib.request.urlopen(CDP + "/json/close/" + tid, timeout=5).read()
    except Exception:# noqa: BLE001
        pass


def evaluate(tid, expr, timeout=25):
    """在标签页里执行 JS，返回结果。用 /json/protocol 的扁平封装。"""
    ws_url = None
    for t in cdp_get("/json/list"):
        if t.get("id") == tid:
            ws_url = t.get("webSocketDebuggerUrl")
            break
    if not ws_url:
        return None
    return _ws_eval(ws_url, expr, timeout)


def _ws_eval(ws_url, expr, timeout):
    """极简 CDP over WebSocket（标准库实现，不引第三方包）。"""
    import base64
    import hashlib
    import socket
    import struct

    u = re.match(r"ws://([^:/]+):(\d+)(/.*)", ws_url)
    host, port, path = u.group(1), int(u.group(2)), u.group(3)
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(4096)
        if not d:
            raise RuntimeError("ws 握手失败")
        buf += d
    msg = json.dumps({"id": 1, "method": "Runtime.evaluate",
                      "params": {"expression": expr, "returnByValue": True,
                                 "awaitPromise": False}})
    payload = msg.encode()
    mask = os.urandom(4)
    n = len(payload)
    frame = bytearray([0x81])
    if n < 126:
        frame.append(0x80 | n)
    elif n < 65536:
        frame.append(0x80 | 126)
        frame += struct.pack(">H", n)
    else:
        frame.append(0x80 | 127)
        frame += struct.pack(">Q", n)
    frame += mask
    frame += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    s.sendall(bytes(frame))

    def _read(n):
        out = b""
        while len(out) < n:
            d = s.recv(n - len(out))
            if not d:
                raise RuntimeError("ws 读失败")
            out += d
        return out

    hdr = _read(2)
    ln = hdr[1] & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", _read(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", _read(8))[0]
    data = _read(ln)
    s.close()
    r = json.loads(data.decode("utf-8", "replace"))
    res = (r.get("result") or {}).get("result") or {}
    if "value" in res:
        return res["value"]
    return res.get("description")


import os            # noqa: E402
import urllib.parse   # noqa: E402

PROBE_JS = """
(function(){
  var b = document.body ? document.body.innerText : '';
  return JSON.stringify({
    url: location.href.slice(0, 120),
    title: (document.title||'').slice(0, 60),
    len: b.length,
    hasLogin: /登录|扫码|手机号登录/.test(b.slice(0, 3000)),
    hasEditor: /发布|草稿箱|编辑器|正文/.test(b.slice(0, 3000))
  });
})()
"""


def main():
    print("=" * 66)
    print("  调试 Chrome 登录态检查（只读）")
    print("=" * 66)
    for name, url, _, login_marks in PLATFORMS:
        tid = None
        try:
            tid = new_tab(url)["id"]
            time.sleep(4)
            raw = evaluate(tid, PROBE_JS)
            info = json.loads(raw) if raw else {}
            u = info.get("url", "")
            redirected = any(m in u for m in login_marks)
            state = ("✗ 未登录" if (redirected or info.get("hasLogin")
                                    and not info.get("hasEditor"))
                     else "✓ 已登录")
            print("\n  %-8s %s" % (name, state))
            print("           最终 URL: %s" % u[:96])
            print("           标题: %s · 正文长度 %s"
                  % (info.get("title", "-"), info.get("len", "-")))
        except Exception as e:                # noqa: BLE001
            print("\n  %-8s ? 检查失败：%s" % (name, e))
        finally:
            if tid:
                time.sleep(0.5)
                close_tab(tid)
    print("\n" + "=" * 66)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
