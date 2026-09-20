# -*- coding: utf-8 -*-
"""backfill_trustmrr.merge_record 的回归测试（离线，不发任何请求）。

为什么要单独测这个小函数：它决定「TrustMRR 的网关验证收入能不能真的落进
metrics」。2026-09-20 发现过一个静默 bug —— TrustMRR 详情页的
`Verified revenue, last 30 days` 被读出来（`last30 = rev.get("last30Days")`）
却从未写入，变量成了死代码。后果不是报错，而是：

  * Current MRR 对非订阅制项目恒为 0，这些条目永远停在「具体数字待补」；
  * 其中 one-aminos-llc 的近 30 天收入是 $79,596 —— 数字就在同一个响应里；
  * 下游 triage.revenue_of() 取不到月收入，把「月流水几万」当成「没有数字」。

同一时期还存在**三套键名**并存：sources.json 的契约名 last_30d_revenue、
harvest.py 内部暂存的 revenue_last30d、以及一个都没写。所以这里的断言不只测
「有没有写」，还测「写的是哪个名字」—— 键名再漂移一次，同样的故障会重演。

用法：在 scripts/ 下 `python -m unittest test_backfill_trustmrr`
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backfill_trustmrr as B                                     # noqa: E402
import harvest as H                                              # noqa: E402
import triage as T                                               # noqa: E402

# 契约名。改这个字符串等于改 data/sources.json 的字段清单，两边必须一起动。
CONTRACT_KEY = "last_30d_revenue"


def api_item(mrr=0, last30=0, total=0, rank=None, slug="acme",
             name="Acme", website="https://acme.dev/", category="", country=""):
    """造一条 /api/ai 形状的记录 —— 与 backfill 的 fetch_batch() 返回同形。"""
    return {
        "name": name, "slug": slug,
        "url": "https://trustmrr.com/startup/%s" % slug,
        "website": website,
        "rank": rank,
        "category": category,
        "country": country,
        "revenue": {"mrr": mrr, "last30Days": last30, "total": total},
        "customers": None, "activeSubscriptions": None,
    }


def rec(**kw):
    """一条存量记录的最小骨架。"""
    base = {"id": "acme", "name": "Acme", "metrics": {}}
    base.update(kw)
    return base


class WritesLast30(unittest.TestCase):
    """本次修的核心：近 30 天收入必须落盘，且落对名字。"""

    def test_mrr为零时_近30天收入仍然写进metrics(self):
        r = rec()
        changed, why = B.merge_record(r, api_item(mrr=0, last30=79596.73, total=88191.25))
        self.assertTrue(changed)
        self.assertEqual(r["metrics"][CONTRACT_KEY], 79596.73)
        self.assertIn("近30天", "".join(why))

    def test_键名是契约名(self):
        """写出的必须是 sources.json 契约里的那个名字。"""
        r = rec()
        B.merge_record(r, api_item(mrr=0, last30=5200))
        self.assertIn(CONTRACT_KEY, r["metrics"])
        self.assertNotIn("revenue_last30d", r["metrics"],
                         "又用回 harvest 内部暂存名了 —— triage 会读不到")

    def test_mrr与近30天同时存在时_两个都写(self):
        r = rec()
        B.merge_record(r, api_item(mrr=3423, last30=2900))
        self.assertEqual(r["metrics"]["mrr"], 3423)
        self.assertEqual(r["metrics"][CONTRACT_KEY], 2900)

    def test_下游triage能读到这个数字(self):
        """端到端：回填写进去的键，triage 必须真的能取到。"""
        r = rec()
        B.merge_record(r, api_item(mrr=0, last30=79596.73))
        self.assertEqual(T.revenue_of(r), 79596.73)
        self.assertTrue(T.has_numbers(r))

    def test_没有近30天数据时不写这个键(self):
        r = rec()
        B.merge_record(r, api_item(mrr=1000, last30=0, total=5000))
        self.assertNotIn(CONTRACT_KEY, r["metrics"])


class Headline(unittest.TestCase):
    """主指标文案。口径必须诚实 —— 月流水不能写成 MRR。"""

    def test_只有近30天收入时_不谎称MRR(self):
        r = rec(metrics={"headline": "TrustMRR 验证收入榜条目（具体数字待补）"})
        B.merge_record(r, api_item(mrr=0, last30=79596.73, rank=63))
        head = r["metrics"]["headline"]
        self.assertIn("近 30 天收入", head)
        self.assertIn("$79,597", head)
        self.assertIn("第 63 名", head)
        # 别把月流水写成 MRR。注意 headline 里的 TrustMRR 是平台名，不算。
        self.assertNotIn("MRR $", head)
        self.assertFalse(head.startswith("MRR"), head)

    def test_有MRR时_主指标写MRR(self):
        r = rec(metrics={"headline": "具体数字待补"})
        B.merge_record(r, api_item(mrr=3423, last30=100, rank=988))
        head = r["metrics"]["headline"]
        self.assertIn("MRR", head)
        self.assertIn("$3,423", head)

    def test_MRR优先于近30天收入(self):
        """两个都有时主指标仍是 MRR —— 经常性收入比单月流水更可比。"""
        r = rec(metrics={"headline": "未获取"})
        B.merge_record(r, api_item(mrr=5000, last30=60000))
        self.assertTrue(r["metrics"]["headline"].startswith("MRR"))

    def test_已有真实headline不被覆盖(self):
        """headline 只补「待补 / 未获取」，绝不覆盖已有的实测文案。

        注意别断言 changed 为整体 False —— 同一次调用里官网、来源页这些空字段
        本来就该被补上。要守的是「主指标这一格没被动过」。
        """
        r = rec(metrics={"headline": "MRR $424,368 · Stripe 验证 · 2026-08-14 最后快照"})
        changed, why = B.merge_record(r, api_item(mrr=1, last30=2))
        self.assertEqual(r["metrics"]["headline"],
                         "MRR $424,368 · Stripe 验证 · 2026-08-14 最后快照")
        self.assertFalse([w for w in why if w.startswith("主指标")], why)
        self.assertIsInstance(changed, bool)

    def test_未公开也要换成真实数字(self):
        r = rec(metrics={"headline": "TrustMRR 收录（收入未公开）"})
        B.merge_record(r, api_item(mrr=0, last30=76))
        self.assertIn("近 30 天收入", r["metrics"]["headline"])


class Idempotent(unittest.TestCase):
    """回填要能重复跑：第二次不能再改任何东西。"""

    def test_跑两次结果一致且第二次无改动(self):
        item = api_item(mrr=1200, last30=3400, total=90000, rank=12,
                        category="SaaS", country="US")
        r = rec()
        first, why1 = B.merge_record(r, item)
        self.assertTrue(first)
        snapshot = {k: v for k, v in r.items()}
        second, why2 = B.merge_record(r, item)
        self.assertFalse(second, "第二次又改了：%s" % why2)
        self.assertEqual(r, snapshot)

    def test_不覆盖已有数字(self):
        r = rec(metrics={"mrr": 999, CONTRACT_KEY: 888, "all_time": 777})
        B.merge_record(r, api_item(mrr=1, last30=2, total=3))
        self.assertEqual(r["metrics"]["mrr"], 999)
        self.assertEqual(r["metrics"][CONTRACT_KEY], 888)
        self.assertEqual(r["metrics"]["all_time"], 777)


class OtherFields(unittest.TestCase):
    """顺手守住同一函数里其它字段的行为，避免以后改动时误伤。"""

    def test_累计收入写all_time(self):
        r = rec()
        B.merge_record(r, api_item(total=88191.25))
        self.assertEqual(r["metrics"]["all_time"], 88191.25)

    def test_官网只补空(self):
        r = rec(website="https://existing.dev/")
        B.merge_record(r, api_item(website="https://new.dev/"))
        self.assertEqual(r["website"], "https://existing.dev/")

    def test_官网不留空时不写(self):
        r = rec()
        B.merge_record(r, api_item(website=""))
        self.assertNotIn("website", r)

    def test_分类只在未分类时改(self):
        r = rec(category="营销工具")
        B.merge_record(r, api_item(category="SaaS"))
        self.assertEqual(r["category"], "营销工具")

    def test_来源页只补空(self):
        r = rec()
        B.merge_record(r, api_item(slug="acme"))
        self.assertIn("trustmrr.com/startup/acme", r["source_url"])

    def test_空revenue不炸(self):
        r = rec()
        changed, why = B.merge_record(r, {"name": "X", "slug": "x", "revenue": {}})
        self.assertIsInstance(changed, bool)
        self.assertIsInstance(why, list)
        self.assertNotIn(CONTRACT_KEY, r["metrics"])

    def test_非字典输入不炸(self):
        """空输入返回「没改动」而不是抛异常 —— 让这个纯函数是全定义的。"""
        r = rec()
        changed, why = B.merge_record(r, None)
        self.assertFalse(changed)
        self.assertEqual(why, [])


class RealData(unittest.TestCase):
    """拿真实数据做一次不变量检查：数字可以少，但键名不能漂。"""

    def test_存量数据里没有漂移键名(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("inbox", "candidates", "cases"):
            p = os.path.join(root, "data", name + ".json")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                continue
            bad = [r.get("id") for r in rows
                   if "revenue_last30d" in (r.get("metrics") or {})]
            self.assertEqual(bad, [],
                             "%s 里有条目用了内部暂存键名 revenue_last30d：%s" % (name, bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
