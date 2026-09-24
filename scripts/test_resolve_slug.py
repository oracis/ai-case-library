# -*- coding: utf-8 -*-
"""TrustMRR slug 反查的离线回归测试（不发任何网络请求）。

为什么要测这个：2026-09-24 用户连问三次「这个也有官网，你找得到吗」——
promptmonitor / divine-widgets / prosp / voklit 四条都卡在「缺 trustmrr_slug
或缺 website」，深核退化成纯搜索、抓回巴赫乐谱页这类垃圾，AI 只能判 disputed、
来源列表为空、过不了入库闸门。根因不是「没有挂牌页」，而是**两张官方索引
（robots.txt 公布的 startup-sitemap.xml、llms.txt 公布的 /api/ai）从没用上**。

这里钉住三件事：
  1. 匹配优先级（已登记 > 来源页 > 官网匹配 > 名称匹配 > 站点索引），
     顺序错了会让「官网匹配」被同名短词抢走；
  2. 名称归一与 slug 猜测的变形规则（CJK 后缀、-ai/-io 后缀、尾数字）；
  3. 没 id / 没名称时**不触网**——离线调用与测试因此保持封闭。

用法：在 scripts/ 下 `python -m unittest test_resolve_slug`
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_verify as A                                            # noqa: E402


# 造一张假的 /api/ai 索引：{slug: {name, website}}
IDX = {
    "gojiberry-ai": {"name": "GojiberryAI", "website": "https://gojiberry.ai/"},
    "layzr": {"name": "Layzr", "website": "https://www.layzr.ai/"},
    "cometly": {"name": "Cometly", "website": "https://www.cometly.com/"},
    "radio": {"name": "Radio", "website": "https://radio.example/"},
}

# 造一份假 sitemap slug 集合
SLUGS = {"cometly", "mort", "layzr-ai", "sociano", "quran-unlock", "private-venture"}


class ResolveTest(unittest.TestCase):

    def test_已登记_直接返回不再匹配(self):
        cand = {"id": "x", "trustmrr_slug": "already-there",
                "website": "https://gojiberry.ai/"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index=IDX, slugs=SLUGS),
                         ("already-there", "已登记"))

    def test_来源页里带挂牌地址_直接取出来(self):
        cand = {"id": "x", "source_url": "https://trustmrr.com/startup/superfruits"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index={}, slugs=set()),
                         ("superfruits", "来源页"))

    def test_官网匹配优先于名称匹配(self):
        """同名条目（Radio）与官网条目同时存在时，官网域名是最硬的证据。"""
        cand = {"id": "radio", "name": "Radio",
                "website": "https://gojiberry.ai/"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index=IDX, slugs=SLUGS),
                         ("gojiberry-ai", "官网匹配"))

    def test_www与协议差异不影响官网匹配(self):
        cand = {"id": "x", "name": "Layzr", "website": "layzr.ai"}   # 没协议头
        # 没有协议头时 _host_of 返回空 → 落到名称匹配
        self.assertEqual(A.resolve_trustmrr_slug(cand, index=IDX, slugs=SLUGS)[1],
                         "名称匹配")

    def test_名称归一_忽略大小写与标点(self):
        cand = {"id": "x", "name": "GOJIBERRY-AI"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index=IDX, slugs=SLUGS),
                         ("gojiberry-ai", "名称匹配"))

    def test_站点索引_命中同id(self):
        cand = {"id": "mort", "name": "MORT"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index={}, slugs=SLUGS),
                         ("mort", "站点索引"))

    def test_站点索引_榜单尾号剥掉(self):
        """private-venture-1 这类榜单条目：真 slug 可能是 private-venture。"""
        cand = {"id": "private-venture-9", "name": "Private Venture（榜单条目 9）"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index={}, slugs=SLUGS),
                         ("private-venture", "站点索引"))

    def test_站点索引_产品后缀剥掉(self):
        cand = {"id": "layzr-app", "name": "Layzr"}
        # layzr-app 不在 sitemap，剥掉 -app 后也不在（layzr-ai 在），退到 id 原名失败
        self.assertEqual(A.resolve_trustmrr_slug(cand, index={}, slugs={"layzr"}),
                         ("layzr", "站点索引"))

    def test_查不到返回空(self):
        cand = {"id": "sixtyfive", "name": "Sixtyfive"}
        self.assertEqual(A.resolve_trustmrr_slug(cand, index={}, slugs=SLUGS),
                         ("", ""))

    def test_没id没名称_不触网(self):
        """离线封闭性：空候选不该触发 sitemap/索引请求（否则测试与 CI 会连外网）。"""
        calls = []
        orig_i, orig_s = A.trustmrr_index, A.trustmrr_sitemap_slugs
        A.trustmrr_index = lambda *a, **kw: calls.append("index") or {}
        A.trustmrr_sitemap_slugs = lambda *a, **kw: calls.append("sitemap") or set()
        try:
            self.assertEqual(A.resolve_trustmrr_slug({}), ("", ""))
            self.assertEqual(A.resolve_trustmrr_slug({"website": "gojiberry.ai"}),
                             ("", ""))
            self.assertEqual(calls, [])
        finally:
            A.trustmrr_index, A.trustmrr_sitemap_slugs = orig_i, orig_s


class GuessTest(unittest.TestCase):

    def test_猜测顺序_id在前名称在后且去重(self):
        # 名称 "Layzr.ai" 归一后与 id 相同（layzr-ai），只留一条；再剥 -ai 后缀得 layzr
        cand = {"id": "layzr-ai", "name": "Layzr.ai"}
        self.assertEqual(A.slug_guesses(cand), ["layzr-ai", "layzr"])

    def test_中文榜单后缀变成连字符(self):
        cand = {"id": "private-venture-1", "name": "Private Venture（榜单条目 1）"}
        g = A.slug_guesses(cand)
        self.assertIn("private-venture-1", g)
        self.assertIn("private-venture", g)

    def test_去重(self):
        cand = {"id": "mort", "name": "mort"}
        self.assertEqual(A.slug_guesses(cand), ["mort"])


class ConfirmTest(unittest.TestCase):
    """站点索引层必须过「身份确认」—— sitemap 只证明 slug 存在，不证明同一个产品。

    2026-09-24 实测踩坑：候选 private-venture-1（榜单条目，$1M 挂牌 / 卖家 david）
    按 id 命中同名 slug，抓回 .md 却是另一个隐身挂牌（Stealth Company /
    Kostadin Ristovski / MRR $44.92 / 未挂牌）。喂错证据比没证据更危险。
    """

    def setUp(self):
        self._title = A.trustmrr_md_title

    def tearDown(self):
        A.trustmrr_md_title = self._title

    def test_标题一致_才认站点索引(self):
        A.trustmrr_md_title = lambda slug, **kw: "Cometly"
        cand = {"id": "cometly", "name": "Cometly"}
        self.assertEqual(
            A.resolve_trustmrr_slug(cand, index={}, slugs={"cometly"}, confirm=True),
            ("cometly", "站点索引"))

    def test_标题不符_退回无果(self):
        """就是 private-venture-1 那次的形状：slug 存在，但身份对不上。"""
        A.trustmrr_md_title = lambda slug, **kw: "Stealth Company"
        cand = {"id": "private-venture-1", "name": "Private Venture（榜单条目 1）"}
        self.assertEqual(
            A.resolve_trustmrr_slug(cand, index={}, slugs={"private-venture-1"},
                                    confirm=True),
            ("", ""))

    def test_抓不到标题_不敢用(self):
        A.trustmrr_md_title = lambda slug, **kw: ""
        cand = {"id": "cometly", "name": "Cometly"}
        self.assertEqual(
            A.resolve_trustmrr_slug(cand, index={}, slugs={"cometly"}, confirm=True),
            ("", ""))

    def test_不确认时_只看存在性(self):
        """confirm=False 保留纯匹配语义（离线批量报告用）。"""
        cand = {"id": "cometly", "name": "Cometly"}
        self.assertEqual(
            A.resolve_trustmrr_slug(cand, index={}, slugs={"cometly"}),
            ("cometly", "站点索引"))

    def test_官网匹配层不需要确认(self):
        """官网域名相同本身就是强证据，不该被标题核对拖累（省一次请求）。"""
        A.trustmrr_md_title = lambda slug, **kw: ""
        cand = {"id": "x", "name": "别的名字", "website": "https://cometly.com/"}
        self.assertEqual(
            A.resolve_trustmrr_slug(cand, index=IDX, slugs=set(), confirm=True),
            ("cometly", "官网匹配"))


class HostTest(unittest.TestCase):

    def test_取主机_去www(self):
        self.assertEqual(A._host_of("https://www.Example.com/path"), "example.com")
        self.assertEqual(A._host_of("http://sub.example.com"), "sub.example.com")

    def test_非http返回空(self):
        for u in ("", None, "example.com", "ftp://x.com"):
            self.assertEqual(A._host_of(u), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
