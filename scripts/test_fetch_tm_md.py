#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_tm_md 的缓存判据与落盘（2026-10-21 修）。

修的洞：缓存只看「文件存在且 > 200 字节」—— 抓取被掐断留下的**半截档案**
照样算有效缓存，摘要里一半字段是 None，写正文的模型就在空素材上发挥。
现在判据是「长度 + 官方页脚」，写盘走原子替换，残片还会被重新抓一遍。

这里不联外网：`fetch` 全部打桩，只验判据与落盘行为。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_tm_md as F  # noqa: E402

SAMPLE = """# Demo

## Profile

- Name: Demo
- Slug: `demo`
- Verified payment provider API source: Stripe (API key)

## Revenue

| Period | Revenue |
|---|---|
| Last 30 days | $1,200 |
| All time | $9,000 |

- Current MRR: $1,200

## Startup Insights

""" + "\n".join("- Insight %d: 这是一行用来把档案撑到真实长度的说明文字。" % i
                for i in range(60)) + """

## Related TrustMRR Pages

- [Acquire startups](https://trustmrr.com/acquire)
- [llms.txt](https://trustmrr.com/llms.txt)

"""

# 抓取被掐断的典型形态：正文还在、页脚没了（长度早已超过旧的 200 字节判据）
FRAGMENT = SAMPLE[:1500]


class TestIsComplete(unittest.TestCase):
    def test_完整档案认(self):
        self.assertTrue(F.is_complete(SAMPLE))

    def test_截断档案不认(self):
        # 砍掉后半（页脚没了）：旧判据「> 200 字节」照样放行
        cut = FRAGMENT
        self.assertGreater(len(cut), 200)
        self.assertFalse(F.is_complete(cut))

    def test_页脚不在文末窗口不算(self):
        md = F.FOOTER_MARK + "\n" + ("填充内容\n" * 600)
        self.assertFalse(F.is_complete(md))

    def test_太短不认(self):
        self.assertFalse(F.is_complete(F.FOOTER_MARK))

    def test_空与None不认(self):
        self.assertFalse(F.is_complete(""))
        self.assertFalse(F.is_complete(None))


class TestLoadCached(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "demo.md")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, text):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write(text)

    def test_完整缓存读得到(self):
        self._write(SAMPLE)
        self.assertEqual(F.load_cached(self.p), SAMPLE)

    def test_残片当没缓存(self):
        self._write(FRAGMENT)
        self.assertIsNone(F.load_cached(self.p), "残片必须重新抓，不许当有效缓存")

    def test_文件不存在返回None(self):
        self.assertIsNone(F.load_cached(os.path.join(self.d, "nope.md")))


class TestMainCacheFlow(unittest.TestCase):
    """main() 的增量逻辑：好缓存跳过、坏缓存重抓、抓不到不落盘。"""

    CANDS = [{"id": "demo", "trustmrr_slug": "demo"},
             {"id": "fresh", "trustmrr_slug": "fresh"}]

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.data = os.path.join(self.d, "data")
        os.makedirs(self.data)
        self._dump("candidates.json", self.CANDS)
        self._dump("cases.json", [])
        self.out = os.path.join(self.d, "tmp", "tm_md")
        self._old = (F.ROOT, F.OUT)
        F.ROOT, F.OUT = self.d, self.out
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        F.ROOT, F.OUT = self._old
        shutil.rmtree(self.d, ignore_errors=True)

    def _dump(self, name, obj):
        with open(os.path.join(self.data, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def _run(self, fetch):
        with mock.patch.object(F, "fetch", fetch), \
                mock.patch.object(F.time, "sleep", lambda s: None):
            F.main()

    def _summary(self):
        with open(os.path.join(self.out, "summary.json"), encoding="utf-8") as f:
            return json.load(f)

    def _cache(self, slug):
        with open(os.path.join(self.out, slug + ".md"), encoding="utf-8") as f:
            return f.read()

    def test_残片缓存被重抓并原子覆盖(self):
        with open(os.path.join(self.out, "demo.md"), "w", encoding="utf-8") as f:
            f.write(FRAGMENT)                          # 上次被打断留下的残片
        calls = []

        def fake_fetch(slug):
            calls.append(slug)
            return SAMPLE, ""
        self._run(fake_fetch)
        self.assertEqual(sorted(calls), ["demo", "fresh"], "两条都得抓（残片不算缓存）")
        self.assertEqual(self._cache("demo"), SAMPLE)
        self.assertEqual(self._cache("fresh"), SAMPLE)
        self.assertEqual(self._summary()["demo"]["rev_30d"], 1200.0)
        self.assertEqual(sorted(os.listdir(self.out)),
                         ["demo.md", "fresh.md", "summary.json"],
                         "不许留下 .tmp 残渣")

    def test_好缓存不重抓(self):
        with open(os.path.join(self.out, "demo.md"), "w", encoding="utf-8") as f:
            f.write(SAMPLE)
        calls = []

        def fake_fetch(slug):
            calls.append(slug)
            return SAMPLE, ""
        self._run(fake_fetch)
        self.assertEqual(calls, ["fresh"])
        self.assertIn("demo", self._summary())

    def test_抓不到不写缓存且进失败清单(self):
        with open(os.path.join(self.out, "demo.md"), "w", encoding="utf-8") as f:
            f.write(FRAGMENT)

        def fake_fetch(slug):
            return None, "HTTPError: 404"
        self._run(fake_fetch)
        with open(os.path.join(self.out, "demo.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), FRAGMENT, "抓取失败不许动已有文件")
        self.assertFalse(os.path.exists(os.path.join(self.out, "fresh.md")))
        with open(os.path.join(self.out, "failed.txt"), encoding="utf-8") as f:
            self.assertEqual(sorted(f.read().split()), ["demo", "fresh"])
        self.assertEqual(self._summary(), {})


if __name__ == "__main__":
    unittest.main()
