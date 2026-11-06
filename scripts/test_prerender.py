# -*- coding: utf-8 -*-
"""prerender.py 的测试。

重点盯四件事：
  1. Python 与 JS 两份常量必须对齐 —— prerender.py 里的标签文案是抄 app.js 的，
     漂移之后同一条案例在抽屉里和独立页上会显示成两个词，而且没有任何报错
  2. 正文必须脱离 JS —— 预渲染的全部意义就在于「不跑 JS 也读得到正文」，
     这条一旦回归，SEO 和分享预览全白做
  3. 转义必须彻底 —— 案例文本来自公开来源，里面出现 < > & 是常事
  4. 只写临时目录，不碰 data/、static/、dist/

不联网。用法：python scripts/test_prerender.py
"""

import contextlib
import html
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

passed = 0
fails = []


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "prerender", os.path.join(ROOT, "scripts", "prerender.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    if rc is None:
        rc = 0
    return rc, buf.getvalue()


def js_const(name, kind="obj"):
    """从 static/app.js 里抠出一个常量，用来和 Python 那份比对。

    两种写法都要认：多行一格一个键（V_LABEL），和挤在一行里的（KIND_LABEL）。
    带嵌套对象的（QUAD_META）只取顶层键。
    """
    src = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    if kind == "obj":
        m = re.search(r"const\s+%s\s*=\s*\{([\s\S]*?)\n\};" % name, src)
        if not m:
            return None
        block = m.group(1)
        if "{" in block:
            return re.findall(r"^\s*(\w+)\s*:\s*\{", block, re.M)
        return re.findall(r"(\w+)\s*:", block)
    m = re.search(r"const\s+%s\s*=\s*\[([^\]]*)\]" % name, src)
    if not m:
        return None
    return re.findall(r"'([^']+)'", m.group(1))


def plain_text(html_text):
    """去掉标签和注释，只留读者能看见的字。"""
    t = re.sub(r"<!--[\s\S]*?-->", " ", html_text)
    t = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main():
    m = load_mod()
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    site = m.load_site(ROOT)
    site["url"] = "https://example.com"

    print("=" * 58)
    print("  prerender.py 测试")
    print("=" * 58)

    # ---------------------------------------------------------- 1 常量对齐
    print("\n[1] Python 与 app.js 的常量对齐")
    js_v = js_const("V_LABEL")
    chk("V_LABEL 键一致", js_v == list(m.V_LABEL.keys()),
        "js=%s py=%s" % (js_v, list(m.V_LABEL.keys())))
    js_kind = js_const("KIND_LABEL")
    chk("KIND_LABEL 键一致", js_kind == list(m.KIND_LABEL.keys()))
    js_rep = js_const("REP_LABEL")
    chk("REP_LABEL 键一致", js_rep == list(m.REP_LABEL.keys()))
    chk("CHINA_DIM_ORDER 一致",
        js_const("CHINA_DIM_ORDER", "arr") == m.CHINA_DIM_ORDER)
    chk("SOLO_DIM_ORDER 一致",
        js_const("SOLO_DIM_ORDER", "arr") == m.SOLO_DIM_ORDER)
    js_china_label = js_const("CHINA_DIM_LABEL")
    chk("CHINA_DIM_LABEL 键一致", js_china_label == list(m.CHINA_DIM_LABEL.keys()))
    js_solo_label = js_const("SOLO_DIM_LABEL")
    chk("SOLO_DIM_LABEL 键一致", js_solo_label == list(m.SOLO_DIM_LABEL.keys()))
    js_quad = js_const("QUAD_META")
    chk("QUAD_META 键一致", js_quad == list(m.QUAD_META.keys()))
    # 反向样本区（2026-10-26）：口径/角色的中文名在抽屉与静态页各有一份副本，
    # 不一致就会出现「同一字段两个词」—— 读者会当成两个分类。
    js_cal = js_const("CAL_LABEL")
    chk("CAL_LABEL 键一致", js_cal == list(m.CAL_LABEL.keys()),
        "js=%s py=%s" % (js_cal, list(m.CAL_LABEL.keys())))
    js_role = js_const("ANTI_ROLE")
    chk("ANTI_ROLE 键一致", js_role == list(m.ANTI_ROLE.keys()),
        "js=%s py=%s" % (js_role, list(m.ANTI_ROLE.keys())))
    # 四象限文案要和 server.py 对得上（那边是第 3 份副本）
    sys.path.insert(0, ROOT)
    import server                                                   # noqa: E402
    same = all(server.QUAD_META[k] == m.QUAD_META[k] for k in m.QUAD_META)
    chk("QUAD_META 文案与 server.py 一致", same)

    # ---------------------------------------------------------- 2 逐条渲染
    print("\n[2] 24 条案例全部可渲染")
    bad = []
    for c in cases:
        try:
            html_text = m.render_case_page(c, {"site": site})
        except Exception as e:                                       # noqa: BLE001
            bad.append("%s: %s" % (c.get("id"), e))
            continue
        if not html_text.startswith("<!DOCTYPE html>"):
            bad.append("%s: 不是完整页面" % c.get("id"))
    chk("无异常、均为完整页面", not bad, "; ".join(bad[:3]) if bad else
        "%d 条" % len(cases))

    # ---------------------------------------------------------- 3 脱离 JS
    print("\n[3] 正文不依赖 JS")
    thin, no_liner, with_js, abs_path = [], [], [], []
    for c in cases:
        h = m.render_case_page(c, {"site": site})
        n = len(plain_text(h))
        if n < 600:
            thin.append("%s(%d字)" % (c["id"], n))
        if c.get("one_liner") and m.esc(c["one_liner"]) not in h:
            no_liner.append(c["id"])
        # 2026-10-15：原来是裸判断 `"app.js" in h`，但内联进页面的 style.css
        # 注释里本来就写着「（app.js 的 switchView 因此改用 el.hidden…）」——
        # 于是每一页都命中，误报成「独立页依赖 app.js」。
        # 这里要验的是**真的引用了脚本**，所以只认 <script src=...app.js>。
        if re.search(r'<script[^>]*\bsrc=["\'][^"\']*app\.js', h):
            with_js.append(c["id"])
        if re.search(r'(href|src)="/(?!/)', h):
            abs_path.append(c["id"])
    chk("每条正文都够厚（>=600 字）", not thin, "偏薄：" + ",".join(thin[:3]) if thin else "")
    chk("每条都含 one_liner 原文", not no_liner, ",".join(no_liner[:3]))
    chk("不引入 app.js", not with_js, ",".join(with_js[:3]))
    chk("无根路径引用（子目录部署不 404）", not abs_path, ",".join(abs_path[:3]))

    # ---------------------------------------------------------- 3b 样式自包含
    # 背景：独立页原本外链 ../style.css，实测在某些打开方式下取不到样式表，
    # 而 style.css 里的 :root 是整套配色的地基 —— 一缺失整页就退化成白底黑字。
    # 所以现在把主样式表内联进每个页面，这里守住它别再退回去。
    print("\n[3b] 样式自包含（不依赖外部样式表）")
    ext_css, missing_css, thin_css, order_bad = [], [], [], []
    css_all = m.page_css()
    for c in cases:
        h = m.render_case_page(c, {"site": site})
        if 'rel="stylesheet"' in h or "style.css" in h:
            ext_css.append(c["id"])
        if css_all not in h:
            missing_css.append(c["id"])
        blocks = re.findall(r"<style>(.*?)</style>", h, re.S)
        if not blocks or len(blocks[0]) < len(m.base_css()):
            thin_css.append(c["id"])
        # 主样式表在前、阅读页补充样式在后，后者才能盖住前者的网格布局
        if blocks and blocks[0].find("--bg:") > blocks[0].find(".doc{max-width:760px"):
            order_bad.append(c["id"])
    chk("页面不引用外部样式表", not ext_css, ",".join(ext_css[:3]))
    chk("主样式表 + 阅读页样式已完整内联", not missing_css, ",".join(missing_css[:3]))
    chk("补充样式排在主样式表之后（覆盖顺序正确）", not order_bad, ",".join(order_bad[:3]))

    # 主样式表读不到时 base_css() 会安静地返回空串 —— 那种情况页面看着「能读但很丑」，
    # 不会报错。所以直接盯住它有没有真的读到内容。
    chk("static/style.css 可读且非空", len(m.base_css()) > 10000,
        "%d 字节" % len(m.base_css()))

    # 页面上真正用到的组件类，缺一个就是那一块局部掉样式（比整页掉样式更难发现）
    used = [".d-head", ".d-p", ".metrics", ".metrics > div.k", ".corr", ".rep-cell",
            ".china-hero", ".quad-badge", ".src-item", ".d-list", ".cat-list", ".docfoot",
            ".promo-bar", ".pb-dot", ".pb-txt", ".promo-bar.inpage"]
    lack = [s for s in used if s not in m.base_css() and s not in m.PAGE_CSS]
    chk("用到的组件类都能在样式里找到", not lack, "缺：" + ",".join(lack))

    # ---------------------------------------------------------- 3c 引流位
    # 引流位是这站唯一的收入通路（站本身不收钱），掉了不会报错、只会没转化，
    # 所以要专门守一道。
    print("\n[3c] 引流位（顶部条 + 页脚）")
    wx_name = (site.get("wechat") or {}).get("name") or ""
    cm = site.get("community") or {}
    no_bar, no_wx, no_foot, bad_ghost = [], [], [], []
    for c in cases:
        h = m.render_case_page(c, {"site": site})
        if wx_name and 'class="promo-bar inpage"' not in h:
            no_bar.append(c["id"])
        if wx_name and wx_name not in h:
            no_wx.append(c["id"])
        if '<aside class="promo">' not in h:
            no_foot.append(c["id"])
        # 社群没开通（没有 url）时不能出现可点按钮：点了没反应比不放更伤信任
        if cm.get("name") and not cm.get("url") and 'class="btn ghost"' not in h:
            bad_ghost.append(c["id"])
    if wx_name:
        chk("配了公众号 → 每条都有顶部引导条", not no_bar, ",".join(no_bar[:3]))
        chk("顶部条带出公众号名", not no_wx, ",".join(no_wx[:3]))
    else:
        chk("没配公众号时跳过顶部条断言（site.json 未填）", True)
    chk("页脚有引流块", not no_foot, ",".join(no_foot[:3]))
    if cm.get("name") and not cm.get("url"):
        chk("社群未开通时不渲染成按钮", not bad_ghost, ",".join(bad_ghost[:3]))

    # 反过来：没配公众号时不能留一条空条在页面顶上
    bare_wx = dict(site)
    bare_wx["wechat"] = {}
    chk("没配公众号 → 不输出空引导条",
        'class="promo-bar inpage"' not in m.render_case_page(cases[0], {"site": bare_wx}))

    # 静态总目录页同样是搜索入口，一样要有
    idx = m.render_index_page(cases, {"site": site})
    chk("静态总目录页也有引导条与页脚",
        (not wx_name or 'class="promo-bar inpage"' in idx) and '<aside class="promo">' in idx)

    # ---------------------------------------------------------- 4 head 元信息
    print("\n[4] SEO 元信息")
    long_title, no_desc, no_canon = [], [], []
    for c in cases:
        h = m.render_case_page(c, {"site": site})
        head = h[:h.index("<body>")]
        t = re.search(r"<title>(.*?)</title>", head, re.S)
        # ⚠️ 量的是**读者看到的那串**，不是 HTML 源码：名字里带 & 的案例
        #（aura-ai…）会被转义成 &amp;，源码多 4 个字符。prerender 卡的是可见
        # 长度（62），按源码量会把「卡得刚好」判成超长 —— 这条断言从此假红。
        title_txt = html.unescape(t.group(1)) if t else ""
        if not t or not title_txt.strip() or len(title_txt) > 62:
            long_title.append("%s(%s)" % (c["id"], len(title_txt)))
        d = re.search(r'<meta name="description" content="(.*?)">', head)
        if not d or not d.group(1).strip():
            no_desc.append(c["id"])
        if '<link rel="canonical" href="https://example.com/case/%s.html">' % c["id"] not in head:
            no_canon.append(c["id"])
    chk("title 非空且不超长", not long_title, ",".join(long_title[:3]))
    chk("description 非空", not no_desc, ",".join(no_desc[:3]))
    chk("canonical 指向自身绝对地址", not no_canon, ",".join(no_canon[:3]))

    # ------------------------------------------------------- 4b 案例标题
    # 2026-11-06 二审：标题里**不许出现金额 / 省略号 / 平台状态语**
    # （「当前 MRR 为 $5,371.18」「MRR $578」「App Store 在架」都是不合格标题）
    print("\n[4b] 案例标题（去重 / 断词 / 去金额 / 无省略号 / 站名）")
    site_name = site["title"]

    _, t = m.case_title("TalkEasy", "TalkEasy 是一个 AI 口语陪练产品，当前 MRR 为 $1,561。", site_name)
    chk("说明开头的产品名不重复", t.count("TalkEasy") == 1, t)
    chk("紧跟的判断词一并去掉", "— 是一个" not in t and "— AI 口语陪练" in t, t)
    chk("标题不带金额（$ / MRR）", "$" not in t and "MRR" not in t and "1,561" not in t, t)

    _, t = m.case_title("Admirror", "MRR $578，Admirror 让静态广告快速适应产品。", site_name)
    chk("说明里第二次出现的名字被去掉", t.count("Admirror") == 1, t)
    chk("金额砍掉后仍留得住说明", "静态广告" in t, t)

    # 名字只是更长单词的前缀（Bolt / Bolty）—— 那不是重复，删了就丢信息
    _, t = m.case_title("Bolty", "Bolt 是一个更容易记的名字", site_name)
    chk("名字只是前缀时不误删", "Bolt 是" in t, t)

    # $5,371.18 里的半角逗号不是分句边界；金额段整段丢掉，不能只抠掉数字
    _, t = m.case_title(
        "RYN VPN",
        "Ryn VPN，高端 VPN 产品，当前 MRR 为 $5,371.18，近 30 天已验证收入 $6,641。", site_name)
    chk("金额整段丢弃且不留断头句", "$" not in t and "MRR 为" not in t, t)
    chk("金额旁边的事实性描述保留", "高端 VPN 产品" in t, t)

    _, t = m.case_title(
        "sotie: AI Best Friend",
        "AI 情感陪伴 App，App Store 在架（4.81 分、32 评），近 30 天收入 1566。", site_name)
    chk("平台状态语不进标题", "App Store" not in t and "在架" not in t, t)
    chk("不留半截括号", t.count("（") == t.count("）"), t)

    # 超长说明：只能截断，不许用省略号收尾
    _, t = m.case_title(
        "Selfie Validator",
        "把一张自拍变成合规证件照，自动换背景并检查面部是否被遮挡，同时给出各国签证尺寸模板。",
        site_name)
    chk("标题不出省略号", "…" not in t and "..." not in t, t)

    # one_liner 全是金额时，去 `fallbacks` 里再找一句
    _, t = m.case_title(
        "Piston", "当前 MRR 为 9079，近 30 天收入 $12,000。", site_name,
        fallbacks=("法国最大的求职搜索 SaaS",))
    chk("素材全被过滤时换下一条", "求职搜索" in t, t)

    _, t = m.case_title("wpconvert-ai-convert-ai-sites-to-wordpress", "把整个网站转成 WordPress，MRR $1200。", site_name)
    chk("超长名字也不切断站名", t.endswith(" · " + site_name) and len(t) <= 62, t)
    chk("超长名字截断也不出省略号", "…" not in t, t)

    _, t = m.case_title("仅名字", "", site_name)
    chk("没有 one_liner 时只留名字", t == "仅名字 · " + site_name, t)

    bad_tail, bad_money, bad_dots, bad_meta = [], [], [], []
    for c in cases:
        full = m.case_title(
            c.get("name") or c["id"], c.get("one_liner") or "", site_name,
            fallbacks=(c.get("what_it_does"), c.get("verdict")))[1]
        if not full.endswith(" · " + site_name):
            bad_tail.append(c["id"])
        if m.MONEY_PAT.search(full) or m.MONEY_WORD_PAT.search(full):
            bad_money.append("%s=%s" % (c["id"], full))
        if "…" in full or "..." in full:
            bad_dots.append("%s=%s" % (c["id"], full))
        if m.META_PAT.search(full):
            bad_meta.append("%s=%s" % (c["id"], full))
    chk("全库标题都以站名收尾（站名永不被切断）", not bad_tail, ",".join(bad_tail[:3]))
    chk("全库标题不含金额", not bad_money, " | ".join(bad_money[:2]))
    chk("全库标题不含省略号", not bad_dots, " | ".join(bad_dots[:2]))
    chk("全库标题不含平台状态语", not bad_meta, " | ".join(bad_meta[:2]))

    # 没配域名时，canonical 和 sitemap 都该干净地消失，而不是留个空 href
    bare = m.load_site(ROOT)
    bare["url"] = ""
    h0 = m.render_case_page(cases[0], {"site": bare})
    chk("无域名时不输出 canonical", 'rel="canonical"' not in h0)
    chk("无域名时不输出 og:url", "og:url" not in h0)
    chk("无域名时 sitemap 为空", m.render_sitemap(cases, bare) == "")

    # ---------------------------------------------------------- 5 转义
    print("\n[5] 转义与注入防护")
    evil = {
        "id": "evil", "name": "<script>alert(1)</script>", "one_liner": "a & b <i>x</i>",
        "verdict": '"><img src=x onerror=alert(1)>',
        "what_it_does": "5 > 3 && 2 < 4", "why_it_works": ["<b>不加粗</b>"],
        "corrections": [{"claim": "<svg onload=alert(1)>", "truth": "x", "source": 'a"b'}],
        "sources": [{"label": "<script>x</script>", "url": "https://e.com/?a=1&b=2",
                     "kind": "official"}],
        "metrics": {"headline": "<b>nope</b>"}, "tags": ["<script>"],
    }
    he = m.render_case_page(evil, {"site": site})
    chk("name 里的 <script> 被转义", "<script>alert(1)</script>" not in he
        and "&lt;script&gt;" in he)
    chk("verdict 里的属性注入被转义", "onerror=alert(1)>" not in he
        or "&quot;&gt;&lt;img" in he)
    chk("列表项里的标签被转义", "<b>不加粗</b>" not in he)
    chk("URL 里的 & 被转义成 &amp;", 'href="https://e.com/?a=1&amp;b=2"' in he)
    body_only = he[he.index("<article>"):he.index("</article>")]
    chk("正文区没有可执行标签", not re.search(r"<(script|img|svg|iframe)\b", body_only))

    # ---------------------------------------------------------- 6 索引与 sitemap
    print("\n[6] 索引页与 sitemap")
    idx = m.render_index_page(cases, {"site": site})
    miss = [c["id"] for c in cases if "./%s.html" % c["id"] not in idx]
    chk("索引页含全部案例链接", not miss, ",".join(miss[:3]))
    chk("索引页有正文（非空壳）", len(plain_text(idx)) > 400)

    sm = m.render_sitemap(cases, site)
    chk("sitemap 是合法 XML 开头", sm.startswith('<?xml version="1.0"'))
    chk("sitemap 条数 = 首页 + 目录 + 案例",
        sm.count("<url>") == len(cases) + 2, "实际 %d" % sm.count("<url>"))
    chk("sitemap 里都是绝对地址", "https://example.com/case/" in sm)
    rb = m.render_robots(site, True)
    chk("robots 指向 sitemap", "Sitemap: https://example.com/sitemap.xml" in rb)
    chk("无域名时 robots 不写 Sitemap 行", "Sitemap" not in m.render_robots(bare, False))

    # ---------------------------------------------------------- 7 降级清单
    print("\n[7] 首页降级清单")
    ns = m.noscript_block(cases, site)
    chk("noscript 包裹", ns.startswith("<noscript>") and ns.endswith("</noscript>\n"))
    chk("含全部案例链接", all("./case/%s.html" % c["id"] in ns for c in cases))
    chk("不含可执行脚本", "<script" not in ns)

    # ---------------------------------------------------------- 7b 反向样本页
    # 2026-10-26 上站：33 条「可以读、抄不动」的案例独立成一页放 anti/。
    # 不放进 case/ 是因为 verify_deploy 断言「case 目录链接数 = curated 案例数」，
    # 那条判据是部署后确认「没少案例」的唯一依据，不能被另一种条目污染。
    print("\n[7b] 反向样本页（这些别照着做）")
    with open(os.path.join(ROOT, "data", "anti_cases.json"), encoding="utf-8") as f:
        anti_doc = json.load(f)
    anti = {"cases": anti_doc["cases"], "volumes": anti_doc["volumes"]}
    ah = m.render_anti_page(anti, {"site": site})
    chk("是完整页面", ah.startswith("<!DOCTYPE html>") and ah.rstrip().endswith("</html>"))
    miss = [a["id"] for a in anti["cases"] if m.esc(a["name"]) not in ah]
    chk("33 条全部出现在页面上", not miss, ",".join(miss[:3]))
    miss_v = [v["key"] for v in anti["volumes"] if m.esc(v["title"]) not in ah]
    chk("7 篇标题全部出现", not miss_v, ",".join(miss_v[:3]))
    chk("不引用 app.js", not re.search(r'<script[^>]*\bsrc=["\'][^"\']*app\.js', ah))
    chk("无根路径引用（子目录部署不 404）", not re.search(r'(href|src)="/(?!/)', ah))
    chk("引用来自数据的一律转义",
        not re.search(r"<(script|img|svg|iframe)\b", ah))
    # 页首那句「N 条里只有 M 条标签没写错」是硬编码文案里的变量，必须跟着数据走：
    # 写死一个数字，数据一变页面就在说谎（这条区的主论点就是「标签写错了」）。
    mrr = sum(1 for a in anti["cases"] if a.get("caliber") == "mrr")
    chk("页首「N 条里只有 M 条」与实际一致",
        ("%d 条里只有 %d 条标签没写错" % (len(anti["cases"]), mrr)) in plain_text(ah),
        "mrr=%d" % mrr)
    # 口径不是收入就必须在页面上写出来 —— 这是这一区唯一不能省的一句话
    no_cal = [a["id"] for a in anti["cases"]
              if a.get("caliber") != "mrr" and m.CAL_LABEL[a["caliber"]] not in ah]
    chk("非月收入条目都标了真实口径", not no_cal, ",".join(no_cal[:3]))
    chk("noscript 降级清单里有反向量入口",
        "./anti/index.html" in m.noscript_block(cases, site, anti=anti))
    chk("没给 anti 时不输出那行", "./anti/index.html" not in m.noscript_block(cases, site))

    # ---------------------------------------------------------- 8 落盘与自检
    print("\n[8] 落盘与产物自检")
    tmp = tempfile.mkdtemp(prefix="prerender_test_")
    try:
        st = m.build_all(cases, tmp, site, quiet=True, anti=anti)
        chk("生成了 24 个案例页", st["pages"] == len(cases), "实际 %d" % st["pages"])
        chk("生成 case/index.html", os.path.isfile(os.path.join(tmp, "case", "index.html")))
        chk("生成 anti/index.html",
            os.path.isfile(os.path.join(tmp, "anti", "index.html")))
        with open(os.path.join(tmp, "sitemap.xml"), encoding="utf-8") as f:
            n_url = f.read().count("<url>")
        chk("sitemap 里多了反向量那一条（首页+目录+案例+anti）",
            n_url == len(cases) + 3, "%d 条" % n_url)
        chk("生成 sitemap.xml", os.path.isfile(os.path.join(tmp, "sitemap.xml")))
        chk("生成 robots.txt", os.path.isfile(os.path.join(tmp, "robots.txt")))
        chk("自检通过", m.check_pages(tmp, cases, verbose=False) == [])

        # 自检必须真的能发现问题，否则等于没有
        victim = os.path.join(tmp, "case", "%s.html" % cases[0]["id"])
        with open(victim, "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html><html><head><title>x</title></head>"
                    "<body><article>太短</article></body></html>")
        chk("自检能抓出空壳页面", bool(m.check_pages(tmp, cases, verbose=False)))
        os.remove(victim)
        chk("删掉一页后自检报缺页", bool(m.check_pages(tmp, cases, verbose=False)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 只写临时目录：dist/ 的修改时间不该被这个测试碰到
    chk("没往项目里写东西", not os.path.exists(os.path.join(ROOT, "prerender_test")), )

    # ---------------------------------------------------------- 汇总
    print("\n" + "=" * 58)
    if fails:
        print("  %d 项通过 · %d 项失败" % (passed, len(fails)))
        for f in fails:
            print("    - %s" % f)
        return 1
    print("  全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
