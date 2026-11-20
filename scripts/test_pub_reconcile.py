#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`pub_reconcile.py` 的纯函数回归锁（不联网、不碰 db）。

锁的都是 2026-11-20 对账时踩过的判据坑：
  · 博客园早期两行把 url 写进了 `title`，id 只能从地址里抠；
  · 标题为空的记录不能当成「线上没有」；
  · 已登记且在线时，线上再出现同标题 = 重复稿，**不许覆盖**；
  · 台账记 draft 而线上已发 = 重复发布隐患，必须改 published。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pub_reconcile as P  # noqa: E402


def _row(cid, state, rid="", title="", url="", draft=""):
    return {"case_id": cid, "state": state, "remote_id": rid, "title": title,
            "url": url, "draft_id": draft, "note": "", "published_at": ""}


class TestIdFromUrl(unittest.TestCase):
    def test_cnblogs_url(self):
        self.assertEqual(
            P.id_from_url("https://www.cnblogs.com/thing-explainer/p/23511986"),
            "23511986")

    def test_bare_address_written_into_title(self):
        # 博客园 chessigma / cod-dex 两行就是这样：title 里躺着裸地址
        self.assertEqual(
            P.id_from_url("cnblogs.com/thing-explainer/p/23511987"), "23511987")

    def test_csdn_details(self):
        self.assertEqual(
            P.id_from_url("https://blog.csdn.net/sinat_32396037/article/details/170136834"),
            "170136834")

    def test_empty(self):
        self.assertEqual(P.id_from_url(""), "")


class TestCleanPageTitle(unittest.TestCase):
    def test_strip_aliyun(self):
        self.assertEqual(
            P._clean_page_title("TrustMRR：把「可核验」做成产品的海外小站-阿里云开发者社区"),
            "TrustMRR：把「可核验」做成产品的海外小站")

    def test_strip_tencent(self):
        self.assertEqual(
            P._clean_page_title("NetHack，能玩死你的地牢模拟器-腾讯云开发者社区-腾讯云"),
            "NetHack，能玩死你的地牢模拟器")


class TestPlan(unittest.TestCase):
    def test_noid_filled_by_title(self):
        recs = {"a": _row("a", "published", "", "古代文明 RTS：读 0 A.D. 的资源与时代")}
        remote = {"171036622": {"title": "古代文明 RTS：读 0 A.D. 的资源与时代",
                                "at": "2026-11-19 12:40:58"}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["noid"], [("a", "171036622",
                                      "古代文明 RTS：读 0 A.D. 的资源与时代")])

    def test_noid_from_url_when_title_is_address(self):
        recs = {"a": _row("a", "published", "", "cnblogs.com/x/p/23511986", "")}
        remote = {"23511986": {"title": "100 万用户只转化 576 个付费", "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["noid"][0][1], "23511986")

    def test_stale_remapped(self):
        recs = {"a": _row("a", "published", "169654106", "TrustMRR：把「可核验」做成产品的海外小站")}
        remote = {"170000001": {"title": "TrustMRR：把「可核验」做成产品的海外小站",
                                "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["stale"], [("a", "169654106", "170000001",
                                       "TrustMRR：把「可核验」做成产品的海外小站")])

    def test_promote_when_ledger_says_draft(self):
        # 台账记 draft、线上已发表 —— 不改它，FIFO 会再发一遍
        recs = {"a": _row("a", "draft", "170000009", "Quake，1999 年开源的雷神之锤")}
        remote = {"170239393": {"title": "Quake，1999 年开源的雷神之锤", "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(len(b["promote"]), 1)
        self.assertEqual(b["promote"][0][2], "170239393")

    def test_orphan_inserted_for_known_case(self):
        recs = {}                                        # 本平台一条都没记
        remote = {"23468183": {"title": "一个被 YC 拒绝的邮件工具", "at": "2026-10-29 12:15:00"}}
        corpus = {"flodesk": ["一个被 YC 拒绝的邮件工具", "Flodesk：不限发送量的邮件平台"]}
        b = P.plan(recs, remote, {"flodesk"}, corpus)
        self.assertEqual(b["orphan"], [("flodesk", "23468183", "一个被 YC 拒绝的邮件工具")])

    def test_orphan_skipped_when_case_missing(self):
        # case 不在 cases 表 ⇒ 补登会造 stub，只报告
        remote = {"999999": {"title": "用免配置多应用连接架构撑住226个活跃订阅", "at": ""}}
        corpus = {"ghost-case": ["用免配置多应用连接架构撑住226个活跃订阅"]}
        b = P.plan({}, remote, set(), corpus)                 # cases 为空
        self.assertEqual(b["orphan"], [])
        self.assertEqual(b["unclaimed"][0][0], "ORPHAN")

    def test_duplicate_publication_not_overwritten(self):
        # 已登记在线 id，线上又出现同标题 ⇒ 重复稿，交人，不许覆盖
        recs = {"a": _row("a", "published", "23559261", "Next.js SaaS 脚手架拆解")}
        remote = {"23559261": {"title": "Next.js SaaS 脚手架拆解", "at": ""},
                  "23493644": {"title": "Next.js SaaS 脚手架拆解", "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["orphan"], [])
        self.assertEqual(b["promote"], [])
        self.assertEqual(b["unclaimed"][0][0], "DUP_PUB")

    def test_title_fix_when_local_title_empty(self):
        recs = {"a": _row("a", "published", "23507950", "")}
        remote = {"23507950": {"title": "TrustMRR：把「可核验」做成产品的海外小站", "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["title_fix"],
                         [("a", "23507950", "TrustMRR：把「可核验」做成产品的海外小站")])

    def test_empty_title_is_not_stale_evidence(self):
        # 标题为空 + id 线上没有 ⇒ 只能说「不知道」，不能认领别人的文章
        recs = {"a": _row("a", "published", "169654106", "")}
        remote = {"170000001": {"title": "另一个案例的文章", "at": ""}}
        b = P.plan(recs, remote, {"a"})
        self.assertEqual(b["stale"], [])
        self.assertEqual(b["unclaimed"][0][0], "STALE")


class TestDupIds(unittest.TestCase):
    def test_same_draft_id_two_cases(self):
        recs = {"a": _row("a", "published", "1783930", "x", draft="1699450"),
                "b": _row("b", "draft", "", "y", draft="1699450")}
        dups = P.dup_ids(recs)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0][0], "draft_id")
        self.assertEqual(dups[0][1], "1699450")


if __name__ == "__main__":
    unittest.main(verbosity=2)
