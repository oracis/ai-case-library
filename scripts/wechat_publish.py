#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_publish.py — 公众号「万物解释者 · 拆解海外」自动发布管线
================================================================

背景：个人订阅号（未认证）没有 freepublish API 权限，无法 API 群发。
只能走「浏览器自动化」：复用本机已登录 Chrome 的登录态，用 CDP 把图文
存成草稿，最后那一下「群发」在后台点一下即可。

设计要点：发布队列不手写，每次运行从 data/cases.json 实时算。新案例入库
（今天进了好几个、扫描持续跑）→ 重跑 build → 队列自动变长，永不手改计划表。

子命令
------
  build     对 cases.json 里「还没有 公众号-<id>拆解.html」的案例，
            从 out/articles/<id>.html 修成发布就绪版（修 ** Markdown 加粗、
            加栏目标签/footer/标题摘要注释）。已有手工精修版不覆盖。
  queue     从 data/cases.json 实时算待发队列（有就绪 HTML 且未发布），按编撰顺序。
  plan      生成 发布队列-自动生成.md（动态，永不手改）。
  inspect   校验单文件抽取（标题 / 摘要 / 正文长度）。
  discover  连 Chrome CDP，打开草稿编辑器，导出 DOM 供锁定选择器。
  publish   连 Chrome CDP，把队列逐条存成草稿（最终群发需后台手动点）。

依赖：纯标准库。Chrome 需本机已装，且以远程调试端口启动（见 discover 提示）。
"""
import argparse
import base64
import glob
import json
import os
import re
import select
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_PATH = os.path.join(ROOT, "data", "cases.json")
ARTICLES_DIR = os.path.join(ROOT, "out", "articles")
PUBLISHED_PATH = os.path.join(ROOT, "data", "wechat_published.json")
PLAN_PATH = os.path.join(ROOT, "发布队列-自动生成.md")

CDP_PORT = int(os.environ.get("CDP_PORT", 9222))

# 已登录主页（靠 cookie 登录态，无需 token，会被 mp 重定向到图文列表/看板）
MP_HOME = "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN"

# 新建图文编辑器路径（token 必须在运行时从已登录页面动态抠，不能写死）
MP_EDITOR_PATH = (
    "cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=10&isMul=0&isNew=1"
)

# 赞赏自动回复文案。回复**素材**是账号级的（配一次全站共用），
# 但「赞赏自动回复」开关是**文章级**的 —— 每篇新草稿都要打开，见 _setup_reward_reply。
REWARD_REPLY_TEXT = "非常感谢你的打赏，感谢认可，祝你万事顺遂✨"

# ---- 选择器：由 `discover` 在真实编辑器页面探测得来（2026-09-20 实测）----
# 编辑器正文是 ProseMirror（主帧内、不在 iframe）。候选还有个 ce=false 的
# `editor_content_placeholder` 同名 ProseMirror，用 [contenteditable="true"] 排掉。
SEL = {
    "editor": ".ProseMirror[contenteditable=\"true\"]",  # 正文编辑区
    "title": "#title",          # 标题 textarea (id=title)
    "author": "#author",        # 作者 input (id=author)
    "summary": "#js_description",  # 摘要 textarea (id=js_description, name=digest)
    "cover_input": None,        # 封面图：个人号走默认首图，暂不自动设
    "save_draft": None,         # 保存草稿按钮（无稳定 id，按文本点，见 _click_by_text）
}


# ======================================================================
# 基础工具
# ======================================================================
def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def make_wechat_title(c):
    """从 cases.json 生成适合公众号的简洁标题（≤64 字，优先 ≤30 字）。

    规则：名字 + one_liner 最简；能放下再加月收入/累计收入。
    例：Insect Bite ID → "Insect Bite ID：用 AI 识别虫咬类型"
    """
    name = (c.get("name") or "").strip()
    one = (c.get("one_liner") or "").strip()
    metrics = c.get("metrics") or {}
    mrr = metrics.get("mrr")
    all_time = metrics.get("all_time")

    def _ok(t):
        return t and len(t) <= 64

    if name and one:
        t = "%s：%s" % (name, one)
        if _ok(t):
            return t
    if name and one and mrr:
        mrr_text = "%d" % mrr
        if mrr >= 1000:
            k = round(mrr / 1000, 1)
            mrr_text = ("%g" % k).rstrip(".") + "K"
        t = "%s：%s，月收 $%s" % (name, one, mrr_text)
        if _ok(t):
            return t
    if name and one and all_time:
        t = "%s：%s，累计 $%s" % (name, one, "{:,}".format(all_time))
        if _ok(t):
            return t
    if name:
        return name[:64]
    return (c.get("id") or "untitled")[:64]


# ---- 微信编辑器（ProseMirror）样式白名单 ------------------------------
# 公众号新版编辑器会净化粘贴进来的 HTML：class / id 基本无效，<style>/<link>
# 必丢，渐变 / 阴影 / flex / 定位 / 伪元素等一律被吃。唯一能活下来的是
# 「内联 style 里的这几十个属性」。所以正文不是「写得越漂亮越好」，而是
# 必须主动降级到白名单内，否则保存后就是一坨无样式文字。
WX_STYLE_KEEP = (
    "color", "font-size", "font-weight", "font-style", "font-family",
    "line-height", "text-align", "text-indent", "text-decoration",
    "letter-spacing", "white-space", "word-break",
    "background", "background-color",
    "padding", "padding-top", "padding-right", "padding-bottom",
    "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-radius", "border-color", "border-width", "border-style",
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius",
    "opacity", "vertical-align",
    # ↓ 这一组以前被 WX_STYLE_DROP 拉黑，理由是「微信一定会吃」——**那是猜的**。
    #   2026-09-21 在真实编辑器里逐条实测（tmp/probe_wxstyle*.py）：
    #   送进去 → 保存草稿 → 重载读回，编辑器解析结果与服务端存储结果完全一致，
    #   display:flex / justify-content / float / inline-block / width / clear
    #   全部原样保留。所以「标签左、数值右」的两端对齐、定宽栏、卡片圆角都能用，
    #   不必再拿「······」凑位。唯一真会被剔除的是**里面没有文字的空 span**
    #   （整块连属性一起消失，塞零宽字符也救不回）→ 进度条那类空盒子方案不可行。
    "display", "float", "clear",
    "justify-content", "align-items", "align-self",
    "flex-direction", "flex-wrap",
    "width", "height", "max-width", "min-width",
)

# 一定会丢 / 一定会出问题的属性，显式拉黑（即使上面白名单里出现过）。
# 定位类（position/top/overflow）与装饰类（transform/渐变/阴影/动画）实测确实留不住，
# 且它们留一半反而会错位，继续拉黑。
WX_STYLE_DROP = (
    "position", "top", "left", "right", "bottom", "z-index",
    "transform", "transition", "animation",
    "box-shadow", "background-image", "background-size", "background-position",
    "gap", "grid", "grid-template", "overflow", "flex", "flex-grow", "flex-shrink",
    "max-height", "min-height", "filter", "list-style", "content",
)

# 少数属性只放行确定的取值，避免误用（比如 display:none 把内容藏了）。
WX_STYLE_VALUE_OK = {
    "display": ("flex", "inline-block", "inline-flex", "block", "inline"),
    "float": ("left", "right", "none"),
    "justify-content": ("space-between", "space-around", "space-evenly",
                        "center", "flex-start", "flex-end"),
    "align-items": ("center", "flex-start", "flex-end", "baseline", "stretch"),
}


def sanitize_style(style):
    """把一段 CSS 声明收敛到微信认的白名单属性。"""
    if not style:
        return ""
    out = []
    for decl in (style or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if not prop or not val:
            continue
        if prop.startswith("-"):          # -webkit- / -moz- 前缀
            continue
        if prop in WX_STYLE_DROP:
            continue
        if prop not in WX_STYLE_KEEP:
            continue
        if "gradient" in val or "url(" in val:   # 渐变 / 外链图 必丢
            continue
        # !important 没必要（正文就我们一份样式），留着反而可能被编辑器拦下整条声明
        val = re.sub(r"\s*!\s*important\s*$", "", val, flags=re.I).strip()
        if not val:
            continue
        ok_vals = WX_STYLE_VALUE_OK.get(prop)
        if ok_vals is not None:
            head = val.lower().replace("!important", "").split()
            if not head or head[0] not in ok_vals:
                continue
        out.append("%s:%s" % (prop, val))
    return ";".join(out)


def pm_safe_body(html):
    """把文章 HTML 降级成「微信编辑器认得住」的稿子。

    与浏览器预览版的区别：预览版可以随便用 section/div + 渐变 + flex，
    公众号稿必须：容器 unwrap（div/section 会被整块吞）、样式收敛到内联
    白名单、去掉 h1（标题单独填在标题区）。

    关键：不是把 style 全删（那会变成一坨没样式的字），而是把 style
    「过滤」到只剩微信支持的那几个属性，这样颜色和字号能活下来。
    """
    if not html:
        return ""
    # script / style / link 必丢，直接砍掉内容
    html = re.sub(r"<(script|style|link)[^>]*>.*?</\1>", "", html,
                  flags=re.S | re.I)
    html = re.sub(r"<(script|style|link)[^>]*/?>", "", html, flags=re.I)
    # 注释（含 build 时的标题/摘要建议注释）不进正文
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    # Markdown 残留的 **加粗** / *斜体* → 语义标签
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html, flags=re.S)
    html = re.sub(r"(?<![\*\w])\*(?!\*)([^\*\n]+?)\*(?![\*\w])",
                  r"<em>\1</em>", html)
    # h1：标题区已单独填，正文里不再重复（同时避免变成正文首行长标题）
    html = re.sub(r"<h1[^>]*>.*?</h1>", "", html, flags=re.S | re.I)
    # div / section 会被 ProseMirror 整块吞掉 → unwrap（只去标签，留内容）
    html = re.sub(r"</?(div|section|article|main|header|footer)\b[^>]*>",
                  "", html, flags=re.I)
    # 表格微信不认，塌成段落
    html = re.sub(r"</?(table|thead|tbody|tr|colgroup)\b[^>]*>",
                  "", html, flags=re.I)
    html = re.sub(r"<t[dh]\b[^>]*>", "<span>", html, flags=re.I)
    html = re.sub(r"</t[dh]>", "</span> ", html, flags=re.I)
    # class / id / data-* 微信不认，全清
    html = re.sub(r'\s+(class|id|data-[a-z-]+|on[a-z]+)="[^"]*"', "", html,
                  flags=re.I)
    # 核心：style 属性收敛到白名单（保留颜色/字号，丢掉 flex/渐变/定位）
    def _style_repl(m):
        new = sanitize_style(m.group(1))
        return ' style="%s"' % new if new else ""
    html = re.sub(r'\sstyle="([^"]*)"', _style_repl, html, flags=re.I)
    # 空样式属性 / 多余空白
    html = re.sub(r'\sstyle=""', "", html)
    html = re.sub(r"\n\s*\n+", "\n", html)
    return html.strip()


# 封面主题：(底色, 右侧色块, 强调色)。三色都刻意**避开近黑区**。
# 旧版用线性渐变 #0f2b1c -> #0d1117，两端亮度都在 0~40，整图只跨约 20 个色阶，
# 显示端只要把暗部提一档，量化台阶就变成肉眼可见的斜向色带（2026-09-21 实测）。
COVER_THEMES = [
    ("#10362c", "#1d5f4a", "#5fe3a6"),     # 松绿
    ("#221b3d", "#3b2c66", "#b79bff"),     # 紫罗兰
    ("#0f2c40", "#1c4f6e", "#6cc7f0"),     # 深海蓝
    ("#3a2418", "#5e3a22", "#f0a860"),     # 暖棕
    ("#2f2a12", "#554b1e", "#e8d46b"),     # 橄榄金
    ("#33182a", "#5a2745", "#ff9ecb"),     # 洋红
]


def _text_em_width(s):
    """估算一段文字占多少 em（字宽相对于字号）：CJK/全角按 1.0，拉丁按 0.56。"""
    em = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E7F or ch in "（）：，。、·—":      # CJK / 全角标点
            em += 1.0
        elif ch == " ":
            em += 0.28
        else:
            em += 0.56
    return em


def _fit_font_size(text, max_w, sizes):
    """从大到小挑第一个装得下的字号，避免出现省略号截断。"""
    em = max(0.1, _text_em_width(text))
    for f in sizes:
        if em * f <= max_w:
            return f
    return sizes[-1]


# 封面文字区的可用宽度（左 78px 起，右侧留 330px 给色块/标注）
_COVER_TEXT_W = 664


def cover_html(c, index, w=1080, h=460):
    """生成 1080x460 公众号封面图的完整 HTML 文档（2.35:1）。

    设计要点（每一条都是踩坑换来的）：
    1. 必须带 <!doctype html> + <meta charset="utf-8">：data URL 不声明编码时
       Chrome 按 Latin-1 解码，中文全变乱码（实测「用 AI」→「ç"¨ AI」）。
    2. **不用平滑渐变**，改成「硬停」渐变（色标重叠，无插值）＝两块纯色 + 一条硬边。
       纯色区域在 PNG 里是常量，压缩、缩放、色彩变换都不会产生色带。
    3. 画布按 1080x460 设计（微信最终就存这个尺寸），字号在这里定死，
       避免「大画布设计 + 被微信缩小」导致字号等效变小而发虚。
    4. 字号按实测字宽自适应，任意长度都不截断（截断比小字号更难看）。
    """
    name = (c.get("name") or c.get("id") or "").strip()
    line = (c.get("one_liner") or "").strip()
    tag = "拆解海外 · 第 %d 篇" % (index + 1)
    theme = COVER_THEMES[sum(ord(x) for x in c.get("id", "")) % len(COVER_THEMES)]
    base, band, ac = theme

    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    f_name = _fit_font_size(name, _COVER_TEXT_W, [68, 62, 56, 50, 46, 42, 38, 34, 30])
    f_line = _fit_font_size(line, _COVER_TEXT_W, [28, 25, 23, 21, 19, 17])

    body = (
        '<div style="position:relative;width:%dpx;height:%dpx;'
        'background:linear-gradient(100deg,%s 0%%,%s 63%%,%s 63%%,%s 100%%);'
        "font-family:'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif;"
        'box-sizing:border-box;overflow:hidden;">'
        # 左侧强调竖条
        '<div style="position:absolute;left:0;top:0;width:12px;height:100%%;'
        'background:%s;"></div>'
        # 右上装饰短线（硬边矩形，抗压缩）
        '<div style="position:absolute;right:56px;top:56px;width:150px;height:10px;'
        'background:%s;opacity:.55;"></div>'
        '<div style="position:absolute;right:56px;top:80px;width:96px;height:10px;'
        'background:%s;opacity:.32;"></div>'
        # 文字区
        '<div style="position:absolute;left:78px;top:104px;width:%dpx;">'
        '<div style="font-size:17px;font-weight:700;color:%s;'
        'letter-spacing:6px;">CASE STUDY</div>'
        '<div id="t-name" style="font-size:%dpx;font-weight:800;color:#ffffff;'
        'line-height:1.1;margin-top:20px;overflow:hidden;'
        'text-overflow:ellipsis;white-space:nowrap;">%s</div>'
        '<div id="t-line" style="font-size:%dpx;font-weight:500;color:%s;'
        'line-height:1.45;margin-top:20px;overflow:hidden;'
        'text-overflow:ellipsis;white-space:nowrap;">%s</div>'
        '</div>'
        '<div style="position:absolute;right:56px;bottom:38px;font-size:17px;'
        'color:rgba(255,255,255,.72);letter-spacing:2px;">%s</div>'
        '</div>'
        % (w, h, base, band, band, band, ac, ac, ac,
           _COVER_TEXT_W, ac, f_name, esc(name), f_line, ac, esc(line), tag)
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>html,body{margin:0;padding:0;}</style></head>"
        "<body>%s</body></html>" % body
    )


COVER_W, COVER_H = 1080, 460


def _gen_cover(cdp, c, index, out_path):
    """用 Chrome CDP 把封面 HTML 截成 PNG。

    按 1080x460 画布设计、scale=2 截图（约 2160x920 起，实际再乘浏览器
    deviceScaleFactor），微信最终会归一化到 1080x460 —— 走它自己的高质量
    降采样，比自己先缩好更锐。
    """
    html = cover_html(c, index, COVER_W, COVER_H)
    data_url = "data:text/html;charset=utf-8;base64," + base64.b64encode(
        html.encode("utf-8")).decode("ascii")
    cover_tid = cdp.new_target(data_url)["id"]
    cover_cdp = None
    try:
        cover_cdp = CDP(cdp.port)
        cover_cdp.connect_target(cover_tid)
        # 等页面渲染（含字体）
        time.sleep(1.2)
        # 文字自适应：估算公式只是起手式（对「拉丁+CJK 混排 + 粗体」会偏窄 5%），
        # 真正以页面实测为准 —— 溢出就把该行字号缩 7% 再来一轮，最多 10 轮。
        # 这样任何长度的名字都不会出现省略号（截断比小字号难看得多）。
        check = (
            "(function(){var chg=[];var over=[];"
            "['t-name','t-line'].forEach(function(id){"
            "var e=document.getElementById(id);if(!e)return;"
            "if(e.scrollWidth>e.clientWidth+1){"
            "var fs=parseFloat(getComputedStyle(e).fontSize);"
            "var nf=Math.max(18,fs*0.93);"
            "e.style.fontSize=nf+'px';"
            "if(e.scrollWidth>e.clientWidth+1)over.push(id);"
            "chg.push(id+':'+Math.round(fs)+'->'+Math.round(nf));}});"
            "return JSON.stringify({chg:chg,over:over});})()")
        try:
            for _ in range(10):
                o = json.loads(cover_cdp.eval(check, refresh_context=True))
                if not o.get("chg"):
                    break
            if o.get("over"):
                print("      [warn] 封面文字缩到下限仍溢出: %s (%s)"
                      % (o["over"], (c.get("name") or "")[:30]))
        except Exception as e:                                   # noqa: BLE001
            print("      [warn] 封面文字自检异常: %s" % e)
        r = cover_cdp.send("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": 0, "y": 0, "width": COVER_W, "height": COVER_H,
                     "scale": 2}
        })
        data = r.get("result", {}).get("data", "")
        if not data:
            return "NO_DATA"
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(data))
        return out_path
    finally:
        try:
            cdp.close_target(cover_tid)
        except Exception:
            pass
        if cover_cdp and cover_cdp.bws is not None:
            try:
                cover_cdp.bws.close()
            except Exception:
                pass


def _upload_cover(cdp, path):
    """把封面图插进正文最前面，送进微信图床（= 同时进了素材库）。

    实测（2026-09-20）：公众号封面菜单**没有「本地上传」**
    （只有 从正文选择 / 从图片库选择 / 微信扫码上传 / AI 配图），
    Page.setInterceptFileChooserDialog 也拦不到（它走自定义弹层）；
    而页面里有一个正文插图用的 input[type=file][name=file]。

    ⚠ 2026-09-21 重要更正：本函数**只负责把图送进素材库**，它**不设封面**。
    旧注释写的「编辑器的默认策略是默认首图为封面，插进正文就自动有了」
    是**错的** ——「默认首图为封面」只是封面菜单里的一句可选文案，不点它
    系统不会自动设。草稿箱列表项缩略图读的是服务端 `cover` 字段，不设就
    永远是灰块（用户就是这么发现的）。真正设封面见 _set_cover_from_body()。
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return "NO_FILE:%s" % path
    try:
        # 先把光标顶到正文开头，保证图是正文第一张（= 封面候选首图）
        cdp.eval(
            "(function(){"
            "var eds=[].slice.call(document.querySelectorAll("
            "'.ProseMirror[contenteditable=\"true\"]'))"
            ".filter(function(e){return (''+e.className).indexOf('js_reprint')<0});"
            "eds.sort(function(a,b){return a.getBoundingClientRect().top"
            "-b.getBoundingClientRect().top});"
            "var el=eds[1];if(!el)return 'NO_EDITOR';"
            "el.focus();"
            "var r=document.createRange();r.selectNodeContents(el);r.collapse(true);"
            "var s=window.getSelection();s.removeAllRanges();s.addRange(r);"
            "return 'OK';})()"
        )
        cdp.send("DOM.enable")
        doc = cdp.send("DOM.getDocument", {"depth": 0})
        root = (doc.get("result") or {}).get("root", {}).get("nodeId")
        if not root:
            return "NO_ROOT"
        q = cdp.send("DOM.querySelector", {
            "nodeId": root, "selector": "input[type=file][name=file]"})
        node_id = (q.get("result") or {}).get("nodeId")
        if not node_id:
            return "NO_FILE_INPUT"
        r = cdp.send("DOM.setFileInputFiles",
                     {"nodeId": node_id, "files": [path]})
        if "error" in r:
            return "SET_ERR:%s" % r["error"]
        # 等图真的上传到微信图床（src 变成 mmbiz.qpic.cn）
        for _ in range(25):
            time.sleep(1)
            got = cdp.eval(
                "(function(){"
                "var eds=[].slice.call(document.querySelectorAll("
                "'.ProseMirror[contenteditable=\"true\"]'))"
                ".filter(function(e){return (''+e.className).indexOf('js_reprint')<0});"
                "eds.sort(function(a,b){return a.getBoundingClientRect().top"
                "-b.getBoundingClientRect().top});"
                "var el=eds[1];if(!el)return 'NO_EDITOR';"
                "var im=el.querySelectorAll('img');"
                "for(var i=0;i<im.length;i++){"
                "var src=im[i].getAttribute('src')||'';"
                "if(src.indexOf('mmbiz')>=0)return 'IMG:'+src.slice(0,50);}"
                "return 'NO_IMG';})()")
            if isinstance(got, str) and got.startswith("IMG:"):
                # 这里只管「图进没进图床」，不再顺带判断封面有没有设上 ——
                # 旧代码用 .js_cover_preview_new img 轮询封面，那个节点在没设
                # 封面时是 display:none 且里面没有 <img>（是 background-image），
                # 所以轮询永远拿不到 COVER_OK，等于白等 10 秒。
                return "OK:%s" % got[4:]
        return "SET_BUT_NO_IMG"
    except Exception as e:
        return "ERR:%s" % e


