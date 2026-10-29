#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""independent_source 的单元测试（离线，不打网络）。

重点守 HN 通道的两条判据 —— 它们都是被实测打脸后才补上的：

1. **实体锚定**：第三方页面里必须出现产品官网域名。没有它，`kodo` 会命中
   日本熊野古道徒步游记（「Kumano Kodo」，104 分），`static-forms` 会命中一篇
   讲通用概念的观点文 —— 名字撞车，跟当年弃用 cn.bing 是同一个病。
2. **Show HN 跳过**：那是创始人自荐，本库里 founder 只能当方向参考。

顺带守 mentions_domain 的「抓不到 = 没证据」：拿不到页面时不能当作通过。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import independent_source as IS                              # noqa: E402


def hit(**over):
    h = {"objectID": "1", "title": "Some review", "url": "https://third.dev/post",
         "points": 12, "num_comments": 3, "created_at": "2026-05-01T00:00:00Z"}
    h.update(over)
    return h


class HnChannel(unittest.TestCase):
    def setUp(self):
        self.orig_jget = IS.jget
        self.orig_mentions = IS.mentions_domain
        IS.mentions_domain = lambda url, domain: True        # 默认锚定通过

    def tearDown(self):
        IS.jget = self.orig_jget
        IS.mentions_domain = self.orig_mentions

    def _feed(self, hits):
        IS.jget = lambda url, timeout=15: {"hits": hits}

    def test_命中第三方页面且锚定成立(self):
        self._feed([hit()])
        got = IS.probe_hn("Foo", "foo.dev")
        self.assertIsNotNone(got)
        self.assertEqual(got["url"], "https://third.dev/post")
        self.assertEqual(got["kind"], "press")

    def test_聚合站降级为aggregator(self):
        """聚合站也在转述别人，不能冒充独立报道。"""
        self._feed([hit(url="https://howtomakemi.com/cases/foo/")])
        self.assertEqual(IS.probe_hn("Foo", "foo.dev")["kind"], "aggregator")

    def test_命中熊野古道那类名同实异的页面要被锚定挡下(self):
        """这是 kodo 的实测翻车：标题里都有 kodo，但页面压根没提产品官网。"""
        self._feed([hit(title="Koya Bound – Eight Days on the Kumano Kodo",
                        url="http://walkkumano.com", points=104)])
        IS.mentions_domain = lambda url, domain: False
        self.assertIsNone(IS.probe_hn("Kodo", "usekodo.ai"))

    def test_Show_HN是创始人自荐_不算独立来源(self):
        self._feed([hit(title="Show HN: Foo – do things faster")])
        self.assertIsNone(IS.probe_hn("Foo", "foo.dev"))

    def test_自家域名不算第三方(self):
        self._feed([hit(url="https://foo.dev/blog/launch")])
        self.assertIsNone(IS.probe_hn("Foo", "foo.dev"))

    def test_trustmrr不算(self):
        self._feed([hit(url="https://trustmrr.com/startup/foo")])
        self.assertIsNone(IS.probe_hn("Foo", "foo.dev"))

    def test_社交与UGC站不算(self):
        for u in ("https://news.ycombinator.com/item?id=1",
                  "https://www.reddit.com/r/foo/comments/1",
                  "https://twitter.com/foo/status/1",
                  "https://www.youtube.com/watch?v=1"):
            self._feed([hit(url=u)])
            self.assertIsNone(IS.probe_hn("Foo", "foo.dev"), u)

    def test_第一个不合格时继续看后面的(self):
        self._feed([hit(url="https://foo.dev/self"), hit(url="https://ok.dev/p")])
        self.assertEqual(IS.probe_hn("Foo", "foo.dev")["url"], "https://ok.dev/p")

    def test_检索失败不炸(self):
        def boom(url, timeout=15):
            raise OSError("network down")
        IS.jget = boom
        self.assertIsNone(IS.probe_hn("Foo", "foo.dev"))

    def test_evidence里写清锚定到哪个域名(self):
        self._feed([hit()])
        self.assertIn("foo.dev", IS.probe_hn("Foo", "foo.dev")["evidence"])


class MentionsDomain(unittest.TestCase):
    def setUp(self):
        self.orig = IS.A.http_get

    def tearDown(self):
        IS.A.http_get = self.orig

    def test_页面里出现官网域名才算(self):
        IS.A.http_get = lambda url, timeout=15, proxy=None: "<a href='https://foo.dev'>x</a>"
        self.assertTrue(IS.mentions_domain("https://third.dev/p", "foo.dev"))

    def test_页面不含就不算(self):
        IS.A.http_get = lambda url, timeout=15, proxy=None: "<p>无关内容</p>"
        self.assertFalse(IS.mentions_domain("https://third.dev/p", "foo.dev"))

    def test_抓不到页面时不采信(self):
        """拿不到证据 = 没有证据，不能因为「标题看着像」就放行。"""
        def boom(url, timeout=15, proxy=None):
            raise OSError("timeout")
        IS.A.http_get = boom
        self.assertFalse(IS.mentions_domain("https://third.dev/p", "foo.dev"))

    def test_缺参数直接False(self):
        self.assertFalse(IS.mentions_domain("", "foo.dev"))
        self.assertFalse(IS.mentions_domain("https://third.dev/p", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
