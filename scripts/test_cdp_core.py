#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cdp_core.py 纯协议回归锁（不联网、不连 Chrome、不碰 data）。

WS/CDP 从 wechat_publish.py 搬出前，这部分协议逻辑只能靠真机验证。
这里用假 socket / 假 WS 锁住最容易出错的几处：
  · WS 帧收发：126/127 长度扩展、分片帧、客户端掩码、断连；
  · CDP.send/eval：消息编号递增、事件帧过滤、result 两层解包；
  · new_target / click / close_browser / connect_target 的消息形状；
  · re-export：wp.CDP 必须与 cdp_core.CDP 是同一个类（52 个调用方零改动的依据）。
"""
import json
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cdp_core as C  # noqa: E402


# ---- 测试替身 --------------------------------------------------------

class FakeSock:
    """只实现 WS 用到的 recv / sendall / close。"""

    def __init__(self, incoming=b""):
        self.incoming = incoming
        self.pos = 0
        self.sent = []
        self.closed = False

    def recv(self, n):
        chunk = self.incoming[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def sendall(self, data):
        self.sent.append(bytes(data))

    def close(self):
        self.closed = True


class FakeWS:
    """只实现 CDP 用到的 send_text / recv_text。"""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)

    def recv_text(self):
        if not self.replies:
            raise AssertionError("FakeWS recv 队列空了")
        return self.replies.pop(0)


def _frame(payload, opcode=0x1, fin=True):
    """构造服务端 -> 客户端的 WebSocket 帧（服务端帧按协议不掩码）。"""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    b0 = (0x80 if fin else 0) | opcode
    if n < 126:
        head = bytes([b0, n])
    elif n < 65536:
        head = bytes([b0, 126]) + struct.pack(">H", n)
    else:
        head = bytes([b0, 127]) + struct.pack(">Q", n)
    return head + payload


def _exc_reply(msg_id, description):
    """真实形状的 Runtime.evaluate 响应：exceptionDetails 嵌在 result 里。"""
    return json.dumps({
        "id": msg_id,
        "result": {
            "result": {"type": "undefined"},
            "exceptionDetails": {
                "text": "Uncaught",
                "exception": {"description": description},
            },
        },
    })


# ---- WS 帧收发 --------------------------------------------------------

class TestWSFrames(unittest.TestCase):
    def _ws(self, incoming=b""):
        ws = C.WS.__new__(C.WS)      # 跳过 __init__，不建真 socket
        ws.sock = FakeSock(incoming)
        ws._buf = b""
        return ws

    def test_recv_simple_text(self):
        ws = self._ws(_frame('{"id":1,"ok":true}'))
        self.assertEqual(ws.recv_text(), '{"id":1,"ok":true}')

    def test_recv_126_length(self):
        text = "x" * 200              # 200 >= 126 -> 走 2 字节长度扩展
        self.assertEqual(self._ws(_frame(text)).recv_text(), text)

    def test_recv_127_length(self):
        text = "长" * 70000           # > 65535 -> 走 8 字节长度扩展
        self.assertEqual(self._ws(_frame(text)).recv_text(), text)

    def test_recv_fragmented(self):
        raw = _frame("hello", opcode=0x1, fin=False) + _frame(" world", opcode=0x0)
        self.assertEqual(self._ws(raw).recv_text(), "hello world")

    def test_recv_empty_raises_connection_error(self):
        with self.assertRaises(ConnectionError):
            self._ws().recv_text()

    def test_send_text_is_masked_and_decodable(self):
        ws = self._ws()
        ws.send_text("中文 hello")
        raw = ws.sock.sent[0]
        self.assertEqual(raw[0], 0x81)          # FIN + text opcode
        self.assertTrue(raw[1] & 0x80)          # 客户端帧必须掩码
        mask = raw[2:6]
        body = raw[6:]
        decoded = bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        self.assertEqual(decoded.decode("utf-8"), "中文 hello")

    def test_close(self):
        ws = self._ws()
        ws.close()
        self.assertTrue(ws.sock.closed)


# ---- CDP 协议 ---------------------------------------------------------

class TestCDPProtocol(unittest.TestCase):
    def _cdp(self, replies=None):
        cdp = C.CDP.__new__(C.CDP)   # 跳过 __init__，不连 Chrome
        cdp.ws = FakeWS(replies)
        cdp.bws = FakeWS()
        cdp.port = 9222
        cdp._id = 0
        cdp.debug = False
        cdp.tid = "T1"
        cdp._main_context = 7
        return cdp

    def test_send_increments_id_and_filters_events(self):
        replies = [
            json.dumps({"method": "Runtime.consoleAPICalled", "params": {}}),
            json.dumps({"id": 1, "result": {"ok": True}}),
        ]
        cdp = self._cdp(replies)
        self.assertEqual(cdp.send("Runtime.enable"), {"id": 1, "result": {"ok": True}})
        self.assertEqual(json.loads(cdp.ws.sent[0]),
                         {"id": 1, "method": "Runtime.enable", "params": {}})
        cdp.ws.replies.append(json.dumps({"id": 2, "result": {}}))
        cdp.send("Page.enable")
        self.assertEqual(json.loads(cdp.ws.sent[1])["id"], 2)

    def test_send_without_target_raises(self):
        cdp = self._cdp()
        cdp.ws = None
        with self.assertRaises(RuntimeError):
            cdp.send("Runtime.evaluate")

    def test_eval_unwraps_nested_result(self):
        cdp = self._cdp()
        cdp.ws.replies.append(json.dumps(
            {"id": 1, "result": {"result": {"value": 42}}}))
        self.assertEqual(cdp.eval("1+1"), 42)
        msg = json.loads(cdp.ws.sent[0])
        self.assertEqual(msg["method"], "Runtime.evaluate")
        self.assertEqual(msg["params"]["contextId"], 7)
        self.assertTrue(msg["params"]["returnByValue"])

    def test_eval_uses_explicit_context_id(self):
        cdp = self._cdp()
        cdp.ws.replies.append(json.dumps(
            {"id": 1, "result": {"result": {"value": "iframe"}}}))
        self.assertEqual(cdp.eval("document.title", context_id=3), "iframe")
        self.assertEqual(json.loads(cdp.ws.sent[0])["params"]["contextId"], 3)

    def test_eval_raises_on_protocol_error(self):
        cdp = self._cdp()
        cdp.ws.replies.append(json.dumps(
            {"id": 1, "error": {"code": -32601, "message": "nope"}}))
        with self.assertRaises(RuntimeError) as ctx:
            cdp.eval("x")
        self.assertIn("CDP 协议错误", str(ctx.exception))

    def test_eval_raises_on_js_exception_in_result(self):
        """真实 CDP：exceptionDetails 藏在 result 里，不在响应顶层。"""
        # send() 按 id 匹配响应，故每条回复的 id 必须与递增的 _id 对齐
        cdp = self._cdp([_exc_reply(i, "ReferenceError: boom is not defined")
                         for i in range(1, 9)])
        with mock.patch.object(C.time, "sleep"):  # 别为重试等 6 秒
            with self.assertRaises(RuntimeError) as ctx:
                cdp.eval("boom")
        self.assertIn("JS 执行异常", str(ctx.exception))
        self.assertIn("ReferenceError: boom", str(ctx.exception))

    def test_eval_fallback_also_raises_on_js_exception(self):
        """兜底分支（不带 contextId 重试）同样要抓 result 里的异常详情。"""
        empty = [json.dumps({"id": i, "result": {"result": {"type": "undefined"}}})
                 for i in range(1, 7)]
        cdp = self._cdp(empty + [_exc_reply(7, "TypeError: bad")])
        with mock.patch.object(C.time, "sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                cdp.eval("bad")
        self.assertIn("JS 执行异常: TypeError: bad", str(ctx.exception))

    def test_list_targets_delegates_http(self):
        cdp = self._cdp()
        seen = []
        cdp._http = lambda p: seen.append(p) or [{"id": "T1"}]
        self.assertEqual(cdp.list_targets(), [{"id": "T1"}])
        self.assertEqual(seen, ["/json"])

    def test_new_target_shape_and_error(self):
        cdp = self._cdp()
        cdp.send_browser = lambda m, p=None: {"result": {"targetId": "NEW"}}
        self.assertEqual(cdp.new_target("about:blank"),
                         {"id": "NEW", "targetId": "NEW"})
        cdp.send_browser = lambda m, p=None: {"error": {"message": "denied"}}
        with self.assertRaises(RuntimeError):
            cdp.new_target("about:blank")

    def test_send_browser_uses_browser_ws(self):
        cdp = self._cdp()
        cdp.bws = FakeWS([json.dumps({"id": 1, "result": {"ok": 1}})])
        out = cdp.send_browser("Target.createTarget", {"url": "u"})
        self.assertEqual(out["result"], {"ok": 1})
        self.assertEqual(json.loads(cdp.bws.sent[0])["method"], "Target.createTarget")

    def test_click_dispatches_two_mouse_events(self):
        cdp = self._cdp()
        calls = []
        cdp.send = lambda m, p=None: calls.append((m, p))
        self.assertTrue(cdp.click(10, 20))
        self.assertEqual([m for m, _ in calls], ["Input.dispatchMouseEvent"] * 2)
        self.assertEqual([p["type"] for _, p in calls],
                         ["mousePressed", "mouseReleased"])
        self.assertEqual(calls[0][1]["x"], 10)

    def test_close_browser_uses_fixed_id(self):
        cdp = self._cdp()
        cdp.bws = FakeWS()
        cdp.close_browser()
        msg = json.loads(cdp.bws.sent[0])
        self.assertEqual(msg["method"], "Browser.close")
        self.assertEqual(msg["id"], 9999)

    def test_connect_target_sets_tid_and_context(self):
        cdp = self._cdp()
        cdp.list_targets = lambda: [{"id": "T1", "webSocketDebuggerUrl": "ws://x/y"}]
        made = []

        def fake_ws(url):
            made.append(url)
            return FakeWS()

        old_ws = C.WS
        C.WS = fake_ws
        cdp._enable_and_grab_context = lambda: 99
        try:
            self.assertTrue(cdp.connect_target("T1"))
        finally:
            C.WS = old_ws
        self.assertEqual(cdp.tid, "T1")
        self.assertEqual(cdp._main_context, 99)
        self.assertEqual(made, ["ws://x/y"])


# ---- re-export 兼容性（52 个调用方零改动的依据）------------------------

class TestReExport(unittest.TestCase):
    def test_wechat_publish_reexports_same_classes(self):
        import wechat_publish as wp
        self.assertIs(wp.CDP, C.CDP)
        self.assertIs(wp.WS, C.WS)
        self.assertEqual(wp.CDP.__module__, "cdp_core")


if __name__ == "__main__":
    unittest.main()