# ---- 封面设置：步骤文案（2026-09-21 实测）-------------------------------
# 提成常量是为了防手滑：最后一步是「确认」**不是**「确定」，写错就点不到
# 按钮，而弹窗照样会关掉 —— 封面静默失败，极难发现。
COVER_MENU_FROM_BODY = "从正文选择"
COVER_STEP_NEXT = "下一步"
COVER_STEP_CONFIRM = "确认"


def _mouse_click(cdp, x, y):
    """真实鼠标点击。Vue 组件（弹窗/下拉/开关）对 JS .click() 经常无响应，
    必须走 Input.dispatchMouseEvent 才能进状态；先 mouseMoved 再按下，
    因为部分组件只认「hover 过」的元素。"""
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y, "buttons": 0})
    time.sleep(0.05)
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": x, "y": y,
              "button": "left", "clickCount": 1, "buttons": 1})
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": x, "y": y,
              "button": "left", "clickCount": 1, "buttons": 0})


def _center_of(cdp, sel, text=None, scroll=False):
    """取第一个可见匹配元素的中心坐标，找不到返回 None。

    优先「最内层」（children.length===0）：同一段文案常常外层容器和里面
    按钮都能匹配，点容器中心偶尔落在视觉空隙上。

    scroll=True 时**必须分两次 eval** —— 同一次里 scrollIntoView() 之后
    立刻 getBoundingClientRect() 拿到的还是滚动前的旧值，表现为坐标停在
    y=3391 这种视口外位置；dispatchMouseEvent 用的是视口坐标，等于点空。
    今天在这个坑上栽了两次，第二次才发现原因。
    """
    def _mk(leaf_only, with_scroll):
        return (
            "(function(){"
            "var els=[].slice.call(document.querySelectorAll(%s));"
            "for(var i=0;i<els.length;i++){"
            "var e=els[i],cs=getComputedStyle(e);"
            "if(cs.display==='none'||cs.visibility==='hidden')continue;"
            "%s"
            "var t=(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();"
            "if(%s&&t.indexOf(%s)<0)continue;"
            "var r=e.getBoundingClientRect();"
            "if(r.width<5||r.height<5)continue;"
            "%s"
            "return JSON.stringify({x:Math.round(r.left+r.width/2),"
            "y:Math.round(r.top+r.height/2),txt:t.slice(0,24)});}"
            "return '';})()" % (
                json.dumps(sel),
                "if(e.children.length>0)continue;" if leaf_only else "",
                ("true" if text else "false"), json.dumps(text or ""),
                "e.scrollIntoView({block:'center'});" if with_scroll else ""))
    if scroll:
        if not cdp.eval(_mk(True, True)) and not cdp.eval(_mk(False, True)):
            return None
        time.sleep(0.7)
    raw = cdp.eval(_mk(True, False)) or cdp.eval(_mk(False, False))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _click_visible(cdp, sel, text=None, scroll=False):
    """真实鼠标点击第一个可见匹配元素。

    ⚠ 点**弹窗按钮**别用这个：它按文本「包含」在**全文档**搜，正文里出现同名
    文字就会先命中（见 _center_in_dialog 的实测记录）。弹窗按钮用 _click_in_dialog。
    """
    d = _center_of(cdp, sel, text, scroll)
    if not d:
        return "NO_VISIBLE:%s" % sel
    _mouse_click(cdp, d["x"], d["y"])
    return "CLICKED:%s@(%d,%d)" % (d.get("txt", ""), d["x"], d["y"])


_DLG_CANDS_JS = (
    "(function(){"
    "var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
    "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
    "if(r.width>300&&r.height>100){dlg=ds[i];break;}}"
    "if(!dlg)return '';"
    "var out=[];"
    "var all=[].slice.call(dlg.querySelectorAll('button,a,span,div'));"
    "for(var j=0;j<all.length;j++){var e=all[j],cs=getComputedStyle(e);"
    "var r=e.getBoundingClientRect();"
    "out.push({t:(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40),"
    "left:Math.round(r.left),top:Math.round(r.top),"
    "w:Math.round(r.width),h:Math.round(r.height),"
    "disp:cs.display,vis:cs.visibility,leaf:e.children.length===0});}"
    "return JSON.stringify(out);})()")


def _pick_dialog_button(cands, text):
    """从弹窗候选元素里挑点击目标（纯函数，便于回归测试）。

    优先级：① 文本**完全相等**的叶子 → ② 完全相等 → ③ 包含的叶子 → ④ 包含。
    并且要求可见、够大（≥5px）、坐标落在视口内（left/top ≥ 0）。

    为什么不直接「包含就选」：正文段落也「包含」按钮文字，且在 DOM 里更靠前，
    会先被选中（点击落在正文上、弹窗按钮没点到）。所以先要「完全相等」，
    再要求是叶子节点；正文段落是容器、且文字长，两道都过不了。
    """
    def usable(c):
        return (c.get("disp") != "none" and c.get("vis") != "hidden"
                and c.get("w", 0) >= 5 and c.get("h", 0) >= 5
                and c.get("left", -1) >= 0 and c.get("top", -1) >= 0)

    def has(c, exact):
        return (c.get("t", "") == text) if exact else (text in c.get("t", ""))

    for exact, leaf_only in ((True, True), (True, False),
                             (False, True), (False, False)):
        for c in cands:
            if usable(c) and has(c, exact) and (not leaf_only or c.get("leaf")):
                return c
    return None


def _center_in_dialog(cdp, text):
    """在**当前可见弹窗内部**找按钮并返回中心坐标，找不到返回 None。

    为什么要单独一个函数（2026-09-21 实测，代价是一次批量发布里 3 篇残废）：
    用 `_click_visible(cdp, "a,button,span,div", "确认")` 点封面的「确认」时，
    它是**全文档**按文本「包含」搜、按 DOM 顺序取第一个 —— 正文里只要出现
    「确认」二字（案例里写"外部无法确认哪个是当前值"这种句子太常见了），
    先命中的就是**正文段落**，于是：
      ① 弹窗的「确认」根本没点到 → 封面设不上（服务端 cover 为空 → 草稿箱灰块）；
      ② 「编辑封面」弹窗一直留着 → 紧接着的原创声明找到的可见弹窗是它，
         里面没有作者输入框 → 报「作者框没找到(NO)」。
    一个错点同时造出两个"故障"。修法：把搜索范围钉死在可见弹窗里，
    并优先**文本完全相等**的叶子节点，再要求坐标落在视口内。
    """
    raw = cdp.eval(_DLG_CANDS_JS)
    if not raw:
        return None                      # 没有可见弹窗
    try:
        cands = json.loads(raw)
    except Exception:
        return None
    c = _pick_dialog_button(cands, text)
    if not c:
        return None
    return {"x": c["left"] + c["w"] // 2,
            "y": c["top"] + c["h"] // 2,
            "txt": c["t"][:24]}


def _click_in_dialog(cdp, text):
    """真实鼠标点击弹窗内的按钮（搜索范围限于可见弹窗）。"""
    d = _center_in_dialog(cdp, text)
    if not d:
        return "NO_IN_DIALOG:%s" % text
    _mouse_click(cdp, d["x"], d["y"])
    return "CLICKED:%s@(%d,%d)" % (d.get("txt", ""), d["x"], d["y"])


def _cover_state(cdp):
    """读封面预览节点的真实状态 -> (是否已设, 背景图 url)。

    `.js_cover_preview_new` 用的是 background-image（不是 <img>）；
    没设封面时它是 display:none 且 bg 为空 url("")。
    """
    raw = cdp.eval(
        "(function(){"
        "var p=document.querySelector('.js_cover_preview_new');"
        "if(!p)return '';"
        "var cs=getComputedStyle(p);"
        "return cs.display+'|'+(cs.backgroundImage||'');})()")
    if not isinstance(raw, str) or "|" not in raw:
        return False, ""
    disp, bg = raw.split("|", 1)
    return (disp != "none" and "mmbiz" in bg), bg


def _set_cover_from_body(cdp, tries=3):
    """把正文首图真正设为封面（服务端 cover 字段会落值）。

    2026-09-21 实测的完整路径，每一步都验证过：
      ① 点 `.js_cover_btn_area` 展开封面菜单（Vue 弹层，偶发不展开 → 重试）
      ② 菜单项「从正文选择」（用 JS click：弹层对鼠标移出很敏感，坐标点易扑空）
      ③ 弹窗「选择图片」里点 `li.appmsg_content_img_item` 选中正文首图
         —— 它是 background-image 的 span，不是 <img>，别按 img 找
      ④ 「下一步」→ 进「编辑封面」裁剪页
      ⑤ 「确认」  ← 是「确认」不是「确定」
    最后用 _cover_state 做**结果导向**校验：不看弹窗有没有关（微信弹窗提交
    成功后不会自动关闭，拿它当判据必然误判）。
    """
    ok, _bg = _cover_state(cdp)
    if ok:
        return "SKIP:已有封面"
    # ① 展开封面菜单（重试，弹层偶发不展开）
    opened = False
    for _ in range(tries):
        d = _center_of(cdp, ".js_cover_btn_area", scroll=True)
        if not d:
            return "NO_COVER_BTN"
        _mouse_click(cdp, d["x"], d["y"])
        time.sleep(1.3)
        n = cdp.eval(
            "(function(){var n=0;"
            "[].forEach.call(document.querySelectorAll('.pop-opr__item'),"
            "function(e){var cs=getComputedStyle(e);"
            "if(cs.display!=='none'&&cs.visibility!=='hidden'){"
            "var r=e.getBoundingClientRect();if(r.width>5)n++;}});"
            "return ''+n;})()")
        if str(n).strip() not in ("0", "", "None"):
            opened = True
            break
    if not opened:
        return "NO_MENU"
    # ② 菜单项「从正文选择」
    r = cdp.eval(
        "(function(){"
        "var its=[].slice.call(document.querySelectorAll('.pop-opr__item'));"
        "for(var i=0;i<its.length;i++){"
        "var t=(its[i].innerText||'').replace(/\\s+/g,' ').trim();"
        "if(t!==%s)continue;"
        "if(getComputedStyle(its[i]).display==='none')continue;"
        "var a=its[i].querySelector('a')||its[i];a.click();"
        "return 'OK';}"
        "return 'NO_ITEM';})()" % json.dumps(COVER_MENU_FROM_BODY))
    if not (isinstance(r, str) and r == "OK"):
        return "MENU_ITEM_ERR:%s" % r
    time.sleep(2.5)
    # ③ 选中正文首图
    d = _center_of(cdp, "li.appmsg_content_img_item")
    if not d:
        return "NO_THUMB"
    _mouse_click(cdp, d["x"], d["y"])
    time.sleep(1.3)
    # ④⑤ 下一步 → 确认（**弹窗内**找按钮，别搜全文档：正文里出现「确认」
    #     二字就会先命中正文段落，实测害惨了 outrank/bustem/kibu 三篇）
    r1 = _click_in_dialog(cdp, COVER_STEP_NEXT)
    if "CLICKED" not in r1:
        return "NEXT_ERR:%s" % r1
    time.sleep(2.8)
    r2 = _click_in_dialog(cdp, COVER_STEP_CONFIRM)
    if "CLICKED" not in r2:
        return "CONFIRM_ERR:%s" % r2
    time.sleep(2.5)
    ok, bg = _cover_state(cdp)
    if not ok:
        # 没设上就把残留弹窗收掉：留着会挡住后面的原创声明
        # （_declare_original 找可见弹窗时会把「编辑封面」当原创弹窗，
        #   然后报"作者框没找到"）。
        _close_dialog(cdp)
        time.sleep(0.6)
    return ("OK:%s" % bg[:70]) if ok else "NOT_SET"


