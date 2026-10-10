"""audit_delisted.py 与 promote_to_candidates.py 的单元测试。

这一层要守住的核心不变量
------------------------
1. **残月必须剔除**。TrustMRR 的月度时间线最后一行是「当月至今」，
   拿它跟完整月比会得出暴跌几十倍的假结论。
   这不是理论风险：`vid-ai` 的 10 月行只有 $9,643，上一月是 $41,609 ——
   直接比会得出「暴跌 77%」，而它真实的 MRR 环比只跌 19%。
2. **404 要看内容不看状态码**。TrustMRR 的 `/startup/<slug>.md` 会
   以 HTTP 200 返回一个 Next.js 404 骨架页，只看状态码会把垃圾当证据。
3. **下架要双证据**。sitemap 没有 + `.md` 抓不到，才算下架。
   单看一次 404 会把「slug 与 id 不同」误判成下架。
4. **订阅数不能覆盖 `customers`**（DATA_SCHEMA.md:212 那个自由文本字段）。
   `vid-ai` 缓存 customers=6,626、实测订阅 726，差一个数量级。

方法论：全部用固件，不联网（与 test_triage.py 一致）。
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import audit_delisted as AD                                   # noqa: E402
import promote_to_candidates as PC                            # noqa: E402


def live(mrr=48380.0, monthly=None, synced="2026-10-10T02:07:37.798Z"):
    """一个「活着」的探测结果样本。"""
    return {
        "slug": "x",
        "state": "live",
        "title": "X",
        "metrics": {
            "mrr": mrr,
            "last_30d_revenue": (mrr or 0) * 0.8,
            "growth_30d": -19.4,
            "all_time": 1627259.0,
            "subscriptions": 726,
            "profit_margin": None,
        },
        "synced_at": synced,
        "verified_via": "Stripe (API key)",
        "listed_for_sale": True,
        "monthly": monthly if monthly is not None else [
            {"month": "2026-08", "revenue": 52082.0},
            {"month": "2026-09", "revenue": 41609.0},
            {"month": "2026-10", "revenue": 9643.0},
        ],
    }


class TestResidualMonth(unittest.TestCase):
    def test_残月不参与环比(self):
        """10 月只有 $9,643，9 月是 $41,609 —— 残月必须被剔除。"""
        g, mth = PC.calendar_month_growth(live().get("monthly"),
                                          "2026-10-10T00:00:00Z")
        # 正确答案是 8 月 52,082 → 9 月 41,609 = -20.1%
        self.assertEqual(mth, "2026-09")
        self.assertAlmostEqual(g, (41609.0 - 52082.0) / 52082.0, places=4)

    def test_当月未过完仍算残月(self):
        """sync 是 9 月中旬时，9 月还没过完 ⇒ 最后一个月应是 8 月。

        这是同一个道理的时间维面：判据是「sync 所在月还没结束」，
        不是「sync 出现在数据里」。
        """
        ms = [{"month": "2026-07", "revenue": 100.0},
              {"month": "2026-08", "revenue": 200.0},
              {"month": "2026-09", "revenue": 300.0}]
        g, mth = PC.calendar_month_growth(ms, "2026-09-15T00:00:00Z")
        self.assertEqual(mth, "2026-08")
        self.assertAlmostEqual(g, 1.0, places=4)   # 100 → 200

    def test_跨月后的首日当月算残月(self):
        ms = [{"month": "2026-07", "revenue": 100.0},
              {"month": "2026-08", "revenue": 200.0},
              {"month": "2026-09", "revenue": 300.0}]
        g, mth = PC.calendar_month_growth(ms, "2026-10-01T00:00:00Z")
        self.assertEqual(mth, "2026-09")
        self.assertAlmostEqual(g, 0.5, places=4)

    def test_不足两个月不给结论(self):
        self.assertEqual(
            PC.calendar_month_growth([{"month": "2026-09", "revenue": 1.0}],
                                     "2026-10-10T00:00:00Z"), (None, None))


class TestPeakRetention(unittest.TestCase):
    def test_留存率剔除残月(self):
        ms = [{"month": "2026-03", "revenue": 35909.0},
              {"month": "2026-06", "revenue": 5461.0},
              {"month": "2026-08", "revenue": 7939.0},
              {"month": "2026-09", "revenue": 5702.0},
              {"month": "2026-10", "revenue": 1363.0}]
        ret, peak, last = PC.peak_retention(live(monthly=ms))
        self.assertEqual(peak, 35909.0)
        self.assertEqual(last, 5702.0)          # 不是残月的 1,363
        self.assertAlmostEqual(ret, 5702.0 / 35909.0, places=4)

    def test_数据不足返回None(self):
        self.assertEqual(PC.peak_retention(live(monthly=[])), (None, None, None))


class TestDelistedGate(unittest.TestCase):
    def test_下架条目被挡(self):
        """趋势story 实测：sitemap 与 .md 双 404。"""
        gone = dict(live(), state="gone", http=404)
        ok, why, _, _ = PC.check_one({"id": "trendstory", "trade": None}, gone)
        self.assertFalse(ok)
        self.assertIn("下架", why)

    def test_缓存与实测偏差过大要人工确认(self):
        """vidfarm 实测：缓存 $2,962 → 实测 $1,458，偏差 -51%。"""
        rec = {"id": "vidfarm-cc", "trade": None,
               "metrics": {"mrr": 2962}, "added_at": "2026-09-20"}
        lv = live(mrr=1458.0, monthly=[{"month": "2026-08", "revenue": 2612.0},
                                      {"month": "2026-09", "revenue": 2398.0}])
        lv["synced_at"] = "2026-10-06T00:00:00Z"
        ok, why, _, _ = PC.check_one(rec, lv)
        self.assertFalse(ok)
        self.assertIn("偏差", why)

    def test_破例名单必须留痕(self):
        """vid-ai 被作者破例放行，但破例理由要能写进口径说明。"""
        self.assertIn("vid-ai", PC.OVERRIDE_RETAIN)
        rec = {"id": "vid-ai", "trade": {"price": "$1,500,000", "multiple": "2.87x"},
               "metrics": {"mrr": 57663.33}, "added_at": "2026-09-23"}
        lv = live(mrr=48380.0, monthly=[
            {"month": "2026-01", "revenue": 89846.0},
            {"month": "2026-06", "revenue": 61905.0},
            {"month": "2026-08", "revenue": 52082.0},
            {"month": "2026-09", "revenue": 41609.0},
            {"month": "2026-10", "revenue": 9643.0}])
        lv["synced_at"] = "2026-10-10T02:07:37.798Z"
        ok, why, m, extra = PC.check_one(rec, lv)
        self.assertTrue(ok, "破例条目应当放行：%s" % why)
        self.assertIn("override", extra)
        note = PC.build_metric_note(rec, lv, m, extra.get("growth"),
                                    extra.get("ret"), extra.get("peak"),
                                    extra.get("last"), extra.get("override"))
        self.assertIn("留存", note)
        self.assertIn("破例", note)
        # 下滑事实不许被破例掩盖
        self.assertIn("46%", note)

    def test_未破例的腰斩条目被挡(self):
        """linkpost 实测：峰值 $35,909 → $5,702，留存 16%。

        fixture 的月线刻意让**最近完整月是平的**（07→08→09 在涨），
        这样挡下它的必须是留存判据而不是「最近一月在跌」那道 ——
        两道判据的职责不同，测试要能区分。
        """
        rec = {"id": "linkpost", "trade": None, "metrics": {"mrr": 4874.35}}
        lv = live(mrr=5087.0, monthly=[
            {"month": "2026-03", "revenue": 35909.0},
            {"month": "2026-07", "revenue": 6000.0},
            {"month": "2026-08", "revenue": 6100.0},
            {"month": "2026-09", "revenue": 6200.0},
            {"month": "2026-10", "revenue": 1363.0}])
        lv["synced_at"] = "2026-10-10T00:00:00Z"
        ok, why, _, extra = PC.check_one(rec, lv)
        self.assertFalse(ok)
        self.assertIn("留存", why)
        self.assertLess(extra.get("ret", 1.0), PC.PEAK_RETENTION)


class TestNoFakeNumberMatch(unittest.TestCase):
    """订阅数是自由文本，不能拿去覆盖 customers。"""

    def test_漂移比对不认customers(self):
        rec = {"metrics": {"mrr": 48380.0, "all_time": 1606538.0,
                           "customers": 6626}}
        lv = live(mrr=48380.0)
        lv["metrics"]["all_time"] = 1627259.0
        lv["metrics"]["subscriptions"] = 726
        # customers 差了一个数量级，但不应报漂移
        self.assertIsNone(AD.drift_of(rec, lv))

    def test_实测归零要报出来(self):
        rec = {"metrics": {"mrr": 3970.9}}
        lv = live(mrr=0.0)
        diffs = AD.drift_of(rec, lv)
        self.assertTrue(diffs)
        self.assertIn("归零", diffs[0][3])


class TestRefreshInPool(unittest.TestCase):
    """🚨 `--refresh-in-pool`（2026-10-10，cometly 触发）。

    这条路径解决的是一类真实存在的死局：**条目早已躺在候选池里，但当时
    收入没公开，`metrics.mrr=None`**（cometly 就是 2026-09-11 入池，
    2026-10-10 TrustMRR 才公开 $202,060）。
    晋升脚本帮不上忙 —— 它只处理 inbox 的条目，而池内条目 inbox 里没有。
    """

    def test_slug能从source_url反推(self):
        """池内老条目没有 `trustmrr_slug` 字段，只有 source_url。"""
        self.assertEqual(
            PC.slug_of({"source_url": "https://trustmrr.com/startup/cometly"}),
            "cometly")
        # 显式字段优先
        self.assertEqual(
            PC.slug_of({"trustmrr_slug": "podawaa",
                        "source_url": "https://trustmrr.com/startup/other"}),
            "podawaa")
        # 两个都没有 → 空串（调用方据此跳过，而不是拿 None 去 probe 静默失败）
        self.assertEqual(PC.slug_of({"source_url": "https://example.com"}), "")
        self.assertEqual(PC.slug_of({}), "")
        # 后面不能跟路径尾巴
        self.assertEqual(
            PC.slug_of({"source_url": "https://trustmrr.com/startup/x?a=1#f"}),
            "x")

    def test_池内刷新补齐字段并清阻塞(self):
        rec = {
            "id": "cometly", "name": "Cometly",
            "source_url": "https://trustmrr.com/startup/cometly",
            "added_at": "2026-09-11",
            "blocking": "缺收入数据",
            "verification": "unverified",
            "metrics": {"mrr": None, "all_time": 5162.3},
        }
        lv = live(mrr=202060.0)
        lv["metrics"].update({"all_time": 9983810.0, "subscriptions": 288,
                               "growth_30d": 35.4, "last_30d_revenue": 269169.0})
        lv["verified_via"] = "Stripe (API key)"
        out = PC.to_candidate(rec, lv, lv["metrics"], 0.016, 0.93,
                              221038.0, 205353.0, None)
        # 阻塞标记必须消失（它描述的正是本次要解决的问题）
        self.assertIsNone(out.get("blocking"))
        # 可信度按实测来源升级
        self.assertEqual(out["verification"], "stripe")
        # 数字补齐
        m = out["metrics"]
        self.assertEqual(m["mrr"], 202060.0)
        self.assertEqual(m["subscriptions"], 288)
        self.assertEqual(m["caliber"], "mrr")
        # 数据血缘要保住：added_at 不能被刷掉
        self.assertEqual(out["added_at"], "2026-09-11")
        # 老 note 说「未拿到收入数字」，刷新后必须改写，否则自己打自己的脸
        self.assertNotIn("未拿到", str(out.get("note")))
        # metric_note 要说清「刷新前没有数字」，而不是编一个采集日期
        self.assertIn("没有任何收入数字", m["metric_note"])

    def test_刷新不覆盖原始晋升日期(self):
        """`promoted_from_inbox` 是**首次**晋升日，刷新不能改成今天。"""
        rec = {"id": "x", "source_url": "https://trustmrr.com/startup/x",
               "promoted_from_inbox": "2026-09-11", "metrics": {"mrr": 100.0}}
        lv = live(mrr=202060.0)
        out = PC.to_candidate(rec, lv, lv["metrics"], None, None, None, None, None)
        self.assertEqual(out["promoted_from_inbox"], "2026-09-11")
        # 刷新日期另存，别混进同一个字段
        self.assertEqual(out.get("refreshed_at"), "2026-10-10")

    def test_破例必须留blocking痕迹(self):
        """人工破例要让人看见它在带风险通过（与 vid-ai 的做法一致）。"""
        rec = {"id": "vid-ai", "source_url": "https://trustmrr.com/startup/vid-ai"}
        lv = live(mrr=48380.0)
        g = PC.calendar_month_growth(lv["monthly"], lv["synced_at"])[0]
        ret, peak, last = PC.peak_retention(lv)
        # 不破例时留存不足 ⇒ 判据本身就该拦住
        if ret is not None and ret < PC.PEAK_RETENTION:
            ok, _, _, extra = PC.check_one(rec, lv)
            self.assertFalse(ok)
            self.assertIsNone(extra.get("override"))
        # 破例名单里的条目才允许过，且必须带 override 说明
        self.assertIn("vid-ai", PC.OVERRIDE_RETAIN)
        self.assertTrue(PC.OVERRIDE_RETAIN["vid-ai"])

    def test_池内条目不在inbox里也能刷(self):
        """回归钉：`to_candidate` 只依赖 rec/live/m，不碰 inbox ——
        这正是它能被池内刷新复用的前提。"""
        rec = {"id": "cometly", "source_url": "https://trustmrr.com/startup/cometly"}
        lv = live(mrr=202060.0)
        out = PC.to_candidate(rec, lv, lv["metrics"], None, None, None, None, None)
        self.assertEqual(out["id"], "cometly")


class TestGrowthCaliber(unittest.TestCase):
    """🚨🚨 **MRR 增长率 ≠ 收入增长率**（2026-10-10，cometly 触发）。

    TrustMRR 的 `.md` 上**同时**列了两个 growth，口径不同、数值可正负相反：

        Last 30 days revenue growth: +35.4%   ← 近 30 天「收入」
        Last 30 days MRR growth:     -4.9%   ← 近 30 天「MRR」

    `cometly` 就是活例子：收入 +35.4%（一次性大额入账），
    但 MRR 其实在**跌** 4.9%。把它存进 `growth_30d` 会让一家
    「稳定的大盘生意」看起来在暴涨。

    🚨 这比金额口径混用更隐蔽：金额字段搞错会让**数字**错，
    而趋势字段搞错只让**方向**错 —— 数字看着完全合理。
    这正是红线 1「收入口径不许混用」在趋势字段上的表现。
    """

    MD = """- Last 30 days | $269,169 |
    - Last 30 days revenue snapshot: $269,169
    - Last 30 days revenue growth: +35.4%
    ### Daily revenue — last 30 days
    - Current MRR: $202,060
    - Last 30 days MRR growth: -4.9%
    - Last 30 days profit margin: 90.0%
    """

    def _metrics(self):
        out = {"metrics": {}}

        def g(pat, cast=float):
            m = re.search(pat, self.MD)
            if not m:
                return None
            try:
                return cast(m.group(1))
            except ValueError:
                return None
        out["metrics"] = {
            "mrr": g(r"Current MRR:\s*\$([\d,\.]+)"),
            "mrr_growth_30d": g(r"Last 30 days MRR growth:\s*\*{0,2}([\-\+\d\.]+)%"),
            "revenue_growth_30d": g(r"Last 30 days revenue growth:\s*\*{0,2}([\-\+\d\.]+)%"),
        }
        return out["metrics"]

    def test_两个growth必须分开存(self):
        m = self._metrics()
        self.assertEqual(m["mrr_growth_30d"], -4.9)
        self.assertEqual(m["revenue_growth_30d"], 35.4)
        # 正负相反 —— 这正是不能混用的理由
        self.assertNotEqual(m["mrr_growth_30d"], m["revenue_growth_30d"])

    def test_旧字段名必须指MRR口径(self):
        """`growth_30d` 是下遊/前端在用的名字，必须锁死为 MRR 增长。

        本项目主口径是 MRR（红线 1），所以 `growth_30d` 只能 = MRR 增长。
        留着旧名但塞收入增速，会让调用方拿到 +35.4% 当 MRR 增速用。
        """
        import audit_delisted as _ad
        import inspect
        src = inspect.getsource(_ad.probe)
        self.assertIn('out["metrics"]["growth_30d"] = '
                      'out["metrics"]["mrr_growth_30d"]', src)

    def test_真实数据复核_cometly(self):
        """真数据回归：cometly 的两个 growth 实测值（2026-10-10 抓）。"""
        live_cometly = self._metrics()
        # MRR 在跌这件事必须被如实反映，不能被收入增速盖掉
        self.assertLess(live_cometly["mrr_growth_30d"], 0)
        self.assertGreater(live_cometly["revenue_growth_30d"], 0)


if __name__ == "__main__":
    unittest.main()