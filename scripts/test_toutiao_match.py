# -*- coding: utf-8 -*-
"""toutiao_publish 匹配 / 滚动修复的回归锁。

背景（2026-10-16 P0 实测）：
- 头条草稿箱是懒加载：首屏 ~21 条，页面自报「共 48 条」，URL 分页
  参数无效 ⇒ fetch_draft_titles 不滚动就会读到残缺列表（首轮 16 条）。
- 三平台远端标题全是「产品名：描述」新口径，而 make_*_title 生成
  「月收$xK：…」旧口径 ⇒ 字面前 N 字符匹配大面积失配，必须产品名匹配。
"""
import json
import os
import sys
import inspect
from urllib.parse import parse_qs
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


class TestPrefixMatch(unittest.TestCase):
    """P1 实测漏判：远端标题用缩写/简称，子串匹配失效（2026-10-16）。"""

    def test_缩写标题能匹配上(self):
        # 「PromptMon：盯 AI 里的品牌」vs id=promptmonitor-io
        c = {"id": "promptmonitor-io", "name": "Promptmonitor.io"}
        t = tp.draft_title_match("promptmonitor-io", c,
                                 ["PromptMon：盯 AI 里的品牌"])
        self.assertIsNotNone(t)

    def test_简称去后缀能匹配上(self):
        # 「Divine：给站长卖网站挂件」vs id=divine-widgets
        c = {"id": "divine-widgets", "name": "Divine Widgets"}
        t = tp.draft_title_match("divine-widgets", c,
                                 ["Divine：给站长卖网站挂件"])
        self.assertIsNotNone(t)

    def test_前缀互补不许误伤(self):
        # 短词（<5 字符）不参与前缀互补，防「ai」命中一切
        c = {"id": "vid-ai", "name": "Vid.AI"}
        t = tp.draft_title_match("vid-ai", c,
                                 ["某品牌：让 AI 帮你做别的事"])
        self.assertIsNone(t)


class TestArticleApi(unittest.TestCase):
    """已发布图文列表 API 的纯函数部分（2026-10-16 P1 实证）。"""

    def test_url_构造(self):
        u = tp._article_api_url(10, 2)
        self.assertIn("offset=10", u)
        self.assertIn("%22page_index%22%3A%222%22", u)
        self.assertIn("/api/feed/mp_provider/v1/", u)
        # 第 1 页 offset=0
        self.assertIn("offset=0", tp._article_api_url(0, 1))

    def test_标题提取路径(self):
        item = {"assembleCell": {"itemCell": {"articleBase": {
            "title": "Bustem：帮电商品牌全网打假"}}}}
        self.assertEqual(tp._extract_article_title(item),
                         "Bustem：帮电商品牌全网打假")
        # 缺层不许炸
        self.assertEqual(tp._extract_article_title({}), "")
        self.assertEqual(tp._extract_article_title({"assembleCell": {}}), "")
        self.assertEqual(tp._extract_article_title(
            {"assembleCell": {"itemCell": {}}}), "")


class TestDeleteDraftApi(unittest.TestCase):
    """delete_draft 形态锁定（2026-10-16 P1 hook 抓包实证）。"""

    def test_body真实形态(self):
        b = json.loads(tp._delete_draft_api_body("7693148369283285538"))
        self.assertEqual(b, {"drafts": [
            {"draft_type": 2, "gid": "7693148369283285538"}]})

    def test_url(self):
        self.assertEqual(tp._delete_draft_api_url(),
                         "/mp/agw/creator_center/delete_draft?app_id=1231")

    def test_猜错的形态不许回来(self):
        # 猜过的错误形态：{"gid":...}（顶层就是 gid，服务端返 success 但不真删）
        d = json.loads(tp._delete_draft_api_body("123"))
        self.assertNotEqual(d, {"gid": "123"})
        self.assertIn("drafts", d)                 # 必须在 drafts 数组里
        self.assertEqual(d["drafts"][0]["gid"], "123")
        # form 形态也不行
        self.assertFalse(tp._delete_draft_api_body("123").startswith("gid="))