def load_cases():
    if not os.path.exists(CASES_PATH):
        return []
    with open(CASES_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, list) else d.get("cases", [])


def load_published():
    if not os.path.exists(PUBLISHED_PATH):
        return {}
    with open(PUBLISHED_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_published(pub):
    with open(PUBLISHED_PATH, "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False, indent=2)


def find_article(cid, name=None):
    """在 ROOT 下找 公众号-<id|name>拆解.html（大小写不敏感）。"""
    want = [cid.lower()]
    if name:
        want.append(name.lower())
    for p in glob.glob(os.path.join(ROOT, "公众号-*.html")):
        base = os.path.basename(p).lower()
        for w in want:
            if w and w in base:
                return p
    return None


# ======================================================================
# 文章抽取（root 精修版 与 build 生成版 通用）
# ======================================================================
def extract_article(path):
    text = open(path, encoding="utf-8").read()
    title = summary = cover = None
    for c in re.findall(r"<!--(.*?)-->", text, re.S):
        if "主标题" in c or "标题" in c:
            m = re.search(r"主标题[:：]\s*(.+)", c)
            if m:
                title = m.group(1).strip()
            m = re.search(
                r"摘要[^：:]*[:：]?\s*\n(.*?)(?=\n\s*(封面图|原文链接|同步|={4,}|-->))",
                c, re.S)
            if m:
                summary = m.group(1).strip()
            m = re.search(r"封面图[:：]\s*(.+)", c)
            if m:
                cover = m.group(1).strip()
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
        if m:
            title = strip_tags(m.group(1)).strip()
    m = re.search(r"<(div|section)[^>]*>(.*?)</\1>\s*(?:<!--|$)", text, re.S)
    body = m.group(2) if m else text
    return {
        "title": title, "summary": summary, "cover": cover,
        "body_html": body, "path": path,
        "body_len": len(body),
    }


# ======================================================================
# build：把 out/articles/<id>.html 修成发布就绪版
# ======================================================================
def fix_markdown(html):
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html, flags=re.S)
    html = re.sub(r"(?<![\*\w])\*(?!\*)([^\*\n]+?)\*(?![\*\w])", r"<em>\1</em>", html)
    return html


def build_one(c, index):
    cid = c["id"]
    src = os.path.join(ARTICLES_DIR, cid + ".html")
    if not os.path.exists(src):
        return "no-src"
    out = open(src, encoding="utf-8").read()
    out = fix_markdown(out)
    m = re.search(r"<h1[^>]*>(.*?)</h1>", out, re.S)
    title = strip_tags(m.group(1)).strip() if m else c.get("name", "")
    subtitle = c.get("one_liner", "")
    tag = "拆解海外 · 第 %d 篇" % (index + 1)
    block = []
    block.append("<!--\n万物解释者 · 公众号图文（自动生成，可直接粘贴版）\n"
                 "使用方法：浏览器打开 → 全选复制 → 粘进公众号后台「新建图文」正文区。\n"
                 "封面图、标题、摘要在后台另行填写（见文末建议）。\n-->")
    block.append('<div style="font-family:-apple-system,BlinkMacSystemFont,'
                 "'PingFang SC','Microsoft YaHei',sans-serif;color:#2b2b2b;"
                 'font-size:16px;line-height:1.85;letter-spacing:.2px;">')
    block.append('  <p style="margin:0 0 18px;font-size:13px;color:#1aad19;'
                 'font-weight:700;letter-spacing:2px;">%s</p>' % tag)
    block.append(out)
    block.append('  <hr style="border:none;border-top:1px solid #eee;'
                 'margin:22px 0 14px;">')
    block.append('  <p style="margin:0;text-align:center;font-size:13px;'
                 'color:#bbb;">— 万物解释者 · 拆解海外 —</p>')
    block.append("</div>")
    block.append("<!--\n==================== 公众号后台填写建议（不要复制这一段进正文）====================\n"
                 "标题（建议，≤30 字）：\n  主标题：%s\n"
                 "摘要（≤120 字）：\n  %s\n"
                 "封面图：公众号封面比例 2.35:1（900×383 或 1080×460），建议用「%s」相关的极简图。\n"
                 "原文链接：可留空。\n同步：本篇长文由「万物解释者」公众号发布。\n-->" %
                 (title, subtitle, c.get("name", "")))
    dest = os.path.join(ROOT, "公众号-%s拆解.html" % cid)
    if not os.path.exists(dest):                 # 文件名按 id；旧的按 name 命名的兼容一下
        alt = find_article(cid, c.get("name"))
        if alt:
            dest = alt
    text = "\n".join(block) + "\n"
    try:
        old = open(dest, encoding="utf-8").read()
    except OSError:
        old = None
    if old == text:
        return "same"
    # 原子写：本文件可能正被 publish 读取（read 中途看到半个文件会写出残稿）
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dest)
    return "updated" if old is not None else "built"


def cmd_build():
    cases = load_cases()
    built = updated = same = nosrc = 0
    for i, c in enumerate(cases):
        r = build_one(c, i)
        if r == "built":
            built += 1
            print("  [新建]    %-22s %s" % (c["id"], c.get("name", "")))
        elif r == "updated":
            updated += 1
            print("  [更新]    %-22s %s" % (c["id"], c.get("name", "")))
        elif r == "same":
            same += 1
        else:
            nosrc += 1
            print("  [缺源]    %-22s %s  (out/articles/%s.html 不存在)" %
                  (c["id"], c.get("name", ""), c["id"]))
    print("-" * 60)
    print("新建 %d · 更新 %d · 未变 %d · 缺源 %d" % (built, updated, same, nosrc))


# ======================================================================
# queue / plan
# ======================================================================
def compute_queue():
    cases = load_cases()
    published = load_published()
    ready, missing = [], []
    for c in cases:
        cid = c["id"]
        if cid in published:
            continue
        art = find_article(cid, c.get("name"))
        if not art:
            missing.append(c)
        else:
            ready.append((c, art))
    return ready, missing


def cmd_queue():
    ready, missing = compute_queue()
    print("待发布（有就绪 HTML 且未发布，按 cases.json 顺序）：%d 篇\n" % len(ready))
    for i, (c, art) in enumerate(ready, 1):
        ex = extract_article(art)
        print("  %2d. %-22s %s" % (i, c["id"], ex["title"] or c.get("name", "")))
    if missing:
        print("\n缺发布就绪 HTML（先跑 build）：%d 条" % len(missing))
        for c in missing:
            print("  - %-22s %s" % (c["id"], c.get("name", "")))


def cmd_plan():
    cases = load_cases()
    published = load_published()
    ready, missing = compute_queue()
    L = []
    L.append("# 拆解海外 · 发布队列（自动生成，勿手改）\n")
    L.append("> 本文件由 `scripts/wechat_publish.py plan` 实时生成。")
    L.append("> 待发队列 = cases.json 里有就绪 HTML 且未发布的案例，"
             "随新案例入库自动变长。\n")
    L.append("## 已发布（%d）\n" % len(published))
    for cid, info in sorted(published.items()):
        L.append("- %s — %s（%s）" % (cid, info.get("title", ""), info.get("published_at", "")))
    L.append("\n## 待发布（%d，按 cases.json 顺序）\n" % len(ready))
    for i, (c, art) in enumerate(ready, 1):
        ex = extract_article(art)
        L.append("- %d. %s — %s" % (i, c["id"], ex["title"] or c.get("name", "")))
    if missing:
        L.append("\n## 缺发布就绪 HTML（先跑 build，%d 条）\n" % len(missing))
        for c in missing:
            L.append("- %s（%s）" % (c["id"], c.get("name", "")))
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("已生成 %s" % os.path.relpath(PLAN_PATH, ROOT))
    print("待发布 %d · 已发布 %d · 缺源 %d" % (len(ready), len(published), len(missing)))


def cmd_inspect(args):
    ex = extract_article(args.file)
    print("文件：%s" % os.path.basename(args.file))
    print("标题：%s" % (ex["title"] or "（未抽取到）"))
    print("摘要：%s" % ((ex["summary"] or "（无，用 cases.one_liner 兜底）")[:120]))
    print("封面建议：%s" % (ex["cover"] or "（无）"))
    print("正文长度：%d 字符" % ex["body_len"])
    if args.file == args.file:  # 始终打印正文前 200 字预览
        preview = strip_tags(ex["body_html"])[:200].replace("\n", " ")
        print("正文预览：%s" % preview)


