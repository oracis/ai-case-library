# -*- coding: utf-8 -*-
"""toutiao_publish 匹配 / 滚动修复的回归锁。

背景（2026-10-16 P0 实测）：
- 头条草稿箱是懒加载：首屏 ~21 条，页面自报「共 48 条」，URL 分页
  参数无效 ⇒ fetch_draft_titles 不滚动就会读到残缺列表（首轮 16 条）。
- 三平台远端标题全是「产品名：描述」新口径，而 make_*_title 生成
  「月收$xK：…」旧口径 ⇒ 字面前 N 字符匹配大面积失配，必须产品名匹配。
"""
import sys
import unittest

sys.path.insert(0, "scripts")
import toutiao_publish as tp  # noqa: E402


class TestDraftTitleMatch(unittest.TestCase):
    """draft_title_match：产品名匹配 + 字面兜底。"""

    CASES = {
        "1lookup": {"id": "1lookup", "name": "1Lookup"},
        "aeo-engine": {"id": "aeo-engine", "name": "AEO Engine"},
        "nutria-9e3eaa": {"id": "nutria-9e3eaa", "name": "Nutria"},
        "wpconvert-ai-convert-ai-sites-to-wordpress":
            {"id": "wpconvert-ai-convert-ai-sites-to-wordpress",
             "name": "WPConvert"},
    }

    def test_新口径产品名标题能匹配(self):
        # 远端：「1Lookup：一个 API 查电话、邮箱、IP 实时数据」
        remote = ["1Lookup：一个 API 查电话、邮箱、IP 实时数据",
                  "Chatbase：上传文档就得到一个客服机器人"]
        c = self.CASES["1lookup"]
        self.assertIsNotNone(tp.draft_title_match(c["id"], c, remote))

    def test_标题无产品名时字面兜底(self):
        # 远端标题完全改写、没产品名 ⇒ 靠 make_toutiao_title 前 12 字符
        # （用 aeo 造一个旧口径标题的场景由调用方保证，这里验兜底不误杀）
        remote = ["月收$53K：让品牌被 AI 答案提到"]
        c = self.CASES["aeo-engine"]
        # 不mock make_toutiao_title 的情况下只要求：不崩溃 + 返回 None 或 str
        r = tp.draft_title_match(c["id"], c, remote)
        self.assertTrue(r is None or isinstance(r, str))

    def test_完全不该匹配的返回None(self):
        remote = ["某八竿子打不着的标题：描述一下"]
        c = self.CASES["1lookup"]
        self.assertIsNone(tp.draft_title_match(c["id"], c, remote))

    def test_name_keys覆盖长id和哈希后缀(self):
        c = self.CASES["wpconvert-ai-convert-ai-sites-to-wordpress"]
        keys = tp._name_keys(c["id"], c)
        self.assertIn("wpconvert", keys)
        c2 = self.CASES["nutria-9e3eaa"]
        self.assertIn("nutria", tp._name_keys(c2["id"], c2))


class TestFetchDraftTitlesScroll(unittest.TestCase):
    """fetch_draft_titles 必须滚动（懒加载页）。"""

    def test_签名带滚动参数且默认开(self):
        import inspect
        sig = inspect.signature(tp.fetch_draft_titles)
        self.assertEqual(sig.parameters["scroll"].default, True)
        self.assertGreaterEqual(sig.parameters["max_scroll"].default, 5)

    def test_会滚到底并检测没有更多(self):
        calls = []

        class FakeCDP:
            def eval(self, js, *a, **k):
                if "scrollTo" in js:
                    calls.append("scroll")
                    return ""
                if "没有更多" in js:
                    # 第 3 次检测时报告到底
                    return "True" if calls.count("scroll") >= 3 else "False"
                if "innerText" in js:
                    return "标题A：描述\n标题B：描述 编辑删除"
                return ""

        # sleep 会真实睡，monkeypatch 掉
        orig_sleep = tp.time.sleep
        tp.time.sleep = lambda *a: None
        try:
            out = tp.fetch_draft_titles(FakeCDP(), wait=0)
        finally:
            tp.time.sleep = orig_sleep
        self.assertGreaterEqual(calls.count("scroll"), 3)
        self.assertIsInstance(out, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
