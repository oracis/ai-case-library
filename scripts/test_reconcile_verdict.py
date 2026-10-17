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