# ======================================================================
# CDP：纯标准库 WebSocket 客户端 + Chrome DevTools Protocol
# ======================================================================
class WS:
    def __init__(self, url):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((self.host, self.port), timeout=30)
        self._handshake()
        self._buf = b""

    def _handshake(self):
        key = os.urandom(16)
        req = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: %s\r\n\r\n"
            % (self.path, self.host, self.port, base64.b64encode(key).decode())
        )
        self.sock.sendall(req.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def _recv_frame(self):
        b0, b1 = self._read_exact(2)
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        if masked:
            self._read_exact(4)
        return fin, opcode, self._read_exact(length)

    def recv_text(self):
        parts = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode in (0x1, 0x0):
                parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "replace")

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytes([0x81])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(n))
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    def __init__(self, port=CDP_PORT):
        self.port = port
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            ver = self._http("/json/version")
        except urllib.error.URLError as e:
            raise SystemExit(
                "\n✗ 连不上 Chrome 调试端口 127.0.0.1:%d\n"
                "  请按顺序确认：\n"
                "  1) 已【完全退出】所有 Chrome 窗口（任务管理器确认无 chrome.exe 残留）\n"
                "  2) 用下面命令重启（同一用户目录，复用登录态）：\n"
                "     & \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" "
                "--remote-debugging-port=%d "
                "--user-data-dir=\"C:\\Users\\DELL\\AppData\\Local\\Google\\Chrome\\User Data\"\n"
                "  3) 浏览器打开 http://127.0.0.1:%d/json/version 应返回一段 JSON\n"
                "  原始错误：%s" % (port, port, port, e)
            )
        self.bws = WS(ver["webSocketDebuggerUrl"])
        self.ws = None
        self.tid = None          # 当前页面 target id，断线重连时要用
        self._id = 0
        self.debug = False

    def _http(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        with self._opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def list_targets(self):
        return self._http("/json")

    def send_browser(self, method, params=None):
        """走浏览器级 WebSocket（self.bws）发 CDP 命令，避开新版 Chrome
        已禁用的 HTTP /json/new 端点（GET/POST 都返回 405）。"""
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.bws.send_text(json.dumps(msg))
        while True:
            resp = json.loads(self.bws.recv_text())
            if resp.get("id") == self._id:
                return resp

    def new_target(self, url):
        # CDP 原生 Target.createTarget（HTTP /json/new 在新版 Chrome 被禁用）
        r = self.send_browser("Target.createTarget", {"url": url})
        if "error" in r:
            raise RuntimeError("Target.createTarget 失败: %s" % r["error"])
        return {"id": r["result"]["targetId"], "targetId": r["result"]["targetId"]}

    def connect_target(self, target_id):
        for _ in range(10):
            for t in self.list_targets():
                if t.get("id") == target_id and t.get("webSocketDebuggerUrl"):
                    self.ws = WS(t["webSocketDebuggerUrl"])
                    self.tid = target_id
                    self._main_context = self._enable_and_grab_context()
                    return True
            time.sleep(0.5)
        return False

    def _ws_readable(self, timeout=0.3):
        try:
            r, _, _ = select.select([self.ws.sock], [], [], timeout)
            return bool(r)
        except Exception:
            return False

    def _enable_and_grab_context(self, timeout=6):
        """发 Runtime.enable 并抓主帧默认执行上下文 id（SPA 导航会换上下文）。"""
        try:
            self.send("Runtime.enable")
        except Exception:
            pass
        end = time.time() + timeout
        best = None
        while time.time() < end:
            if not self._ws_readable(0.3):
                if best is not None:
                    break
                continue
            try:
                raw = self.ws.recv_text()
            except Exception:
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if ev.get("method") == "Runtime.executionContextCreated":
                p = ev.get("params", {})
                if p.get("auxData", {}).get("isDefault"):
                    best = p.get("id")
            elif ev.get("method") == "Runtime.executionContextsCleared":
                best = None
        return best

    def close_target(self, target_id):
        """只关掉某个具体 tab（不会影响用户其它窗口/登录态）。"""
        try:
            self.send_browser("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass

    def _fire(self, method, params=None):
        """只写命令、不读响应（避免 send 的读循环把事件帧吞掉）。"""
        self._id += 1
        self.ws.send_text(json.dumps(
            {"id": self._id, "method": method, "params": params or {}}))
        return self._id

    def collect_contexts(self, timeout=12):
        """采集当前 tab 的全部执行上下文（主帧 + 每个 iframe 各一个）。

        mp 编辑器的正文编辑区渲染在 iframe 内，必须钻进对应上下文才能拿到
        contenteditable 元素。返回 [{id, frameId, isDefault, name}]。

        关键坑1：导航后旧的 contextId 失效。
        关键坑2：send() 在等响应时会把无关帧（包括 executionContextCreated 事件）
        一并读掉并丢弃，若 Chrome 在 enable 响应「之前」就把事件推出来，就会被吞。
        → 这里用 _fire 只写 disable+enable（不读），然后在【同一个】读循环里收事件，
        不依赖 send 的任何读，确保事件无论先后顺序都能被抓到。
        """
        self._fire("Runtime.disable")
        self._fire("Runtime.enable")
        self._main_context = None
        ctxs = []
        seen = set()
        end = time.time() + timeout
        while time.time() < end:
            if not self._ws_readable(0.5):
                continue
            try:
                raw = self.ws.recv_text()
            except Exception:
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if "method" not in ev:
                continue  # 命令响应，忽略
            if ev["method"] == "Runtime.executionContextCreated":
                p = ev.get("params", {})
                cid = p.get("id")
                if cid in seen:
                    continue
                seen.add(cid)
                if p.get("auxData", {}).get("isDefault") and cid is not None:
                    self._main_context = cid
                ctxs.append({
                    "id": cid,
                    "frameId": p.get("auxData", {}).get("frameId"),
                    "isDefault": p.get("auxData", {}).get("isDefault", False),
                    "name": p.get("name") or "",
                    "type": p.get("auxData", {}).get("type", ""),
                })
            elif ev["method"] == "Runtime.executionContextsCleared":
                ctxs = []
                seen = set()
                self._main_context = None
        return ctxs

    def send(self, method, params=None):
        if self.ws is None:
            raise RuntimeError("未连接页面 target，先 new_target + connect_target")
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}

        def _roundtrip():
            self.ws.send_text(json.dumps(msg))
            if getattr(self, "debug", False):
                print("[debug send] → %s" % json.dumps(msg, ensure_ascii=False)[:300])
            while True:
                raw = self.ws.recv_text()
                if getattr(self, "debug", False):
                    print("[debug send] ← %s" % raw[:600])
                resp = json.loads(raw)
                if resp.get("id") == self._id:
                    return resp
                # 其余是事件，忽略

        try:
            return _roundtrip()
        except ConnectionError:
            # 页面 WebSocket 会**偶发**被对端断开。实测（2026-09-21）：长批次跑到
            # 第 13 篇时突然 "ws closed"，异常一路冒到 publish_one，整个 publish
            # 进程死掉，后面 16 篇全没发。重连同一 target 再试一次即可恢复。
            tid = getattr(self, "tid", None)
            if not tid or not self.connect_target(tid):
                raise
            print("  [warn] CDP 页面连接断开，已重连同一标签页")
            return _roundtrip()

    def eval(self, expr, refresh_context=False, debug=False, context_id=None):
        if refresh_context or (context_id is None
                               and getattr(self, "_main_context", None) is None):
            self._main_context = self._enable_and_grab_context()
        params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
        ctx = context_id if context_id is not None else getattr(self, "_main_context", None)
        if ctx is not None:
            params["contextId"] = ctx
        if debug:
            print("[debug eval] contextId=%s" % ctx)
        last = None
        for _ in range(6):
            r = self.send("Runtime.evaluate", params)
            if debug:
                print("[debug eval] 原始响应=%s" % json.dumps(r, ensure_ascii=False)[:1000])
            if "error" in r:
                # 执行上下文被销毁/SPA 导航导致，刷新上下文后重试一次
                if r["error"].get("code") == -32000 and _ < 3:
                    self._main_context = self._enable_and_grab_context()
                    if self._main_context is not None:
                        params["contextId"] = self._main_context
                    time.sleep(1)
                    continue
                raise RuntimeError("CDP 协议错误: %s" % r["error"])
            if "exceptionDetails" in r:
                exc = r["exceptionDetails"].get("exception", {})
                raise RuntimeError("JS 执行异常: %s" %
                                   exc.get("description", r["exceptionDetails"]))
            # CDP Runtime.evaluate 的返回结构是 result.result.value（两层 result）
            last = r.get("result", {}).get("result", {}).get("value")
            if last is not None:
                return last
            time.sleep(1)  # 页面/执行上下文偶尔未就绪，重试
        # 带显式 contextId（iframe）查询时不再兜底到主上下文（会查错帧）；
        # 只有用默认主上下文时，才最后不带 contextId 兜底试一次。
        if last is None and ctx is not None and context_id is None:
            r = self.send("Runtime.evaluate",
                          {"expression": expr, "returnByValue": True,
                           "awaitPromise": True})
            if debug:
                print("[debug eval] 兜底原始响应=%s" % json.dumps(r, ensure_ascii=False)[:1000])
            if "error" in r:
                raise RuntimeError("CDP 协议错误: %s" % r["error"])
            if "exceptionDetails" in r:
                exc = r["exceptionDetails"].get("exception", {})
                raise RuntimeError("JS 执行异常: %s" %
                                   exc.get("description", r["exceptionDetails"]))
            last = r.get("result", {}).get("result", {}).get("value")
        return last

    def close_browser(self):
        try:
            self.bws.send_text(json.dumps(
                {"id": 9999, "method": "Browser.close", "params": {}}))
        except Exception:
            pass


# ======================================================================
# discover：导出编辑器 DOM，供锁定 SEL 选择器
# ======================================================================
def _get_token(cdp, debug=False):
    """从已登录主页页面动态抠出 token（mp 的链接/全局变量里都有）。"""
    expr = (
        "(function(){"
        "var m=(location.href||'').match(/[?&]token=(\\d+)/);"
        "if(m)return m[1];"
        "try{if(window.wx&&window.wx.cgiData&&window.wx.cgiData.token)"
        "return window.wx.cgiData.token;}catch(e){}"
        "m=document.documentElement.outerHTML.match(/[?&]token=(\\d+)/);"
        "return m?m[1]:'NO_TOKEN';})()"
    )
    return cdp.eval(expr, refresh_context=True, debug=debug)


def _try_login(cdp):
    """token 拿不到时，尝试点击页面上的登录/重新登录按钮（本地微信已登录时可自动登入）。"""
    expr = (
        "(function(){"
        "var els=[].slice.call(document.querySelectorAll('a,button,.js_login,.login_link,label'));"
        "for(var i=0;i<els.length;i++){"
        "var t=(els[i].textContent||els[i].title||'').trim();"
        "if(t.indexOf('重新登录')>=0||t.indexOf('登录')>=0||t.indexOf('扫码登录')>=0){"
        "els[i].click();return 'CLICKED:'+t;}"
        "if(els[i].className&&els[i].className.indexOf('login')>=0){"
        "els[i].click();return 'CLICKED:cls';}}"
        "return 'NO_LOGIN_BTN';})()"
    )
    try:
        return cdp.eval(expr, refresh_context=True)
    except Exception as e:
        return "ERR:%s" % e


def _open_editor(cdp, debug=False):
    """开一个已登录主页 tab，抠 token，再导航进【新建图文】编辑器。返回 tid。
    不能直接用带 token 的 URL 开 target——token 会过期，必须运行时抠。"""
    tid = cdp.new_target(MP_HOME)["id"]
    cdp.connect_target(tid)
    # token 在主页 URL 里，但页面可能还在跳转/加载，轮询重试（最多 ~25 秒）。
    # 若碰到「请重新登录」页面，尝试点一下登录按钮（本地微信已登录时可自动登入）。
    token = None
    login_attempted = False
    for i in range(25):
        time.sleep(1)
        try:
            token = _get_token(cdp, debug=debug)
        except Exception:
            token = None
        if token and token != "NO_TOKEN":
            break
        if i >= 3 and not login_attempted:
            login_attempted = True
            lr = _try_login(cdp)
            if debug:
                print("[debug] 尝试登录: %s" % lr)
            if isinstance(lr, str) and lr.startswith("CLICKED"):
                # 点击登录后页面会跳转，继续轮询 token
                continue
    if not token or token == "NO_TOKEN":
        try:
            href = cdp.eval("(function(){return location.href;})()", refresh_context=True)
        except Exception:
            href = "(无法读取)"
        cdp.close_target(tid)
        raise RuntimeError(
            "没从主页抠到 token。请确认调试窗口里 mp 是【已登录】状态，再重试。"
            "当前 href=%s" % href)
    if debug:
        print("[debug] 拿到 token=%s" % token)
    editor_url = ("https://mp.weixin.qq.com/%s&token=%s&lang=zh_CN"
                  % (MP_EDITOR_PATH, token))
    cdp.send("Page.enable")
    cdp.send("Page.navigate", {"url": editor_url})
    # 导航后旧 contextId 失效，且编辑器是重型页面（ProseMirror 等），必须等 #title
    # 真正出现再返回——否则 publish_one 复用过期上下文去查 #title 会 NO_EL。
    # 轮询期间 refresh_context=True 持续刷新执行上下文到编辑器页的 default。
    ready = False
    for _ in range(20):
        try:
            ready = cdp.eval(
                "(function(){return !!document.querySelector('#title');})()",
                refresh_context=True)
        except Exception:
            ready = False
        if ready:
            break
        time.sleep(1)
    if not ready:
        try:
            href = cdp.eval("(function(){return location.href;})()",
                            refresh_context=True)
        except Exception:
            href = "(无法读取)"
        cdp.close_target(tid)
        raise RuntimeError(
            "导航进编辑器后 20 秒内 #title 仍未出现（疑似未登录或加载失败）。"
            "当前 href=%s" % href)
    return tid


def cmd_discover(args):
    cdp = CDP(args.port)
    cdp.debug = args.debug
    tid = _open_editor(cdp, debug=args.debug)
    print("已导航到草稿编辑器，等待加载…")
    print("(编辑器正文区通常在 iframe 内，本工具会逐个上下文探测：主帧 + 每个 iframe)")
    time.sleep(3)

    # 1) 采集全部执行上下文（主帧默认上下文 + 每个 iframe 一个）
    ctxs = cdp.collect_contexts(timeout=12)
    print("\n[上下文] 共 %d 个执行上下文：" % len(ctxs))
    for c in ctxs:
        print("  ctx=%s frame=%s default=%s type=%s name=%s"
              % (c["id"], c["frameId"], c["isDefault"], c["type"], c["name"]))
    if not ctxs:
        cdp.close_target(tid)
        raise RuntimeError("连一个执行上下文都没采到，说明页面没起来。确认 Chrome 调试窗口"
                           "里 mp 已登录、编辑器已打开，再 discover 一次。")

    probe = (
        "(function(){try{"
        "var inputs=[].slice.call(document.querySelectorAll('input,textarea')).map(function(e){"
        "return {t:e.tagName,id:e.id,name:e.name,ph:e.placeholder,"
        "cls:(''+e.className).slice(0,40)};});"
        "var eds=[].slice.call(document.querySelectorAll("
        "'[contenteditable=\"true\"],.edui-editor-body,[contenteditable]')).map(function(e){"
        "return {cls:(''+e.className).slice(0,50),id:e.id,ce:e.getAttribute('contenteditable')};});"
        "var btns=[].slice.call(document.querySelectorAll('button,a')).map(function(e){"
        "return (e.textContent||'').trim();}).filter(function(s){return s&&s.length<16;});"
        "return JSON.stringify({href:location.href,nodes:document.querySelectorAll('*').length,"
        "inputs:inputs.slice(0,50),editable:eds.slice(0,10),"
        "buttons:Array.from(new Set(btns)).slice(0,80)});"
        "}catch(e){return 'ERR:'+e.message;}})()"
    )

    any_hit = False
    for c in ctxs:
        print("\n================ 上下文 %s (frame=%s, default=%s) ================"
              % (c["id"], c["frameId"], c["isDefault"]))
        out = None
        try:
            out = cdp.eval(probe, context_id=c["id"], debug=args.debug)
        except RuntimeError as e:
            print("  [探针失败] %s" % e)
            continue
        if not out or out.startswith("ERR:"):
            print("  [空] %s" % (out or "(无返回)"))
            continue
        try:
            data = json.loads(out)
        except ValueError:
            print("  [非 JSON] %s" % out[:200])
            continue
        if not data.get("editable") and not data.get("inputs"):
            print("  [无输入框/可编辑区] nodes=%s href=%s"
                  % (data.get("nodes"), data.get("href")))
            continue
        any_hit = True
        print("  URL/href: %s" % data.get("href"))
        print("  --- 输入框（input/textarea）---")
        for e in data.get("inputs", []):
            print("    %-8s id=%-18s name=%-14s ph=%-22s cls=%s"
                  % (e["t"], e["id"] or "-", e["name"] or "-",
                     (e["ph"] or "-"), e["cls"]))
        print("  --- 可编辑区（contenteditable）---")
        for e in data.get("editable", []):
            print("    id=%-14s ce=%-8s cls=%s" % (e["id"] or "-", e["ce"] or "-", e["cls"]))
        print("  --- 按钮/链接文本（去重，前 80）---")
        print("    " + " | ".join(data.get("buttons", [])))

    if not any_hit:
        # 兜底：打印主帧原始 DOM 片段，便于判断到底加载成什么样
        print("\n[兜底] 所有上下文都没抓到输入框/可编辑区，打印主帧 DOM 前 3000 字：")
        try:
            html = cdp.eval(
                "(function(){try{return document.documentElement.outerHTML.slice(0,3000);}"
                "}catch(e){return 'ERR:'+e.message;}})()", refresh_context=True)
            print(html)
        except RuntimeError as e:
            print("  [DOM 片段失败] %s" % e)
        cdp.close_target(tid)
        raise RuntimeError(
            "编辑器 DOM 探测返回空。常见两种：\n"
            "  (1) 页面没加载完（nodes 很少 / href 不是 appmsg_edit）——再 discover 一次；\n"
            "  (2) 编辑区在 iframe 里但 iframe 上下文还没报到——多等几秒再 discover。\n"
            "把上方「[上下文]」列表和任一 «=== 上下文 ... ===» 段落贴给我，我据此填 SEL。")

    print("\n把上面标 «可编辑区» / «输入框» 的 id/cls 填进脚本顶部的 SEL 字典，再跑 publish。")
    cdp.close_target(tid)   # 只关 discover 开的这个 tab，不动你的浏览器


# ======================================================================
# publish：把队列逐条存成草稿
# ======================================================================
def _need(sel_key):
    if SEL[sel_key] == "TODO":
        raise SystemExit("✗ 选择器 SEL['%s'] 还是 TODO。先跑 `discover` 锁定后填进脚本。"
                         % sel_key)


# 新编辑器里可见标题/正文都是 ProseMirror contenteditable（隐藏域 #title 只是
# 预览卡用的表单字段，填它不会更新可见编辑区）。
# 实测结论（2026-09-20）：
#   合成 ClipboardEvent('paste') → 只改 DOM 表面，ProseMirror 内部文档不变，
#     于是「正文字数 0」、保存后正文为空。
#   document.execCommand('insertText'/'insertHTML') → 编辑器走原生输入路径，
#     ProseMirror 会同步内部文档，字数统计与保存都认。这是唯一可靠方式。
JS_PICK_PM = (
    "(function(){"
    "var eds=[].slice.call(document.querySelectorAll('.ProseMirror[contenteditable=\"true\"]'))"
    ".filter(function(e){return (''+e.className).indexOf('js_reprint')<0});"
    "eds.sort(function(a,b){return a.getBoundingClientRect().top-b.getBoundingClientRect().top});"
    "return eds[%d]||null;"
    "})()"
)


def _set_editor(cdp, index, content, mode="text"):
    """往第 index 个 ProseMirror（0=标题，1=正文）写入内容。

    mode='text'：execCommand('insertText')，纯文本（标题）。
    mode='html'：execCommand('insertHTML')，保留段落/加粗等结构（正文）。
    返回 'OK:<字数>' / 'DOM_FALLBACK:<字数>' / 错误码。
    """
    cmd = "insertText" if mode == "text" else "insertHTML"
    expr = (
        "(function(){try{"
        "var el=%s;if(!el)return 'NO_EL';"
        "el.focus();"
        "var rg=document.createRange();rg.selectNodeContents(el);"
        "var s=window.getSelection();s.removeAllRanges();s.addRange(rg);"
        "var ok=document.execCommand(%s,false,%s);"
        "if(!ok){el.innerHTML=%s;el.dispatchEvent(new InputEvent('input',{bubbles:true}));"
        "return 'DOM_FALLBACK:'+(''+el.innerText).length;}"
        "return 'OK:'+(''+el.innerText).length;"
        "}catch(e){return 'ERR:'+e.message;}})()"
        % (JS_PICK_PM % index, json.dumps(cmd), json.dumps(content),
           json.dumps(content))
    )
    return cdp.eval(expr)


def _fill_input(cdp, sel, value):
    expr = (
        "(function(){var el=document.querySelector(%s);if(!el)return 'NO_EL';"
        "var proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:"
        "HTMLInputElement.prototype;"
        "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,%s);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));return 'OK';})()"
        % (json.dumps(sel), json.dumps(value))
    )
    return cdp.eval(expr)


def _set_body_via_api(cdp, html):
    """用公众号新版编辑器官方 JS API 设置正文。

    编辑器（ProseMirror 版）暴露了 window.__MP_Editor_JSAPI__，其中
    mp_editor_set_content 是官方下发的「整篇替换」入口，比 execCommand
    更贴近编辑器原生能力，样式保留率更高。API 是异步的，这里只负责触发，
    实际字数由调用方再读一次编辑器校验。
    """
    expr = (
        "(function(){try{"
        "var api=window.__MP_Editor_JSAPI__;"
        "if(!api||typeof api.invoke!=='function')return 'NO_API';"
        "var state='PENDING';"
        "api.invoke({"
        "apiName:'mp_editor_set_content',"
        "apiParam:{content:%s},"
        "sucCb:function(res){state='SUC';},"
        "errCb:function(err){state='ERR:'+JSON.stringify(err);}"
        "});"
        "return 'API_TRIGGERED';"
        "}catch(e){return 'ERR:'+e.message;}})()" % json.dumps(html)
    )
    return cdp.eval(expr)


def _body_len(cdp):
    """读第 1 个可见 ProseMirror（正文）的当前文本长度。"""
    expr = (
        "(function(){"
        "var eds=[].slice.call(document.querySelectorAll('.ProseMirror[contenteditable=\"true\"]'))"
        ".filter(function(e){return (''+e.className).indexOf('js_reprint')<0});"
        "eds.sort(function(a,b){return a.getBoundingClientRect().top-b.getBoundingClientRect().top});"
        "var el=eds[1];return el?(''+el.innerText).length:-1;})()"
    )
    try:
        return int(cdp.eval(expr))
    except (TypeError, ValueError):
        return -1


def _set_body(cdp, html, min_len=50):
    """正文注入：官方 API 优先，失败/没落库再退回 execCommand('insertHTML')。"""
    api = _set_body_via_api(cdp, html)
    if isinstance(api, str) and api.startswith("API_TRIGGERED"):
        time.sleep(1.5)
        n = _body_len(cdp)
        if n >= min_len:
            return "API_OK:%d" % n
        # API 触发了但内容没进去，退回 execCommand
    r = _set_editor(cdp, 1, html, mode="html") or ""
    if r.startswith("OK:") or r.startswith("DOM_FALLBACK:"):
        n = int(r.split(":", 1)[1])
        return ("FALLBACK_OK:%d(api=%s)" % (n, api)) if n >= min_len else "TOO_SHORT:%d" % n
    return "FAIL:%s(api=%s)" % (r, api)


def _paste_html(cdp, sel, html):
    # 关键坑：合成 ClipboardEvent 的 paste 只会把文本插进 DOM 表面，不会更新
    # ProseMirror 的内部文档——于是「正文字数 0」、保存后正文为空。
    # 正确做法：从 DOM 节点抠出 ProseMirror 的 EditorView（__pmViewDesc.view），
    # 用 view 自带的 domParser 把 HTML 解析成同 schema 的节点，再 dispatch 一个
    # replaceWith 事务直接替换整篇文档。这样字数统计/保存都会认。
    expr = (
        "(function(){"
        "var el=document.querySelector(%s);if(!el)return 'NO_EL';"
        "// ProseMirror 把 ViewDesc 挂在 DOM 节点上（属性名 pmViewDesc，个别版本 __pmViewDesc）；"
        "// 从编辑区本体逐级向上找，任一命中即可拿到 EditorView。"
        "var pm=null,cur=el,probe='';"
        "while(cur&&!pm){"
        "var d=cur.pmViewDesc||cur.__pmViewDesc;"
        "if(d){probe+=(cur.tagName||'?')+':desc;';if(d.view)pm=d.view;}"
        "cur=cur.parentElement;}"
        "if(!pm)return 'NO_PM:'+probe.slice(0,200);"
        "var dom=document.createElement('div');dom.innerHTML=%s;"
        "var node=null;"
        "try{node=pm.domParser.parse(dom);}catch(e){return 'PARSE_ERR:'+e.message;}"
        "var tr=pm.state.tr;"
        "tr.replaceWith(0,pm.state.doc.content.size,node.content);"
        "pm.focus();pm.dispatch(tr);"
        "return 'PM_OK:'+pm.state.doc.textContent.length;"
        "})()"
        % (json.dumps(sel), json.dumps(html))
    )
    return cdp.eval(expr)


def _click(cdp, sel):
    expr = (
        "(function(){var el=document.querySelector(%s);if(!el)return 'NO_EL';"
        "el.click();return 'CLICKED';})()" % json.dumps(sel)
    )
    return cdp.eval(expr)


def _click_by_text(cdp, text):
    expr = (
        "(function(){var els=[].slice.call(document.querySelectorAll('button,a'));"
        "for(var i=0;i<els.length;i++){var t=(els[i].textContent||'').trim();"
        "if(t&&t.indexOf(%s)>=0){els[i].click();return 'CLICKED:'+t;}}"
        "return 'NO_EL';})()" % json.dumps(text)
    )
    return cdp.eval(expr)


def _editor_text_len(cdp, sel):
    expr = (
        "(function(){var el=document.querySelector(%s);"
        "return el?(''+el.innerText).length:-1;})()" % json.dumps(sel)
    )
    r = cdp.eval(expr)
    try:
        return int(r)
    except (TypeError, ValueError):
        return -1


def _post_save_diagnose(cdp):
    """点击保存后扫描页面状态：URL、弹窗、报错、保存按钮是否还在。"""
    expr = (
        "(function(){"
        "var out={url:location.href,alerts:[],dialogs:[],toasts:[],errs:[],saveBtn:null};"
        "try{"
        "out.titleExists=!!document.querySelector('#title');"
        "out.editorExists=!!document.querySelector('.ProseMirror[contenteditable=\"true\"]');"
        "// 常见弹窗/遮罩"
        "var masks=document.querySelectorAll('.weui-desktop-dialog, .dialog_wrp, .js_dialog, [class*=\"modal\"], [class*=\"mask\"]');"
        "[].forEach.call(masks,function(m){out.dialogs.push((m.textContent||'').trim().slice(0,200));});"
        "// toast"
        "var toasts=document.querySelectorAll('.weui-desktop-toast, .toast, [class*=\"tips\"]');"
        "[].forEach.call(toasts,function(t){out.toasts.push((t.textContent||'').trim().slice(0,200));});"
        "// 红字报错"
        "var errs=document.querySelectorAll('.frm_msg, .error, .weui-desktop-form__error, .warn');"
        "[].forEach.call(errs,function(e){out.errs.push((e.textContent||'').trim().slice(0,200));});"
        "// 保存按钮还在不在"
        "var btns=[].slice.call(document.querySelectorAll('button,a')).filter(function(b){"
        "return (b.textContent||'').trim().indexOf('保存为草稿')>=0;});"
        "out.saveBtn=btns.length?{text:btns[0].textContent.trim(),disabled:btns[0].disabled,className:btns[0].className.slice(0,80)}:null;"
        "}catch(e){out.jsErr=e.message;}"
        "return JSON.stringify(out);"
        "})()"
    )
    raw = cdp.eval(expr, refresh_context=True)
    try:
        return json.loads(raw)
    except Exception:
        return {"_parse_err": str(raw)}


def _screenshot(cdp, path):
    """用 CDP Page.captureScreenshot 抓图存本地。"""
    try:
        r = cdp.send("Page.captureScreenshot", {"format": "png"})
        data = r.get("result", {}).get("data", "")
        if data:
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            return path
    except Exception as e:
        return "ERR:%s" % e
    return "NO_DATA"


def _tab_url(cdp, tid):
    """不依赖页面执行上下文，直接问浏览器要这个 tab 的当前 URL。

    点「保存为草稿」后页面会导航，页面执行上下文会短暂失效，此时用
    Runtime.evaluate 读 location.href 会返回空。改从浏览器 Target 列表读 URL
    最稳（保存成功的信号是 URL 里出现 appmsgid=）。"""
    try:
        for t in cdp.list_targets():
            if t.get("id") == tid:
                return t.get("url") or ""
    except Exception:
        pass
    return ""


def _declare_original(cdp, author="万物解释者"):
    """在编辑器设置区自动声明「文字原创」。

    ⚠ **别指望把 author 留空来减少头部重复**（2026-09-21 实测，浪费了一篇
    草稿）：微信文章头部是「作者名 + 公众号名 + 日期」三段固定展示，作者名
    跟号名都是「万物解释者」时会印两遍。试过两条路都不行 ——
      ① `author=""` 真发一篇（草稿 100000113）：作者行仍显示「万物解释者」；
      ② 在已声明草稿上清空 `#author` 再重提交声明：作者行依然不动。
    结论：**微信不接受空作者，留空时用公众号名兜底**，所以留空 == 显式填
    号名，效果完全一样。这里保持显式填写，别再改成空串。

    2026-09-20 实测要点：
    - 入口：设置区「原创」组里最内层的「未声明」文本，点其父行开弹窗。
    - 弹窗里有多个 .weui-desktop-dialog 节点，必须按 getBoundingClientRect
      取可见的那个（第一个匹配常常是 display:none 的）。
    - 作者输入框：native setter / execCommand('insertText') / Input.insertText
      都进不了框架状态（计数恒 0/8）；唯一有效的是 CDP Input.dispatchKeyEvent
      逐键 keyDown/keyUp（先真实鼠标点击聚焦）。
    - 协议勾选框在 .original_agreement 里，直接点它父 label。
    - 提交被拒（如正文<300字）时弹窗会重开，靠「弹窗是否还开着」判断成败。
    - 赞赏开关在同一弹窗里但账号层面被禁（需赞赏账户小程序授权），不自动开。
    返回 "OK" / "FAIL:..."（失败不抛异常，不挡发布主链路）。
    """
    JS_VIS_DLG = (
        "var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
        "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
        "if(r.width>300&&r.height>100){dlg=ds[i];break;}}"
    )
    try:
        # 0) 已声明过就直接跳过（重发场景：行状态是「已声明」而非「未声明」）
        try:
            st = cdp.eval(
                "(function(){var t=document.body.innerText.replace(/\\s+/g,'');"
                "return t.indexOf('编辑声明')>=0?'DECLARED':'NOT';})()")
            if st == "DECLARED":
                return "SKIP:已声明"
        except Exception:
            pass
        # 1) 打开弹窗
        JS_OPEN_ORIG = (
            "(function(){"
            "var all=[].slice.call(document.querySelectorAll('*'));"
            "for(var i=0;i<all.length;i++){var e=all[i];"
            "if(e.children.length===0&&(e.textContent||'').trim()==='未声明'){"
            "var tgt=e.closest('[class*=item],[class*=row],[class*=cell]')||e.parentElement;"
            "tgt.click();return 'OK';}}return 'NO_ROW';})()")
        JS_AUTHOR = (
            "(function(){" + JS_VIS_DLG +
            "if(!dlg)return 'NO';"
            "var e=dlg.querySelector('input[placeholder=\"请输入作者\"]');"
            "if(!e)return 'NO';"
            "var r=e.getBoundingClientRect();"
            "return JSON.stringify({x:Math.round(r.x+r.width/2),"
            "y:Math.round(r.y+r.height/2)});})()")
        r = cdp.eval(JS_OPEN_ORIG)
        if r != "OK":
            return "FAIL:找不到未声明入口(%s)" % r
        time.sleep(1.5)
        # 2) 作者：真实鼠标点击聚焦 + 逐键输入（唯一能进框架状态的方式）
        info = cdp.eval(JS_AUTHOR)
        if info == "NO" or not info.startswith("{"):
            # 可见弹窗不是原创弹窗 —— 多半是别的残留弹窗挡着。
            # 收掉残留后重开一次（2026-09-21 实测：封面「确认」点错导致
            # 「编辑封面」弹窗残留，这里就误报"作者框没找到(NO)"）。
            _close_dialog(cdp)
            time.sleep(1.0)
            if cdp.eval(JS_OPEN_ORIG) == "OK":
                time.sleep(1.5)
                info = cdp.eval(JS_AUTHOR)
        if info == "NO" or not info.startswith("{"):
            return "FAIL:作者框没找到(%s)" % info
        d = json.loads(info)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": d["x"], "y": d["y"],
            "button": "left", "clickCount": 1, "buttons": 1})
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": d["x"], "y": d["y"],
            "button": "left", "clickCount": 1, "buttons": 0})
        time.sleep(0.3)
        # 关键：弹窗作者框会同步主编辑器 #author 的值（预填过一次），
        # 直接再打字就变成重复两遍（10/8 被拒）。先 Ctrl+A 全选 + 删除再输入。
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "code": "KeyA",
            "modifiers": 2})   # 2 = Ctrl
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA", "modifiers": 2})
        time.sleep(0.1)
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Delete", "code": "Delete"})
        cdp.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Delete", "code": "Delete"})
        time.sleep(0.2)
        for ch in author:
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "text": ch, "key": ch,
                "unmodifiedText": ch})
            cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": ch})
            time.sleep(0.03)
        time.sleep(0.3)
        # 3) 勾协议
        r = cdp.eval(
            "(function(){" + JS_VIS_DLG +
            "var box=dlg.querySelector('.original_agreement input[type=checkbox]');"
            "if(!box)return 'NO_CB';"
            "if(box.checked)return 'ALREADY';"
            "(box.closest('label')||box).click();"
            "return 'CLICKED:'+box.checked;})()")
        if not (r.startswith("CLICKED") or r == "ALREADY"):
            return "FAIL:协议勾不上(%s)" % r
        time.sleep(0.3)
        # 4) 确定
        r = cdp.eval(
            "(function(){" + JS_VIS_DLG +
            "var btns=dlg.querySelectorAll('button');"
            "for(var i=0;i<btns.length;i++){"
            "var t=(btns[i].textContent||'').trim();"
            "if(t==='确定'&&!btns[i].disabled){btns[i].click();return 'OK';}}"
            "return 'NO_BTN';})()")
        if r != "OK":
            return "FAIL:确定点不了(%s)" % r
        # 5) 校验：结果导向 —— 轮询「弹窗关闭」或「编辑声明标记出现」（排除弹窗内
        #    文本），最多等 10 秒。早先只等 3 秒看弹窗，微信弹窗会挂一会 → 误报失败。
        for _ in range(10):
            time.sleep(1)
            st = cdp.eval(
                "(function(){"
                "var ds=document.querySelectorAll('.weui-desktop-dialog');"
                "var open=false;"
                "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
                "if(r.width>300&&r.height>100)open=true;}"
                "var all=document.querySelectorAll('*');var marked=false;"
                "for(var j=0;j<all.length;j++){var e=all[j];"
                "if(e.closest('.weui-desktop-dialog'))continue;"
                "var t=(e.textContent||'').trim();"
                "if(t==='编辑声明'){marked=true;break;}}"
                "return (marked?'MARKED':'')+(open?'|OPEN':'|CLOSED');})()")
            if "MARKED" in st or st.endswith("|CLOSED"):
                return "OK"
        # 10 秒还开着 → 真被拒（常见原因：正文不足 300 字）
        _close_dialog(cdp)
        return "FAIL:提交被拒弹窗未关（常见原因：正文不足300字）"
    except Exception as e:
        return "FAIL:%s" % e