class TestSaveDraftApi(unittest.TestCase):
    """save_draft_api（2026-10-17 抓包实证）：form body + token 注入。"""

    def _cdp(self, resp='{"code":7050,"message":"保存失败"}'):
        class FakeCDP:
            js = ""

            def eval(self, js, **kw):
                self.js = js
                return resp
        return FakeCDP()

    def test_body是form不是json(self):
        b = T_url = tp._publish_draft_body("标题", "<p>正文</p>")
        q = parse_qs(b)
        self.assertEqual(q["save"][0], "0")        # 0 = 存草稿
        self.assertEqual(q["source"][0], "29")
        self.assertNotIn("pgc_id", q)              # 创建不带
        self.assertIn("pgc_id", parse_qs(
            tp._publish_draft_body("t", "c", pgc_id="9")))

    def test_无token不加header(self):
        cdp = self._cdp()
        tp.save_draft_api(cdp, "t", "<p>c</p>")
        self.assertNotIn("tt-anti-token", cdp.js)
        self.assertIn("form-urlencoded", cdp.js)

    def test_Accept头必须带(self):
        """回归：少 Accept 头会被 argus 风控挡成 HTML（实测）。"""
        cdp = self._cdp()
        tp.save_draft_api(cdp, "t", "<p>c</p>", anti_token="tok")
        self.assertIn("Accept", cdp.js)
        self.assertIn("application/json, text/plain, */*", cdp.js)

    def test_三头齐全(self):
        """真实请求头就 3 个：Accept / Content-Type / tt-anti-token。"""
        cdp = self._cdp()
        tp.save_draft_api(cdp, "t", "<p>c</p>", anti_token="tok")
        for h in ("Accept", "Content-Type", "tt-anti-token"):
            self.assertIn('"%s"' % h, cdp.js)

    def test_必须json转义不能裸塞中文(self):
        """🚨 回归：body 拼进 JS 必须 json.dumps 转义（纯 ASCII 源码）。

        实测（同会话同 token 同 body 的对照，唯一变量是拼法）：
          · 原始字符串替换（裸 UTF-8 中文进 Runtime.evaluate 表达式）
            → 🚫 argus 风控页
          · json.dumps 转义后拼                 → ✅ code:0
        裸中文会被 CDP 通道破坏，而 body 里 extra/search_creation_info
        等字段全是 JSON 串，一坏整篇废掉 ⇒ 症状伪装成「风控」。

        判据：拼出来的 JS 里不能出现原始中文（现在被 urlencode + json.dumps
        双重转义成 `%E4%B8%AD` 这种纯 ASCII 形态）。
        """
        cdp = self._cdp()
        tp.save_draft_api(cdp, "中文标题：冒号描述", "<p>中文正文</p>",
                          anti_token="tok")
        self.assertNotIn("中文标题", cdp.js)     # 必须被转义
        self.assertNotIn("中文正文", cdp.js)
        self.assertIn("%E4%B8%AD", cdp.js)      # 「中」的 percent-encoding
        # 且 headers 必须是 JSON 字面量（带引号），不是裸拼接
        self.assertIn('headers:{"Accept"', cdp.js)

    def test_有token必注入(self):
        cdp = self._cdp()
        tp.save_draft_api(cdp, "t", "<p>c</p>", anti_token="wAR3gsQKW-xyz")
        self.assertIn("tt-anti-token", cdp.js)
        self.assertIn("wAR3gsQKW-xyz", cdp.js)

    def test_7050判失败(self):
        pid, msg = tp.save_draft_api(self._cdp(), "t", "c")
        self.assertEqual(pid, "")
        self.assertIn("7050", msg)

    def test_成功回pgc_id(self):
        cdp = self._cdp('{"code":0,"data":{"pgc_id":"769"},"message":"保存成功"}')
        pid, msg = tp.save_draft_api(cdp, "t", "c")
        self.assertEqual(pid, "769")


