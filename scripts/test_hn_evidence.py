#!/usr/bin/env python
"""HN 证据通道（PRD #166）的离线断言：不联网、不碰正式台账。"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import hn_evidence as HE  # noqa: E402


def lead(cid="hn-10000001", points=120):
    return {
        "id": cid, "name": "Demo", "name_en": "Demo", "origin": "",
        "one_liner": "一个演示产品", "category": "未分类", "models": [],
        "source_url": "https://example.com", "website": "https://example.com",
        "harvest_source": "hn", "source_kind": "self_reported",
        "hn_id": cid.replace("hn-", ""), "hn_role": "lead",
        "hn_title": "Show HN: Demo", "hn_points": points, "hn_comments": 10,
        "hn_created_at": "2026-11-01", "hn_discussion": "https://news.ycombinator.com/item?id=1",
        "hn_flags": [], "hidden_urls": {},
        "metrics": {"headline": "未获取"},
    }


def item(**kw):
    base = {
        "evidence_url": "https://example.com/pricing", "source_kind": "homepage",
        "verification": "founder", "revenue": None, "caliber": "undisclosed",
        "text_path": "", "checked_at": "2026-11-13", "checked_by": "human",
        "note": "",
    }
    base.update(kw)
    return base


class TestDerive(unittest.TestCase):
    def test_homepage_无收入_落founder(self):
        self.assertEqual(HE.derive_verification("homepage", False), "founder")

    def test_official_有收入_落official(self):
        self.assertEqual(HE.derive_verification("official", True), "official")

    def test_official_无收入降founder(self):
        # 官方页没写数字时，它只能证产品/定价存在，不能给「收入已核实」背书。
        self.assertEqual(HE.derive_verification("official", False), "founder")

    def test_二手转述撑不起任何等级(self):
        self.assertIsNone(HE.derive_verification("secondary", True))

    def test_创始人自述(self):
        self.assertEqual(HE.derive_verification("founder", False), "founder")


class TestValidate(unittest.TestCase):
    def test_合法无收入(self):
        self.assertEqual(HE.validate(item(), lead()), [])

    def test_缺链接(self):
        errs = HE.validate(item(evidence_url=""), lead())
        self.assertTrue(any("evidence_url" in e for e in errs), errs)

    def test_链接不是http(self):
        errs = HE.validate(item(evidence_url="ftp://x"), lead())
        self.assertTrue(any("http(s)" in e for e in errs), errs)

    def test_来源类型未登记(self):
        errs = HE.validate(item(source_kind="twitter"), lead())
        self.assertTrue(any("source_kind" in e for e in errs), errs)

    def test_分数不足(self):
        errs = HE.validate(item(), lead(points=30))
        self.assertTrue(any("社区验证不足" in e for e in errs), errs)

    def test_有金额缺时点(self):
        it = item(revenue={"value": 100, "caliber": "mrr", "as_of": ""},
                  caliber="mrr", verification="official", source_kind="official")
        errs = HE.validate(it, lead())
        self.assertTrue(any("as_of" in e for e in errs), errs)

    def test_有金额却填undisclosed口径(self):
        it = item(revenue={"value": 100, "caliber": "undisclosed", "as_of": "2026-11-13"},
                  caliber="undisclosed", verification="official", source_kind="official")
        errs = HE.validate(it, lead())
        self.assertTrue(any("undisclosed" in e for e in errs), errs)

    def test_无金额却填mrr(self):
        it = item(caliber="mrr")
        errs = HE.validate(it, lead())
        self.assertTrue(any("undisclosed" in e for e in errs), errs)

    def test_手改等级虚标(self):
        it = item(verification="official")   # homepage + 无收入只能到 founder
        errs = HE.validate(it, lead())
        self.assertTrue(any("手改" in e for e in errs), errs)


class TestBuild(unittest.TestCase):
    def test_无收入_候选形状(self):
        e = HE.build_inbox_entry(lead(), item())
        self.assertEqual(e["harvest_source"], "hn_evidence")
        self.assertEqual(e["verification"], "founder")
        self.assertEqual(e["metrics"]["headline"], "收入未披露")
        self.assertEqual(e["metrics"]["caliber"], "undisclosed")
        self.assertEqual(e["metrics"]["revenue_status"], "undisclosed")
        self.assertIn("不得", e["blocking"])

    def test_有收入_mrr落键(self):
        ev = item(revenue={"value": 12000, "caliber": "mrr", "as_of": "2026-11-13",
                           "currency": "USD"},
                  caliber="mrr", verification="official", source_kind="official")
        e = HE.build_inbox_entry(lead(), ev)
        self.assertEqual(e["metrics"]["mrr"], 12000)
        self.assertEqual(e["metrics"]["caliber"], "mrr")
        self.assertIn("$12,000", e["metrics"]["headline"])
        self.assertNotIn("TrustMRR", e["metrics"]["headline"])

    def test_累计收入落all_time(self):
        ev = item(revenue={"value": 5000, "caliber": "lifetime", "as_of": "2026-11-13"},
                  caliber="lifetime", verification="founder", source_kind="founder")
        e = HE.build_inbox_entry(lead(), ev)
        self.assertEqual(e["metrics"]["all_time"], 5000)


class TestDisplay(unittest.TestCase):
    def test_静态页显示收入未披露(self):
        import prerender as P
        html = P.frag_metrics({
            "metrics": {"headline": "收入未披露", "caliber": "undisclosed",
                        "metric_note": "收入未公开；证据为官网定价页。"}})
        self.assertIn("收入未披露", html)
        self.assertIn("口径说明", html)
        self.assertNotIn("$", html, "未披露案例不该渲染出任何金额")


class TestCommandLineIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hnev_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.leads = os.path.join(self.tmp, "leads.json")
        self.ev = os.path.join(self.tmp, "ev.json")
        self.inbox = os.path.join(self.tmp, "inbox.json")
        with io.open(self.leads, "w", encoding="utf-8") as f:
            json.dump([lead()], f, ensure_ascii=False)
        with io.open(self.inbox, "w", encoding="utf-8") as f:
            f.write("[]")
        self._old = (HE.LEADS_PATH, HE.EVIDENCE_PATH, HE.INBOX_PATH)
        self.addCleanup(self._restore)

    def _restore(self):
        HE.LEADS_PATH, HE.EVIDENCE_PATH, HE.INBOX_PATH = self._old

    def _args(self, *extra):
        return list(extra) + ["--leads", self.leads, "--path", self.ev,
                              "--inbox", self.inbox]

    def test_set与promote幂等(self):
        rc = HE.main(self._args("--set", "hn-10000001", "--url", "https://example.com/p",
                                "--kind", "homepage"))
        self.assertEqual(rc, 0)
        with io.open(self.ev, encoding="utf-8") as f:
            self.assertIn("hn-10000001", json.load(f)["items"])

        for _ in range(2):
            self.assertEqual(HE.main(self._args("--promote", "hn-10000001", "--yes")), 0)
        with io.open(self.inbox, encoding="utf-8") as f:
            box = json.load(f)
        self.assertEqual(len(box), 1, "重复 promote 不应增加条目")
        self.assertEqual(box[0]["harvest_source"], "hn_evidence")

    def test_无收入给口径被拒(self):
        with self.assertRaises(SystemExit):
            HE.main(self._args("--set", "hn-10000001", "--url", "https://example.com/p",
                               "--kind", "homepage", "--caliber", "mrr"))
        self.assertFalse(os.path.exists(self.ev), "被拒时不应建表")

    def test_未登记证据不许晋升(self):
        with self.assertRaises(SystemExit):
            HE.main(self._args("--promote", "hn-10000001", "--yes"))
        with io.open(self.inbox, encoding="utf-8") as f:
            self.assertEqual(json.load(f), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