def _dialog_open(cdp):
    """页面上是否有可见的 weui 弹窗（多个节点，只认尺寸够大的那个）。"""
    return cdp.eval(
        "(function(){"
        "var ds=document.querySelectorAll('.weui-desktop-dialog');"
        "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
        "if(r.width>300&&r.height>100)return 'OPEN';}"
        "return 'CLOSED';})()")


def _close_dialog(cdp):
    """点弹窗「取消」收场，别把弹窗留在页面上。失败不影响主链路。"""
    try:
        cdp.eval(
            "(function(){"
            "var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
            "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
            "if(r.width>300&&r.height>100){dlg=ds[i];break;}}"
            "if(!dlg)return 'NO_DLG';"
            "var btns=dlg.querySelectorAll('button');"
            "for(var j=0;j<btns.length;j++){"
            "if(btns[j].textContent.trim()==='取消'){btns[j].click();return 'OK';}}"
            "return 'NO_CANCEL';})()")
    except Exception:
        pass


def _reward_is_on(cdp):
    """赞赏行是否真的开启了 —— 结果导向判据。

    body.innerText 不能用：弹窗里也有「赞赏账户」标签，会误判。这里扫元素，
    要求文本以「赞赏账户:」开头（带冒号和账户名），且不在弹窗内部。
    """
    try:
        return cdp.eval(
            "(function(){"
            "var all=document.querySelectorAll('*');"
            "for(var i=0;i<all.length;i++){var e=all[i];"
            "if(e.closest('.weui-desktop-dialog'))continue;"
            "var t=(e.textContent||'').replace(/\\s+/g,'');"
            "if((t.indexOf('赞赏账户:')===0||t.indexOf('赞赏账户：')===0)"
            "&&t.length<60){var r=e.getBoundingClientRect();"
            "if(r.height>0&&r.height<80)return 'ON';}}"
            "return 'OFF';})()") == "ON"
    except Exception:
        return False