class TestAntiToken(unittest.TestCase):
    """tt-anti-token 的定性（2026-10-17 同会话对照实验）。

    背景：上午一度判定「token 带时效 ⇒ 纯 API 化需逆向生成算法 ⇒ 不可行」。
    **那个结论是错的** —— token 过期和 token 缺失返的 7050 一模一样，
    光看状态码分不开。同会话四段对照（tmp/tt_fresh_replay.py）全部 code:0：
      B 原 body + 抓到的头 → ✅   C 同一 token 换 body → ✅
      D 我方 body + 抓到的头 → ✅  ⇒ 无需逆向、token 不绑 body、形态本来就对。
    这些断言把结论钉死，防止将来又滑回「要逆向」的错误叙事。
    """

    def test_hook三件套齐全(self):
        """hook 必须记 header（URL/header 两个 hook 分支都在）。"""
        src = tp._HOOK_ANTI_TOKEN
        self.assertIn("window.fetch", src)          # fetch 分支
        self.assertIn("setRequestHeader", src)     # XHR header 分支
        self.assertIn("prototype.send", src)       # XHR send 分支
        self.assertIn("tt-anti-token", src)

    def test_旧结论不许回来(self):
        """docstring 里必须钉住三个已证事实，且不能再断言「需逆向/不可行」。"""
        d = tp.grab_anti_token.__doc__ or ""
        self.assertIn("无需逆向", d)        # ① 不必逆向生成算法
        self.assertIn("不绑定 body", d)     # ② token 与 body 无关
        self.assertIn("唯一的约束", d)      # ③ 唯一约束是新鲜度
        # 「不可行」只允许出现在明确否定的行里（「…是错的」/「错误结论」）
        for line in d.splitlines():
            if "不可行" in line:
                self.assertTrue("错的" in line or "错误" in line,
                                "不该无条件断言不可行：%s" % line)

    def test_取不到返回None不抛(self):
        """掉登录 / 无头不触发 ⇒ 返回 None，让上层退 UI，不静默失败。"""
        class FakeBox:
            def new_target(self, url):
                return {"id": "t1"}

            def connect_target(self, tid):
                return False

        old_cdp, old_box = tp.wp.CDP, tp.CDP_PORT
        tp.wp.CDP = lambda port: FakeBox()
        tp.CDP_PORT = 65535            # 免得真去连
        try:
            self.assertIsNone(tp.grab_anti_token(None))
        finally:
            tp.wp.CDP = old_cdp
            tp.CDP_PORT = old_box


class TestCleanupProbeDraft(unittest.TestCase):
    """探针草稿清理：只删 __probe 前缀，不误伤真稿。"""

    def test_只删探针前缀(self):
        calls = []
        state = {"left": [{"title": "__probe_tok__", "gid": "1",
                           "draft_type": 2},
                          {"title": "TrustMRR：…", "gid": "2",
                           "draft_type": 2}]}

        def fake_raw(cdp):
            # 模拟服务端真删：gid=1 删完就从列表里消失
            return list(state["left"])

        def fake_del(cdp, gid, dt=2):
            calls.append(gid)
            state["left"] = [x for x in state["left"]
                             if str(x["gid"]) != str(gid)]
            return "OK"

        old_raw, old_del, old_sleep = (tp.fetch_draft_api_raw,
                                        tp.delete_draft_api, tp.time.sleep)
        tp.fetch_draft_api_raw, tp.delete_draft_api = fake_raw, fake_del
        tp.time.sleep = lambda *a: None      # 别真等 3s
        try:
            n = tp.cleanup_probe_draft(object())
        finally:
            tp.fetch_draft_api_raw = old_raw
            tp.delete_draft_api = old_del
            tp.time.sleep = old_sleep
        self.assertEqual(n, 1)
        self.assertEqual(calls, ["1"])       # 只删 gid=1，gid=2 是真稿

    def test_首轮没落库会重试(self):
        """实测坑：hook 抓到 token 时那次自动保存可能还没落库，
        首轮 draft_list 里没有 ⇒ 必须重试，否则留垃圾稿。"""
        seq = [[],                                   # 首轮：还没落库
               [{"title": "__probe_tok__", "gid": "9", "draft_type": 2}],
               []]                                   # 删完：空了

        def fake_raw(cdp):
            return seq.pop(0) if seq else []

        old_raw, old_del, old_sleep = (tp.fetch_draft_api_raw,
                                        tp.delete_draft_api, tp.time.sleep)
        tp.fetch_draft_api_raw = fake_raw
        tp.delete_draft_api = lambda c, g, d=2: "OK"
        tp.time.sleep = lambda *a: None
        try:
            n = tp.cleanup_probe_draft(object())
        finally:
            tp.fetch_draft_api_raw = old_raw
            tp.delete_draft_api = old_del
            tp.time.sleep = old_sleep
        self.assertEqual(n, 1)

    def test_复用调用方socket会被污染(self):
        """回归：grab_anti_token 绝不能 connect/close 任何 target。

        实测坑：它 connect 新标签页后 finally close_target，调用方的
        `cdp.ws` 就指向已关闭的 socket ⇒ 后续 save_draft_api 直接
        WinError 10053。所以它只能在传入的 cdp 上就地挂 hook。
        """
        import inspect
        src = inspect.getsource(tp.grab_anti_token)
        self.assertNotIn("new_target", src)
        self.assertNotIn("close_target", src)

    def test_必须在编辑器页开会话(self):
        """回归：API 全流程要同一个编辑器 tab。

        实测坑：首页 tab 里 fetch(article/publish) 被 argus 风控挡回来，
        返回 `<!doctypehtml>…argus…` 而不是 JSON ⇒ open_api_session
        必须开 TT_EDITOR。
        """
        src = inspect.getsource(tp.open_api_session)
        self.assertIn("TT_EDITOR", src)
        # 掉登录要判出来返回 None，不能硬着头皮往下跑
        self.assertIn("/auth/page/login", src)


