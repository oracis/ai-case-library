# -*- coding: utf-8 -*-
r"""小红书草稿箱「打开方式」回归锁（bat + 回读脚本）。

锁的是 2026-10-17 的故障：`xhs_open_right.bat` 在「端口已起」分支用
`start "" "%DRAFT%"` 把草稿箱页交给**默认浏览器**（用户日常 Chrome，
草稿箱 0），控制台却打印 "opening draft box in that window"。
症状：每次重启后第一次能用，之后再开必然 0。

修法：同 `--user-data-dir` 单例唤起 CfT（Chrome 按 profile 单例，
已在跑的实例收下 URL），再加 `xhs_draftbox_verify.py` 回读侧边栏
`草稿箱(N)` 到控制台。

本文件全部是**静态 + 失败软化**断言，不依赖 9223 是否在跑。
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(HERE, "xhs_open_right.bat")
MD = os.path.join(HERE, "xhs_open_right.md")


def _bat_text():
    with io.open(BAT, "r", encoding="ascii", errors="replace") as f:
        return f.read()


class TestBatRegression(unittest.TestCase):
    def setUp(self):
        self.text = _bat_text()
        self.lines = self.text.splitlines()

    def test_rem_lines_all_have_space(self):
        # 2026-10-17 实测：`REMTwo profiles...`（REM 后缺空格）会让 cmd
        # 报 'REMTwo' is not recognized —— 纯 ASCII 批处理的老坑。
        for i, ln in enumerate(self.lines, 1):
            if ln.startswith("REM") and len(ln) > 3 and ln[3] != " ":
                self.fail("第 %d 行 REM 后缺空格: %r" % (i, ln[:40]))

    def test_up_branch_hands_to_same_profile_not_default_browser(self):
        # 端口已起时必须唤起**同 profile** 的 CfT（单例收下 URL），
        # 绝不许 start "" "%DRAFT%" —— 那是默认浏览器（草稿箱 0）。
        # 只查**可执行行**：注释里写明旧 bug 模式是故意的（文档用）。
        self.assertIn('--user-data-dir="%XHS_PROFILE%"', self.text)
        self.assertIn('"%DRAFT%"', self.text)
        code = "\n".join(ln for ln in self.lines
                         if not ln.lstrip().startswith("REM"))
        self.assertNotIn('start "" "%DRAFT%"', code)
        # 唤起行必须带 CFT（Chrome for Testing），不能是别的浏览器
        self.assertIn('start "" "%CFT%" --user-data-dir="%XHS_PROFILE%"',
                      code)

    def test_down_branch_still_launches_with_port_and_lock_cleanup(self):
        self.assertIn("--remote-debugging-port=%PORT%", self.text)
        self.assertIn('del /f /q "%XHS_PROFILE%\\Default\\LOCK"', self.text)

    def test_bat_wires_verify_script(self):
        self.assertIn("xhs_draftbox_verify.py", self.text)
        self.assertTrue(os.path.isfile(
            os.path.join(HERE, "xhs_draftbox_verify.py")))

    def test_ascii_only(self):
        # cmd.exe 按 GBK 读 .bat，UTF-8 中文注释会被截断吃掉下一行
        for i, ln in enumerate(self.lines, 1):
            try:
                ln.encode("ascii")
            except UnicodeEncodeError:
                self.fail("第 %d 行含非 ASCII（会被 GBK 吞）: %r"
                          % (i, ln[:40]))


class TestVerifyScript(unittest.TestCase):
    def test_sig_js_reads_sidebar_count(self):
        import xhs_draftbox_verify as v
        # 页面内正则必须盯侧边栏「草稿箱(N)」—— 唯一可信判据
        self.assertIn("草稿箱", v._SIG_JS)
        self.assertIn("count", v._SIG_JS)

    def test_fail_soft_when_port_dead(self):
        # 9229 必然没东西：脚本必须打印原因并返回 0，不能裸崩
        import xhs_draftbox_verify as v
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = v.main(["--port", "9229", "--wait", "1"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertIn("[verify]", buf.getvalue())


class TestDocUpdated(unittest.TestCase):
    def test_md_documents_the_2026_10_17_fix(self):
        with io.open(MD, "r", encoding="utf-8") as f:
            md = f.read()
        self.assertIn("2026-10-17", md)
        self.assertIn("默认浏览器", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