def _setup_reward_reply(cdp, text=REWARD_REPLY_TEXT):
    """把赞赏弹窗里的「赞赏自动回复」开关打开（文章级），必要时补一条回复素材。

    2026-09-21 实测结论：
    - 页面文案里**没有**「去添加」这种链接，它就是一个标准 switch：
      `.reward-reply-switch__wrp .weui-desktop-switch`，真状态在内层
      `input.weui-desktop-switch__input` 的 checked 上。
    - 回复**素材**是账号级的（配一次全部文章共用，用户已在手机端配好），
      但这个开关是**文章级**的 —— 每篇新草稿都要重新打开，所以必须自动化。
    - 开关打开后 `.reward-reply-setting` 里会列出已有素材 + 「添加新的回复」。

    返回 "SKIP:已开" / "OK:开关已打开" / "OK:已补素材" / "FAIL:..."
    """
    js_dlg = ("var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
              "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
              "if(r.width>300&&r.height>100){dlg=ds[i];break;}}")

    def mouse(x, y):
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved",
                                              "x": x, "y": y, "buttons": 0})
        time.sleep(0.05)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 1})
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 0})

    JS_FIND = (
        "(function(){" + js_dlg +
        "if(!dlg)return 'NO_DLG';"
        "var w=dlg.querySelector('.reward-reply-switch__wrp')"
        "||dlg.querySelector('.reward-reply-setting');"
        "if(!w)return 'NO_SWITCH_ROW';"
        "var sw=w.querySelector('.weui-desktop-switch');"
        "if(!sw)return 'NO_SWITCH';"
        "var inp=sw.querySelector('input');"
        "var box=sw.querySelector('.weui-desktop-switch__box')||sw;"
        "var r=box.getBoundingClientRect();"
        "if(r.width<=0)return 'SWITCH_HIDDEN';"
        "return JSON.stringify({x:Math.round(r.x+r.width/2),"
        "y:Math.round(r.y+r.height/2),checked:inp?!!inp.checked:false});})()")

    JS_PROBE = (
        "(function(){" + js_dlg +
        "if(!dlg)return 'NO_DLG';"
        "var e=dlg.querySelector('.reward-reply-setting');"
        "return e?e.innerText.replace(/\\s+/g,''):'NO_SECTION';})()")

    try:
        info = cdp.eval(JS_FIND)
        for _ in range(4):
            if not isinstance(info, str) or info.startswith("{"):
                break
            time.sleep(1)
            info = cdp.eval(JS_FIND)
        if not isinstance(info, str) or not info.startswith("{"):
            return "FAIL:找不到自动回复开关(%s)" % info
        d = json.loads(info)
        if d["checked"]:
            # 已开：确认素材还在（不在也不强行处理，避免动到用户已有配置）
            body = cdp.eval(JS_PROBE)
            if text[:8] in str(body):
                return "SKIP:已开"
            return "SKIP:已开(素材待确认)"
        mouse(d["x"], d["y"])
        time.sleep(1.5)
        info2 = cdp.eval(JS_FIND)
        if not (isinstance(info2, str) and info2.startswith("{")):
            return "FAIL:开关状态读不到(%s)" % info2
        if not json.loads(info2)["checked"]:
            # JS click 兜底（部分 Vue switch 对真实鼠标也偶发无响应）
            cdp.eval("(function(){" + js_dlg + "if(!dlg)return 'NO';"
                     "var sw=dlg.querySelector('.reward-reply-switch__wrp "
                     ".weui-desktop-switch');"
                     "if(!sw)return 'NO';sw.click();return 'OK';})()")
            time.sleep(1.5)
            info3 = cdp.eval(JS_FIND)
            if not (isinstance(info3, str) and info3.startswith("{")
                    and json.loads(info3)["checked"]):
                return "FAIL:开关打不开"

        # 素材检查：已有就收工（素材是账号级的，通常已存在）
        body = cdp.eval(JS_PROBE)
        if text[:8] in str(body):
            return "OK:开关已打开"
        # 兜底：补一条素材
        r = cdp.eval(
            "(function(){" + js_dlg + "if(!dlg)return 'NO_DLG';"
            "var best=null,area=1e9;dlg.querySelectorAll('*').forEach(function(e){"
            "var t=(e.textContent||'').replace(/\\s+/g,'');"
            "if(t!=='添加新的回复')return;"
            "var r=e.getBoundingClientRect();if(r.width<=0)return;"
            "var a=r.width*r.height;if(a<area){area=a;best=e;}});"
            "if(!best)return 'NO_ADD_BTN';var r=best.getBoundingClientRect();"
            "return JSON.stringify({x:Math.round(r.x+r.width/2),"
            "y:Math.round(r.y+r.height/2)});})()")
        if not (isinstance(r, str) and r.startswith("{")):
            return "OK:开关已打开(素材缺失且未找到入口:%s)" % r
        mouse(json.loads(r)["x"], json.loads(r)["y"])
        time.sleep(1.5)
        # 填文案：优先 Input.insertText（emoji/中文都稳），再校验值
        r = cdp.eval(
            "(function(){var out=[];"
            "document.querySelectorAll('textarea,input[type=text]')"
            ".forEach(function(e){var r=e.getBoundingClientRect();if(r.width<=0)return;"
            "if((e.placeholder||'').indexOf('赞赏账户')>=0)return;"
            "out.push({x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),"
            "tag:e.tagName});});"
            "return JSON.stringify(out);})()")
        try:
            cands = json.loads(r) if isinstance(r, str) and r.startswith("[") else []
        except Exception:                                        # noqa: BLE001
            cands = []
        if not cands:
            return "OK:开关已打开(素材输入框没找到)"
        mouse(cands[0]["x"], cands[0]["y"])
        time.sleep(0.3)
        cdp.send("Input.insertText", {"text": text})
        time.sleep(0.3)
        got = cdp.eval(
            "(function(){var e=document.activeElement;"
            "return e?(e.value!==undefined?e.value:e.textContent||''):'';})()")
        if text[:6] not in str(got):
            return "OK:开关已打开(素材写入未确认:%s)" % str(got)[:20]
        # 保存素材（回复编辑区里的保存/确定按钮）
        r = cdp.eval(
            "(function(){" + js_dlg + "if(!dlg)return 'NO';"
            "var b=null;dlg.querySelectorAll('button').forEach(function(e){"
            "var t=(e.textContent||'').trim();"
            "if((t==='保存'||t==='确定')&&!e.disabled&&!b)b=e;});"
            "if(!b)return 'NO_SAVE_BTN';var r=b.getBoundingClientRect();"
            "return JSON.stringify({x:Math.round(r.x+r.width/2),"
            "y:Math.round(r.y+r.height/2),t:b.textContent.trim()});})()")
        if isinstance(r, str) and r.startswith("{"):
            mouse(json.loads(r)["x"], json.loads(r)["y"])
            time.sleep(1.5)
        return "OK:已补素材"
    except Exception as e:                                       # noqa: BLE001
        return "FAIL:%s" % e


def _enable_reward(cdp, name="万物解释者"):
    """在编辑器设置区自动开启赞赏（赞赏作者 + 指定赞赏账户）。

    2026-09-21 实测要点：
    - 赞赏行入口：去空白文本以「不开启」开头的最小元素（不能只找叶子节点，
      「不开启」和箭头图标在同一个 SPAN 里），且父链 4 层内含「赞赏」。
    - 弹窗里「赞赏作者」radio 默认已选中；账户字段初始为空。
    - 账户选择两条路：①「最近使用/最近绑定」快捷项——只在弹窗非首次打开时出现；
      ②搜索框输入账户名等下拉候选（新草稿的全新弹窗只有这条路）。
    - 协议勾选框在含「我已阅读」的 label 里。
    - 行已是开启状态（文本含「赞赏账户:」）时跳过。
    返回 "OK" / "SKIP:..." / "FAIL:..."，失败不抛异常不挡发布。
    """
    JS_VIS_DLG = (
        "var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
        "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
        "if(r.width>300&&r.height>100){dlg=ds[i];break;}}"
    )
    def _mouse_click(x, y):
        """真实鼠标点击。Vue 组件（弹窗/下拉）对 JS .click() 经常无响应，
        必须走 Input.dispatchMouseEvent 才能进状态（与作者框同一坑）。
        先 mouseMoved 再按下：部分 Vue 组件只认「hover 过」的元素。"""
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y, "buttons": 0})
        time.sleep(0.05)
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 1})
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1, "buttons": 0})

    try:
        # 0) 已开启就跳过（结果导向：看赞赏行文本，不看弹窗）
        if _reward_is_on(cdp):
            return "SKIP:已开启"
        # 1) 打开赞赏弹窗
        r = cdp.eval(
            "(function(){"
            "var all=[].slice.call(document.querySelectorAll('*'));"
            "var best=null,bestArea=1e9;"
            "for(var i=0;i<all.length;i++){var e=all[i];"
            "var t=(e.textContent||'').replace(/\\s+/g,'');"
            "if(t.indexOf('不开启')!==0)continue;"
            "var r=e.getBoundingClientRect();"
            "if(r.height<=0||r.height>80)continue;"
            "var p=e.parentElement,found=false;"
            "for(var j=0;j<4&&p;j++){if((p.textContent||'').indexOf('赞赏')>=0){found=true;break;}p=p.parentElement;}"
            "if(!found)continue;"
            "var area=r.width*r.height;"
            "if(area<bestArea){bestArea=area;best=e;}}"
            "if(!best)return 'NO_ROW';"
            "best.click();return 'OK';})()")
        if r != "OK":
            return "FAIL:赞赏行没找到(%s)" % r
        time.sleep(1.2)
        # 1.5) 「赞赏自动回复」开关（实测 2026-09-21）：
        #   页面文案里**没有**「去添加」这种链接，它就是一个标准 switch
        #   （`.reward-reply-switch__wrp .weui-desktop-switch`）。回复**素材**是
        #   账号级的（配一次全站共用），但这个开关是**文章级**的 —— 每篇新草稿
        #   都要重新打开，所以必须放进自动流程。
        #   ⚠️ 顺序很关键：开关会触发表单重渲染，把「账户选择」在 Vue 模型层清掉
        #   （DOM 里还留着值，看着正常，但点确定会被静默拒绝 —— 实测 100000067
        #   就是这样：acct/协议都在、开关却是 false）。所以必须**先开开关，
        #   再选账户**，并且开关真动过时强制重选一次，不吃「值已正确」的捷径。
        reply_st = _setup_reward_reply(cdp)
        print("  赞赏自动回复: %s" % reply_st)
        force_reselect = reply_st.startswith("OK")
        time.sleep(0.5)
        # 2) 账户：微信打开弹窗时会**预填**已保存的账户名（草稿里恢复出来的），
        #    此时再选一次/再打一遍字 → 值变成「万物解释者万物解释者」→ 校验
        #    不过，点确定静默失败（与原创弹窗作者框重复填充是同一类问题，
        #    2026-09-21 实测诊断串 acct=万物解释者万物解释者 抓到的）。
        #    正确顺序：先读值 → 已是目标账户直接跳过；否则清空后再选/再输。
        def _acct_input():
            info = cdp.eval(
                "(function(){" + JS_VIS_DLG +
                "if(!dlg)return 'NO';"
                "var e=dlg.querySelector('input[placeholder*=\"赞赏账户\"]');"
                "if(!e)return 'NO';"
                "var r=e.getBoundingClientRect();"
                "return JSON.stringify({x:Math.round(r.x+r.width/2),"
                "y:Math.round(r.y+r.height/2)});})()")
            return json.loads(info) if info.startswith("{") else None

        def _acct_value():
            try:
                return cdp.eval(
                    "(function(){" + JS_VIS_DLG +
                    "if(!dlg)return null;"
                    "var i=dlg.querySelector('input[placeholder*=\"赞赏账户\"]');"
                    "return i?i.value:null;})()")
            except Exception:
                return None

        def _acct_clear():
            """清空账户框（含框架状态），并**校验真的空了**。

            坑（2026-09-21 实测）：这个框是 Vue 组件，Ctrl+A + Delete 有时
            不生效 —— 之前所有流程都走「值已正确就直接跳过」的捷径，从没暴露；
            一旦真的需要重选，就会在旧值后面接着打字，变成
            「万物解释者万物解释者万物解释者」。所以必须循环重试 + 校验。
            """
            d = _acct_input()
            if not d:
                return "NO_INPUT"
            for _ in range(4):
                _mouse_click(d["x"], d["y"])
                time.sleep(0.3)
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "a", "code": "KeyA",
                    "modifiers": 2})
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "a", "code": "KeyA",
                    "modifiers": 2})
                time.sleep(0.15)
                for _ in range(2):
                    cdp.send("Input.dispatchKeyEvent", {
                        "type": "keyDown", "key": "Delete",
                        "code": "Delete"})
                    cdp.send("Input.dispatchKeyEvent", {
                        "type": "keyUp", "key": "Delete", "code": "Delete"})
                time.sleep(0.35)
                if not _acct_value():
                    return "CLEARED"
            return "STILL:%r" % _acct_value()

        def _acct_pick_recent():
            """点「最近使用/最近绑定」里的账户候选（真实鼠标）。"""
            r2 = "NO_RECENT"
            for _ in range(3):
                time.sleep(1.2)
                info = cdp.eval(
                    "(function(){" + JS_VIS_DLG +
                    "if(!dlg)return 'NO_DLG';"
                    "var all=dlg.querySelectorAll('*');"
                    "var holder=null;"
                    "for(var i=0;i<all.length;i++){"
                    "var t=(all[i].textContent||'').replace(/\\s+/g,'');"
                    "if((t.indexOf('最近绑定')>=0||t.indexOf('最近使用')>=0)"
                    "&&t.length<30){holder=all[i];break;}}"
                    "if(!holder)return 'NO_RECENT';"
                    "var nm=(holder.textContent||'').replace(/\\s+/g,'')"
                    ".replace('最近绑定','').replace('最近使用','').trim();"
                    "var best=null,bestArea=1e9;"
                    "for(var j=0;j<all.length;j++){var e=all[j];"
                    "if((e.textContent||'').replace(/\\s+/g,'')!==nm)continue;"
                    "var rr=e.getBoundingClientRect();"
                    "if(rr.height<=0||rr.height>60)continue;"
                    "var area=rr.width*rr.height;"
                    "if(area<bestArea){bestArea=area;best=e;}}"
                    "if(!best)return 'NO_CAND';"
                    "var r3=best.getBoundingClientRect();"
                    "return JSON.stringify({nm:nm,"
                    "x:Math.round(r3.x+r3.width/2),"
                    "y:Math.round(r3.y+r3.height/2)});})()")
                if info.startswith("{"):
                    d = json.loads(info)
                    _mouse_click(d["x"], d["y"])
                    r2 = "PICKED:" + d["nm"]
                    break
            return r2

        def _acct_type_and_pick(text):
            """搜索框逐键输入 → 等下拉候选 → 真实鼠标点它。"""
            d = _acct_input()
            if not d:
                return "NO_INPUT"
            _mouse_click(d["x"], d["y"])
            time.sleep(0.3)
            for ch in text:
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "text": ch, "key": ch,
                    "unmodifiedText": ch})
                cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": ch})
                time.sleep(0.03)
            r2 = "NO_DROPDOWN"
            for _ in range(5):
                time.sleep(1)
                info = cdp.eval(
                    "(function(){" + JS_VIS_DLG +
                    "if(!dlg)return 'NO_DLG';"
                    "var all=dlg.querySelectorAll('*');"
                    "var best=null,bestArea=1e9;"
                    "for(var i=0;i<all.length;i++){var e=all[i];"
                    "if((e.textContent||'').replace(/\\s+/g,'')!=='%s')continue;"
                    "var rr=e.getBoundingClientRect();"
                    "if(rr.height<=0||rr.height>60)continue;"
                    "var area=rr.width*rr.height;"
                    "if(area<bestArea){bestArea=area;best=e;}}"
                    "if(!best)return 'NO_DROPDOWN';"
                    "var r3=best.getBoundingClientRect();"
                    "return JSON.stringify({x:Math.round(r3.x+r3.width/2),"
                    "y:Math.round(r3.y+r3.height/2)});})()" % text)
                if info.startswith("{"):
                    d = json.loads(info)
                    _mouse_click(d["x"], d["y"])
                    r2 = "PICKED:%s" % text
                    break
            return r2

        r = "NO_RECENT"
        must_repick = force_reselect
        for attempt in range(2):
            if not must_repick and _acct_value() == name:
                r = "PICKED:%s" % name
                break
            cl = _acct_clear()
            if cl != "CLEARED":
                # 清不干净就**别打字** —— 否则会在旧值后面接着写，
                # 值变成「名字×N」，确定会被静默拒绝（实测踩过）。
                must_repick = False
                continue
            r = _acct_pick_recent()
            if not r.startswith("PICKED"):
                _acct_type_and_pick(name)
            time.sleep(0.8)
            if _acct_value() == name:
                r = "PICKED:%s" % name
                break
            must_repick = False
        if not r.startswith("PICKED") or _acct_value() != name:
            return "FAIL:赞赏账户值不对(当前=%r, 期望=%r, %s)" % (
                _acct_value(), name, r)
        time.sleep(0.8)
        # 3) 勾协议（真实鼠标点击勾选区，避免 JS click 不进状态）
        r = cdp.eval(
            "(function(){" + JS_VIS_DLG +
            "if(!dlg)return 'NO_DLG';"
            "var labels=dlg.querySelectorAll('label');"
            "for(var i=0;i<labels.length;i++){"
            "var t=(labels[i].textContent||'');"
            "var inp=labels[i].querySelector('input[type=checkbox]');"
            "if(inp&&t.indexOf('我已阅读')>=0){"
            "if(inp.checked)return 'ALREADY';"
            "var r2=labels[i].getBoundingClientRect();"
            "return JSON.stringify({x:Math.round(r2.x+r2.width/2),"
            "y:Math.round(r2.y+r2.height/2)});}}"
            "return 'NO_AGREE';})()")
        if r.startswith("{"):
            d = json.loads(r)
            _mouse_click(d["x"], d["y"])
            time.sleep(0.3)
            r2 = cdp.eval(
                "(function(){" + JS_VIS_DLG +
                "if(!dlg)return 'NO_DLG';"
                "var labels=dlg.querySelectorAll('label');"
                "for(var i=0;i<labels.length;i++){"
                "var inp=labels[i].querySelector('input[type=checkbox]');"
                "if(inp&&(labels[i].textContent||'').indexOf('我已阅读')>=0)"
                "return inp.checked?'CHECKED':'UNCHECKED';}}"
                "return 'NO_AGREE';})()")
            r = "CLICKED" if r2 == "CHECKED" else "STILL_UNCHECKED"
        if not (r.startswith("CLICKED") or r == "ALREADY"):
            return "FAIL:协议勾不上(%s)" % r
        time.sleep(0.3)
        # 4) 确定（真实鼠标点击）。
        #    先 scrollIntoView：上面把自动回复开关打开后弹窗会长高，
        #    确定按钮可能被推出视口 —— 坐标点击打不到屏幕外的元素。
        r = cdp.eval(
            "(function(){" + JS_VIS_DLG +
            "var btns=dlg.querySelectorAll('button');"
            "for(var i=0;i<btns.length;i++){"
            "var t=(btns[i].textContent||'').trim();"
            "if(t==='确定'&&!btns[i].disabled){"
            "btns[i].scrollIntoView({block:'center'});"
            "var r2=btns[i].getBoundingClientRect();"
            "return JSON.stringify({x:Math.round(r2.x+r2.width/2),"
            "y:Math.round(r2.y+r2.height/2)});}}"
            "return 'BTN_DISABLED';})()")
        if r.startswith("{"):
            d = json.loads(r)
            _mouse_click(d["x"], d["y"])
            r = "OK"
        if r != "OK":
            return "FAIL:确定点不了(%s)" % r
        # 5) 校验：结果导向 —— 轮询赞赏行是否变成「赞赏账户:xxx」。
        #    不能看「弹窗是否关闭」：微信点完确定后弹窗 DOM 会挂一段时间（动画/
        #    重渲染），早先的 3 秒窗口判据会把成功误报成失败（2026-09-21 实测：
        #    日志报 FAIL 的草稿，赞赏其实都开好了）。
        for _ in range(10):
            time.sleep(1)
            if _reward_is_on(cdp):
                if _dialog_open(cdp) == "OPEN":
                    _close_dialog(cdp)
                return "OK"
        # 10 秒还没开 → 真失败。诊断串留全，便于下次直接定位。
        diag = cdp.eval(
            "(function(){"
            "var ds=document.querySelectorAll('.weui-desktop-dialog');var dlg=null;"
            "for(var i=0;i<ds.length;i++){var r=ds[i].getBoundingClientRect();"
            "if(r.width>300&&r.height>100){dlg=ds[i];break;}}"
            "if(!dlg)return 'NO_DLG';"
            "var inp=dlg.querySelector('input[placeholder*=\"赞赏账户\"]');"
            "var bs=dlg.querySelectorAll('button');var btn='';"
            "for(var j=0;j<bs.length;j++){btn+=bs[j].textContent.trim()+"
            "'='+(bs[j].disabled?'DIS':'EN')+' ';}"
            "return 'acct='+(inp?inp.value:'?')+' btns='+btn;})()")
        _close_dialog(cdp)
        return "FAIL:10 秒内赞赏未开启(%s)" % diag
    except Exception as e:
        return "FAIL:%s" % e


