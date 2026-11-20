#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cdp_core.py — 平台无关的 Chrome DevTools Protocol 基座（WS + CDP）
==================================================================

2026-11-20 从 wechat_publish.py 原样搬出（CDP 基座拆解第 1 步）。

这两个类是**纯协议实现**，与公众号/任何具体平台无关：
  · WS   —— 最小 WebSocket 客户端（裸 socket，握手 + 帧收发 + 掩码）
  · CDP  —— DevTools 协议封装（target 管理 / Runtime.evaluate / 输入事件）

此前它们被埋在 5705 行的 wechat_publish.py 里，被 52 个非测试脚本通过
wp.CDP（121 次调用）反复 import。搬出后 wechat_publish.py 顶部用
`from cdp_core import WS, CDP` 做 re-export，所有调用方零改动。

⚠ 零逻辑改写，纯搬家：方法体、常量、注释逐字保留。唯一增量是
  ensure_no_proxy_for_localhost() 在这里也留了一份——它原本在
  wechat_publish 模块导入时全局调用，是 CDP 能连上 127.0.0.1 的前提；
  cdp_core 被独立 import 时也需要这个副作用（幂等，重复调用无害）。
  urllib.error 由 urllib.request 间接带出，这里显式 import 固化依赖。

依赖：纯标准库。Chrome 需以远程调试端口启动（见 wechat_publish 的报错提示）。
"""
import base64
import json
import os
import re
import select
import socket
import struct
import time
import urllib.error
import urllib.request

CDP_PORT = int(os.environ.get("CDP_PORT", 9222))

_LOCAL_NO_PROXY_HOSTS = ("127.0.0.1", "localhost", "::1")


def ensure_no_proxy_for_localhost():
    """import 成库时调用：把本地地址加进 no_proxy / NO_PROXY。

    避免 CDP 连 127.0.0.1 时被继承的 http_proxy 掐断（10053），同时【不】pop
    掉 http_proxy/https_proxy——否则会误伤 importer 的外网代理。幂等。
    """
    for _env in ("no_proxy", "NO_PROXY"):
        _val = os.environ.get(_env, "")
        _parts = [p.strip() for p in _val.split(",") if p.strip()]
        for _h in _LOCAL_NO_PROXY_HOSTS:
            if _h not in _parts:
                _parts.append(_h)
        os.environ[_env] = ",".join(_parts)


ensure_no_proxy_for_localhost()  # 模块导入即生效（覆盖 import-as-library 路径）


# ======================================================================
# 以下 WS / CDP 两类和原文件相比是逐字搬家，勿在此文件里改逻辑。
# 如需改协议行为，改这里；调用方通过 wechat_publish 的 re-export 自动获得。
# ======================================================================

class WS:
    def __init__(self, url):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        # 2026-10-14：头条发文页的 Runtime.evaluate（尤其正文 insertHTML /
        # 封面上传）偶发要 20s+ 才回包，原 30s 太紧会冒 socket.timeout 把
        # publish_one 打挂。放宽到 45s 兜底（封面 20s+ 仍留 25s 余量）；
        # 真·卡死也会在 45s 后冒异常被上层 try 接住（不会无限挂）。
        # 2026-10-15：从 90s 降到 45s —— 小红书删稿偶发 eval 卡死，90s 单点
        # 拖垮整轮重试循环；45s 能在半程内恢复，且对头条/公众号/B站无害。
        self.sock = socket.create_connection((self.host, self.port), timeout=45)
        self._handshake()
        self._buf = b""

    def _handshake(self):
        key = os.urandom(16)
        req = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: %s\r\n\r\n"
            % (self.path, self.host, self.port, base64.b64encode(key).decode())
        )
        self.sock.sendall(req.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def _recv_frame(self):
        b0, b1 = self._read_exact(2)
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        if masked:
            self._read_exact(4)
        return fin, opcode, self._read_exact(length)

    def recv_text(self):
        parts = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode in (0x1, 0x0):
                parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "replace")

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytes([0x81])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(n))
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def _js_exception_details(resp):
    """从 Runtime.evaluate 响应里取 JS 异常详情。

    真实 CDP 的形状是 resp['result']['exceptionDetails']；历史代码查 resp 顶层，
    永远命中不了，JS 异常于是被静默吞掉、eval 最后返回 None。以 result 内为准，
    同时兼容顶层形状（老 mock / 个别包装层）。
    """
    if not isinstance(resp, dict):
        return None
    result = resp.get("result")
    if isinstance(result, dict) and result.get("exceptionDetails"):
        return result["exceptionDetails"]
    return resp.get("exceptionDetails")


class CDP:
    def __init__(self, port=CDP_PORT):
        self.port = port
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            ver = self._http("/json/version")
        except urllib.error.URLError as e:
            raise SystemExit(
                "\n✗ 连不上 Chrome 调试端口 127.0.0.1:%d\n"
                "  Chrome 136+ 拒绝在【默认 user-data-dir】上开调试端口（官方说明：\n"
                "  https://developer.chrome.com/blog/remote-debugging-port）。\n"
                "  ⇒ CDP 只能跑在【独立 profile】上，登录态天然与日常 Chrome 分家，\n"
                "    这是官方设计，不是本机故障。\n"
                "  ⚠ 别再尝试 junction / 复制 Cookies 复用主 profile —— 实测 ChromeCDP\n"
                "    并非 junction，且 App-Bound 加密绑定原始路径，副本解不开。\n"
                "  请按顺序确认：\n"
                "  1) 已【完全退出】所有 Chrome 窗口（任务管理器确认无 chrome.exe 残留）\n"
                "  2) 删除强杀残留的锁（否则 Chrome 启动即退，无报错无窗口）：\n"
                "     del /f /q \"%%LOCALAPPDATA%%\\Google\\ChromeCDP\\Default\\LOCK\"\n"
                "  3) 用【Chrome for Testing】启动（官方对自动化场景的推荐浏览器）：\n"
                "     \"%%LOCALAPPDATA%%\\Google\\ChromeForTesting\\chrome-win64\\chrome.exe\" "
                "--remote-debugging-port=%d "
                "--user-data-dir=\"%%LOCALAPPDATA%%\\Google\\ChromeCDP\"\n"
                "     或直接双击 scripts\\chrome_cdp_check.bat（会自动清 LOCK + 自检登录态）\n"
                "  4) 浏览器打开 http://127.0.0.1:%d/json/version 应返回一段 JSON\n"
                "  5) 若 /json/version 通了但页面提示「请重新登录」，说明微信后台会话\n"
                "     cookie 确实不在这个 profile 里，必须人工扫码一次。\n"
                "     ⚠ 排查细节见 scripts\\chrome_cdp_setup.md（区分进程被收走 vs Cookie 真丢）\n"
                "  原始错误：%s" % (port, port, port, e)
            )
        self.bws = WS(ver["webSocketDebuggerUrl"])
        self.ws = None
        self.tid = None          # 当前页面 target id，断线重连时要用
        self._id = 0
        self.debug = False

    def _http(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        with self._opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def list_targets(self):
        return self._http("/json")

    def send_browser(self, method, params=None):
        """走浏览器级 WebSocket（self.bws）发 CDP 命令，避开新版 Chrome
        已禁用的 HTTP /json/new 端点（GET/POST 都返回 405）。"""
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.bws.send_text(json.dumps(msg))
        while True:
            resp = json.loads(self.bws.recv_text())
            if resp.get("id") == self._id:
                return resp

    def new_target(self, url):
        # CDP 原生 Target.createTarget（HTTP /json/new 在新版 Chrome 被禁用）
        r = self.send_browser("Target.createTarget", {"url": url})
        if "error" in r:
            raise RuntimeError("Target.createTarget 失败: %s" % r["error"])
        return {"id": r["result"]["targetId"], "targetId": r["result"]["targetId"]}

    def connect_target(self, target_id):
        for _ in range(10):
            for t in self.list_targets():
                if t.get("id") == target_id and t.get("webSocketDebuggerUrl"):
                    self.ws = WS(t["webSocketDebuggerUrl"])
                    self.tid = target_id
                    self._main_context = self._enable_and_grab_context()
                    return True
            time.sleep(0.5)
        return False

    def _ws_readable(self, timeout=0.3):
        try:
            r, _, _ = select.select([self.ws.sock], [], [], timeout)
            return bool(r)
        except Exception:
            return False

    def _enable_and_grab_context(self, timeout=6):
        """发 Runtime.enable 并抓主帧默认执行上下文 id（SPA 导航会换上下文）。"""
        try:
            self.send("Runtime.enable")
        except Exception:
            pass
        end = time.time() + timeout
        best = None
        while time.time() < end:
            if not self._ws_readable(0.3):
                if best is not None:
                    break
                continue
            try:
                raw = self.ws.recv_text()
            except Exception:
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if ev.get("method") == "Runtime.executionContextCreated":
                p = ev.get("params", {})
                if p.get("auxData", {}).get("isDefault"):
                    best = p.get("id")
            elif ev.get("method") == "Runtime.executionContextsCleared":
                best = None
        return best

    def close_target(self, target_id):
        """只关掉某个具体 tab（不会影响用户其它窗口/登录态）。"""
        try:
            self.send_browser("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass

    def _fire(self, method, params=None):
        """只写命令、不读响应（避免 send 的读循环把事件帧吞掉）。"""
        self._id += 1
        self.ws.send_text(json.dumps(
            {"id": self._id, "method": method, "params": params or {}}))
        return self._id

    def collect_contexts(self, timeout=12):
        """采集当前 tab 的全部执行上下文（主帧 + 每个 iframe 各一个）。

        mp 编辑器的正文编辑区渲染在 iframe 内，必须钻进对应上下文才能拿到
        contenteditable 元素。返回 [{id, frameId, isDefault, name}]。

        关键坑1：导航后旧的 contextId 失效。
        关键坑2：send() 在等响应时会把无关帧（包括 executionContextCreated 事件）
        一并读掉并丢弃，若 Chrome 在 enable 响应「之前」就把事件推出来，就会被吞。
        → 这里用 _fire 只写 disable+enable（不读），然后在【同一个】读循环里收事件，
        不依赖 send 的任何读，确保事件无论先后顺序都能被抓到。
        """
        self._fire("Runtime.disable")
        self._fire("Runtime.enable")
        self._main_context = None
        ctxs = []
        seen = set()
        end = time.time() + timeout
        while time.time() < end:
            if not self._ws_readable(0.5):
                continue
            try:
                raw = self.ws.recv_text()
            except Exception:
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if "method" not in ev:
                continue  # 命令响应，忽略
            if ev["method"] == "Runtime.executionContextCreated":
                p = ev.get("params", {})
                cid = p.get("id")
                if cid in seen:
                    continue
                seen.add(cid)
                if p.get("auxData", {}).get("isDefault") and cid is not None:
                    self._main_context = cid
                ctxs.append({
                    "id": cid,
                    "frameId": p.get("auxData", {}).get("frameId"),
                    "isDefault": p.get("auxData", {}).get("isDefault", False),
                    "name": p.get("name") or "",
                    "type": p.get("auxData", {}).get("type", ""),
                })
            elif ev["method"] == "Runtime.executionContextsCleared":
                ctxs = []
                seen = set()
                self._main_context = None
        return ctxs

    def send(self, method, params=None):
        if self.ws is None:
            raise RuntimeError("未连接页面 target，先 new_target + connect_target")
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}

        def _roundtrip():
            self.ws.send_text(json.dumps(msg))
            if getattr(self, "debug", False):
                print("[debug send] → %s" % json.dumps(msg, ensure_ascii=False)[:300])
            while True:
                raw = self.ws.recv_text()
                if getattr(self, "debug", False):
                    print("[debug send] ← %s" % raw[:600])
                resp = json.loads(raw)
                if resp.get("id") == self._id:
                    return resp
                # 其余是事件，忽略

        try:
            return _roundtrip()
        except ConnectionError:
            # 页面 WebSocket 会**偶发**被对端断开。实测（2026-09-21）：长批次跑到
            # 第 13 篇时突然 "ws closed"，异常一路冒到 publish_one，整个 publish
            # 进程死掉，后面 16 篇全没发。重连同一 target 再试一次即可恢复。
            tid = getattr(self, "tid", None)
            if not tid or not self.connect_target(tid):
                raise
            print("  [warn] CDP 页面连接断开，已重连同一标签页")
            return _roundtrip()

    def click(self, x, y):
        """真实坐标点击（mousePressed + mouseReleased 一对）。

        为什么不用 JS 的 el.click()：React/Vue 按钮常常不绑合成事件，
        el.click() 返回 true 但页面纹丝不动（2026-09-30 在 B站「新的创作」
        按钮上实测过四种 JS 派发方式全部无效）。真实坐标事件走浏览器命中
        测试，才是我们要的「用户真的点了一下」。

        供 multiplatform/verify.py 的头条「加载更多」复用。
        """
        for ev in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent",
                      {"type": ev, "x": x, "y": y,
                       "button": "left", "clickCount": 1})
        return True

    def eval(self, expr, refresh_context=False, debug=False, context_id=None):
        if refresh_context or (context_id is None
                               and getattr(self, "_main_context", None) is None):
            self._main_context = self._enable_and_grab_context()
        params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
        ctx = context_id if context_id is not None else getattr(self, "_main_context", None)
        if ctx is not None:
            params["contextId"] = ctx
        if debug:
            print("[debug eval] contextId=%s" % ctx)
        last = None
        for _ in range(6):
            r = self.send("Runtime.evaluate", params)
            if debug:
                print("[debug eval] 原始响应=%s" % json.dumps(r, ensure_ascii=False)[:1000])
            if "error" in r:
                # 执行上下文被销毁/SPA 导航导致，刷新上下文后重试一次
                if r["error"].get("code") == -32000 and _ < 3:
                    self._main_context = self._enable_and_grab_context()
                    if self._main_context is not None:
                        params["contextId"] = self._main_context
                    time.sleep(1)
                    continue
                raise RuntimeError("CDP 协议错误: %s" % r["error"])
            exc_details = _js_exception_details(r)
            if exc_details:
                exc = exc_details.get("exception", {})
                raise RuntimeError("JS 执行异常: %s" %
                                   exc.get("description", exc_details))
            # CDP Runtime.evaluate 的返回结构是 result.result.value（两层 result）
            last = r.get("result", {}).get("result", {}).get("value")
            if last is not None:
                return last
            time.sleep(1)  # 页面/执行上下文偶尔未就绪，重试
        # 带显式 contextId（iframe）查询时不再兜底到主上下文（会查错帧）；
        # 只有用默认主上下文时，才最后不带 contextId 兜底试一次。
        if last is None and ctx is not None and context_id is None:
            r = self.send("Runtime.evaluate",
                          {"expression": expr, "returnByValue": True,
                           "awaitPromise": True})
            if debug:
                print("[debug eval] 兜底原始响应=%s" % json.dumps(r, ensure_ascii=False)[:1000])
            if "error" in r:
                raise RuntimeError("CDP 协议错误: %s" % r["error"])
            exc_details = _js_exception_details(r)
            if exc_details:
                exc = exc_details.get("exception", {})
                raise RuntimeError("JS 执行异常: %s" %
                                   exc.get("description", exc_details))
            last = r.get("result", {}).get("result", {}).get("value")
        return last

    def close_browser(self):
        try:
            self.bws.send_text(json.dumps(
                {"id": 9999, "method": "Browser.close", "params": {}}))
        except Exception:
            pass
