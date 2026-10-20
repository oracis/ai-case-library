# -*- coding: utf-8 -*-
"""xhs_draft_fix_images 的纯函数 + 会话时序测试。

判据三条铁律（2026-10-19 定的）：量的是**用户看到的**还是内部表示、
是真失败还是没数据、多做了还是违规了。

本文件两条重点：
  1. 计划层（要不要修、本地图够不够）是纯逻辑，直接测。
  2. 删图的**时序**是踩出来的坑（close-btn 平时 visibility:hidden，
     不 hover 就点不中）——用假 CDP 会话看它有没有先 hover 再点。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_draft_fix_images as F


# ---------------------------------------------------------------- 假 CDP
class _FakeSub(object):
    """假的 CDP 会话：按顺序吐预设的 JS 返回值，并记下所有调用。

    `img_seq` 是「每次 eval 返回几张图」（None 表示用 JS 分支默认：无图）。
    """

    def __init__(self, img_counts=None, close_at=None):
        self.img_counts = list(img_counts or [])
        self.close_at = close_at or {"cx": 626, "cy": 139, "vis": "visible",
                                     "at": "DIV.close-btn", "onBtn": True}
        self.calls = []          # 所有 eval / send 的时序记录（expr 存全文）
        self._last = 0           # 预设值取完后**停在最后那个**（别假装图没了）

    def eval(self, expr, refresh_context=False):
        self.calls.append(("eval", expr))
        if "elementFromPoint" in expr:            # 先判它：取点的 JS 里也有
            return json.dumps(self.close_at)      # querySelectorAll('.img-container')
        if "document.querySelectorAll('.img-container')" in expr:
            if self.img_counts:
                self._last = self.img_counts.pop(0)
            return [{"x": 586, "y": 179, "cx": 626, "cy": 139,
                     "nw": 0}] * self._last
        if "elementFromPoint" in expr:
            return json.dumps(self.close_at)
        return 0

    def send(self, method, params=None):
        self.calls.append(("send", method, params or {}))


def _session(img_counts=None, close_at=None):
    s = F.Session()
    s.sub = _FakeSub(img_counts, close_at)
    return s


def _first_index(pred, calls):
    return next((i for i, c in enumerate(calls) if pred(c)), None)


# ---------------------------------------------------------------- 纯函数
class TestNeedRepair(unittest.TestCase):
    def test_全裂要修(self):
        self.assertTrue(F.need_repair(5, 5))

    def test_部分裂也要修(self):
        self.assertTrue(F.need_repair(5, 2))

    def test_图都好着不修(self):
        self.assertFalse(F.need_repair(5, 0))

    def test_本来就没图不归它管(self):
        """没图是创作问题，不是签名过期 —— 不修，也不算 blocked。"""
        self.assertFalse(F.need_repair(0, 0))

    def test_force才重传好图(self):
        self.assertTrue(F.need_repair(5, 0, force=True))


class TestResolveLocal(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for i in (1, 2):
            with open(os.path.join(self.d, "card-%d.png" % i), "wb") as f:
                f.write(b"\x89PNG")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_齐全(self):
        ok, miss = F.resolve_local(self.d, ["card-1.png", "card-2.png"])
        self.assertEqual(len(ok), 2)
        self.assertEqual(miss, [])
        self.assertTrue(all(os.path.isabs(p) for p in ok))

    def test_缺一张要报出来(self):
        ok, miss = F.resolve_local(self.d, ["card-1.png", "card-3.png"])
        self.assertEqual(len(ok), 1)
        self.assertEqual(miss, ["card-3.png"])

    def test_空清单不炸(self):
        self.assertEqual(F.resolve_local(self.d, []), ([], []))


class TestPlanRepair(unittest.TestCase):
    ROW = {"title": "T", "cid": "c", "total": 5, "broken": 5,
           "local": ["a.png"] * 5}

    def test_裂图且有本地图才动手(self):
        r = F.plan_repair([dict(self.ROW)])[0]
        self.assertEqual(r["action"], "repair")
        self.assertIn("5/5", r["reason"])

    def test_图正常就跳过(self):
        r = F.plan_repair([dict(self.ROW, broken=0)])[0]
        self.assertEqual(r["action"], "skip")

    def test_找不到本地图是blocked不是skip(self):
        """「修不了」和「不用修」是两回事 —— 不能都叫 skip 混过去。"""
        r = F.plan_repair([dict(self.ROW, local=[])])[0]
        self.assertEqual(r["action"], "blocked")
        self.assertIn("缺本地图", r["reason"])

    def test_图删光了要补回来(self):
        """0 张图不是「没数据」，是上一轮失手 —— 必须补，不许 skip 过去。"""
        r = F.plan_repair([{"total": 0, "broken": 0, "local": ["a.png"]}])[0]
        self.assertEqual(r["action"], "repair")

    def test_正常但张数不同就别动(self):
        r = F.plan_repair([{"total": 3, "broken": 0, "local": ["a.png"] * 5}])[0]
        self.assertEqual(r["action"], "skip")


# ---------------------------------------------------------------- 会话时序
class TestImgHealth(unittest.TestCase):
    def test_按裂图数报数(self):
        s = _session(img_counts=[3])
        s.sub.calls.clear()
        total, broken = s.img_health()
        self.assertEqual((total, broken), (3, 3))     # 这个桩全是 nw=0

    def test_没图是0不是None(self):
        total, broken = _session(img_counts=[0]).img_health()
        self.assertEqual((total, broken), (0, 0))


def _info(nws):
    """nws=[(src, nw)] 或 [nw] —— 后者自动生成 src。"""
    out = []
    for k, v in enumerate(nws):
        src, nw = (v if isinstance(v, (tuple, list)) else ("card-%d.png" % k, v))
        out.append({"src": src, "nw": nw, "x": 0, "y": 0, "cx": 0, "cy": 0})
    return out


class TestBrokenSrcs(unittest.TestCase):
    def test_只把nw0的记进黑名单(self):
        got = F.broken_srcs(_info([("old1.png", 0), ("new.png", 2160)]))
        self.assertEqual(got, {"old1.png"})

    def test_刚传上去正在处理的图不算坏(self):
        """这是把 Podawaa 删成 0 张的那个坑 —— 名单必须**体检时**就钉死。"""
        got = F.broken_srcs(_info([("uploading.png", 0)]))
        self.assertEqual(got, {"uploading.png"})   # 体检那一刻它确实是 0
        later = _info([("uploading.png", 2160)])   # 同一份 src 加载好了
        self.assertEqual(F.broken_srcs(later), set())   # 复查时它不算坏


class TestDeleteTargets(unittest.TestCase):
    """该删哪几张：只照 src 名单，不看位置 —— 别人插了图也删不错。"""

    def test_全裂且留够就全删(self):
        info = _info([("o1", 0), ("o2", 0), ("o3", 0), ("o4", 0), ("o5", 0),
                      ("n1", 9), ("n2", 9), ("n3", 9), ("n4", 9), ("n5", 9)])
        self.assertEqual(F.delete_targets(info, {"o1", "o2", "o3", "o4", "o5"},
                                          5), [0, 1, 2, 3, 4])

    def test_只删名单上的不动好图(self):
        info = _info([("n1", 9), ("o1", 0), ("n2", 9), ("o2", 0), ("n3", 9)])
        self.assertEqual(F.delete_targets(info, {"o1", "o2"}, 3), [1, 3])

    def test_留住keep就少删(self):
        """keep=3、总共 5 张 ⇒ 只能删 2 张：数量归零了就再也删不动了。"""
        info = _info([("o1", 0), ("o2", 0), ("o3", 0), ("o4", 0), ("o5", 0)])
        self.assertEqual(F.delete_targets(info, {"o1", "o2", "o3", "o4", "o5"},
                                          3), [0, 1])

    def test_名单对不上就一张不删(self):
        info = _info([("n1", 9), ("n2", 9)])
        self.assertEqual(F.delete_targets(info, {"o1"}, 2), [])


class TestDeleteOne(unittest.TestCase):
    """删图时序：必须先 hover 让按钮显形，再点（不然点到外层占位节点）。"""

    def _run(self, img_counts=(5, 4)):
        s = _session(img_counts=list(img_counts))
        s.sub.calls.clear()
        ok = s.delete_index(0)
        return ok, s.sub.calls

    def test_先甩鼠标再hover再点(self):
        ok, calls = self._run()
        self.assertTrue(ok)
        i_img = _first_index(lambda c: c[0] == "send" and
                             c[2].get("type") == "mouseMoved" and
                             c[2].get("x") == 586, calls)
        i_read = _first_index(lambda c: c[0] == "eval" and
                              "elementFromPoint" in c[1], calls)
        i_press = _first_index(lambda c: c[0] == "send" and
                               c[2].get("type") == "mousePressed", calls)
        self.assertIsNotNone(i_img)
        self.assertIsNotNone(i_read)
        self.assertIsNotNone(i_press)
        # ① 甩到角落（4,4）破 hover 死锁  ② hover 图上  ③ 取坐标  ④ 按下
        self.assertLess(i_img, i_read)
        self.assertLess(i_read, i_press)

    def test_按钮没显形就先别按(self):
        """`onBtn:false` 说明该点被外层节点挡着 —— 按下去就是误点。"""
        s = _session(img_counts=[5], close_at={"cx": 626, "cy": 139,
                                               "vis": "hidden",
                                               "at": "DIV.feature-guide-anchor",
                                               "onBtn": False})
        s.sub.calls.clear()
        ok = s.delete_index(0)
        self.assertFalse(ok)
        self.assertFalse(any(c[0] == "send" and c[2].get("type") == "mousePressed"
                             for c in s.sub.calls))

    def test_数量没降就不算删成功(self):
        """DOM 还在 = 图还在：**没删掉就是没删掉**，不许装作成功。"""
        ok, _ = self._run(img_counts=(5, 5))
        self.assertFalse(ok)


# ---------------------------------------------------------------- JS 取点
_NODE_HARNESS = """
var RECTS = __RECTS__;              // [{item:[x,y,w,h], close:[x,y,w,h]}]
function mk(cls, rect){ return {
  tagName:'DIV', className:cls, innerText:'',
  getBoundingClientRect:function(){return {x:rect[0],y:rect[1],
      width:rect[2],height:rect[3]};},
  contains:function(){ return false; },
  querySelector:function(sel){
    if(sel.indexOf('img.preview')>=0) return {naturalWidth:0};
    if(sel.indexOf('close-btn')>=0) return CB;
    return null;}
}; }
var CB = mk('close-btn hoverShow', RECTS.close);
var OUTER = mk('feature-guide-anchor', [0,0,10,10]);
var FIRST = RECTS.item;
function inside(r,x,y){
  return x>=r[0] && x<=r[0]+r[2] && y>=r[1] && y<=r[1]+r[3];
}
document = { querySelectorAll: function(sel){
  return sel.indexOf('.img-container')>=0 ? [mk('img-container', FIRST)] : [];
}, querySelector: function(sel){
  return sel.indexOf('.img-container')>=0 ? mk('img-container', FIRST) : null;
}, elementFromPoint: function(x,y){
  return inside(RECTS.close,x,y) ? CB : OUTER;
} };
window = { getComputedStyle: function(){ return {visibility:'visible'}; } };
getComputedStyle = window.getComputedStyle;
// ⚠ 必须**带参调用**：真机上 delete_index 是 "(JS)(idx)" 这种形式。
//   2026-10-20 的真 bug 就藏在这儿 —— JS_CLOSE_AT 末尾多了自调用 `()`，
//   拼成 "(fn)()(0)" 变成拿 0 去调字符串，eval 抛 TypeError 返回 None，
//   单测因为内插成自求值表达式而全绿，真机却一张图都删不掉。
console.log(JSON.stringify({info:(__JS1__), at:(__JS2__)(0)}));
"""


def _run_node(rects, js1, js2):
    node = shutil.which("node")
    if not node:
        return None
    src = (_NODE_HARNESS.replace("__RECTS__", json.dumps(rects))
                        .replace("__JS1__", js1).replace("__JS2__", js2))
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(src)
        p = subprocess.run([node, path], capture_output=True, text=True,
                           encoding="utf-8")
        if p.returncode != 0:
            raise AssertionError("node 跑这段 JS 失败：%s" % p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        os.unlink(path)


class TestJsContracts(unittest.TestCase):
    """页面 JS 的契约：**真的按 DOM 取点**，别拿源码文本自证。"""

    def test_取到的中心点是几何中心不是角落(self):
        out = _run_node({"item": [545, 138, 82, 82], "close": [616, 129, 20, 20]},
                        F.JS_IMG_INFO, F.JS_CLOSE_AT)
        if out is None:
            self.skipTest("没装 node，跳过页面 JS 契约测试")
        # 页面 JS 返回的是 JSON 字符串（浏览器里由 Session.js 解析），这里也解析
        info = json.loads(out["info"]) if isinstance(out["info"], str) \
            else out["info"]
        at = json.loads(out["at"]) if isinstance(out["at"], str) else out["at"]
        self.assertEqual(info[0]["cx"], 626)
        self.assertEqual(info[0]["cy"], 139)
        self.assertEqual(info[0]["x"], 586)    # 545+82/2
        self.assertTrue(at["onBtn"])


class TestReplaceImages(unittest.TestCase):
    """换图记账：上一轮传过的好图必须认账，只补差额。

    2026-10-20 试点：删图 bug 导致跑了一半（传了 5 张好图、裂图没删成），
    修好删图后重跑——不认账就会再传 5 张，草稿里 15 张图。
    """

    def _run(self, info0, paths):
        s = F.Session()
        uploads, state = [], {"info": [dict(i) for i in info0]}

        s.img_info = lambda: [dict(i) for i in state["info"]]
        s.upload = lambda ps, gap=0: (uploads.extend(ps),
                                      [state["info"].append(
                                          {"src": "new-%s" % os.path.basename(p),
                                           "nw": 9, "x": 0, "y": 0,
                                           "cx": 0, "cy": 0}) for p in ps],
                                      (list(ps), []))[-1]
        s.delete_index = lambda idx, tries=3: (
            state["info"].pop(idx) if idx < len(state["info"]) else False) and True
        s.wait_new_loaded = lambda n, **k: True
        s.wait_new_saved = lambda n, **k: True
        out = s.replace_images(paths)
        return out, uploads, state["info"]

    def _info(self, n_bad, n_good):
        out = [{"src": "bad%d.png" % i, "nw": 0, "x": 0, "y": 0,
                "cx": 0, "cy": 0} for i in range(n_bad)]
        out += [{"src": "good%d.png" % i, "nw": 9, "x": 0, "y": 0,
                 "cx": 0, "cy": 0} for i in range(n_good)]
        return out

    def test_重跑时认账不再多传(self):
        out, uploads, info = self._run(self._info(5, 5), ["a.png"] * 5)
        self.assertEqual(uploads, [])          # 好图已够，一张不多传
        self.assertEqual(len(info), 5)         # 裂图删完剩 5 张好图
        self.assertEqual((out["total"], out["broken"]), (5, 0))

    def test_全新草稿全量传(self):
        out, uploads, info = self._run(self._info(5, 0), ["a.png"] * 5)
        self.assertEqual(len(uploads), 5)
        self.assertEqual((out["total"], out["broken"]), (5, 0))

    def test_部分好图只补差额(self):
        out, uploads, info = self._run(self._info(3, 2), ["a.png"] * 5)
        self.assertEqual(len(uploads), 3)
        self.assertEqual((out["total"], out["broken"]), (5, 0))


class TestWaitNewSaved(unittest.TestCase):
    """（保留）blob: 永远不会换正式地址 —— 不能当保存判据。"""

    @staticmethod
    def _session(seq):
        s = F.Session()
        state = {"seq": list(seq)}
        s.img_info = lambda: (state["seq"].pop(0) if len(state["seq"]) > 1
                              else state["seq"][0])
        return s

    def test_一直blob就超时报否(self):
        s = self._session([
            [{"src": "blob:https://creator.xhscdn.com/abc", "nw": 9}],
        ])
        self.assertFalse(s.wait_new_saved(1, tries=3, gap=0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