def _reward_after_reload(cdp, tid, appmsgid):
    """保存草稿后重新加载草稿页，以**落库状态**判断赞赏是否真的开了。

    为什么要重载：微信点完「确定」后弹窗不关、设置区的行文本也刷新滞后，
    在同一个页面里读到的都是过期状态（2026-09-21 实测：日志报 FAIL 的草稿，
    重载后行文本其实是「赞赏账户:万物解释者」）。重新加载 = 只信服务端。
    """
    try:
        url = _tab_url(cdp, tid) or ""
        m = re.search(r"token=(\d+)", url)
        if not (m and appmsgid):
            return False
        cdp.send("Page.navigate", {"url": (
            "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit"
            "&action=edit&reprint_confirm=0&type=77&appmsgid=%s&token=%s&lang=zh_CN"
            % (appmsgid, m.group(1)))})
        for _ in range(25):
            time.sleep(1)
            try:
                if cdp.eval("!!document.querySelector('#title')",
                            refresh_context=True):
                    break
            except Exception:
                pass
        time.sleep(1)
        return _reward_is_on(cdp)
    except Exception:
        return False


def _finish_status(cover_ok, declared):
    """收尾结论：封面与原创**都**到位才算 ok，否则 partial。

    2026-09-21 实测：这里曾无条件返回 ok，导致「封面没设上 / 原创声明失败」
    也被记成 draft —— 续完逻辑（build_todo 只挑 status=partial）于是永远碰不到
    它们，草稿箱里就一直躺着灰块和未声明的稿子（comp-ai/outrank/bustem/kibu
    四篇就这么漏了一轮）。判定抽成纯函数，便于回归测试。
    """
    return "ok" if (cover_ok and declared) else "partial"


def publish_one(cdp, c, art, dry=False, index=0):
    ex = extract_article(art)
    title = make_wechat_title(c)
    author = "万物解释者"
    summary = ex["summary"] or c.get("one_liner", "")
    body_html = pm_safe_body(ex["body_html"])
    print("\n→ %s — %s" % (c["id"], title))
    if dry:
        print("  [dry] 标题=%s 摘要=%d字 正文=%d字（不打开浏览器）" %
              (title, len(summary), len(strip_tags(body_html))))
        return {"status": "dry", "appmsgid": None}
    # 微信公众号标题上限 64 字，make_wechat_title 已保证；兜底再截一次。
    title = title.strip()
    if len(title) > 64:
        print("  [注意] 标题 %d 字 > 64 上限，已截断" % len(title))
        title = title[:64]
    # 新建一个草稿页（每条独立开一个 tab，避免串稿）
    tid = _open_editor(cdp, debug=False)
    # 标题：第 0 个 ProseMirror（可见），用 insertText 写入，编辑器同步内部文档。
    r = _set_editor(cdp, 0, title, mode="text") or ""
    if not r.startswith("OK:") and not r.startswith("DOM_FALLBACK:"):
        raise RuntimeError("填标题失败（可见 ProseMirror）: %s" % r)
    # 作者 / 摘要：仍是表单字段，直接赋 value（它们本就在主编辑区可见）。
    _fill_input(cdp, "#author", author)
    if summary:
        _fill_input(cdp, "#js_description", summary[:120])
    # 正文：优先走官方 JS API（样式保留率更高），失败再退 execCommand。
    r = _set_body(cdp, body_html) or ""
    if r.startswith("TOO_SHORT:") or r.startswith("FAIL:"):
        raise RuntimeError("正文注入失败：%s" % r)
    try:
        n = int(re.search(r":(\d+)", r).group(1))
    except (AttributeError, ValueError):
        raise RuntimeError("正文注入返回异常：%r" % r)
    print("  标题 %d字 · 摘要 %d字 · 正文注入 %d字 [%s]"
          % (len(title), len(summary), n, r.split("(")[0]))
    time.sleep(1)
    # 封面图：公众号后台要求必填，自动生成并上传
    cover_ok = True      # 封面是否真的落库（服务端 cover 字段），失败记 partial 待补
    cover_path = os.path.join(ROOT, "tmp", "covers", "%s.png" % c["id"])
    try:
        gen = _gen_cover(cdp, c, index, cover_path)
        if isinstance(gen, str) and gen.endswith(".png") and os.path.isfile(gen):
            # 两步走：① 把图插进正文（进微信图床/素材库）；② 再从正文选它当封面。
            # 少了 ② 草稿箱列表项就是灰块 —— 服务端 cover 字段为空。
            up = _upload_cover(cdp, gen)
            print("  封面入素材库: %s" % up)
            sc = _set_cover_from_body(cdp)
            print("  设封面: %s" % sc)
            if not str(sc).startswith(("OK", "SKIP")):
                cover_ok = False
                print("  [warn] 封面没设上（草稿箱列表会显示灰块）: %s" % sc)
        else:
            cover_ok = False
            print("  [warn] 封面生成失败: %s" % gen)
    except Exception as e:
        cover_ok = False
        print("  [warn] 封面处理异常: %s" % e)
    # 保存草稿：按钮无稳定 id，按文本点（"保存为草稿"）
    r = _click_by_text(cdp, "保存为草稿")
    if not (isinstance(r, str) and r.startswith("CLICKED")):
        raise RuntimeError("保存草稿点击失败：%s" % r)
    print("  已点「保存为草稿」(%s)" % r)
    # 等保存请求落库：成功后 tab URL 会带上草稿 id（appmsgid=）。
    # 用 Target 列表读 URL（不经页面上下文，保存导航时也能读到）。
    appmsgid = None
    url = ""
    for _ in range(10):
        time.sleep(1)
        url = _tab_url(cdp, tid)
        m = re.search(r"appmsgid=(\d+)", url or "")
        if m and m.group(1) != "0":
            appmsgid = m.group(1)
            break
    print("  [保存后] URL=%s" % (url or "?"))
    if appmsgid:
        print("  ✓ 已存草稿 appmsgid=%s" % appmsgid)
    else:
        # 读不到 appmsgid 时再看一眼页面报错，便于定位
        diag = _post_save_diagnose(cdp)
        cdp.close_target(tid)
        raise RuntimeError(
            "点保存后 10 秒内 URL 未出现 appmsgid，草稿可能没存成功。"
            "URL=%s 页面报错=%s" % (url, diag.get("errs")))
    # ---- 收尾阶段：原创声明 / 赞赏 / 补保存 ----
    # 草稿此刻已经建好了（appmsgid 已拿到），所以这一段**任何异常都不能让整批死掉**。
    # 实测（2026-09-21）：跑到第 13 篇时页面 WebSocket 突然断开（ws closed），
    # 异常从 _click_by_text 冒出，publish 进程当场退出，后面 16 篇全没发。
    # 这里降级成 "partial" 返回，由 cmd_publish 记账；下次运行按 appmsgid
    # **就地补完**（见 finish_one），不会新建重复稿。
    try:
        # 原创声明必须在首次保存之后做（草稿有 appmsgid 才能提交成功），
        # 声明完再补一次保存把状态固化。失败不挡发布主链路。
        r2 = None
        declared = False
        r = _declare_original(cdp)
        if r == "OK":
            declared = True
            print("  原创声明: ✓ 文字原创")
            # 原创声明成功后顺手开启赞赏（依赖原创声明，失败不挡发布）
            r2 = _enable_reward(cdp)
        elif str(r).startswith("SKIP"):
            declared = True
            print("  原创声明: %s" % r)
        else:
            print("  原创声明: [warn] %s" % r)
        # 补一次保存把原创/赞赏状态固化
        r = _click_by_text(cdp, "保存为草稿")
        time.sleep(4)
        print("  补保存: %s" % r)
        # 赞赏最终校验：只信落库状态（重新加载草稿页读行文本）。
        # 不看弹窗是否关闭、也不看当前页面行文本——那两样都是滞后的过程量，
        # 会把成功误报成失败（2026-09-21 实测踩过）。
        if r2 is not None:
            if r2 == "OK":
                print("  赞赏开启: ✓ 赞赏作者")
            elif _reward_after_reload(cdp, tid, appmsgid):
                print("  赞赏开启: ✓ 赞赏作者（重载确认）")
            else:
                print("  赞赏开启: [warn] %s" % r2)
    except Exception as e:
        print("  [warn] 收尾中断（草稿已存好，仅原创/赞赏未完成）: %s" % e)
        return {"status": "partial", "appmsgid": appmsgid}
    cdp.close_target(tid)   # 这篇存完即关 tab，不堆积
    st = _finish_status(cover_ok, declared)
    if st != "ok":
        print("  [warn] 收尾不完整（封面%s / 原创%s）→ 记 partial，"
              "下次运行按 appmsgid 就地补完"
              % ("OK" if cover_ok else "缺", "OK" if declared else "缺"))
    return {"status": st, "appmsgid": appmsgid}


def finish_one(cdp, c, appmsgid, tok):
    """把「草稿已建、收尾未完成」的条目**就地补完**。

    为什么不重跑 publish_one：那会再建一篇新草稿，草稿箱里立刻多一份重复稿
    （2026-09-21 实测踩过）。这里按 appmsgid 打开**已有**草稿补原创/赞赏。
    `_declare_original` / `_enable_reward` 都是幂等的：已声明过返回 SKIP。
    返回 "ok"（原创已落） / "partial"（仍没搞定）。
    """
    print("\n→ %s — %s（补完已有草稿 appmsgid=%s）"
          % (c["id"], make_wechat_title(c), appmsgid))
    # 上次中断往往在页面上留着一个开着的弹窗，顺手把那个残留标签页关掉，
    # 免得用户看着一堆僵住的窗口。
    for t in cdp.list_targets():
        if t.get("type") == "page" and ("appmsgid=%s" % appmsgid) in (t.get("url") or ""):
            cdp.close_target(t["id"])
    tid = _open_draft_editor(cdp, appmsgid, tok)
    try:
        try:
            _close_dialog(cdp)
        except Exception:
            pass
        # 封面：中断也可能发生在「设封面」之前（实测 trustmrr 赶上「设封面 NO_MENU」）。
        # 只信服务端 cover 字段——正文里有图不代表草稿封面已设。
        cover_ok = True
        try:
            for d in _draft_list(cdp, tok):
                if str(d.get("appmsgid")) == str(appmsgid):
                    if not str(d.get("cover") or "").strip():
                        sc = _set_cover_from_body(cdp)
                        print("  补封面: %s" % sc)
                        if not str(sc).startswith(("OK", "SKIP")):
                            cover_ok = False
                    break
        except Exception as e:
            print("  [warn] 封面检查跳过: %s" % e)
        r = _declare_original(cdp)
        declared = r == "OK" or str(r).startswith("SKIP")
        if r == "OK":
            print("  原创声明: ✓ 文字原创")
        elif declared:
            print("  原创声明: %s" % r)
        else:
            print("  原创声明: [warn] %s" % r)
        r2 = _enable_reward(cdp) if declared else None
        _click_by_text(cdp, "保存为草稿")
        time.sleep(4)
        print("  补保存: OK")
        if r2 == "OK":
            print("  赞赏开启: ✓ 赞赏作者")
        elif r2 is not None:
            if _reward_after_reload(cdp, tid, appmsgid):
                print("  赞赏开启: ✓ 赞赏作者（重载确认）")
            else:
                print("  赞赏开启: [warn] %s" % r2)
        return _finish_status(cover_ok, declared)
    except Exception as e:
        print("  [warn] 补完失败: %s" % e)
        return "partial"
    finally:
        cdp.close_target(tid)


