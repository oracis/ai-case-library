# -*- coding: utf-8 -*-
"""reconcile_verdict.py 的回归锁（2026-10-17 修完 5 个 bug 后立）。

锁的都是「静默失败」型 bug——不报错、不退出，只是结果为空/错位：
  1. 函数内局部 import wp 遮蔽顶层 import ⇒ 头条/B站路径 UnboundLocalError
  2. 调不存在的 wp._port_alive ⇒ AttributeError
  3. new_target 返 dict，整个传给 connect_target ⇒ 字符串比较永不命中
  4. connect_target 失败不检查 ⇒ 之后每次 send 抛「未连接页面 target」
  5. draft_list 在首页 tab 读（argus 挡）⇒ 恒 None，必须导航到草稿箱页
"""
import io
import os
import sys
import unittest

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

with open(os.path.join(ROOT, "reconcile_verdict.py"), encoding="utf-8") as f:
    RV = f.read()
# ⚠ 只查**可执行行**：注释/文档字符串里出现旧 bug 模式是故意的
# （写给人看的踩坑记录），别让判据撞上注释（2026-10-17 实测自撞两次）。
RV_CODE = "\n".join(line.split("#", 1)[0] for line in RV.splitlines())
# 行为级测试要真 import（静态断言之外，verdict_for 的判据本身也要守）。
# 模块顶层 import wechat_publish / chrome_cdp_launch 都是安全的（别的
# 测试文件也这么干）。
import reconcile_verdict as RV_MOD                          # noqa: E402


class TestNoSilentFailures(unittest.TestCase):
    def test_顶层已import_wp和time(self):
        # 修法锚点：wp/_L/time 都从顶层进，函数内不许再 import
        self.assertIn("import wechat_publish as wp", RV_CODE)
        self.assertIn("import chrome_cdp_launch as _L", RV_CODE)
        self.assertIn("import time", RV_CODE)

    def test_main函数体内不许再import_wp(self):
        # Python 作用域：函数里一次局部 import ⇒ 整个函数体的 wp 都变局部变量
        # （2026-10-17 实测：头条/B站 一跑就 UnboundLocalError）
        body = RV_CODE[RV_CODE.find("def main("):]
        self.assertNotIn("import wechat_publish as wp", body)
        self.assertNotIn("import wechat_publish", body)

    def test_不许调不存在的_port_alive(self):
        # wechat_publish 里没有 _port_alive；存活判断用
        # _autostart_chrome_if_needed 的返回值
        self.assertNotIn("_port_alive", RV_CODE)
        self.assertIn("if not wp._autostart_chrome_if_needed", RV_CODE)

    def test_new_target返回值必须取targetId(self):
        # new_target 返 {"id":..,"targetId":..}；整个 dict 传进
        # connect_target 会因 str != dict 重试 10 次后静默失败
        self.assertIn('_t["targetId"] if isinstance(_t, dict) else _t', RV_CODE)

    def test_connect_target失败必须显式检查(self):
        self.assertIn("if not box.connect_target(tid):", RV_CODE)
        self.assertIn("连不上新开的 target", RV_CODE)

    def test_头条草稿箱必须导航到草稿箱页(self):
        # 首页 tab 被 argus 挡：draft_list 相对路径 fetch 恒 None
        # （2026-10-17 实测：草稿箱读 0 条、已发表读 20 条的诡异组合）
        self.assertIn("tt.TT_DRAFT", RV_CODE)
        self.assertIn('cdp.send("Page.navigate", {"url": tt.TT_DRAFT})', RV_CODE)

    def test_probe_url用平台首页但草稿读取会导航(self):
        # main 里仍开 TT_HOME（探针入口），真正读草稿前 _probe_toutiao 导航
        self.assertIn("tt.TT_HOME", RV_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestVerdictForWechat(unittest.TestCase):
    """公众号分支的行为级判据（2026-10-21 修）。

    修的洞：此前只翻 diffs 里两个 code（DRAFT_GONE_BUT_PUBLISHED /
    REMOTE_MISSING），classify 在**读取失败**时发的 UNKNOWN 级行
    （DRAFT_LIST_UNREADABLE / PUB_LIST_UNREADABLE / TITLE_COLLISION）
    不在其中，被跳过之后 fallthrough 成 RATE_LIMIT「草稿箱里还有这条」
    —— 把「没读到」说成「草稿还在」，正是本模块铁律
    「双侧都读成功才下结论」要防的误判（比不判更坏）。
    """

    def _p(self, diffs, draft_ok=True, pub_ok=True):
        """构造 _probe_wechat 形状的探针结果。"""
        return {"diffs": diffs,
                "meta": {"draft_ok": draft_ok, "pub_ok": pub_ok},
                "draft_ok": draft_ok, "pub_ok": pub_ok}

    def test_草稿箱读失败是UNKNOWN不是RATE_LIMIT(self):
        p = self._p([("UNKNOWN", "DRAFT_LIST_UNREADABLE", "mort",
                      "草稿箱没读到 ⇒ 无法区分「真没了」和「没读到」")],
                    draft_ok=False)
        v, detail = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "UNKNOWN")
        self.assertIn("草稿箱没读到", detail)

    def test_已发表读失败是UNKNOWN(self):
        p = self._p([("UNKNOWN", "PUB_LIST_UNREADABLE", "mort",
                      "已发表列表没读到")], pub_ok=False)
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "UNKNOWN")

    def test_标题撞车行透传为UNKNOWN(self):
        """TITLE_COLLISION 是 UNKNOWN 级：认亲不可靠，不许下结论。"""
        p = self._p([("UNKNOWN", "TITLE_COLLISION", "mort", "认亲不可靠")])
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "UNKNOWN")

    def test_双侧读成且群发过才判PUBLISHED(self):
        p = self._p([("FIX", "DRAFT_GONE_BUT_PUBLISHED", "mort", "群发过")])
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "PUBLISHED")

    def test_双侧读成且远端都没有才判MISSING(self):
        p = self._p([("FIX", "REMOTE_MISSING", "mort", "真没了")])
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "MISSING")

    def test_没有该case的diff且双侧读成才是RATE_LIMIT(self):
        p = self._p([("FIX", "REMOTE_MISSING", "other", "别的 case")])
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "RATE_LIMIT")

    def test_没读到时连没有diff都不许判RATE_LIMIT(self):
        """草稿箱读失败 + 恰好没有本 case 的行 ⇒ 仍须 UNKNOWN。"""
        p = self._p([], draft_ok=False)
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "UNKNOWN")

    def test_diffs为None走通用路径仍UNKNOWN(self):
        p = {"draft_ok": False, "pub_ok": False, "why": "公众号 token 没拿到"}
        v, _ = RV_MOD.verdict_for(p, "mort")
        self.assertEqual(v, "UNKNOWN")
