# -*- coding: utf-8 -*-
"""封面白边故障的回归锁（2026-10-17）。

锁两层：
A. verify_cover_edge.py 的判据本身（阈值 200 / 右缘 4.5% / 退出码语义）
B. wechat_publish._gen_cover 的修复点（视口撑到画布、target 双断言、
   clip 用画布常量、captureBeyondViewport）
任何一层被回退，这批测试必须红。
"""
import io
import os
import sys
import unittest

# Windows 控制台默认 GBK，中文 traceback 会 UnicodeEncodeError。
# ⚠ 必须用 reconfigure **就地改**，不能 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, …)`：
# 新包装器和原 stdout **共享同一个底层 buffer**，等它被后续替换/回收时，
# TextIOWrapper.__del__ 会把那个 buffer 一并关掉 —— 整池测试的 stdout 当场
# 死掉（2026-10-19 实测：discover 全量跑由此产生 50 个
# "I/O operation on closed file" 假错误，还连带 test_wechat_publish
# 导入 make_article 失败、66 项测试被静默跳过）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

with open(os.path.join(ROOT, "wechat_publish.py"), encoding="utf-8") as f:
    WP = f.read()
with open(os.path.join(ROOT, "verify_cover_edge.py"), encoding="utf-8") as f:
    VCE = f.read()


class TestGenCoverFixes(unittest.TestCase):
    """_gen_cover 必须保留 2026-10-17 白边故障的全部修复点。"""

    def test_视口必须撑到画布宽(self):
        # 真凶：headless 视口 ~780px < 画布 1080px ⇒ clip 被裁、右侧白边
        self.assertIn('"width": COVER_W, "height": COVER_H', WP)
        self.assertIn('"deviceScaleFactor": 2', WP)

    def test_target断言双保险(self):
        # connect_target 失败会静默留在上一个 target，截到编辑器页面
        self.assertIn("WRONG_TARGET", WP)
        self.assertIn("NO_T_NAME_NODE", WP)
        self.assertIn("getElementById('t-name')", WP)

    def test_截屏参数必须带captureBeyondViewport和画布clip(self):
        self.assertIn('"captureBeyondViewport": True', WP)
        self.assertIn('"width": COVER_W, "height": COVER_H,\n                     "scale": 1', WP)

    def test_clip不许写死数字(self):
        # 写死 1080/460 会在改画布尺寸时静默失配——必须走常量
        self.assertNotIn('"width": 1080, "height": 460', WP)
        self.assertNotIn('"width": 1080, "height": 920', WP)


class TestVerifyCoverEdge(unittest.TestCase):
    """体检脚本自身的判据锁。"""

    def test_判据阈值与校准值一致(self):
        # 2026-10-17 对 47 张线上封面实测：脏=255（白），净=47~75（设计底色）
        self.assertIn("EDGE_THRESHOLD = 200", VCE)
        self.assertIn("EDGE_FRACTION = 0.045", VCE)

    def test_退出码语义(self):
        self.assertIn("return 1 if dirty else 0", VCE)
        self.assertIn("return 2", VCE)

    def test_判据方向不能反(self):
        # 亮度高=白边=脏；反向了就会把干净设计全判脏
        self.assertIn("bad = r[\"edgeLum\"] > EDGE_THRESHOLD", VCE)
        self.assertNotIn("bad = r[\"edgeLum\"] < EDGE_THRESHOLD", VCE)

    def test_支持本地目录和URL两种输入(self):
        self.assertIn('--dir", action="append"', VCE)
        self.assertIn('--url", action="append"', VCE)

    def test_连接失败必须报错而不是静默放行(self):
        self.assertIn("CDP 连接失败", VCE)
        self.assertIn("return 2", VCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