class TestCoverApi(unittest.TestCase):
    """封面上传（2026-10-17 hook 抓包实证）：/spice/image 两级 + pgc_feed_covers。"""

    def test_封面走pgc_feed_covers不是cover_tos_url(self):
        """回归：字段名是 pgc_feed_covers。旧代码的 `cover_tos_url` 是臆测，
        服务端根本不认（带封面的真实 body 里没有这个键）。"""
        q = parse_qs(tp._publish_draft_body("t", "c"))
        self.assertEqual(q["pgc_feed_covers"][0], "[]")   # 无封面 = 空数组
        q2 = parse_qs(tp._publish_draft_body(
            "t", "c", cover_url="https://x/cover.image", cover_uri="tos-cn/i/1"))
        covers = json.loads(q2["pgc_feed_covers"][0])
        self.assertEqual(len(covers), 1)
        self.assertEqual(covers[0]["url"], "https://x/cover.image")
        self.assertEqual(covers[0]["uri"], "tos-cn/i/1")
        self.assertEqual(covers[0]["thumb_width"], 3840)
        self.assertEqual(covers[0]["thumb_height"], 2160)
        self.assertEqual(covers[0]["extra"],
                         {"from_content_uri": "", "from_content": "0"})
        self.assertNotIn("cover_tos_url", q2)

    def test_上传两级接口形态(self):
        up, cover = tp._cover_upload_urls()
        self.assertEqual(up, "/spice/image?upload_source=20020003&aid=1231"
                            "&device_platform=web")
        self.assertTrue(cover.endswith("&need_cover_url=1"))

    def _fake(self, js_sink, resp):
        class FakeCDP:
            js = ""

            def eval(self, js, **kw):
                self.js = js
                return resp
        cd = FakeCDP()
        js_sink.append(cd)
        return cd

    def test_上传注入的JS必须是纯ASCII(self):
        """回归：裸中文进 Runtime.evaluate 会坏（argus 症状）。"""
        import tempfile
        png = os.path.join(tempfile.mkdtemp(), "c.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        sink = []
        cdp = self._fake(sink, json.dumps(
            {"s1": 200, "s2": 200, "img": "https://i/o.image", "uri": "u/1",
             "w": 3840, "h": 2160, "cover": "https://i/c.image",
             "r1": "{}", "r2": "{}"}))
        url, uri, msg, wh = tp.upload_cover_api(cdp, png, anti_token="tok")
        self.assertEqual(url, "https://i/c.image")
        self.assertEqual(uri, "u/1")
        self.assertEqual(msg, "ok")
        self.assertEqual(wh, (3840, 2160))     # thumb 尺寸取上传返回值
        # 源码里不能有裸中文/换行；base64 是 ASCII
        self.assertNotIn("\n", cdp.js.split("return JSON.stringify")[-1][:80])
        self.assertIn("need_cover_url=1", cdp.js)
        self.assertIn("tt-anti-token", cdp.js)
        # 🚨 字段名必须是 image_uri / image_url（实测真名，别写错）
        self.assertIn("image_uri", cdp.js)
        self.assertIn("image_url", cdp.js)
        self.assertIn("cover_url", cdp.js)

    def test_缺uri要报错不静默(self):
        """实测坑：第①步字段抽错 → uri 空 → save 报 4009 封面数据异常。"""
        sink = []
        cdp = self._fake(sink, json.dumps(
            {"s1": 200, "s2": 200, "img": "u", "uri": "", "w": 0, "h": 0,
             "cover": "https://i/c.image", "r1": "{}", "r2": "{}"}))
        import tempfile
        png = os.path.join(tempfile.mkdtemp(), "c.png")
        with open(png, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        url, _uri, msg, _wh = tp.upload_cover_api(cdp, png)
        self.assertIsNone(url)
        self.assertIn("image_uri", msg)

    def test_封面回读校验(self):
        """结果导向校验：认 image_uri（与上传返回值一致），不只看有没有图。"""
        raw = [
            {"title": "A", "cover_image": {"image_uri": "tos/i/1",
                                            "width": 3840, "height": 2160}},
            {"title": "B", "cover_image": {}},
            {"title": "C"},
        ]
        self.assertEqual(tp.verify_draft_cover(None, "A", raw=raw)[0], "OK")
        self.assertIn("tos/i/1", tp.verify_draft_cover(None, "A", raw=raw)[1])
        self.assertEqual(tp.verify_draft_cover(None, "B", raw=raw)[0],
                         "NO_COVER")
        self.assertEqual(tp.verify_draft_cover(None, "C", raw=raw)[0],
                         "NO_COVER")      # 字段整个没有 = 同样没封面
        self.assertEqual(tp.verify_draft_cover(None, "Z", raw=raw)[0],
                         "NOT_FOUND")

    def test_上传失败不静默(self):
        sink = []
        cdp = self._fake(sink, json.dumps(
            {"s1": 200, "s2": 500, "img": "u", "uri": "", "w": 0, "h": 0,
             "cover": "", "r1": "{}", "r2": "boom"}))
        url, _uri, msg, _wh = tp.upload_cover_api(cdp, "/nope/missing.png")
        self.assertIsNone(url)
        self.assertIn("不存在", msg)


class TestCleanupTitles(unittest.TestCase):
    """cleanup_titles：精确标题清理。

    背景（2026-10-17 实测）：`cleanup_probe_draft` 只匹配 `__probe`
    前缀，探针脚本用中文测试标题时全部漏删 —— 草稿箱实留 7 条
    （「中文标题探针」等），最后按 gid 手工删才清掉。本类把
    「精确匹配、不误伤」的语义钉死。
    """

    def _run(self, drafts, rounds=3):
        deleted = set()
        state = {"n": 0}

        def fake_fetch(_cdp):
            state["n"] += 1
            if state["n"] == 1:
                return list(drafts)
            return [d for d in drafts if d["gid"] not in deleted]

        def fake_delete(_cdp, gid, dtype=2):
            deleted.add(str(gid))
            return "OK"

        old_f, old_d = tp.fetch_draft_api_raw, tp.delete_draft_api
        tp.fetch_draft_api_raw, tp.delete_draft_api = fake_fetch, fake_delete
        try:
            n = tp.cleanup_titles(object(), ["中文标题探针", "__probe_tok0__"],
                                  rounds=rounds, pause=0)
        finally:
            tp.fetch_draft_api_raw, tp.delete_draft_api = old_f, old_d
        return n, deleted

    def test_精确匹配才删_子串不误伤(self):
        drafts = [
            {"gid": "1", "title": "中文标题探针", "draft_type": 2},
            # 含测试标题作子串的正稿标题 —— 不许删
            {"gid": "2", "title": "中文标题探针段落含中文标题探针",
             "draft_type": 2},
            {"gid": "3", "title": "PROSP：把 B2B 销售的一整天，压成一个按钮",
             "draft_type": 2},
            {"gid": "4", "title": "__probe_tok0__", "draft_type": 2},
        ]
        n, deleted = self._run(drafts)
        self.assertEqual(n, 2)
        self.assertEqual(deleted, {"1", "4"})

    def test_空目标直接返回0(self):
        n, deleted = self._run([])
        self.assertEqual(n, 0)
        self.assertEqual(deleted, set())

    def test_空标题列表不拉列表(self):
        # titles 全空 ⇒ 一次 fetch 都不该发（省 argus 记账）
        calls = []

        def fake_fetch(_cdp):
            calls.append(1)
            return []

        old = tp.fetch_draft_api_raw
        tp.fetch_draft_api_raw = fake_fetch
        try:
            n = tp.cleanup_titles(object(), ["", None])
        finally:
            tp.fetch_draft_api_raw = old
        self.assertEqual(n, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
