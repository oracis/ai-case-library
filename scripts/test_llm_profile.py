# -*- coding: utf-8 -*-
"""llm_profile 读取器测试。

为什么补这个文件
----------------
`llm_profile.py` 接手的是「**核验流程用哪个模型**」这个决策 ——
选错不会立刻报错，会让每一条核验都失败、而日志显示「ok=true 已选中」。
这类 bug 只靠肉眼 review抓不住，必须钉死。

最关键的一条：**`ok=true` 不等于能用**。
free-llm-probe v2.0 起档案带五档时效，`ok=true` + `freshness=expired`
表示「上次能用、现已连挂 ≥3 次或超 7 天没成功」。
只判 `ok` 会挑中这种条目，然后每次调用都 401/5xx。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm_profile as LP# noqa: E402


def _p(model, ok=True, freshness="fresh", **kw):
    d = {"id": "src/" + model, "source": "src", "base": "https://x/v1",
         "model": model, "auth": "", "no_key": True, "ok": ok,
         "reason": "OK" if ok else "AUTH"}
    d.update(kw)
    if freshness is not None:
        d["freshness"] = freshness
    return d


def _payload(*profiles, **kw):
    d = {"generated_at": "2026-10-01T18:00:00", "profiles": list(profiles)}
    d.update(kw)
    return d


class UsableTest(unittest.TestCase):

    def test_过期条目不被选中(self):
        """核心回归：ok=true + expired 必须排除。"""
        pl = _payload(_p("fresh-one"), _p("dead-one", freshness="expired"))
        got = LP.pick(pl)
        self.assertEqual(got["model"], "fresh-one")
        self.assertNotIn("src/dead-one", LP.list_usable(pl))

    def test_全部过期时报错要指向重跑探测(self):
        pl = _payload(_p("a", freshness="expired"),
                      _p("b", freshness="expired"))
        with self.assertRaises(RuntimeError) as cm:
            LP.pick(pl)
        msg = str(cm.exception)
        # 报错必须区分「全挂」与「有但过期」—— 处置完全不同
        self.assertIn("过期", msg)
        self.assertIn("probe.py", msg)

    def test_全挂的报错保持原措辞(self):
        with self.assertRaises(RuntimeError) as cm:
            LP.pick(_payload(_p("a", ok=False), _p("b", ok=False)))
        self.assertIn("ok=true 的条目为 0", str(cm.exception))

    def test_显式允许过期时能取到(self):
        pl = _payload(_p("dead", freshness="expired"))
        got = LP.pick(pl, allow_expired=True)
        self.assertEqual(got["model"], "dead")

    def test_非过期档全部放行(self):
        for f in ("fresh", "stale", "aging", "unknown"):
            pl = _payload(_p("m", freshness=f))
            self.assertEqual(LP.pick(pl)["model"], "m", "被误拒: " + f)

    def test_老档案无freshness字段照常可用(self):
        """v1.0 档案没有这个字段，不能因此全废。"""
        pl = _payload(_p("m", freshness=None))
        self.assertEqual(LP.pick(pl)["model"], "m")

    def test_非字典条目不炸(self):
        pl = _payload(_p("good"), "坏条目", None, 123)
        self.assertEqual(LP.pick(pl)["model"], "good")


class MatchTest(unittest.TestCase):

    def test_按完整id精确匹配(self):
        pl = _payload(_p("a"), _p("b"))
        self.assertEqual(LP.pick(pl, "src/b")["model"], "b")

    def test_只写模型名(self):
        pl = _payload(_p("a"), _p("b"))
        self.assertEqual(LP.pick(pl, "b")["model"], "b")

    def test_同名多源要求写全id(self):
        pl = _payload(_p("m", source="s1"),
                      _p("m", source="s2"))
        pl["profiles"][0]["id"] = "s1/m"
        pl["profiles"][1]["id"] = "s2/m"
        with self.assertRaises(RuntimeError) as cm:
            LP.pick(pl, "m")
        self.assertIn("s1/m", str(cm.exception))

    def test_找不到时报错列出可用id(self):
        pl = _payload(_p("only-one"))
        with self.assertRaises(RuntimeError) as cm:
            LP.pick(pl, "nope")
        self.assertIn("only-one", str(cm.exception))

    def test_过期条目不出现在报错提示里(self):
        """提示里混进不可用 id，会让人去选一个注定失败的。"""
        pl = _payload(_p("good"), _p("dead", freshness="expired"))
        with self.assertRaises(RuntimeError) as cm:
            LP.pick(pl, "nope")
        self.assertNotIn("dead", str(cm.exception))


class DescribeTest(unittest.TestCase):

    def test_普通条目带时效(self):
        s = LP.describe(_p("m", freshness="fresh"))
        self.assertIn("fresh", s)

    def test_过期条目显式警告(self):
        s = LP.describe(_p("m", freshness="expired", last_ok_at="x"))
        self.assertIn("已过期", s)
        self.assertIn("x", s)


class ToArgsTest(unittest.TestCase):

    def test_免key返回空串而非None(self):
        """ai_verify 靠 `AI_KEY or ""` 走空 Bearer 分支。"""
        b, m, k = LP.to_llm_args(_p("m", no_key=True))
        self.assertEqual(k, "")
        self.assertEqual(b, "https://x/v1")
        self.assertEqual(m, "m")

    def test_缺字段不抛异常(self):
        b, m, k = LP.to_llm_args({})
        self.assertEqual((b, m, k), ("", "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
