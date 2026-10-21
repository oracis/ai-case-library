# -*- coding: utf-8 -*-
"""xhs_publish「草稿箱 → 发布」纯函数测试。

判据三条铁律：量的是**用户看到的**还是内部表示、是真失败还是没数据、
多做了还是违规了。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as x

#: 用户 2026-10-20 从 devtools 拍的真实按钮
REAL_BTN = {"tag": "button", "cls": "ce-btn bg-red", "text": "发布",
            "attr": {"aria-busy": "false", "aria-disabled": "false"}}

#: 左侧导航「发布笔记」—— 老策略就是被它骗的，class 里没有 bg-red
NAV_BTN = {"tag": "button", "cls": "nav-item el-btn", "text": "发布笔记",
           "attr": {}}

#: node 里跑 JS_PUBLISH_BTN 的最小 DOM 桩。量的是 JS 真正的行为，不是源码文本。
#: querySelectorAll 真按 tag + class 过滤 —— 不然就成了「用选择器验证选择器」
#: 的循环论证，页面改 class 了测试还绿。
_JS_HARNESS = """
var CLICKED = [];
function makeBtn(o){
  return {
    tag: o.tag || 'button',
    cls: o.cls || '',
    innerText: o.text || '',
    src: o.src || '',
    naturalWidth: (o.nw === undefined ? 0 : o.nw),
    _a: o.attr || {},
    getAttribute: function(k){ return (k in this._a) ? this._a[k] : null; },
    getBoundingClientRect: function(){
      return {x:(o.x===undefined?800:o.x), y:(o.y===undefined?600:o.y),
              width:(o.w===undefined?80:o.w), height:(o.h===undefined?32:o.h)};
    },
    scrollIntoView: function(){},
    parentElement: null,
    dispatchEvent: function(){ CLICKED.push(this.innerText); },
    click: function(){ CLICKED.push(this.innerText); }
  };
}
function matchOne(b, sel){
  var parts = sel.split('.');
  var tag = parts.shift().trim();
  if (tag && b.tag !== tag) return false;
  return parts.every(function(c){ return b.cls.split(' ').indexOf(c) >= 0; });
}
function matches(b, sel){
  return sel.split(',').some(function(s){ return matchOne(b, s.trim()); });
}
var MouseEvent = function(t, opt){ this.type = t; this.opt = opt; };
var ALL = __BTNS__.map(makeBtn);
var document = {querySelectorAll: function(sel){
  return ALL.filter(function(b){ return matches(b, sel); });
}};
console.log(JSON.stringify({r: (__JS__), clicked: CLICKED}));
"""


def _run_js(btns, js=None):
    """把元素描述喂给一段页面 JS，返回 (返回值, 被点过的元素文本集合)。

    没有 node 就**明说跳过**（静默 pass 是另一种假绿）。
    """
    node = shutil.which("node")
    if not node:
        return None, None
    src = (_JS_HARNESS.replace("__BTNS__", json.dumps(btns))
                      .replace("__JS__", js or x.JS_PUBLISH_BTN))
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(src)
        p = subprocess.run([node, path], capture_output=True, text=True,
                           encoding="utf-8")
        if p.returncode != 0:
            raise AssertionError("node 跑这段 JS 失败：%s" % p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        return out["r"], set(out["clicked"])
    finally:
        os.unlink(path)


#: `<xhs-publish-btn>` 的正常属性（2026-10-20 实测）
HOST_OK = {"is-publish": "true", "is-save-draft": "true", "submit-text": "发布",
           "save-text": "暂存离开", "submit-disabled": "false",
           "submit-loading": "false", "save-disabled": "false"}

#: CDP `DOM.getDocument(pierce=True)` 的返回里，发布按钮挂在 shadow root 下
TREE_WITH_BTN = {"nodeName": "#document", "nodeId": 1, "children": [
    {"nodeName": "XHS-PUBLISH-BTN", "nodeId": 3,
     "shadowRoots": [{"nodeName": "#shadow-root", "nodeId": 4, "children": [
         {"nodeName": "BUTTON", "nodeId": 41,
          "attributes": ["class", "ce-btn white"]},
         {"nodeName": "BUTTON", "nodeId": 42,
          "attributes": ["class", "ce-btn bg-red"]}]}]}]}

#: shadow root 里只有「暂存离开」，没有发布按钮
TREE_NO_BTN = {"nodeName": "#document", "nodeId": 1, "children": [
    {"nodeName": "XHS-PUBLISH-BTN", "nodeId": 3,
     "shadowRoots": [{"nodeName": "#shadow-root", "nodeId": 4, "children": [
         {"nodeName": "BUTTON", "nodeId": 41,
          "attributes": ["class", "ce-btn white"]}]}]}]}


class _FakeSub(object):
    """假的 CDP 会话：eval 按顺序吐预设值，send 按 method 给固定回应。"""

    def __init__(self, seq, tree=None, quads=None):
        self.seq = list(seq)
        self.tree = TREE_WITH_BTN if tree is None else tree
        self.quads = [[100, 200, 140, 200, 140, 220, 100, 220]] \
            if quads is None else quads
        self.calls = []

    def eval(self, expr, refresh_context=False):
        self.calls.append(expr)
        if "innerWidth" in expr:
            return 1080                 # 坐标兜底要读视口尺寸
        if "innerHeight" in expr:
            return 676
        return self.seq.pop(0) if self.seq else "none"

    def send(self, method, params=None):
        self.calls.append(method)
        if method == "DOM.getDocument":
            return {"result": {"root": self.tree}}
        if method == "DOM.getContentQuads":
            return {"result": {"quads": self.quads}}
        return {"result": {}}


def _d(title, saved, raw=None):
    return {"title": title, "saved": saved, "raw": raw or title}


DRAFTS = [
    _d("Bustem：帮电商品牌全网打假", "2026-10-19 01:36:44"),
    _d("CheckVibe：扫描 vibe-coded 应用漏洞", "2026-10-10 01:15:01"),
    _d("AutoReels：素材一键剪成短视频", "2026-10-05 20:16:34"),
]


class TestPickDraft(unittest.TestCase):
    def test_默认取最久(self):
        d, why = x.pick_draft(DRAFTS)
        self.assertEqual(d["title"], "AutoReels：素材一键剪成短视频")
        self.assertIn("最久", why)

    def test_空箱返回None而不是抛(self):
        d, why = x.pick_draft([])
        self.assertIsNone(d)
        self.assertIn("空", why)

    def test_关键词命中(self):
        d, why = x.pick_draft(DRAFTS, keyword="CheckVibe")
        self.assertEqual(d["title"], "CheckVibe：扫描 vibe-coded 应用漏洞")

    def test_关键词没命中要说清楚不是空箱(self):
        d, why = x.pick_draft(DRAFTS, keyword="不存在的产品")
        self.assertIsNone(d)
        self.assertIn("不存在的产品", why)

    def test_标题精确匹配(self):
        d, _ = x.pick_draft(DRAFTS, title="CheckVibe：扫描 vibe-coded 应用漏洞")
        self.assertIn("CheckVibe", d["title"])

    def test_标题归一后匹配_容忍空格标点(self):
        d, _ = x.pick_draft(DRAFTS, title="CheckVibe：扫描vibe-coded应用漏洞")
        self.assertIn("CheckVibe", d["title"])

    def test_关键词限定后再取最久(self):
        pool = [_d("A产品 甲", "2026-10-01 10:00:00", raw="X甲"),
                _d("A产品 乙", "2026-10-09 10:00:00", raw="X乙")]
        d, _ = x.pick_draft(pool, keyword="X")
        self.assertEqual(d["title"], "A产品 甲")


class TestImgVerdict(unittest.TestCase):
    def test_全好就放行(self):
        v, msg = x.img_verdict(5, 0)
        self.assertEqual(v, "ok")
        self.assertIn("5", msg)

    def test_有裂图默认拦下_发裂图是事故(self):
        v, msg = x.img_verdict(15, 10)
        self.assertEqual(v, "abort")
        self.assertIn("10", msg)

    def test_加fix才进修复分支(self):
        self.assertEqual(x.img_verdict(15, 10, fix=True)[0], "fix")

    def test_零图也算ok_不发图是允许的(self):
        self.assertEqual(x.img_verdict(0, 0)[0], "ok")


class TestUrlPublished(unittest.TestCase):
    def test_草稿发布落在published参数(self):
        self.assertTrue(
            x.url_published("https://creator.xiaohongshu.com/publish/publish"
                            "?target=draft&published=true"))

    def test_新建发布落在success页(self):
        self.assertTrue(
            x.url_published("https://creator.xiaohongshu.com/publish/success"))

    def test_没变的URL不算(self):
        self.assertFalse(
            x.url_published("https://creator.xiaohongshu.com/publish/publish"
                            "?target=draft"))

    def test_空值不炸(self):
        self.assertFalse(x.url_published(None))
        self.assertFalse(x.url_published(""))


class TestEvaluateSend(unittest.TestCase):
    def test_没点到就是failed_不是unverified(self):
        r = x.evaluate_send(False, True, True)
        self.assertEqual(r["status"], "failed")

    def test_回读命中即算发布_URL只是旁证(self):
        r = x.evaluate_send(True, False, True)
        self.assertEqual(r["status"], "published")

    def test_两条都成立(self):
        self.assertEqual(x.evaluate_send(True, True, True)["status"], "published")

    def test_URL变了但回读没有_不敢记published(self):
        r = x.evaluate_send(True, True, False)
        self.assertEqual(r["status"], "unverified")
        self.assertIn("人工核对", r["reason"])

    def test_点了但毫无信号(self):
        self.assertEqual(x.evaluate_send(True, False, False)["status"],
                         "unverified")


class TestPublishBtnJs(unittest.TestCase):
    """发布按钮定位：用 node 真跑那一段 JS（2026-10-20 实测 DOM）。

    之前三套策略全扑空，是因为按「innerText 含『发布』」搜出来 32 个全是
    左侧导航。这组测试守的就是「别再点错地方」。
    """

    def _skip_no_node(self, r):
        if r is None:
            self.skipTest("本机没有 node，发布按钮 JS 未验证（不是通过）")

    def test_真实按钮点得到(self):
        r, clicked = _run_js([REAL_BTN])
        self._skip_no_node(r)
        self.assertEqual(r, "ok")
        self.assertEqual(clicked, {"发布"})

    def test_只有左侧导航时认怂_不误点(self):
        r, clicked = _run_js([NAV_BTN])
        self._skip_no_node(r)
        self.assertEqual(r, "none")
        self.assertEqual(clicked, set())

    def test_按钮灰着不点_并且明确报disabled(self):
        r, clicked = _run_js([{"cls": "ce-btn bg-red", "text": "发布",
                               "attr": {"aria-disabled": "true"}}])
        self._skip_no_node(r)
        self.assertEqual(r, "disabled")
        self.assertEqual(clicked, set())

    def test_正在提交中报busy(self):
        r, clicked = _run_js([{"cls": "ce-btn bg-red", "text": "发布",
                               "attr": {"aria-busy": "true"}}])
        self._skip_no_node(r)
        self.assertEqual(r, "busy")
        self.assertEqual(clicked, set())

    def test_红按钮有多个时只点发布那个(self):
        r, clicked = _run_js([{"cls": "ce-btn bg-red", "text": "预览",
                               "attr": {}},
                              {"cls": "ce-btn bg-red", "text": "存草稿",
                               "attr": {}},
                              REAL_BTN])
        self._skip_no_node(r)
        self.assertEqual(r, "ok")
        self.assertEqual(clicked, {"发布"})

    def test_红按钮文本不是发布_不点(self):
        r, clicked = _run_js([{"cls": "ce-btn bg-red", "text": "保存草稿",
                               "attr": {}}])
        self._skip_no_node(r)
        self.assertEqual(r, "not-publish")
        self.assertEqual(clicked, set())

    def test_隐藏按钮被过滤(self):
        r, _ = _run_js([{"cls": "ce-btn bg-red", "text": "发布", "attr": {},
                         "w": 0, "h": 0}])
        self._skip_no_node(r)
        self.assertEqual(r, "none")

    def test_导航和真按钮同时在_只认bg_red(self):
        r, clicked = _run_js([NAV_BTN, REAL_BTN, NAV_BTN])
        self._skip_no_node(r)
        self.assertEqual(r, "ok")
        self.assertEqual(clicked, {"发布"})


class TestImgHealth(unittest.TestCase):
    """图片体检：只数笔记图、按 src 去重。

    老版本 `querySelectorAll('img')` 通吃，把头像、活动卡片封面全数进来，
    6 条草稿数出一模一样的「15 张裂 10 张」—— 整齐得像常量，其实全是噪声。
    """

    IMG = {"tag": "img", "cls": "preview", "src": "https://x/1", "nw": 240,
           "w": 80, "h": 80}
    MIRROR = {"tag": "img", "cls": "preivew-image", "src": "https://x/1",
              "nw": 240, "w": 330, "h": 440}
    AVATAR = {"tag": "img", "cls": "user_avatar", "src": "https://x/avatar",
              "nw": 80, "w": 28, "h": 28}
    ACTIVITY = {"tag": "img", "cls": "activity-cover", "src": "https://x/act",
                "nw": 240, "w": 44, "h": 44}

    def _run(self, imgs):
        r, _ = _run_js(imgs, x.JS_IMG_HEALTH)
        if r is None:
            self.skipTest("本机没有 node，图片体检 JS 未验证（不是通过）")
        return json.loads(r)

    def test_缩略图和预览区是同一张图_只算一次(self):
        o = self._run([self.IMG, self.MIRROR])
        self.assertEqual(o["total"], 1)
        self.assertEqual(o["broken"], 0)

    def test_头像和活动卡片封面不算笔记图(self):
        o = self._run([self.AVATAR, self.ACTIVITY, self.IMG])
        self.assertEqual(o["total"], 1)

    def test_全裂都数出来(self):
        imgs = [dict(self.IMG, src="https://x/%d" % i, nw=0)
                for i in range(5)]
        o = self._run(imgs)
        self.assertEqual((o["total"], o["broken"]), (5, 5))

    def test_五张里裂两张(self):
        imgs = [dict(self.IMG, src="https://x/%d" % i,
                     nw=(0 if i < 2 else 240)) for i in range(5)]
        o = self._run(imgs)
        self.assertEqual((o["total"], o["broken"]), (5, 2))

    def test_太小的图忽略(self):
        o = self._run([dict(self.IMG, w=12, h=12)])
        self.assertEqual(o["total"], 0)

    def test_纯文字笔记返回零_不是报错(self):
        o = self._run([])
        self.assertEqual(o["total"], 0)
        self.assertEqual(o["broken"], 0)

    def test_裂图会留样例src(self):
        o = self._run([dict(self.IMG, src="https://cdn/broken", nw=0)])
        self.assertTrue(o["bad"])
        self.assertIn("cdn/broken", o["bad"][0])


class TestPublishComponentState(unittest.TestCase):
    """发布按钮「能不能点」看宿主组件属性 —— 不用穿透 shadow 就能读。"""

    OK = {"is-publish": "true", "is-save-draft": "true", "submit-text": "发布",
          "save-text": "暂存离开", "submit-disabled": "false",
          "submit-loading": "false", "save-disabled": "false"}

    def test_全绿就是可点(self):
        v, why = x.publish_component_state(self.OK)
        self.assertEqual(v, "ok")
        self.assertIn("发布", why)

    def test_提交中算busy_别重复点(self):
        v, _ = x.publish_component_state(dict(self.OK,
                                              **{"submit-loading": "true"}))
        self.assertEqual(v, "busy")

    def test_禁用时不点(self):
        v, why = x.publish_component_state(dict(self.OK,
                                                **{"submit-disabled": "true"}))
        self.assertEqual(v, "disabled")
        self.assertIn("校验", why)

    def test_组件没开发布就不给发(self):
        v, _ = x.publish_component_state(dict(self.OK, **{"is-publish": "false"}))
        self.assertEqual(v, "no-publish")

    def test_组件不存在要说清楚_不能当可点(self):
        v, why = x.publish_component_state({})
        self.assertEqual(v, "unknown")
        self.assertIn("xhs-publish-btn", why)

    def test_属性缺失按不可点处理(self):
        self.assertEqual(x.publish_component_state({"is-publish": "true"})[0],
                         "unknown")


class TestFindShadowButton(unittest.TestCase):
    """从 CDP `DOM.getDocument(pierce=True)` 的树里挖 shadow 里的按钮。

    真实结构（2026-10-20）：XHS-PUBLISH-BTN > #shadow-root >
    [BUTTON.ce-btn.white（暂存离开）, BUTTON.ce-btn.bg-red（发布）]
    """

    def _tree(self, shadow_btns, host="XHS-PUBLISH-BTN"):
        return {"nodeName": "#document", "nodeId": 1, "children": [
            {"nodeName": "DIV", "nodeId": 2, "children": [
                {"nodeName": host, "nodeId": 3, "attributes": [
                    "is-publish", "true", "submit-text", "发布"],
                 "shadowRoots": [{"nodeName": "#shadow-root", "nodeId": 4,
                                  "children": shadow_btns}]},
            ]}]}

    RED = {"nodeName": "BUTTON", "nodeId": 42,
           "attributes": ["type", "button", "class", "ce-btn bg-red",
                          "aria-disabled", "false"]}
    WHITE = {"nodeName": "BUTTON", "nodeId": 41,
             "attributes": ["type", "button", "class", "ce-btn white"]}

    def test_挖到红色发布按钮(self):
        t = self._tree([self.WHITE, self.RED])
        self.assertEqual(x.find_shadow_button(t), 42)

    def test_只认红色_不认暂存离开(self):
        t = self._tree([self.WHITE])
        self.assertIsNone(x.find_shadow_button(t))

    def test_light_DOM里的同名按钮不算_要shadow里的(self):
        t = {"nodeName": "DIV", "nodeId": 9, "children": [
            {"nodeName": "BUTTON", "nodeId": 7,
             "attributes": ["class", "ce-btn bg-red"]}]}
        self.assertIsNone(x.find_shadow_button(t))

    def test_宿主不存在返回None(self):
        t = self._tree([self.RED], host="XHS-OTHER-BTN")
        self.assertIsNone(x.find_shadow_button(t))

    def test_空树不炸(self):
        self.assertIsNone(x.find_shadow_button({}))
        self.assertIsNone(x.find_shadow_button(None))


class TestQuadCenter(unittest.TestCase):
    def test_正常四边形取中心(self):
        c = x.quad_center([100, 200, 140, 200, 140, 220, 100, 220])
        self.assertEqual((c["x"], c["y"]), (120, 210))
        self.assertEqual((c["w"], c["h"]), (40, 20))

    def test_缺数据返回None_不给假坐标(self):
        self.assertIsNone(x.quad_center([]))
        self.assertIsNone(x.quad_center(None))
        self.assertIsNone(x.quad_center([1, 2, 3]))


class TestClickPublishFallback(unittest.TestCase):
    """_click_publish 的策略选择：主策略（穿透 shadow）命中就不许再往下戳。"""

    def _host(self, **over):
        d = dict(HOST_OK)
        d.update(over)
        return {"host": d, "shadow": "closed",
                "rect": {"x": 323, "y": 817, "w": 680, "h": 90}}

    def test_主策略命中就返回并记下策略名(self):
        s = _FakeSub([self._host()])
        name, clicked = x._click_publish(s)
        self.assertTrue(clicked)
        self.assertIn("shadow-publish-btn", name)
        self.assertIn("DOM.getDocument", s.calls)     # 真去 pierce 了
        self.assertIn("Input.dispatchMouseEvent", s.calls)

    def test_点的是红色发布按钮_不是暂存离开(self):
        s = _FakeSub([self._host()])
        x._click_publish(s)
        # 坐标来自 getContentQuads，quad 中心 (120,210)
        self.assertIn("(120,210)", x._click_publish(_FakeSub([self._host()]))[0])

    def test_灰按钮返回未点到_好让上层中止(self):
        s = _FakeSub([self._host(**{"submit-disabled": "true"})])
        name, clicked = x._click_publish(s)
        self.assertFalse(clicked)
        self.assertEqual(name, "btn-disabled")
        self.assertNotIn("Input.dispatchMouseEvent", s.calls)  # 一下都没点

    def test_组件没开发布就别点(self):
        s = _FakeSub([self._host(**{"is-publish": "false"})])
        name, clicked = x._click_publish(s)
        self.assertFalse(clicked)
        self.assertEqual(name, "btn-no-publish")
        self.assertNotIn("Input.dispatchMouseEvent", s.calls)

    def test_提交中不再点第二次(self):
        s = _FakeSub([self._host(**{"submit-loading": "true"})])
        name, clicked = x._click_publish(s)
        self.assertTrue(clicked)
        self.assertIn("busy", name)
        self.assertNotIn("Input.dispatchMouseEvent", s.calls)

    def test_组件说能点但pierce不到坐标_退老兜底(self):
        s = _FakeSub([self._host(), "ok"], tree=TREE_NO_BTN)
        name, clicked = x._click_publish(s)
        self.assertTrue(clicked)
        self.assertEqual(name, "dom-text")

    def test_组件不在才退老兜底(self):
        s = _FakeSub([{"host": None}, "ok"])
        self.assertEqual(x._click_publish(s), ("dom-text", True))

    def test_全扑空落到坐标兜底(self):
        s = _FakeSub([{"host": None}, "none", "none"])
        name, clicked = x._click_publish(s)
        self.assertTrue(name.startswith("coord"), name)
        self.assertTrue(clicked)


class TestCliRegistered(unittest.TestCase):
    def test_send_draft子命令存在(self):
        # 量的是人看到的东西：CLI 里真有这个子命令
        import subprocess
        p = subprocess.run([sys.executable, "xhs_publish.py", "send-draft",
                            "--help"], capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("send-draft", p.stdout)
        self.assertIn("--dry", p.stdout)


if __name__ == "__main__":
    unittest.main()