def build_todo(all_cases, published, ready):
    """构造待办列表 [(case, art, resume_appmsgid)]，按 cases.json 顺序。

    = 未发的（新发，resume 为 None） + 「草稿已建、收尾没做完」的（就地补完）。

    ⚠ 不能直接拿 `ready` 当待办：`compute_queue()` 会把 published 里的条目
    **整条剔掉**，包括 status=partial 的半成品 —— 那样半成品就被永远当成
    「已发」跳过了（2026-09-21 实测：shipfast 断连后漏了一整轮）。
    """
    ready_ids = set(c["id"] for c, _ in ready)
    todo = []
    for c in all_cases:
        cid = c["id"]
        if cid in ready_ids:
            todo.append((c, find_article(cid, c.get("name")), None))
            continue
        rec = published.get(cid) or {}
        if rec.get("status") == "partial":
            art = find_article(cid, c.get("name"))
            if not art:
                continue
            # 有 appmsgid → 就地补完那篇旧草稿；没记 appmsgid（老记录）就没法定位，
            # 只能当新发（宁可重发一篇，也别把它永久卡在「跳过」里）。
            todo.append((c, art, rec.get("appmsgid") or None))
    return todo


def cmd_publish(args):
    all_cases = load_cases()
    case_index = {c["id"]: i for i, c in enumerate(all_cases)}
    published = load_published() if not args.dry else {}
    # --case 模式：只发指定 id，不跳过已发记录（允许重发修正）
    if args.case:
        c = next((x for x in all_cases if x["id"] == args.case), None)
        if not c:
            print("未找到 case id=%s" % args.case)
            return
        art = find_article(args.case, c.get("name"))
        if not art:
            print("未找到 %s 的公众号-*.html，先跑 build。" % args.case)
            return
        todo = [(c, art, None)]
        print("指定单篇重发：%s" % args.case)
    else:
        ready, missing = compute_queue()
        if missing:
            print("注意：%d 条缺发布就绪 HTML，先跑 `build`。以下只发已有的 %d 条。" %
                  (len(missing), len(ready)))
        todo = build_todo(all_cases, published, ready)
        n_fix = sum(1 for t in todo if t[2])
        if args.limit:
            todo = todo[:args.limit]
            n_fix = sum(1 for t in todo if t[2])
        print("跳过已发完 %d 条；本次处理 %d 条（新发 %d、就地补完 %d）。"
              % (len(published) - n_fix, len(todo), len(todo) - n_fix, n_fix))
        if not todo:
            print("队列为空（都已发过或全缺源），没有可发的。")
            return
    if not args.dry:
        _need("title")
        _need("editor")
    cdp = None
    tok = None
    done = 0
    try:
        if not args.dry:
            cdp = CDP(args.port)
        for c, art, resume_id in todo:
            idx = case_index.get(c["id"], 0)
            try:
                if resume_id:
                    if tok is None:
                        _tid0, tok = _connect_mp(cdp)
                        if not tok:
                            raise RuntimeError("拿不到 token，确认调试窗口里已登录")
                    status = finish_one(cdp, c, resume_id, tok)
                    appmsgid = resume_id
                else:
                    st = publish_one(cdp, c, art, dry=args.dry, index=idx)
                    status = st.get("status")
                    appmsgid = st.get("appmsgid")
            except Exception as e:
                # 单篇失败不能拖垮整批（2026-09-21：一次瞬时断连让后面 16 篇全没发）。
                # 记录/落库都在下面按篇进行，所以直接继续下一篇即可。
                print("  [error] %s 处理失败，跳过继续：%s" % (c["id"], e))
                # 连接可能已经坏了，丢掉它让下次重新连（send 里也有自动重连兜底）
                if cdp is not None:
                    cdp.ws = None
                continue
            if args.dry or status not in ("ok", "partial"):
                continue
            if resume_id and status == "ok":
                print("  ✓ 已就地补完 appmsgid=%s" % appmsgid)
            published[c["id"]] = {
                "title": make_wechat_title(c),
                "published_at": time.strftime("%Y-%m-%d %H:%M"),
                "status": "draft" if status == "ok" else "partial",
                "appmsgid": appmsgid,
                "file": os.path.basename(art),
            }
            # 逐篇落库：全量要跑几个小时，中途被 kill / 断电时如果只在结尾写，
            # 已建好的草稿就成了「未登记」——下次全量会重发一遍，草稿箱里
            # 一堆重复稿（2026-09-21 实测踩过）。
            save_published(published)
            done += 1
            if status == "partial":
                print("  ⚠ 草稿已建（appmsgid=%s）但收尾没做完；"
                      "重跑 publish 会就地补完，不会新建重复稿。" % appmsgid)
    finally:
        # 每篇 tab 已在 publish_one / finish_one 里各自关闭；这里不再关整个浏览器，
        # 保留你的登录窗口，方便直接去后台点「群发」。
        if cdp and cdp.bws is not None:
            try:
                cdp.bws.close()
            except Exception:
                pass
    if not args.dry:
        save_published(published)
        print("\n已记录 %d 条到 %s" % (done, os.path.relpath(PUBLISHED_PATH, ROOT)))
        left = [c["id"] for (c, _a, _r) in todo
                if published.get(c["id"], {}).get("status") != "draft"]
        if left:
            print("⚠ 这些没发完，重跑 `publish` 会接着处理：%s" % ", ".join(left))


# ======================================================================
# 补封面（历史草稿）
# ======================================================================
def _connect_mp(cdp, debug=False):
    """连到一个已登录的 mp 页面，返回 (tid, token)。优先复用已开着的 tab。"""
    for t in cdp.list_targets():
        u = t.get("url") or ""
        if t.get("type") == "page" and "mp.weixin.qq.com" in u:
            if cdp.connect_target(t["id"]):
                tok = _get_token(cdp, debug=debug)
                if tok and tok != "NO_TOKEN":
                    return t["id"], tok
    tid = cdp.new_target(MP_HOME)["id"]
    cdp.connect_target(tid)
    for _ in range(25):
        time.sleep(1)
        tok = _get_token(cdp, debug=debug)
        if tok and tok != "NO_TOKEN":
            return tid, tok
    return tid, None


def _draft_list(cdp, tok, count=50):
    """拉服务端草稿列表 -> [{appmsgid,title,cover,...}]。

    在 mp 页面上下文里发同步 XHR，走浏览器自己的 cookie，不需要额外鉴权。
    注意 **type=77 才是图文草稿**（type=10 返回空列表，实测）。
    """
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_ex&type=77"
           "&sub=all&begin=0&count=%d&token=%s&lang=zh_CN&f=json&ajax=1"
           "&random=0.7" % (count, tok))
    js = ("(function(){var x=new XMLHttpRequest();"
          "x.open('GET',%s,false);x.send(null);"
          "return x.responseText;})()" % json.dumps(url))
    raw = cdp.eval(js, refresh_context=True)
    if not isinstance(raw, str):
        return []
    try:
        return json.loads(raw).get("app_msg_list") or []
    except Exception:
        return []


def _open_draft_editor(cdp, appmsgid, tok, timeout=30):
    """新开 tab 打开**已存在**的草稿编辑页，返回 tid。"""
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit"
           "&action=edit&reprint_confirm=0&type=77&appmsgid=%s&token=%s"
           "&lang=zh_CN" % (appmsgid, tok))
    tid = cdp.new_target(url)["id"]
    cdp.connect_target(tid)
    for _ in range(timeout):
        time.sleep(1)
        try:
            if cdp.eval("!!document.querySelector('#title')",
                        refresh_context=True):
                return tid
        except Exception:
            pass
    return tid


def cmd_fix_cover(args):
    """给已有草稿补封面。

    缘由（2026-09-21）：早期 publish_one 只把封面图插进正文，误以为
    「默认首图会自动成为封面」—— 服务端 `cover` 字段其实一直是空的，
    于是草稿箱列表项全是灰块。本命令按**服务端状态**逐个补齐。
    """
    cdp = CDP(args.port)
    _tid0, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
    drafts = _draft_list(cdp, tok)
    if not drafts:
        raise SystemExit("✗ 拉不到草稿列表（登录态失效或接口变了）")
    only = getattr(args, "appmsgid", None)
    todo = [d for d in drafts
            if (not only or str(d.get("appmsgid")) == str(only))
            and not str(d.get("cover") or "").strip()]
    print("草稿共 %d 篇%s，缺封面 %d 篇"
          % (len(drafts), "（已按 appmsgid 过滤）" if only else "", len(todo)))
    if args.limit:
        todo = todo[:args.limit]
    fixed = skipped = 0
    for d in todo:
        aid = d.get("appmsgid")
        print("\n→ %s — %s" % (aid, (d.get("title") or "")[:34]))
        tid = _open_draft_editor(cdp, aid, tok)
        try:
            r = _set_cover_from_body(cdp)
            print("  设封面: %s" % r)
            if not str(r).startswith(("OK", "SKIP")):
                skipped += 1
                continue
            _click_by_text(cdp, "保存为草稿")
            ok = False
            for _ in range(20):
                time.sleep(1)
                if "appmsgid=" in (_tab_url(cdp, tid) or ""):
                    ok = True
                    break
            print("  保存: %s" % ("OK" if ok else "超时"))
            if ok:
                fixed += 1
            else:
                skipped += 1
        finally:
            cdp.close_target(tid)
    print("\n完成：补齐 %d 篇，跳过/失败 %d 篇" % (fixed, skipped))


def cmd_refresh(args):
    """把本地重新生成过的正文，就地灌回**已存在**的草稿（不新建草稿）。

    为什么需要它（2026-09-22）：改了正文模板后，草稿箱里那些已经建好的草稿
    不会自动跟着变 —— 它们存的是发布当时的 HTML。要让改动生效，只能按
    appmsgid 打开原草稿、整篇替换正文、再保存。

    两个要点：
      · `_set_body` 是**整篇替换**语义，配合「草稿已存在」，不会新建重复稿；
      · 封面 / 原创 / 赞赏是与正文分开的字段，重灌正文不动它们
        （但它们如果本来就没设上，刷新也补不了 —— 那种走 `finish_one`）。

    appmsgid 的取法：优先用记录里存的；13 条老记录没存（加固前的批次），
    退回按标题在服务端草稿列表里反查。
    """
    cases = load_cases()
    published = load_published()
    only = set(x.strip() for x in (args.case or "").split(",") if x.strip())

    todo = []
    for c in cases:
        cid = c["id"]
        if only and cid not in only:
            continue
        rec = published.get(cid)
        if not rec:
            continue                      # 没发过，无需刷新
        art = find_article(cid, c.get("name"))
        if not art:
            print("  [skip] %s 缺发布就绪 HTML" % cid)
            continue
        todo.append((cid, art, rec))
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("没有可刷新的草稿（--case 没匹配到、或都没发过）。")
        return
    print("待刷新 %d 篇：%s" % (len(todo), "、".join(c for c, _, _ in todo[:8])
                             + ("…" if len(todo) > 8 else "")))
    if args.dry:
        return

    cdp = CDP(args.port)
    _tid0, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
    drafts = _draft_list(cdp, tok, count=60)
    by_title = {}
    for d in drafts:
        t = (d.get("title") or "").strip()
        if t:
            by_title[t] = d.get("appmsgid")

    ok_n = fail_n = skip_n = 0
    for cid, art, rec in todo:
        aid = rec.get("appmsgid") or by_title.get((rec.get("title") or "").strip())
        if not aid:
            print("\n→ %-18s [skip] 查不到 appmsgid（标题没在草稿列表里匹配上）" % cid)
            skip_n += 1
            continue
        ex = extract_article(art)
        body = pm_safe_body(ex["body_html"])
        print("\n→ %-18s appmsgid=%s 正文 %d 字" % (cid, aid, len(strip_tags(body))))
        tid = _open_draft_editor(cdp, aid, tok)
        try:
            r = _set_body(cdp, body)
            print("  set_body: %s" % r)
            time.sleep(2)
            rc = _click_by_text(cdp, "保存为草稿")
            saved = False
            for _ in range(20):
                time.sleep(1)
                if "appmsgid=" in (_tab_url(cdp, tid) or ""):
                    saved = True
                    break
            print("  保存: %s (%s)" % ("OK" if saved else "超时", rc))
            if saved:
                ok_n += 1
                # 顺手把 appmsgid 补进记录：以后不必再靠标题反查
                rec["appmsgid"] = str(aid)
                rec["refreshed_at"] = time.strftime("%Y-%m-%d %H:%M")
                save_published(published)
            else:
                fail_n += 1
        except Exception as e:
            print("  [error] %s" % e)
            fail_n += 1
        finally:
            cdp.close_target(tid)
    print("\n完成：刷新 %d 篇，失败 %d 篇，跳过 %d 篇" % (ok_n, fail_n, skip_n))


# ======================================================================
# CLI
# ======================================================================
def main():
    ap = argparse.ArgumentParser(description="公众号拆解自动发布管线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="生成缺的发布就绪 HTML")
    sub.add_parser("queue", help="打印实时待发队列")
    sub.add_parser("plan", help="生成动态发布队列 md")
    pi = sub.add_parser("inspect", help="校验单文件抽取")
    pi.add_argument("file", help="公众号-*.html 路径")
    pd = sub.add_parser("discover", help="连 CDP 导出编辑器 DOM")
    pd.add_argument("--port", type=int, default=CDP_PORT)
    pd.add_argument("--debug", action="store_true", help="打印 Chrome 原始 CDP 响应，排查 eval 返回空")
    pp = sub.add_parser("publish", help="连 CDP 把队列存成草稿")
    pp.add_argument("--port", type=int, default=CDP_PORT)
    pp.add_argument("--limit", type=int, default=0, help="只发前 N 条")
    pp.add_argument("--case", help="只发指定 case id（可覆盖已发记录重发）")
    pp.add_argument("--dry", action="store_true", help="只打印计划不打开浏览器")
    pf = sub.add_parser("fix-cover", help="给已有草稿补封面（服务端 cover 为空的）")
    pf.add_argument("--port", type=int, default=CDP_PORT)
    pf.add_argument("--limit", type=int, default=0, help="只处理前 N 篇")
    pf.add_argument("--appmsgid", help="只处理指定草稿 id")
    pr = sub.add_parser("refresh", help="把本地重生成过的正文就地灌回已有草稿")
    pr.add_argument("--port", type=int, default=CDP_PORT)
    pr.add_argument("--case", help="只刷指定 case id（逗号分隔）")
    pr.add_argument("--limit", type=int, default=0, help="只刷前 N 篇")
    pr.add_argument("--dry", action="store_true", help="只打印计划不打开浏览器")
    args = ap.parse_args()

    # 127.0.0.1 必须绕开系统代理，否则 websocket 连 Chrome 调试端口会被掐（WinError 10053）
    for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(_k, None)
    _np = os.environ.get("no_proxy", "")
    if "127.0.0.1" not in _np:
        os.environ["no_proxy"] = ("127.0.0.1,localhost" + ("," + _np if _np else ""))

    if args.cmd == "build":
        cmd_build()
    elif args.cmd == "queue":
        cmd_queue()
    elif args.cmd == "plan":
        cmd_plan()
    elif args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "discover":
        cmd_discover(args)
    elif args.cmd == "publish":
        cmd_publish(args)
    elif args.cmd == "fix-cover":
        cmd_fix_cover(args)
    elif args.cmd == "refresh":
        cmd_refresh(args)


if __name__ == "__main__":
    main()
