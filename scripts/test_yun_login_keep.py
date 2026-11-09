#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`yun_login_keep.py` 离线回归（不起浏览器、不碰真 profile）。

钉住四件事：到期时间运行时算、只挑 .aliyun.com 的四个票据、
改写只加 expires 不动值、页面登录判据不被「已登录页含登录字样」误伤。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_login_keep as K  # noqa: E402


class FakeCDP:
    def __init__(self, body, href="https://developer.aliyun.com/"):
        self.body = body
        self.href = href

    def open(self, url):
        pass

    def eval(self, js):
        if "location.href" in js:
            return self.href
        return self.body


class Expiry(unittest.TestCase):
    def test_30_days_after_given_now(self):
        now = 1_700_000_000.0
        self.assertAlmostEqual(K.expiry(now) - now, 30 * 86400, places=0)

    def test_no_hardcoded_timestamp(self):
        src = inspect.getsource(K.expiry)
        self.assertIn("time.time()", src)


class SelectTickets(unittest.TestCase):
    def test_only_domain_and_whitelist(self):
        cookies = [
            {"name": "login_aliyunid_ticket", "domain": ".aliyun.com", "value": "a"},
            {"name": "login_aliyunid_ticket", "domain": "evil.com", "value": "b"},
            {"name": "cna", "domain": ".aliyun.com", "value": "c"},
            {"name": "hssid", "domain": "aliyun.com", "value": "d"},
            {"name": "cr_token", "domain": ".aliyun.com", "value": "e"},
        ]
        out = K.select_tickets(cookies)
        self.assertEqual(set(out), {"login_aliyunid_ticket", "hssid", "cr_token"})
        self.assertEqual(out["login_aliyunid_ticket"]["value"], "a")

    def test_four_tickets_are_the_frozen_set(self):
        self.assertEqual(K.TICKETS, ("login_aliyunid_ticket", "login_aliyunid",
                                     "hssid", "cr_token"))


class SetParams(unittest.TestCase):
    def setUp(self):
        self.c = {"name": "hssid", "value": "v-123", "domain": ".aliyun.com",
                  "path": "/", "secure": True, "httpOnly": True,
                  "sameSite": "Lax", "session": True, "expires": -1}

    def test_keeps_value_adds_expires(self):
        p = K.set_params(self.c, 1_796_712_791.0)
        self.assertEqual(p["value"], "v-123")
        self.assertEqual(p["domain"], ".aliyun.com")
        self.assertEqual(p["path"], "/")
        self.assertEqual(p["expires"], 1_796_712_791.0)
        self.assertTrue(p["secure"])
        self.assertTrue(p["httpOnly"])
        self.assertEqual(p["sameSite"], "Lax")

    def test_no_session_field(self):
        self.assertNotIn("session", K.set_params(self.c, 1.0))

    def test_bad_samesite_dropped(self):
        c = dict(self.c, sameSite="Unspecified")
        self.assertNotIn("sameSite", K.set_params(c, 1.0))

    def test_missing_optional_fields_default(self):
        p = K.set_params({"name": "cr_token", "value": "x"}, 2.0)
        self.assertEqual(p["domain"], ".aliyun.com")
        self.assertEqual(p["path"], "/")
        self.assertFalse(p["secure"])
        self.assertFalse(p["httpOnly"])


class TicketState(unittest.TestCase):
    def test_session_detected(self):
        cookies = [{"name": n, "domain": ".aliyun.com", "session": True,
                    "expires": -1} for n in K.TICKETS]
        st = K.ticket_state(cookies)
        self.assertEqual(len(st), 4)
        self.assertTrue(all(v[0] for v in st.values()))

    def test_persistent_detected(self):
        cookies = [{"name": n, "domain": ".aliyun.com", "session": False,
                    "expires": 1_796_712_791.0} for n in K.TICKETS]
        st = K.ticket_state(cookies)
        self.assertTrue(all(not v[0] and v[1] > 0 for v in st.values()))


class ProbePage(unittest.TestCase):
    def test_logged_in(self):
        p = K.probe_page(FakeCDP("控制台 开发者社区 粥粥海王星 发布 消息"), wait=0)
        self.assertTrue(p["logged"])

    def test_login_words_block(self):
        p = K.probe_page(FakeCDP("请登录 发布 消息"), wait=0)
        self.assertFalse(p["logged"])
        self.assertIn("请登录", p["marks"])

    def test_passport_url_blocks(self):
        p = K.probe_page(FakeCDP("发布 消息", "https://passport.aliyun.com/login"),
                         wait=0)
        self.assertFalse(p["logged"])


class DiskState(unittest.TestCase):
    def test_busy_is_degraded_not_empty(self):
        with mock.patch.object(K.shutil, "copy2", side_effect=OSError("locked")):
            with mock.patch.object(K.os.path, "exists", return_value=True):
                out = K.disk_state()
        self.assertIn("__error__", out)
        self.assertIn("BUSY", out["__error__"])


class SourceRedlines(unittest.TestCase):
    def test_does_not_touch_preferences_or_main_chain(self):
        with open(os.path.join(ROOT, "scripts", "yun_login_keep.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("restore_on_startup", src)
        self.assertNotIn("gen_platform_md", src)
        self.assertNotIn("publish_one", src.replace("publish_one.py", ""))
        self.assertIn("Network.setCookie", src)
        self.assertIn("Browser.close", src)


if __name__ == "__main__":
    unittest.main()
