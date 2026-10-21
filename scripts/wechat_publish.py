#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_publish.py — 公众号「万物解释者 · 拆解海外」自动发布管线
================================================================

背景：个人订阅号（未认证）没有 freepublish API 权限，无法 API 群发
（probe_wechat_openapi.py 有完整记录）。所以走「浏览器自动化」：复用本机
已登录 Chrome 的登录态，用 CDP 把图文存成草稿，再自动点「发表」。

「发表」有两种模式（2026-10-19 实测，probe_wechat_send.py）：
  · 发布（群发通知开关关）：不推送、不占每日额度
  · 群发（群发通知开关开）：推送粉丝、占订阅号每日 1 次额度
  ⚠⚠ **两种模式都要管理员/运营者扫码**（曾据「探针里发布链路没弹二维码」
  误判发布不扫码 —— 那是探针拦掉了 check 请求的 send()，流程根本没走到
  扫码那一步）。扫码是物理动作，`send` 自动化的是它之前（开草稿、设开关、
  两层确认、check）和之后（判定、回读校验、记账）的所有步骤。
`publish` 子命令负责存草稿，`send` 子命令负责发表（默认发布模式）。

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
  publish   连 Chrome CDP，把队列逐条存成草稿。
  send      连 Chrome CDP，把草稿箱里的稿子**发表**出去（默认发布模式，
            不推送不占额度；--notify 走群发。**两种模式都要管理员扫码**，
            脚本在扫码前全自动、扫码时停下等待、扫码后自动校验落账）。

依赖：纯标准库。Chrome 需本机已装，且以远程调试端口启动（见 discover 提示）。
"""
import argparse
import atexit
import base64
import glob
import json
import os
import subprocess
import re
import select
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_clean import clean_placeholders, strip_placeholders  # noqa: E402

CASES_PATH = os.path.join(ROOT, "data", "cases.json")
ARTICLES_DIR = os.path.join(ROOT, "out", "articles")
PUBLISHED_PATH = os.path.join(ROOT, "data", "wechat_published.json")
# 篇号注册表 {case_id: 第几篇}。**一经分配永不改变**（见 assign_issue_numbers）。
ISSUE_PATH = os.path.join(ROOT, "data", "issue_numbers.json")
PLAN_PATH = os.path.join(ROOT, "发布队列-自动生成.md")

CDP_PORT = int(os.environ.get("CDP_PORT", 9222))
# 草稿箱列表页**每页只渲染这么多张**卡片。实测（2026-10-02）：草稿共 38 篇时，
# `?begin=0&count=10` 的页面 DOM 里只有 10 个 [data-appid]，`begin=30` 才有第 38 张。
#⇒ 任何按appmsgid 找卡片的逻辑都必须先翻页，不能只查第 1 页。
DRAFT_PAGE = 10

# ⚠ 代理污染防御：连 127.0.0.1 的 CDP 调试端口时，父进程继承的 http_proxy
# （如 127.0.0.1:2662）会让 urllib 走代理、把 WS 掐成 10053。本模块被 50+ 脚本
# import 当库用——import 时【绝不能】pop 掉调用方的 http_proxy（会误伤它们的外网
# 代理），所以这里只把本地地址加进 no_proxy，既挡住 localhost 走代理、又不破坏
# importer。真正连 CDP 的两条路径还有额外兜底：CDP._http 用 ProxyHandler({}) 显式
# 禁代理，WS 是裸 socket.create_connection 根本不读代理 env（见下方 __main__ 入口
# 的更激进清法，仅本脚本直接运行时生效）。
_LOCAL_NO_PROXY_HOSTS = ("127.0.0.1", "localhost", "::1")


def ensure_no_proxy_for_localhost():
    """import 成库时调用：把本地地址加进 no_proxy / NO_PROXY。

    避免 CDP 连 127.0.0.1 时被继承的 http_proxy 掐断（10053），同时【不】pop
    掉 http_proxy/https_proxy——否则会误伤 importer 的外网代理。幂等。
    """
    for _env in ("no_proxy", "NO_PROXY"):
        _val = os.environ.get(_env, "")
        _parts = [p.strip() for p in _val.split(",") if p.strip()]
        for _h in _LOCAL_NO_PROXY_HOSTS:
            if _h not in _parts:
                _parts.append(_h)
        os.environ[_env] = ",".join(_parts)


ensure_no_proxy_for_localhost()  # 模块导入即生效（覆盖 import-as-library 路径）

# 已登录主页（靠 cookie 登录态，无需 token，会被 mp 重定向到图文列表/看板）
MP_HOME = "https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN"
# 拿 token 必须从【根域名】进：它会 302 到 `home?...&token=xxx`。
# 直接开 MP_HOME（无 token）实测稳定落到「请重新登录」壳页 —— 即使会话
# 完全有效（2026-10-18 实测：根域名拿到 token=827333358，MP_HOME 同刻 NO_TOKEN）。
# 所以「开新页抠 token」一律用 MP_ENTRY；MP_HOME 只用于已有 token 的场景。
MP_ENTRY = "https://mp.weixin.qq.com/"

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


def _manual_title(cid):
    """手工标题（与正文 h1 同源）。没有手改过就返回 None。

    2026-09-25：草稿箱列表显示的是**标题字段**，它原来只由 name + one_liner
    拼出，与正文里的 h1 是两套逻辑。结果是：正文换成了手改的标题，列表里
    还挂着「UpLinked B.V.：营销类产品」这种占位清理后剩下的残句。
    手改过的案例一律以 TITLE_OVERRIDES 为准，两边才不会分叉。
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import make_article
        ov = (make_article.TITLE_OVERRIDES or {}).get(cid) or []
        t = (ov[0] or "").strip() if ov else ""
        return t if 0 < len(t) <= 64 else None
    except Exception:
        return None


def make_wechat_title(c):
    """从 cases.json 生成适合公众号的简洁标题（≤64 字，优先 ≤30 字）。

    规则：手改过的优先；否则名字 + one_liner 最简。
    例：Insect Bite ID → "Insect Bite ID：用 AI 识别虫咬类型"

    ⚠⚠ 2026-10-14：红线（标题禁金额/收入指标）——
    不再在标题追「，月收 $X」/「，累计 $X」。金额数字是正文该讲的事，
    放进标题既违反红线、也容易被平台判营销感过重。标题只放「产品名：一句话描述」。
    （头条标题以本函数为候选，故一并受益。）
    """
    import make_article as _ma
    manual = _manual_title(c.get("id"))
    if manual:
        return _ma.strip_title_money(manual)
    name = (c.get("name") or "").strip()
    one = _ma.strip_title_money((c.get("one_liner") or "").strip())

    def _ok(t):
        return t and len(t) <= 64

    if name and one:
        t = "%s：%s" % (name, one)
        if _ok(t):
            return _ma.strip_title_money(t)
    if name:
        return _ma.strip_title_money(name[:64])
    return _ma.strip_title_money((c.get("id") or "untitled")[:64])


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
    ("#0d2b45", "#164a6b", "#7fd4ff"),     # 天青
    ("#2b1f0d", "#5a421a", "#ffc861"),     # 琥珀
    ("#1d1035", "#3a1f6e", "#a8e0ff"),     # 靛蓝
    ("#0f3330", "#1d5f5c", "#5fe3d0"),     # 青玉
]

# 同一个 batch 里挨着发的新案例很容易撞色 —— 旧的 `sum(ord) % N` 哈希太弱，
# 实测 mort（m=109×4+…）与 quran-unlock（q=113…）字符数差异刚好抵消，两条
# 都落到索引 0，封面一模一样（2026-09-30 用户在头条后台发现）。
#
# 修法不是「换个更强的哈希」—— 那只能把概率压低，不能归零。真正的坑是
# **分配必须与调用范围无关**：`covers`（全量 39 条）和 `cover --case mort`
# （单条）如果各自从空状态开始探测，同一条案例两次跑会拿到不同颜色，
# 已发布的封面就会跟着变色。所以这里不记录「谁占了哪个槽」，而是把
# 整个 cases.json 的 id 顺序当作已占用的全集：探测时跳过**任何其他案例**
# 已经用掉的槽位，只跟自己比。这样单条跑和全量跑结果必然一致。
def _all_case_ids():
    """库里全部案例 id（封面分配时当作「别人已占用的槽位」）。"""
    global _ALL_IDS_CACHE
    if _ALL_IDS_CACHE is None:
        try:
            import json
            path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "data", "cases.json")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rows = data if isinstance(data, list) else list(data.values())
            _ALL_IDS_CACHE = [str(r.get("id", "")) for r in rows
                              if isinstance(r, dict) and r.get("id")]
        except Exception:                                # noqa: BLE001
            _ALL_IDS_CACHE = []
    return _ALL_IDS_CACHE


_ALL_IDS_CACHE = None


# ==========================================================================
# 封面文案：项目名 + 一句话介绍（**不含金额**）
# ==========================================================================
# 2026-10-02 用户要求：封面不要出现金额。`metrics.headline` 是**抓取当天
# 的快照**，数字会随站点自己变（与 TrustMRR 实测对不上也常见），
# 印在封面上等于印一个会过期的数字。
#
# ⚠ 放在 wechat_publish 而不是 toutiao_publish 的原因：避免循环 import
#   （toutiao → xhs 已存在，xhs不能再 → toutiao）。
#   头条、B站（复用头条 cover.png）、小红书、公众号四平台共用这一份。

# 封面描述里要剔掉的金额片段。
# ⚠ 只剔「金额本身」，**尽量不动 surrounding 语义** —— 但正则有漏网，
#   所以真正的正解是数据层写 `cover_desc`（见 cover_desc() 的优先级）。
_MONEY_TOKEN = re.compile(
    r"(?:[$￥€£]\s?[\d,]+(?:\.\d+)?\s?[KkMmBb]?)"      # $2.4K / $199
    r"|(?:\d[\d,]*(?:\.\d+)?\s?[KkMmBb]?\s?(?:万|亿)?"   # 25 万 / 3.57M
    r"(?:美元|美金|人民币|元|欧元|英镑))"
    r"|(?:[$￥€£]\s?[\d,]+(?:\.\d+)?)"
)

# 描述里的营收修饰语，剔金额后常留下空洞短语（实测 outrank 变废句）
_REVENUE_PHRASE = re.compile(
    r"(?:月入|年收入|年入|营收|月营收|收入|售价|定价|ARR|MRR)"
    r"\s*(?:达到|高达|超过|近|约|突破|达|是)?\s*"
)

# ---- 子句级清洗（2026-10-18 重做）------------------------------------------
# 旧做法 `_MONEY_TOKEN.sub("", raw)` 只剔「金额本身」，把单位和区间留在原地，
# 结果撕出半截句。实测 19/255 条封面文案成了废句，例如：
#   「…并定时发布：–199/月，万、2.7 万用户。」
#   「…开发者服务，万、145 个订阅。」
# 金额/指标是**整段**信息，剔半截必然留残渣 ⇒ 改为按子句判断：
# 整句只承载金额或指标就**整句丢弃**，剩下的自然句拼回去；拼不出一句像样
# 的话就返回空串（封面只显示项目名），绝不把半截句印上去。
_CLAUSE_SPLIT = re.compile(r"[：:，,；;。！!？?\n]")

# 金额（含「19€」这种符号后置写法）
_MONEY_ANY = re.compile(
    r"[$￥€£]\s?\d[\d,.]*"
    r"|\d[\d,.]*\s*[€£]"
    r"|\d[\d,.]*\s*[万千万亿]?\s*(?:美元|美金|人民币|元|欧元|英镑|刀)"
    r"|(?:MRR|ARR|月入|年入|营收|收入|流水|定价|售价|客单价|要价|挂牌)"
    r"\s*[:：]?\s*[$￥€£]?\s*\d[\d,.]*\s*[KkMmBb万亿]?",
    re.I)

# 规模数字（用户数/订阅数也算会过期的快照，同样不上封面）
_COUNT_ANY = re.compile(
    r"\d[\d,.]*\s*[万千万亿]?\+?\s*个?\s*[新名位]?\s*"
    r"(?:用户|订阅者|订阅|付费用户|付费开发者|粉丝|下载量?|客户|企业客户)")

# 比率类指标（毛利 82% / 留存 42% / 倍数 3.4x）
_RATIO_ANY = re.compile(
    r"(?:毛利|留存|倍数|增速|环比|同比|转化率|付费率|估值)"
    r"\s*[:：]?\s*[+\-]?\d[\d.,]*\s*[%％倍xX]?")

# 只剩残渣的子句（剔完金额后常见）
_RESIDUE = re.compile(
    r"^(?:从|自|约|近|达|共|累计|一次性|月|年|起|以上|的|和|与|万|千|[KkMmBb]"
    r"|[/／](?:月|年|次|天)|\d+)$")


def _is_metric_clause(cl):
    """这一小句是否只承载「金额 / 规模 / 比率」这类会过期的快照。"""
    return bool(_MONEY_ANY.search(cl) or _COUNT_ANY.search(cl)
                or _RATIO_ANY.search(cl))


def _desc_from_text(raw):
    """从一句话里拼出「只讲它是干什么的」的封面文案。拼不出就返回空串。"""
    kept = []
    for cl in _CLAUSE_SPLIT.split(raw or ""):
        cl = cl.strip(" 　·-—–_")
        if not cl or _is_metric_clause(cl):
            continue
        # 剔掉嵌在叙述里的指标短语（如「拿下 100 万+ 用户」），再判残渣
        cl = _COUNT_ANY.sub("", cl)
        cl = re.sub(r"(?:做到|冲到|达到|拿下|累计)\s*$", "", cl).strip()
        cl = cl.strip(" 　·-—–、，,；;：:")
        if len(re.sub(r"[^\w\u4e00-\u9fff]", "", cl)) < 3 or _RESIDUE.match(cl):
            continue
        kept.append(cl)
    out = "，".join(kept)
    return _tidy_desc(out)


def _tidy_desc(s):
    """把剥掉金额后的残句收拾干净：去空洞介词/标点、压空白。"""
    s = _REVENUE_PHRASE.sub("", s)
    s = re.sub(r"[，,]\s*(?:[，,]\s*)+", "，", s)       # 连续逗号压成一个
    s = re.sub(r"^\s*(?:的|和|与|、|，|,|；|;|·|-|—)+\s*", "", s)
    s = re.sub(r"\s+(?:的|和|与)\s*$", "", s)
    s = re.sub(r"[，,、；;·]\s*$", "", s)                  # 结尾悬标点
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _strip_name_prefix(text, name):
    """去掉开头重复的项目名。

    封面第一行已经是大字号项目名，第二行再念一遍纯属浪费 —— 实测 38 条
    兜底路径（what_it_does 首句）都是「Demopolish帮助软件和 SaaS 创始人…」
    这种写法。只在整词命中时剥（名字 ≥3 字符、后面紧跟非字母数字）。
    """
    t = (text or "").strip()
    nm = (name or "").strip()
    if not t or len(nm) < 3:
        return t
    m = re.match(r"^\s*%s(?![A-Za-z0-9])\s*[，,：:、·\-—]?\s*" % re.escape(nm),
                 t, re.I)
    if not m:
        return t
    rest = t[m.end():].strip()
    return rest if len(re.sub(r"[^\w\u4e00-\u9fff]", "", rest)) >= 3 else t


def cover_desc(c):
    """封面第二行：项目的一句话介绍。**不含任何金额**。

    取值优先级（高→低）：
      1. `cover_kv.desc` —— 显式覆盖（沿用既有 cover_kv 机制）
      2. `cover_desc`   —— **数据层手写的封面专用文案（推荐）**
      3. `one_liner`    —— 39/39 全有值
      4. `what_it_does` 首句 —— 新案例漏填 one_liner 时的兜底
      5. 空串           —— **都没有就不显示这一行**（用户原话：
                        「如果没简要介绍，就不显示」）

    ⚠ 不要回退到 `metrics.headline`：那正是要移除的金额来源。
    ⚠ 不要用 `how_it_makes_money`：那是营收模型，答非所问。
    ⚠⚠ **one_liner 里本身可能嵌金额**（实测 4/39：shipfast $199、
      visualizee-ai $150/月→$8.6K、outrank 月入 30 万、pieter-levels
      25 万美元）。⇒ **正解是数据层写 `cover_desc` / `cover_kv.desc`**。
    ⚠⚠ 2026-10-18 修正：兜底不再做「剔金额 token」这种半截操作（会撕出
      「，万、」「–199/月」这类废句，实测命中 19 条），改为**子句级**丢弃
      （见 `_desc_from_text`）。one_liner 拼不出像样的一句话时，**继续往
      下试 `what_it_does` 首句**，不拿残句交差；都拼不出就返回空串。
    """
    ck = c.get("cover_kv") or {}
    d = (ck.get("desc") or "").strip()
    if d:
        return d
    d = (c.get("cover_desc") or "").strip()
    if d:
        # 手写文案里也常把项目名写在句首（"PROSP帮销售团队…"），封面第一行
        # 已是大字号项目名 —— 这里同样剥掉。`cover_kv.desc` 保留完全手控，
        # 不再二次加工（要原样显示就走那条路）。
        return _strip_name_prefix(d, c.get("name")) or d
    cands = []
    if (c.get("one_liner") or "").strip():
        # 去掉括注（常有「（数据来自公开披露）」这类尾注）
        cands.append(re.sub(r"（[^）]*）", "", c["one_liner"]))
    w = (c.get("what_it_does") or "").strip()
    if w:
        cands.append(re.split(r"[。！？!?；;]", w, 1)[0])
    for raw in cands:
        t = _strip_name_prefix(_desc_from_text(raw), c.get("name"))
        if t:
            return t
    return ""


def _cover_theme(cid, key="toutiao"):
    """给 case id 分配一个封面主题。

    两条性质，缺一不可：

    1. **稳定** —— 结果只由 id 决定。全量跑和单条跑一致，改库也不会让
       已发布的封面变色。所以不能用「本次运行已占用了哪些槽」这种进程内
       状态，必须每次从 cases.json 全量重算。
    2. **相邻不同色** —— 39 条案例只有 10 套主题，全局互不撞色做不到
       （必然重复）。真正影响观感的是**挨着发的那两条别撞**（用户在
       头条草稿箱一眼就看出来了），所以按 cases.json 顺序做分配，
       每条避开**左右邻居的最终落点**（不是哈希起点 —— 邻居会顺移，
       拿起点当约束等于没约束，实测这样还剩 6 对相邻撞色）。

    撞色只留在不相邻的案例之间，隔若干条就看不到，不影响阅读。
    """
    cid = str(cid)
    n = len(COVER_THEMES)
    ids = _all_case_ids()
    h = [sum((j + 1) * ord(ch) for j, ch in enumerate(s)) % n for s in ids]
    try:
        pos = ids.index(cid)
    except ValueError:
        pos = -1

    def pick(i, avoid):
        """第 i 条在避开 avoid 的前提下取槽位（顺移，不重复）。"""
        for step in range(n):
            cand = (h[i] + step) % n
            if cand not in avoid:
                return cand
        return h[i] % n

    # 从头分配到 pos+1（多算一条后邻，它的落点才能被 pos 避开）
    end = min(pos + 2, len(ids))
    land = [None] * end
    for i in range(end):
        avoid = set()
        if i > 0:
            avoid.add(land[i - 1])
        if i + 1 < len(ids):
            # 后邻的落点由「它避开我」决定 —— 用它的哈希起点做预判，
            # 再加一条硬约束：它绝不会落在我这一条上（见下面对称处理）
            avoid.add(h[i + 1])
        land[i] = pick(i, avoid)
    # 修正：若 pos 与 pos+1 落点相同，后邻让位
    if pos + 1 < len(ids) and land[pos] == land[pos + 1]:
        for step in range(n):
            cand = (h[pos + 1] + step) % n
            if cand != land[pos]:
                land[pos + 1] = cand
                break
    return COVER_THEMES[land[pos]]


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


def cover_html(c, no, w=1080, h=460):
    """生成 1080x460 公众号封面图的完整 HTML 文档（2.35:1）。

    `no` 是**篇号**（历史编号，见 issue_registry），不是排序位置。

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
    # 2026-10-02：统一走cover_desc()，不再直接用 one_liner ——
    # one_liner 实测 4/39 内嵌金额（shipfast $199、outrank 月入 30 万…），
    # 且四平台口径必须一致（铁律：封面文案单一真理）。
    line = cover_desc(c)
    tag = "拆解海外 · 第 %d 篇" % no
    theme = _cover_theme(c.get("id", ""), "wechat")
    base, band, ac = theme

    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    f_name = _fit_font_size(name, _COVER_TEXT_W, [68, 62, 56, 50, 46, 42, 38, 34, 30])
    f_line = _fit_font_size(line, _COVER_TEXT_W, [28, 25, 23, 21, 19, 17])
    # 空介绍不渲染这一行（与头条/B站/小红书一致）。不渲染比渲染空 div
    # 好：空 div 靠 margin-top:20px 撑出一道无意义的空隙。
    line_block = (
        '<div id="t-line" style="font-size:%dpx;font-weight:500;color:%s;'
        "line-height:1.45;margin-top:20px;overflow:hidden;"
        'text-overflow:ellipsis;white-space:nowrap;">%s</div>' % (f_line, ac, esc(line))
        if line else ""
    )

    body = (
        '<div style="position:relative;width:%dpx;height:%dpx;'
        "background:linear-gradient(100deg,%s 0%%,%s 63%%,%s 63%%,%s 100%%);"
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
        "line-height:1.1;margin-top:20px;overflow:hidden;"
        'text-overflow:ellipsis;white-space:nowrap;">%s</div>'
        "%s"
        "</div>"
        '<div style="position:absolute;right:56px;bottom:38px;font-size:17px;'
        'color:rgba(255,255,255,.72);letter-spacing:2px;">%s</div>'
        "</div>"
        % (w, h, base, band, band, band, ac, ac, ac,
           _COVER_TEXT_W, ac, f_name, esc(name), line_block, tag)
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>html,body{margin:0;padding:0;}</style></head>"
        "<body>%s</body></html>" % body
    )


COVER_W, COVER_H = 1080, 460


def _gen_cover(cdp, c, no, out_path):
    """用 Chrome CDP 把封面 HTML 截成 PNG。

    按 1080x460 画布设计、scale=2 截图（约 2160x920 起，实际再乘浏览器
    deviceScaleFactor），微信最终会归一化到 1080x460 —— 走它自己的高质量
    降采样，比自己先缩好更锐。
    """
    html = cover_html(c, no, COVER_W, COVER_H)
    data_url = "data:text/html;charset=utf-8;base64," + base64.b64encode(
        html.encode("utf-8")).decode("ascii")
    cover_tid = cdp.new_target(data_url)["id"]
    cover_cdp = None
    try:
        cover_cdp = CDP(cdp.port)
        if not cover_cdp.connect_target(cover_tid):
            return "CONNECT_FAIL"
        # 🚨🚨 **必须确认真的切到了封面页**（2026-10-17 实测踩坑，症状极隐蔽）：
        # `connect_target` 失败时会**静默留在上一个 target**，于是
        # `Page.captureScreenshot` 截到的是**公众号编辑器页面**（连裁剪弹窗的
        # 黑边和滚动条一起）—— 而返回的 PNG **尺寸仍是 2160x920，完全正常**，
        # 光看尺寸/文件大小发现不了；后果是封面里「拆解海外 · 第 X 篇」
        # 被切在画面外（用户反馈「封面变小、tag 丢了」）。
        # 双保险：① href 必须是 data:；② 页面里必须有我们自己的 #t-name 节点。
        href = ""
        for _ in range(12):
            time.sleep(0.5)
            try:
                href = str(cover_cdp.eval("location.href",
                                          refresh_context=True) or "")
            except Exception:                                 # noqa: BLE001
                href = ""
            if href.startswith("data:"):
                break
        if not href.startswith("data:"):
            return "WRONG_TARGET:%s" % href[:70]
        if str(cover_cdp.eval("!!document.getElementById('t-name')",
                              refresh_context=True)
               or "") not in ("true", "True"):
            return "NO_T_NAME_NODE"
        # 🚨🚨 **必须把视口撑到画布大小**（2026-10-17 实测，这才是真凶）：
        # headless 默认视口约 780px 宽，而封面是 1080px 宽 —— 于是
        # `Page.captureScreenshot` 的 clip 被**裁到视口内**，右侧 ~300px 变成
        # 黑边、页面自身滚动条也被截进来。产物 **尺寸仍是 2160x920**，
        # 所以肉眼看尺寸完全正常，但设计被塞进左上角 70%、
        # 右下角的「拆解海外 · 第 X 篇」被切到画面外 ⇒ 用户看到的
        # 「封面变小 + tag 丢了」。
        # deviceScaleFactor=2 + clip scale=1 ⇒ 输出正好 2160x920 且更锐。
        try:
            cover_cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": COVER_W, "height": COVER_H,
                "deviceScaleFactor": 2, "mobile": False})
            time.sleep(0.6)
        except Exception as e:                                 # noqa: BLE001
            print("      [warn] 设置封面视口失败：%s" % str(e)[:60])
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
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": COVER_W, "height": COVER_H,
                     "scale": 1}
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

_FIRST_IMG_JS = (
    "(function(){"
    "var sels=['.appmsg_content_img_item img','.ProseMirror img',"
    "'#js_content img','.edui_body img','img'];"
    "for(var k=0;k<sels.length;k++){"
    "var imgs=document.querySelectorAll(sels[k]);"
    "for(var i=0;i<imgs.length;i++){"
    "var s=imgs[i].src||imgs[i].getAttribute('src')||'';"
    "if(s&&s.indexOf('http')===0)return s;}}"
    "return '';})()"
)


def _first_body_img_url(cdp):
    """正文第一张图的**完整** URL。

    `_upload_cover` 只回传 src 的前 50 字符（`OK:IMG:<src[:50]>`），
    不够喂给 change_cover，所以这里单独取全量。
    """
    try:
        return str(cdp.eval(_FIRST_IMG_JS) or "")
    except Exception:                                          # noqa: BLE001
        return ""


def _set_cover_via_jsapi(cdp, ori_img_url, timeout=25):
    """用官方 JS API `mp_editor_change_cover` 设封面（绕开 UI 裁剪弹窗）。

    源码依据（`appmsg_edit_v2_gray_fe.js`）：
        var E="mp_editor_change_cover"
        apiParam = {oriImgUrl:<http(s) 链接>, picCrop_235_1:[x1,y1,x2,y2](可选)}
      · 不传 picCrop_* 时自动按 2.35:1 **居中裁剪**（O() 函数）
      · oriImgUrl 会经 `/cgi-bin/uploadimg2cdn` 自动转存到微信 CDN
      · 图文类型(share_page_type=0) 只需 235_1 与 1_1 两个尺寸

    ⚠⚠ **oriImgUrl 绝不能带 query string**（2026-10-16 实测）：带
    `?wx_fmt=png&from=appmsg` 时 uploadimg2cdn 返 `ret:-201`
    「原图转存失败，请检查图片格式」；`split('?')[0]` 后立即成功。

    为什么替掉 UI 路线（`_set_cover_from_body`）：
      · 单篇省约 3 分钟（UI 要在 封面菜单→选图→下一步→确认 间轮询等待）
      · headless 下裁剪弹窗的「确认」按钮点不着（CONFIRM_ERR），
        那 12 篇 refresh --cover 的封面全因此没换上
      · 实测 2 秒返回「封面设置成功」，保存后 cover 字段确认变更

    返回 'SUC:封面设置成功' / 'ERR:...' / 'TIMEOUT' / 'BAD_URL' / 'NO_API'
    """
    url = (ori_img_url or "").split("?")[0]
    if not url.startswith("http"):
        return "BAD_URL"
    expr = (
        "(function(url){"
        "window.__cc='PENDING';"
        "var api=window.__MP_Editor_JSAPI__;"
        "if(!api||typeof api.invoke!=='function')"
        "{window.__cc='NO_API';return;}"
        "try{"
        "api.invoke({apiName:'mp_editor_change_cover',apiParam:{oriImgUrl:url},"
        "sucCb:function(res){"
        "window.__cc='SUC:'+JSON.stringify(res).slice(0,300);},"
        "errCb:function(err){"
        "window.__cc='ERR:'+JSON.stringify(err).slice(0,400);}});"
        "}catch(e){window.__cc='THROW:'+e.message;}"
        "})(%s)" % json.dumps(url)
    )
    cdp.eval(expr)
    for _ in range(timeout):
        time.sleep(1)
        st = cdp.eval("window.__cc")
        if st and not str(st).startswith("PENDING"):
            return str(st)
    return "TIMEOUT"


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

# 全文档兜底：headless 下封面裁剪页的弹窗未必是 `.weui-desktop-dialog`
# （宽>300/高>100），导致 `_center_in_dialog` 永远找不到「确认」。
# 这里直接扫整页，但只取「文本精确相等」的叶子/按钮，不会误命中正文段落。
_DOC_CANDS_JS = (
    "(function(){"
    "var out=[];"
    "var all=[].slice.call(document.querySelectorAll('button,a,span,div,p'));"
    "for(var j=0;j<all.length;j++){var e=all[j],cs=getComputedStyle(e);"
    "var r=e.getBoundingClientRect();"
    "out.push({t:(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim().slice(0,40),"
    "left:Math.round(r.left),top:Math.round(r.top),"
    "w:Math.round(r.width),h:Math.round(r.height),"
    "disp:cs.display,vis:cs.visibility,leaf:e.children.length===0});}"
    "return JSON.stringify(out);})()")


def _pick_dialog_button(cands, text, exact_only=False):
    """从弹窗候选元素里挑点击目标（纯函数，便于回归测试）。

    优先级：① 文本**完全相等**的叶子 → ② 完全相等 → ③ 包含的叶子 → ④ 包含。
    并且要求可见、够大（≥5px）、坐标落在视口内（left/top ≥ 0）。

    为什么不直接「包含就选」：正文段落也「包含」按钮文字，且在 DOM 里更靠前，
    会先被选中（点击落在正文上、弹窗按钮没点到）。所以先要「完全相等」，
    再要求是叶子节点；正文段落是容器、且文字长，两道都过不了。

    exact_only=True 时只走「完全相等」两档（①②③→只留①②），用于全文档兜底，
    避免正文段落（`<p>` 常是叶子、且文字「包含」按钮词）被「包含」档误选中。
    """
    def usable(c):
        return (c.get("disp") != "none" and c.get("vis") != "hidden"
                and c.get("w", 0) >= 5 and c.get("h", 0) >= 5
                and c.get("left", -1) >= 0 and c.get("top", -1) >= 0)

    def has(c, exact):
        return (c.get("t", "") == text) if exact else (text in c.get("t", ""))

    tiers = ((True, True), (True, False)) if exact_only \
        else ((True, True), (True, False), (False, True), (False, False))
    for exact, leaf_only in tiers:
        for c in cands:
            if usable(c) and has(c, exact) and (not leaf_only or c.get("leaf")):
                return c
    return None


def _center_in_dialog(cdp, text):
    """在**当前可见弹窗内部**找按钮并返回中心坐标，找不到时回退全文档兜底。

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

    ⚠ 2026-10-16 补：headless 下微信封面裁剪页弹窗未必是 `.weui-desktop-dialog`
    （宽>300/高>100），`_DLG_CANDS_JS` 扫不到 → 永远 CONFIRM_ERR → 封面设不上。
    所以弹窗范围找不到时，回退 `_DOC_CANDS_JS` 全文档精确匹配（exact_only，
    只取文本==目标、可见、在视口内的元素，正文段落不会被「包含」档误命中）。
    """
    def _pick(raw):
        if not raw:
            return None
        try:
            return _pick_dialog_button(json.loads(raw), text)
        except Exception:
            return None

    # 弹窗范围优先；找不到再全文档兜底（exact_only 防正文误命中）
    c = _pick(cdp.eval(_DLG_CANDS_JS))
    if c is None:
        c = _pick_dialog_button(_doc_cands(cdp), text, exact_only=True)
    if not c:
        return None
    return {"x": c["left"] + c["w"] // 2,
            "y": c["top"] + c["h"] // 2,
            "txt": c["t"][:24]}


def _doc_cands(cdp):
    raw = cdp.eval(_DOC_CANDS_JS)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


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


def _set_cover_from_body(cdp, tries=3, force=False):
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

    `force=True` 时跳过「已有封面就 SKIP」的短路，用来**换封面**
    （2026-09-24：篇号错版草稿的封面要重画，而 cover 字段非空）。
    """
    if not force:
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
        # 2026-09-24：**已经有封面**的草稿走的是另一套 Vue 组件，弹层里没有
        # `.pop-opr__item`（实测点完 popopr=0），照上面的判据必然 NO_MENU，
        # 换封面就卡死。所以补一个「菜单文本出现即算打开」的判据。
        if _text_visible(cdp, COVER_MENU_FROM_BODY):
            opened = True
            break
    # ② 菜单项「从正文选择」：先按类名找（新草稿路径），找不到就按文本点
    #    （已有封面路径 —— 不依赖类名，弹层结构变了也能点）。
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
        rt = _click_text_anywhere(cdp, COVER_MENU_FROM_BODY)
        if "CLICKED" not in str(rt):
            return "MENU_ITEM_ERR:%s/%s(menu_opened=%s)" % (r, rt, opened)
    time.sleep(2.5)
    # ③ 选中正文首图
    d = _center_of(cdp, "li.appmsg_content_img_item")
    if not d:
        # 已有封面的草稿里，正文图列表可能不是这个类名 —— 退一步：直接点
        # 弹窗里第一张可见缩略图（img 或带背景图的格子）。
        d = _center_of(cdp, ".weui-desktop-dialog img, .dialog_wrp img",
                       scroll=True)
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
    cases = d if isinstance(d, list) else d.get("cases", [])
    # 定位占位（「（具体定位未获取）」）不能进标题/摘要/封面文案 —— 与 make_article
    # 用的是同一个清理函数，保证「本地 HTML」与「草稿标题」不会一个清一个没清。
    clean_placeholders(cases)
    return cases


def load_published():
    if not os.path.exists(PUBLISHED_PATH):
        return {}
    with open(PUBLISHED_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_published(pub):
    with open(PUBLISHED_PATH, "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False, indent=2)


# ======================================================================
# 篇号注册表：正文与封面上「拆解海外 · 第 N 篇」的 N
# ======================================================================
# 为什么必须单独存一张表（2026-09-24 事故）：
# 原来 N 是 `enumerate(cases)` 的**位置**。而新案例入库是**从数组头部插入**的
# （5 条入库那次如此，后来 9 条也如此），于是「插入 9 条 → 老 30 篇全体后移 9」：
# 本地重生成的 公众号-*.html 里，insect-bite-id 从第 1 篇变成第 10 篇，
# 而草稿箱里已经存在的 30 篇还是老编号 —— 本地与服务端从此对不上，
# 新发的 9 篇更被编成第 1–9 篇，与老的第 1–9 篇撞号。
#
# 正确语义：篇号是**历史编号**（这篇是创刊以来第几篇），不是当前排序位置。
# 所以只在「第一次见到这个案例」时分配一次，之后任何重排、增删都不动它。
def load_issue_numbers():
    """读篇号注册表；文件缺失/损坏时返回空表（由 initial_issue_numbers 补种）。"""
    if not os.path.exists(ISSUE_PATH):
        return {}
    try:
        with open(ISSUE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def save_issue_numbers(reg):
    """原子写；按篇号升序落盘，便于人工 diff 与审阅。"""
    ordered = dict(sorted(reg.items(), key=lambda kv: (kv[1], kv[0])))
    tmp = ISSUE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ISSUE_PATH)


def initial_issue_numbers(cases, published=None):
    """注册表不存在时的补种：老案例按**历史发布顺序**编号，其余按 cases.json 顺序续号。

    为什么用发布顺序：`wechat_published.json` 是**只追加**的，插入顺序就是
    「第几个进发布管线」，正是篇号的原意；而 cases.json 会被头部插入改写，
    拿它当顺序源就是这次事故的根因。补出的结果与历史一致（1–30 老案例），
    新案例续在 31 起。
    """
    pub = load_published() if published is None else published
    ids = {c.get("id") for c in cases}
    reg, n = {}, 1
    for cid in pub:
        if cid in ids and cid not in reg:
            reg[cid] = n
            n += 1
    for c in cases:
        cid = c.get("id")
        if cid and cid not in reg:
            reg[cid] = n
            n += 1
    return reg


def assign_issue_numbers(cases, reg=None, save=False):
    """给还没编号的案例续号（按 cases.json 顺序），已编号的原样保留。

    返回 (reg, new)；new 是本次新分配的 [(id, 篇号)]，供调用方打日志。
    """
    reg = dict(load_issue_numbers() if reg is None else reg)
    used = set(reg.values())
    nxt = (max(used) + 1) if used else 1
    new = []
    for c in cases:
        cid = c.get("id")
        if not cid or cid in reg:
            continue
        while nxt in used:              # 手工删过条目时可能出现空洞，跳过已占号
            nxt += 1
        reg[cid] = nxt
        used.add(nxt)
        new.append((cid, nxt))
        nxt += 1
    if save and new:
        save_issue_numbers(reg)
    return reg, new


def issue_registry(cases, save=True):
    """cmd_* 的统一入口：拿到（必要时补种）篇号注册表，顺带把新号打出来。"""
    reg = load_issue_numbers()
    seeded = False
    if not reg and cases:
        reg = initial_issue_numbers(cases)
        seeded = True
        print("（篇号注册表不存在，按历史发布顺序补种 1–%d）"
              % max(reg.values()))
    reg, new = assign_issue_numbers(cases, reg=reg, save=False)
    for cid, no in new:
        print("  [篇号] %-22s → 第 %d 篇" % (cid, no))
    # 注意补种后 new 是空的（initial 已经全部分完），所以不能只看 new ——
    # 否则注册表永远落不了盘，下次运行又从头补种（2026-09-24 踩过）。
    if save and (seeded or new):
        save_issue_numbers(reg)
    return reg


def find_article(cid, name=None):
    """在 ROOT 下找 公众号-<id|name>拆解.html（大小写不敏感）。

    ⚠ 只认**精确**文件名（2026-10-21 修）：此前子串命中即返回，
    `find_article("storyshort")` 实测拿到 `公众号-storyshort-ai拆解.html`
    —— 标题正文都是 storyshort-ai 的，账却记到 storyshort 名下，比
    「没发」恶劣得多。实测根目录 60 个母版全部严格按 `公众号-<id>拆解.html`
    命名，子串兜底没有收益只有风险；精确不中返回 None，让调用方走
    build 生成（找不到不该赌一个「长得像」的）。
    """
    for w in (cid, name):
        if not w:
            continue
        hits = glob.glob(os.path.join(ROOT, "公众号-%s拆解.html" % w.lower()))
        if hits:
            return hits[0]
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
                r"摘要[^：:]*[:：]?[ \t]*\n(.*?)(?=\n[ \t]*(封面图|原文链接|同步|={4,}|-->))",
                c)
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


def build_one(c, no):
    cid = c["id"]
    src = os.path.join(ARTICLES_DIR, cid + ".html")
    if not os.path.exists(src):
        return "no-src"
    out = open(src, encoding="utf-8").read()
    out = fix_markdown(out)
    m = re.search(r"<h1[^>]*>(.*?)</h1>", out, re.S)
    title = strip_tags(m.group(1)).strip() if m else c.get("name", "")
    subtitle = c.get("one_liner", "")
    # 2026-10-01：非「海外小生意」内容不占「拆解海外 · 第 N 篇」这个篇号。
    # 篇号是**栏目身份**，工具推广/使用类文章挂在这个栏目下会让读者以为
    # 这是第 42 个海外小生意案例。所以按 content_kind 换标签。
    tag = c.get("series_tag") or ("拆解海外 · 第 %d 篇" % no)
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
    _f = (c.get("series_footer") or "— 万物解释者 · 拆解海外 —")
    _f = _f.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    block.append('  <p style="margin:0;text-align:center;font-size:13px;'
                 'color:#bbb;">%s</p>' % _f)
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


def cmd_build(args=None):
    cases = load_cases()
    single = getattr(args, "case", None) if args is not None else None
    if single:
        hit = [c for c in cases if c["id"] == single]
        if not hit:
            raise SystemExit("没有 case id=%s（不回退全量，避免误改已发稿）" % single)
        cases = hit
    reg = issue_registry(cases)
    built = updated = same = nosrc = 0
    for c in cases:
        r = build_one(c, reg.get(c["id"], 0))
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
        # 2026-10-14：头条发文页的 Runtime.evaluate（尤其正文 insertHTML /
        # 封面上传）偶发要 20s+ 才回包，原 30s 太紧会冒 socket.timeout 把
        # publish_one 打挂。放宽到 45s 兜底（封面 20s+ 仍留 25s 余量）；
        # 真·卡死也会在 45s 后冒异常被上层 try 接住（不会无限挂）。
        # 2026-10-15：从 90s 降到 45s —— 小红书删稿偶发 eval 卡死，90s 单点
        # 拖垮整轮重试循环；45s 能在半程内恢复，且对头条/公众号/B站无害。
        self.sock = socket.create_connection((self.host, self.port), timeout=45)
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
                "  Chrome 136+ 拒绝在【默认 user-data-dir】上开调试端口（官方说明：\n"
                "  https://developer.chrome.com/blog/remote-debugging-port）。\n"
                "  ⇒ CDP 只能跑在【独立 profile】上，登录态天然与日常 Chrome 分家，\n"
                "    这是官方设计，不是本机故障。\n"
                "  ⚠ 别再尝试 junction / 复制 Cookies 复用主 profile —— 实测 ChromeCDP\n"
                "    并非 junction，且 App-Bound 加密绑定原始路径，副本解不开。\n"
                "  请按顺序确认：\n"
                "  1) 已【完全退出】所有 Chrome 窗口（任务管理器确认无 chrome.exe 残留）\n"
                "  2) 删除强杀残留的锁（否则 Chrome 启动即退，无报错无窗口）：\n"
                "     del /f /q \"%%LOCALAPPDATA%%\\Google\\ChromeCDP\\Default\\LOCK\"\n"
                "  3) 用【Chrome for Testing】启动（官方对自动化场景的推荐浏览器）：\n"
                "     \"%%LOCALAPPDATA%%\\Google\\ChromeForTesting\\chrome-win64\\chrome.exe\" "
                "--remote-debugging-port=%d "
                "--user-data-dir=\"%%LOCALAPPDATA%%\\Google\\ChromeCDP\"\n"
                "     或直接双击 scripts\\chrome_cdp_check.bat（会自动清 LOCK + 自检登录态）\n"
                "  4) 浏览器打开 http://127.0.0.1:%d/json/version 应返回一段 JSON\n"
                "  5) 若 /json/version 通了但页面提示「请重新登录」，说明微信后台会话\n"
                "     cookie 确实不在这个 profile 里，必须人工扫码一次。\n"
                "     ⚠ 排查细节见 scripts\\chrome_cdp_setup.md（区分进程被收走 vs Cookie 真丢）\n"
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

    def click(self, x, y):
        """真实坐标点击（mousePressed + mouseReleased 一对）。

        为什么不用 JS 的 el.click()：React/Vue 按钮常常不绑合成事件，
        el.click() 返回 true 但页面纹丝不动（2026-09-30 在 B站「新的创作」
        按钮上实测过四种 JS 派发方式全部无效）。真实坐标事件走浏览器命中
        测试，才是我们要的「用户真的点了一下」。

        供 multiplatform/verify.py 的头条「加载更多」复用。
        """
        for ev in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent",
                      {"type": ev, "x": x, "y": y,
                       "button": "left", "clickCount": 1})
        return True

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
    tid = cdp.new_target(MP_ENTRY)["id"]
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
        "eds.sort(function(a,b){"
        "  return a.getBoundingClientRect().top-b.getBoundingClientRect().top});"
        "var el=eds[1];return el?(''+el.innerText).length:-1;})()"
    )
    try:
        return int(cdp.eval(expr))
    except (TypeError, ValueError):
        return -1


def _set_body(cdp, html, min_len=50):
    """正文注入：官方 API 优先，失败/没落库再退回 execCommand('insertHTML')。

    ⚠ 别再写第三种「合成 ClipboardEvent 触发 paste」的方案 —— 那条路走过，
    坑很硬：合成 paste 只会把文本插进 DOM 表面，**不更新 ProseMirror 的内部
    文档**，于是页面看着有字、但「正文字数 0」、保存后正文为空。真要直接改
    内部 state，唯一办法是从 DOM 节点抠出 EditorView（`el.pmViewDesc.view`，
    个别版本是 `__pmViewDesc`），用 `view.domParser` 解析后 dispatch 一个
    `tr.replaceWith(...)` 事务。但既然官方 `mp_editor_set_content` 能用，
    优先用它，样式保留率还更高。
    """
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


def _click_by_text(cdp, text):
    expr = (
        "(function(){var els=[].slice.call(document.querySelectorAll('button,a'));"
        "for(var i=0;i<els.length;i++){var t=(els[i].textContent||'').trim();"
        "if(t&&t.indexOf(%s)>=0){els[i].click();return 'CLICKED:'+t;}}"
        "return 'NO_EL';})()" % json.dumps(text)
    )
    return cdp.eval(expr)


def _click_text_anywhere(cdp, text, selector="li,div,span,a,button,p"):
    """按**精确文本**在页面任意可见元素里找一个来点（不限 button/a）。

    为什么需要它（2026-09-24）：封面菜单在「草稿还没有封面」和「已经有封面」
    两种状态下的 DOM 不一样 —— 有封面时弹层不是 `.pop-opr__item`，原来的
    按类名找法直接 NO_MENU，换封面就卡住了。文本点法不依赖类名。

    命中多个时取**最深**的那个（文本所在的节点），再爬到最近的 li/a/button
    上点 —— 点内层 span 常常不触发父级 handler。
    """
    expr = (
        "(function(){var want=%s;"
        "var els=[].slice.call(document.querySelectorAll(%s));"
        "var best=null;"
        "for(var i=0;i<els.length;i++){var e=els[i];"
        "var cs=getComputedStyle(e);"
        "if(cs.display==='none'||cs.visibility==='hidden')continue;"
        "var rc=e.getBoundingClientRect();"
        "if(rc.width<5||rc.height<5)continue;"
        "if(rc.top<0||rc.top>window.innerHeight)continue;"
        "var t=(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();"
        "if(t!==want)continue;"
        "if(!best||best.contains(e))best=e;}"
        "if(!best)return 'NO_EL';"
        "var tg=best.closest('li,a,button')||best;"
        "tg.click();return 'CLICKED:'+want;})()"
        % (json.dumps(text), selector)
    )
    return cdp.eval(expr)


def _text_visible(cdp, text):
    """页面上有没有「文本完全等于 text 且可见」的元素（不点，只探测）。"""
    expr = (
        "(function(){var want=%s;"
        "var els=[].slice.call(document.querySelectorAll('li,div,span,a,button,p'));"
        "for(var i=0;i<els.length;i++){var e=els[i];"
        "var cs=getComputedStyle(e);"
        "if(cs.display==='none'||cs.visibility==='hidden')continue;"
        "var rc=e.getBoundingClientRect();"
        "if(rc.width<5||rc.height<5)continue;"
        "var t=(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();"
        "if(t===want)return 'YES';}"
        "return 'NO';})()" % json.dumps(text)
    )
    return cdp.eval(expr) == "YES"


def _post_save_diagnose(cdp):
    """点击保存后扫描页面状态：URL、弹窗、报错、保存按钮是否还在。"""
    expr = (
        "(function(){"
        "var out={url:location.href,alerts:[],dialogs:[],toasts:[],errs:[],saveBtn:null};"
        "try{"
        "out.titleExists=!!document.querySelector('#title');"
        "out.editorExists=!!document.querySelector('.ProseMirror[contenteditable=\"true\"]');"
        "// 常见弹窗/遮罩"
        "var masks=document.querySelectorAll('.weui-desktop-dialog, .dialog_wrp, .js_dialog, "
        "[class*=\"modal\"], [class*=\"mask\"]');"
        "[].forEach.call(masks,function(m){"
        "  out.dialogs.push((m.textContent||'').trim().slice(0,200));});"
        "// toast"
        "var toasts=document.querySelectorAll('.weui-desktop-toast, .toast, [class*=\"tips\"]');"
        "[].forEach.call(toasts,function(t){"
        "  out.toasts.push((t.textContent||'').trim().slice(0,200));});"
        "// 红字报错"
        "var errs=document.querySelectorAll('.frm_msg, .error, .weui-desktop-form__error, .warn');"
        "[].forEach.call(errs,function(e){"
        "  out.errs.push((e.textContent||'').trim().slice(0,200));});"
        "// 保存按钮还在不在"
        "var btns=[].slice.call(document.querySelectorAll('button,a')).filter(function(b){"
        "return (b.textContent||'').trim().indexOf('保存为草稿')>=0;});"
        "out.saveBtn=btns.length?{text:btns[0].textContent.trim(),"
        "disabled:btns[0].disabled,className:btns[0].className.slice(0,80)}:null;"
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
            "for(var j=0;j<4&&p;j++){"
            "if((p.textContent||'').indexOf('赞赏')>=0){found=true;break;}"
            "p=p.parentElement;}"
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


def publish_one(cdp, c, art, dry=False, no=0):
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
        gen = _gen_cover(cdp, c, no, cover_path)
        if isinstance(gen, str) and gen.endswith(".png") and os.path.isfile(gen):
            # 两步走：① 把图插进正文（进微信图床/素材库）；② 再从正文选它当封面。
            # 少了 ② 草稿箱列表项就是灰块 —— 服务端 cover 字段为空。
            up = _upload_cover(cdp, gen)
            print("  封面入素材库: %s" % up)
            # 设封面：优先官方 JS API（mp_editor_change_cover），失败退 UI 路线。
            # 为什么 API 优先（2026-10-16 批量实测）：UI 路线在 headless 下
            # 裁剪弹窗的「确认」按钮点不着（CONFIRM_ERR:NO_IN_DIALOG），
            # 11 篇全中；API 路线 11/11 成功、单篇约 2 秒（UI 要轮询 ~3 分钟）。
            # ⚠ oriImgUrl 必须 split('?')[0]（带 query 会被 uploadimg2cdn 拒，
            # 返 ret:-201）—— _set_cover_via_jsapi 内部已处理。
            iu = _first_body_img_url(cdp)
            sc = _set_cover_via_jsapi(cdp, iu) if iu.startswith("http") else "BAD_URL"
            print("  设封面(API): %s (img=...%s)" % (sc, (iu or "")[-36:]))
            if not str(sc).startswith("SUC"):
                sc = _set_cover_from_body(cdp)
                print("  设封面(UI 兜底): %s" % sc)
            if not str(sc).startswith(("OK", "SKIP", "SUC")):
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
                        # API 优先（headless 下 UI 弹窗「确认」点不着），
                        # 失败退 UI 路线。同 publish_one 的口径。
                        iu = _first_body_img_url(cdp)
                        sc = (_set_cover_via_jsapi(cdp, iu)
                              if iu.startswith("http") else "BAD_URL")
                        print("  补封面(API): %s (img=...%s)"
                              % (sc, (iu or "")[-36:]))
                        if not str(sc).startswith("SUC"):
                            sc = _set_cover_from_body(cdp)
                            print("  补封面(UI 兜底): %s" % sc)
                        if not str(sc).startswith(("OK", "SKIP", "SUC")):
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
    issues = issue_registry(all_cases)
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
            no = issues.get(c["id"], 0)
            try:
                if resume_id:
                    if tok is None:
                        _tid0, tok = _connect_mp(cdp)
                        if not tok:
                            raise RuntimeError("拿不到 token，确认调试窗口里已登录")
                    status = finish_one(cdp, c, resume_id, tok)
                    appmsgid = resume_id
                else:
                    st = publish_one(cdp, c, art, dry=args.dry, no=no)
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
            # ⚠⚠ 出口非零，别让「全篇失败」以 rc=0 溜出去。
            #   单篇异常在上面被 `except: continue` 吞掉（那是 2026-09-21 为了
            #   不让一次断连拖垮 16 篇加的），但**进程退出码仍是 0**
            #   ⇒ 上层 `multiplatform` runner 判`r.ok=True`
            #   ⇒ publish_manifest 写 `draft_saved`
            #   ⇒ **草稿压根没进后台却记成成功**。
            #   2026-10-05 实测踩中：harperai 报「URL 未出现 appmsgid」
            #   被吞 → 台账 draft_saved → 队列清空、再也不会重试。
            #   上层只能靠 stdout 猜，这里必须给出机器可读的出口码。
            if left:
                raise SystemExit(3)


# ======================================================================
# 补封面（历史草稿）
# ======================================================================
def _connect_mp(cdp, debug=False):
    """连到一个已登录的 mp 页面，返回 (tid, token)。优先复用已开着的 tab。

    ⚠ **「请重新登录」不等于掉登录**（2026-10-01 实测）：
    拿不到 token 时页面只显示「请重新登录」，但**重新导航一次通常就能换到
    有效 token**（实测第 10 次、约 30 秒才换到）。所以：
      · 复用已有 tab 拿不到 → **重新导航**再等，不要立刻报错；
      · 重导航后仍拿不到，才可能真是登录态过期（那要人工扫码）。
    原来只原地轮询 25 秒，恰好卡在换 token 需要的 30 秒之前 → 批量直接退出。
    """
    tids = []
    for t in cdp.list_targets():
        u = t.get("url") or ""
        if t.get("type") == "page" and "mp.weixin.qq.com" in u:
            if cdp.connect_target(t["id"]):
                tok = _get_token(cdp, debug=debug)
                if tok and tok != "NO_TOKEN":
                    return t["id"], tok
                # 这个 tab 拿不到 token，先记下来当重导航的落点，
                # **别每个轮次都new_target** —— 实测失败重试会堆出6 个
                # 一模一样的首页 tab，既干扰后续复用也把页面搞乱。
                tids.append(t["id"])
    tid = tids[-1] if tids else cdp.new_target(MP_ENTRY)["id"]
    cdp.connect_target(tid)
    login_attempted = False
    for _ in range(25):
        time.sleep(1)
        tok = _get_token(cdp, debug=debug)
        if tok and tok != "NO_TOKEN":
            return tid, tok
        # 会话 cookie 还在、只是后台要一次手动确认换新 session 时，首页会显示
        # 「登录」按钮（#jumpUrl）。headless 下沙箱可能在 ~25s 就回收进程，若傻等
        # 满 25s 再点按钮，恢复还没完成就被杀 ⇒ 整批连不上。所以几秒拿不到 token
        # 就立刻尝试点登录按钮，争取在回收窗内恢复（实测点一下即进，无需扫码）。
        if _ >= 4 and not login_attempted:
            login_attempted = True
            if _try_click_login(cdp):
                for _ in range(15):
                    time.sleep(2)
                    tok = _get_token(cdp, debug=debug)
                    if tok and tok != "NO_TOKEN":
                        print("  [login] 点「登录」按钮后直接拿到 token=%s（无需扫码）"
                              % tok)
                        return tid, tok
    # 原地等不出来 → 先试「点一下登录按钮」（2026-10-02 用户实测发现）。
    # 公众号会话 cookie 过期时，后台首页**并不显示二维码**，而是显示一个
    # 「登录」按钮 —— cookie 其实还在，只是后台要你点一下才发新session。
    # 点一下就能进，**不需要扫码**。这比让用户掏手机扫码省事得多。
    if _try_click_login(cdp):
        for _ in range(15):
            time.sleep(2)
            tok = _get_token(cdp, debug=debug)
            if tok and tok != "NO_TOKEN":
                print("  [login] 点「登录」按钮后直接拿到 token=%s（无需扫码）" % tok)
                return tid, tok

    # 点按钮也不行 → 重导航到首页（换 token 的正规途径），再等 60 秒。
    print("  [login] 原地拿不到 token，重新导航到首页再等…")
    for attempt in range(10):
        cdp.eval("location.href='https://mp.weixin.qq.com/cgi-bin/home'",
                 refresh_context=True)
        for _ in range(3):
            time.sleep(2)
            tok = _get_token(cdp, debug=debug)
            if tok and tok != "NO_TOKEN":
                print("  [login] 第%d 轮重导航后拿到 token=%s" % (attempt + 1, tok))
                return tid, tok
    return tid, None


def _try_click_login(cdp):
    """后台首页若显示「登录」按钮就跳过去。返回 True 表示点了。

    ⚠ 这不是 hack：会话 cookie 还在，只是后台要一次手动确认才换新 session。
    2026-10-02 实测：跳一次即进，**不需要扫码**。

    ⚠⚠ 真实 DOM（2026-10-02 实抓，**不要凭猜改选择器**）：
    ```
    <a id="jumpUrl" href="/cgi-bin/loginpage?url=%2Fcgi-bin%2Fhome">登录</a>
    ```
    它是 `<a id="jumpUrl">`，**没有任何 login 相关的 class**。
    早先按 `.login_btn / [class*="login"]` 找全部落空⇒ 按钮没被点，
    于是每次都走到「拿不到 token」，看起来像「必须扫码」。
    ⇒ 正解：**优先 `#jumpUrl`**，其次按可见文本「登录」兜底。
    """
    try:
        res = cdp.eval("""
          (function(){
            // 1) 首选：官方那个固定 id
            var el = document.getElementById('jumpUrl');
            if (el) { el.click(); return 'clicked:#jumpUrl'; }
            // 2) 兜底：找可见的、文本恰好是「登录」的链接/按钮
            var all = document.querySelectorAll('a, button, div[role="button"]');
            for (var i = 0; i < all.length; i++) {
              var e = all[i];
              var t = (e.innerText || e.textContent || '').trim();
              if (/^登\\s*录$/.test(t)) {
                e.click();
                return 'clicked:text';
              }
            }
            return null;
          })()
        """)
    except Exception:
        return False
    if res and str(res).startswith("clicked"):
        print("  [login] 页面显示「登录」按钮（会话还在），已自动跳转到登录页")
        return True
    return False

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


def verify_refreshed(cdp, tok, expect, fresh_seconds=1800, count=60, since=None):
    """按**篇号**回读远端草稿，核对 refresh 到底有没有真写进去。

    为什么必须有这个（2026-10-01）
    ------------------------------
    `保存: OK` 的判据只是「保存后 URL 出现了 appmsgid=」，
    那是**编辑器自己跳转**的信号，**不保证正文落库**。
    历史教训：整轮 37 条全报「成功」，用户看到的还是旧排版。

    ⚠ **键必须是篇号，不能是标题** —— 按标题反查时 `0/N` 表示
    「没匹配上远端」，很容易被误读成「远端是空的、所以不用改」。

    可用判据（按可信度从高到低）：
      1. `update_time` 距当前秒数 —— 刚刷的必然是新鲜值（最省事）；
      2. `title` 与本地 `make_wechat_title(c)` 一致；
      3. 列表接口不返回正文长度，正文要另开编辑页读（贵，逐条才做）。

    ⚠ **`fresh_seconds` 别设太紧**（2026-10-01 踩过）。
    批量跑 40 分钟刷 37 条时，最后一条距「批次开始」已 40 分钟，
    阈值卡在边界会把刚刷的误判成 STALE。判断「这条刷了没有」用
    `since`（批次开始时间戳）比用「距今 N 秒」准得多。

    参数
    ----
    expect : {appmsgid: {"title": str, "refreshed_at": str}}
        台账里带 `appmsgid` 的条目。
    since : float | None
        批次开始时间戳。给了就优先用它（`update_time >= since` 即算刷过），
        避免长批次里「距今 N秒」误判。
    返回：{appmsgid: (状态, 说明)}，状态 ∈
        OK / STALE / TITLE_MISMATCH / MISSING
    """
    now = int(time.time())
    remote = {}
    for d in _draft_list(cdp, tok, count=count):
        aid = d.get("appmsgid")
        if aid:
            remote[str(aid)] = d

    out = {}
    for aid, exp in (expect or {}).items():
        aid = str(aid)
        d = remote.get(aid)
        if not d:
            out[aid] = ("MISSING", "远端草稿列表里没有这个篇号")
            continue
        ut = int(d.get("update_time") or 0)
        age = now - ut
        want = (exp.get("title") or "").strip()
        got = (d.get("title") or "").strip()
        if want and got != want:
            out[aid] = ("TITLE_MISMATCH",
                        "远端标题=%r，本地期望=%r（正文大概率也没写进去）"
                        % (got[:30], want[:30]))
            continue
        if since:
            if ut >= int(since):
                out[aid] = ("OK", "update_time 距今 %ds（≥批次起点），标题一致" % age)
            else:
                out[aid] = ("STALE",
                            "update_time 距今 %ds，早于批次起点 %ds，这条没被刷到"
                            % (age, now - int(since)))
        elif age <= fresh_seconds:
            out[aid] = ("OK", "update_time 距今 %ds，标题一致" % age)
        else:
            out[aid] = ("STALE",
                        "update_time 距今 %ds（>%ds），这条可能没被刷到"
                        % (age, fresh_seconds))
    return out




class DraftEditorError(RuntimeError):
    """草稿编辑页**没打开**（错误页 / 限流 / 记录损坏）。

    必须和「打开了但正文没写进去」区分开：错误页上根本没有编辑器 DOM，
    此时 `_set_body` 会返回 NO_EL、`保存` 的点击也必然落空 —— 一路报
    NO_EL / NO_FILE_INPUT / NO_COVER_BTN 全是**后果**，不是选择器失效
    （2026-10-01 排查 `系统错误(320003)` 时踩过）。
    """


def _editor_ready(cdp):
    try:
        return bool(cdp.eval("!!document.querySelector('#title')",
                             refresh_context=True))
    except Exception:
        return False


def _open_draft_editor(cdp, appmsgid, tok, timeout=30, strict=False):
    """新开 tab 打开**已存在**的草稿编辑页，返回 tid。

    strict=True 时，timeout 内编辑器没就绪就抛 `DraftEditorError`
    （并关掉 tab）。批量场景必须 strict：否则错误页会被当成正常编辑器
    继续走，于是每条都假成功、又白等 timeout。
    """
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit"
           "&action=edit&reprint_confirm=0&type=77&appmsgid=%s&token=%s"
           "&lang=zh_CN" % (appmsgid, tok))
    tid = cdp.new_target(url)["id"]
    cdp.connect_target(tid)
    for _ in range(timeout):
        time.sleep(1)
        if _editor_ready(cdp):
            return tid
    if strict:
        try:
            cdp.close_target(tid)
        except Exception:
            pass
        raise DraftEditorError("编辑器未就绪（appmsgid=%s）" % appmsgid)
    return tid


def cmd_fix_cover(args):
    """给已有草稿补封面；`--force` 时**重设**（封面已有也换掉）。

    缘由（2026-09-21）：早期 publish_one 只把封面图插进正文，误以为
    「默认首图会自动成为封面」—— 服务端 `cover` 字段其实一直是空的，
    于是草稿箱列表项全是灰块。本命令按**服务端状态**逐个补齐。

    `--force` 的场景（2026-09-24）：篇号算错那批草稿的封面画的是「第 1 篇」，
    正文改对了但封面字段还指着旧图。默认模式（只看空的）挑不到它，所以要能
    显式重设 —— 前提是正文首图已经是想要的那张（`refresh --cover` 会把新
    封面图插到正文最前，正是为了给这一步备料）。
    """
    cdp = CDP(args.port)
    _tid0, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
    drafts = _draft_list(cdp, tok)
    if not drafts:
        raise SystemExit("✗ 拉不到草稿列表（登录态失效或接口变了）")
    only = getattr(args, "appmsgid", None)
    force = bool(getattr(args, "force", False))
    todo = [d for d in drafts
            if (not only or str(d.get("appmsgid")) == str(only))
            and (force or not str(d.get("cover") or "").strip())]
    print("草稿共 %d 篇%s，%s %d 篇"
          % (len(drafts), "（已按 appmsgid 过滤）" if only else "",
             "重设封面" if force else "缺封面", len(todo)))
    if args.limit:
        todo = todo[:args.limit]
    fixed = skipped = 0
    for d in todo:
        aid = d.get("appmsgid")
        print("\n→ %s — %s" % (aid, (d.get("title") or "")[:34]))
        tid = _open_draft_editor(cdp, aid, tok)
        try:
            r = _set_cover_from_body(cdp, force=force)
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


def _del_api_url(tok):
    """删除接口 URL（2026-10-16 抓包实证，推翻了 9-25 的错误记忆）。"""
    return ("https://mp.weixin.qq.com/cgi-bin/operate_appmsg"
            "?sub=del&t=ajax-response&lang=zh_CN&f=json&ajax=1&token=%s" % tok)


def _del_api_body(aid, tok, fp):
    """删除接口表单体。fp 可空（实测服务端校验宽松，跨篇延用也过）。"""
    return ("AppMsgId=%s&fingerprint=%s&token=%s&lang=zh_CN&f=json&ajax=1"
            % (aid, fp or "", tok))


# 页面里 fingerprint 的候选位置（抓包：删除 body 带 fingerprint=<32位hex>）
_FP_JS = """(function(){
  var c=['fingerprint','__fingerprint','fp','wxFingerprint'];
  for(var i=0;i<c.length;i++){
    try{ if(window[c[i]]) return String(window[c[i]]); }catch(e){}
  }
  var el=document.querySelector('[data-fingerprint]');
  if(el) return el.getAttribute('data-fingerprint')||'';
  var m=(document.documentElement.innerHTML||'').match(
    /fingerprint["'\\s:=]+["']?([0-9a-f]{16,40})/);
  return m?m[1]:'';
})()"""


def _delete_draft_api(cdp, tok, appmsgid, timeout=15):
    r"""走 operate_appmsg?sub=del 删一篇草稿（API，不点 UI）。

    2026-10-16 抓包实测（**推翻 2026-09-25 的错误记忆**）：
    真实接口不是 `cgi-bin/appmsg?action=del`（那个返 200009 not found 是
    **猜错了接口身**，不是「没权限」）——
        POST /cgi-bin/operate_appmsg?sub=del&t=ajax-response&lang=zh_CN&f=json&ajax=1
        body: AppMsgId=<id>&fingerprint=<fp>&token=<tok>&lang=zh_CN&f=json&ajax=1
        成功: base_resp.ret == 0
    fingerprint 是页面生成的 32 位 hex；实测跨篇延用同一份也过 ret:0
    （服务端校验宽松），读不到就空着试。本函数只负责 API 一击，
    失败由上层退 `_delete_draft_ui`（UI 点击删），双保险。

    ⚠⚠ **ret:0 不等于删掉了**：2026-10-16 实测对**不存在的 appmsgid**
    也返 base_resp.ret=0（服务端不校验 id 是否存在，删除幂等宽容）。
    ⇒ 上层必须删后拉 `_draft_list` 核对才算数，绝不能只信本函数返 OK。

    返回 "OK" / "RET:<code>(<msg>)" / "RAW:<...>" / "ERR:<...>" / "TIMEOUT"。
    """
    # 确保当前页面在 mp 同域（fetch 才能带 cookie）
    try:
        cdp.send("Page.navigate", {"url":
                 "https://mp.weixin.qq.com/cgi-bin/appmsg?begin=0&count=10"
                 "&type=77&action=list&lang=zh_CN&token=%s" % tok})
        time.sleep(2.5)
    except Exception:
        pass
    fp = ""
    try:
        fp = str(cdp.eval(_FP_JS, refresh_context=True) or "").strip()
    except Exception:
        pass
    expr = """(function(){
      window.__dd='PENDING';
      fetch(%s,{method:'POST',credentials:'include',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:%s})
        .then(function(r){return r.text();})
        .then(function(t){window.__dd=t.slice(0,400);})
        .catch(function(e){window.__dd='FETCH_ERR:'+e.message;});
    })()""" % (json.dumps(_del_api_url(tok)), json.dumps(_del_api_body(appmsgid, tok, fp)))
    try:
        cdp.eval(expr, refresh_context=True)
    except Exception as e:
        return "ERR:%s" % e
    for _ in range(timeout * 2):
        time.sleep(0.5)
        st = str(cdp.eval("window.__dd") or "")
        if st and st != "PENDING":
            try:
                j = json.loads(st)
                br = j.get("base_resp") or {}
                if br.get("ret") == 0:
                    return "OK"
                return "RET:%s(%s)" % (br.get("ret"), br.get("err_msg"))
            except Exception:
                return "RAW:" + st[:120]
    return "TIMEOUT"


def _delete_draft_ui(cdp, tok, appmsgid):
    r"""在 mp 草稿箱列表页，按 appmsgid 精确点掉一篇草稿（触发器→确认框→确认）。

    ⚠ 历史纠错（2026-10-16）：本文档 2026-09-25 曾写「`appmsg?action=del`
    的 XHR 返 200009 ⇒ 个人订阅号没有删除 API 权限 ⇒ 只能走 UI」——
    **结论错了**。抓包证明真实接口是 `operate_appmsg?sub=del`（见
    `_delete_draft_api`），200009 只是猜错接口身。现在 UI 只是 API 失败
    后的兜底，不再是唯一路径。

    卡片结构是 `data-appid="<appmsgid>"` 容器里，删除触发器
    在 tooltip 文本为「删除」的 `.weui-desktop-popover__wrp` 内的 `<a>` 图标；
    点开后弹确认框，确认按钮是卡片内 `button.weui-desktop-btn_primary` 文本「删除」。
    逐个精确匹配，不会波及别的草稿。

    ⚠⚠ **草稿箱是真分页的**（2026-10-02 实测踩坑）：
    列表 URL 的 `begin`/`count` 生效，每页只有 `count=10` 条DOM 卡片。
    所以草稿数 > 10 时，**目标很可能不在第 1 页 DOM 里**，
    只 `querySelector('[data-appid=...]')` 必然 `NO_CARD`。
    ⚠ 而 `NO_CARD` 这个返回值本身**会误导人**：删除其实完全可行，
    只是得先翻到目标所在页。别据此判定「删不了」。
    ⇒ 这里按 `begin=0,10,20,...` 逐页找，找到再删。
    判据是接口列表 `_draft_list()` 的条数（它走XHR，能一次拿全）。
    """
    cdp.send("Page.enable")

    def _list_url(begin):
        """⚠ 必须当函数生成，别提前 `%` 绑定 —— 提前绑定会把后续占位符吃掉。"""
        return ("https://mp.weixin.qq.com/cgi-bin/appmsg?begin=%d&count=%d"
                "&type=77&action=list&lang=zh_CN&token=%s"
                % (begin, DRAFT_PAGE, tok))

    def _on_page(begin):
        cdp.send("Page.navigate", {"url": _list_url(begin)})
        # ⚠⚠ **一次 eval 同时拿「已渲染数 + 是否命中」**（2026-10-02 优化）：
        # 原版每次循环发 2 个 eval、轮询 25 轮，4 页实测跑了 19 分钟。
        # CDP 的 eval 是同步往返，轮询次数必须压到个位数级。
        # 判据不能是「任意 [data-appid] 出现」—— 卡片是**逐张插入**的，
        # 第一张落地就break 的话，排在末尾的目标还没渲染 ⇒ 后面必然 NO_CARD。
        js = ("(function(){var c=document.querySelectorAll('[data-appid]');"
              "return JSON.stringify([c.length,"
              "!!document.querySelector('[data-appid=\"%s\"]')]);})()"
              % appmsgid)
        rendered = 0
        for attempt in range(10):
            time.sleep(1.5 if attempt else 2.0)
            try:
                r = json.loads(cdp.eval(js, refresh_context=True) or "[0,false]")
                rendered = r[0]
                if r[1]:
                    return True
                # 渲染数稳定且非 0 = 这页已加载完，目标确实不在
                if rendered:
                    break
            except Exception:
                pass
        print("    [翻页] begin=%d 渲染=%d，目标不在本页" % (begin, rendered),
              flush=True)
        return False

    # 总页数上界用接口列表算（拿不到就保守翻 30 页 = 300 篇）
    try:
        total = len(_draft_list(cdp, tok, count=200))
    except Exception:
        total = 0
    pages = max(1, (total + DRAFT_PAGE - 1) // DRAFT_PAGE) if total else 30

    found = False
    for i in range(pages):
        if _on_page(i * DRAFT_PAGE):
            found = True
            break
    if not found:
        return "NO_CARD(scanned=%d pages)" % pages
    print("    [命中] 在第 %d 页找到 appmsgid=%s" % (i + 1, appmsgid), flush=True)
    trig = cdp.eval("""(function(){
      var card=document.querySelector('[data-appid="%s"]');
      if(!card) return 'NO_CARD';
      var wrps=[].slice.call(card.querySelectorAll('.weui-desktop-popover__wrp'));
      var w=null;
      for(var i=0;i<wrps.length;i++){
        var tip=wrps[i].querySelector('.weui-desktop-tooltip__down-center');
        if(tip && (tip.textContent||'').trim()==='删除'){w=wrps[i];break;}
      }
      if(!w) return 'NO_TRIGGER';
      var a=w.querySelector('a')||w.querySelector('button');
      if(!a) return 'NO_TRIGGER_EL';
      a.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
      return 'TRIGGERED';
    })()""" % appmsgid, refresh_context=True)
    if trig != "TRIGGERED":
        return trig
    time.sleep(2)
    conf = cdp.eval("""(function(){
      var card=document.querySelector('[data-appid="%s"]');
      if(!card) return 'CARD_GONE';
      var btns=[].slice.call(card.querySelectorAll('button.weui-desktop-btn_primary'));
      for(var i=0;i<btns.length;i++){
        if((btns[i].textContent||'').trim()==='删除' && btns[i].offsetParent!==null){
          btns[i].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
          return 'CONFIRMED';
        }
      }
      return 'NO_CONFIRM';
    })()""" % appmsgid, refresh_context=True)
    if conf != "CONFIRMED":
        return conf
    time.sleep(3)
    gone = cdp.eval(
        "(function(){return !document.querySelector('[data-appid=\"%s\"]');})()"
        % appmsgid, refresh_context=True)
    return "GONE" if gone else "STILL_THERE"


def cmd_delete(args):
    """把指定草稿从公众号草稿箱删除（按 appmsgid，--dry 先确认再真删）。

    用法：python wechat_publish.py delete --appmsgid 100000263,100000271
    安全：先拉草稿列表按 appmsgid 反查标题打印确认，再逐个删——
    先走 API（`_delete_draft_api`，2026-10-16 抓包实证可用），
    失败退 UI 点删（`_delete_draft_ui`）；删完再拉一次列表核对是否真的消失。
    误传的 id 不会波及别的草稿（逐个精确匹配 data-appid）。
    """
    cdp = CDP(args.port)
    _tid0, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")
    drafts = _draft_list(cdp, tok, count=200)
    by_id = {str(d.get("appmsgid")): d for d in drafts}
    targets = [x.strip() for x in (args.appmsgid or "").split(",") if x.strip()]
    if not targets:
        raise SystemExit("✗ 用 --appmsgid 指定要删的草稿 id（逗号分隔）")
    if args.dry:
        print("草稿列表共 %d 篇；命中待删：" % len(drafts))
        for aid in targets:
            d = by_id.get(aid)
            print("  %s -> %s" % (aid, (d or {}).get("title", "(列表里没找到，可能已删)")))
        return
    print("草稿列表共 %d 篇" % len(drafts))

    def _still_there(aid):
        return aid in {str(d.get("appmsgid"))
                       for d in _draft_list(cdp, tok, count=200)}

    for aid in targets:
        d = by_id.get(aid)
        title = (d or {}).get("title", "(列表里没找到)")
        print("\n→ 准备删除 appmsgid=%s  标题=%s" % (aid, title))
        if not d:
            print("  [skip] 列表里没有这个 id，跳过（可能已删或 id 写错）")
            continue
        st = _delete_draft_api(cdp, tok, aid)
        print("  删除(API): %s" % st)
        # ⚠⚠ API 返 OK 不等于真删掉：2026-10-16 实测对**不存在的 id**
        # 也返 base_resp.ret=0（服务端不校验 id 是否存在）⇒ OK 后必须
        # 拉列表核对，还在箱里就转 UI 兜底。唯一可信判据是核对。
        if st == "OK" and _still_there(aid):
            print("  [warn] API 返 OK 但核对还在箱里，转 UI 点删")
            st = _delete_draft_ui(cdp, tok, aid)
            print("  删除(UI): %s" % st)
        elif st != "OK":
            print("  [api 失败，转 UI 点删]")
            st = _delete_draft_ui(cdp, tok, aid)
            print("  删除(UI): %s" % st)
        print("  核对: %s" % ("已消失 ✅" if not _still_there(aid) else "还在 ❌"))
    after = {str(d.get("appmsgid")) for d in _draft_list(cdp, tok, count=200)}
    gone = [aid for aid in targets if aid not in after]
    print("\n核对：%d/%d 已确认从草稿箱消失：%s"
          % (len(gone), len(targets), "、".join(gone) or "无"))


def cmd_sync(args):
    """远端 ⇄ 本地对账。**只读远端**，默认也只读本地；`--apply` 才回写台账。

    为什么需要（2026-10-05 用户提出）：公众号是订阅号，`freepublish` 无权限，
    群发只能你在后台手动点 ⇒ 本地台账永远不知道文章已经发表。
    结果就是「本地记 draft、远端已发表」，下次 refresh 会去刷一条
    **已经不在草稿箱**的文章，白等几分钟还容易误判成掉登录。

    用法：
        python wechat_publish.py sync              # 只读报告（默认）
        python wechat_publish.py sync --apply      # 确认差异后回写台账
        python wechat_publish.py sync --case nitra # 只看某几条

    ⚠⚠ 三条硬约束：
      1. **默认只读**。改台账必须显式 `--apply`，且 `--apply` 前会自动备份
         `wechat_published.json.bak`。
      2. **UNKNOWN ≠ 已删**。任一侧接口没读成功时，只标 UNKNOWN，
         绝不下「远端已删除」结论 —— 那会建议删掉其实还在的条目。
      3. **不碰远端任何写操作**。已发表列表读不到就报读不到，不猜。
    """
    import wechat_sync as ws
    cases = load_cases()
    cases_by_id = {c["id"]: c for c in cases}
    only = set(x.strip() for x in (getattr(args, "case", "") or "").split(",")
               if x.strip())

    local = ws.load_local()
    if only:
        local = {k: v for k, v in local.items() if k in only}
        print("只看 %d 条：%s" % (len(local), "、".join(sorted(local))))

    def _tmatch(cid, c):
        try:
            return make_wechat_title(c)
        except Exception:
            return (local.get(cid) or {}).get("title")

    cdp = CDP(args.port)
    _tid0, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")

    diffs, local, meta = ws.run_sync(cdp, tok, local=local,
                                    cases_by_id=cases_by_id,
                                    title_matcher=_tmatch)
    print(ws.render_report(diffs, len(meta["remote_draft"]),
                           len(meta["remote_pub"]), len(local),
                           draft_ok=meta["draft_ok"], pub_ok=meta["pub_ok"]))
    if not meta["draft_ok"] or not meta["pub_ok"]:
        print("\n[!] 有接口没读到，结论不完整。修好接口再跑一次，"
              "别拿这次的 UNKNOWN 当删除依据。")
    if not args.apply:
        fix = [d for d in diffs if d[0] == "FIX"]
        if fix:
            print("\n这是只读报告，未改任何文件。确认无误后加 --apply 回写。")
        return
    n_pub, n_missing = ws.apply_fixes(diffs, local)
    print("\n已回写 data/wechat_published.json："
          "status→published %d 条 · 标missing %d 条（备份见 .bak）"
          % (n_pub, n_missing))


# ======================================================================
# 「发表」自动化（2026-10-19 首次实现）
# ======================================================================
# 这条链路此前是人工的：`publish_one` 只把稿子存进草稿箱，「发表」那一下
# 由人去后台点。订阅号没有 freepublish API 权限（`probe_wechat_openapi.py`
# 有完整记录：未认证个人订阅号一律 48001），所以只能走 UI。
#
# 2026-10-19 用 `scripts/probe_wechat_send.py` 实测出的完整链路
# （所有选择器都是抓来的，不是猜的）：
#   ① 顶栏 #js_send「发表」→ 弹出 `.new_mass_send_dialog`
#      表单：群发通知(开关，默认开) / 分组通知(仅群发时出现) / 定时发表(开关)
#      按钮：[发表][取消]
#   ② 点「发表」→ 第二层确认弹窗（文案随开关状态变）：
#      群发开：「已开启群发通知，内容将展示在用户的公众号列表…」
#      群发关：「未开启群发通知，内容将展示在公众号主页…」
#      按钮：[继续发表][取消]
#   ③ 点「继续发表」→ 页面先跑一串 check（masssend?for_check=1、
#      check_same_material、check_ad、check_music…），全过才会弹
#   ④ 「微信验证」扫码框（容器 `.dialog_wrp`，老版弹窗组件）：
#      「扫码后，请联系管理员进行验证」⇒ **管理员/运营者微信扫码**。
#      扫完发布自己继续。
#   ⑤ 成功后：草稿从 type=77 列表消失；已发表列表
#      （`appmsgpublish?sub=list`）多一条，且 **appmsg_id 是新的**
#      （草稿 100000546 → 发表后 2247484064；群发后 id 必变）。
#
# 两种模式的分界（2026-10-19 实测**修正**过一次，别再搞反）：
#   · 群发通知 ON  = **群发**：推送粉丝、占订阅号每日 1 次通知额度
#   · 群发通知 OFF = **发布**：不推送、不占额度
#   **两种都要管理员扫码** —— 曾据「探针里发布链路没弹二维码」断定发布
#   不扫码，那是错的：探针拦掉了 check 请求的 send()，流程根本没走到
#   扫码那一步。扫码是物理动作，`send` 能自动化的是它之前的所有步骤
#   （开草稿、设开关、过两层确认）和之后的所有步骤（校验落库、记账）。

SEND_DIALOG_SEL = ".new_mass_send_dialog"


def _send_dialog_text(cdp):
    """发表弹窗的全文；没打开返回 None。

    ⚠ 判可见要看里面那层 `.weui-desktop-dialog__wrp` 的 display：
    关掉的弹窗只是 wrp 被藏起来，外层容器还在 DOM 里（实测）。
    """
    js = (
        "(function(){"
        "var d=document.querySelector('%s');"
        "if(!d)return null;"
        "var w=d.querySelector('.weui-desktop-dialog__wrp');"
        "if(w&&getComputedStyle(w).display==='none')return null;"
        "return d.innerText||'';"
        "})()" % SEND_DIALOG_SEL)
    raw = cdp.eval(js, refresh_context=True)
    return raw if isinstance(raw, str) and raw.strip() else None


def _notify_switch_state(cdp):
    """「群发通知」开关状态：True=群发 / False=发布 / None=弹窗没开。"""
    js = (
        "(function(){"
        "var d=document.querySelector('%s');"
        "if(!d)return null;"
        "var bs=d.querySelectorAll('input[type=checkbox]');"
        "for(var i=0;i<bs.length;i++){"
        "  var row=bs[i].closest('.weui-desktop-form__control-group')"
        "          ||bs[i].parentElement;"
        "  var lab=row.querySelector('.weui-desktop-form__label');"
        "  if(lab&&(lab.innerText||'').indexOf('群发通知')>=0)"
        "    return bs[i].checked;"
        "} return null;})()" % SEND_DIALOG_SEL)
    return cdp.eval(js, refresh_context=True)


def _set_notify(cdp, on):
    """把开关设成目标态。返回 (改前, 改后)；弹窗没开返回 (None, None)。"""
    before = _notify_switch_state(cdp)
    if before is None:
        return None, None
    if before != on:
        js = (
            "(function(){"
            "var d=document.querySelector('%s');"
            "var bs=d.querySelectorAll('input[type=checkbox]');"
            "for(var i=0;i<bs.length;i++){"
            "  var row=bs[i].closest('.weui-desktop-form__control-group')"
            "          ||bs[i].parentElement;"
            "  var lab=row.querySelector('.weui-desktop-form__label');"
            "  if(lab&&(lab.innerText||'').indexOf('群发通知')>=0){"
            "    var box=row.querySelector('.weui-desktop-switch__box')||bs[i];"
            "    box.click(); return true;"
            "  }} return false;})()" % SEND_DIALOG_SEL)
        cdp.eval(js, refresh_context=True)
        time.sleep(1)
    after = _notify_switch_state(cdp)
    return before, after


def _wait_send_dialog(cdp, timeout=15):
    """等发表弹窗出现，返回是否等到。"""
    for _ in range(timeout):
        if _send_dialog_text(cdp) is not None:
            return True
        time.sleep(1)
    return False


def _scan_dialog_text(cdp):
    """「微信验证」扫码框的文本；没出现返回 None。

    ⚠⚠ 容器是 `.dialog_wrp`（老版弹窗组件），**不是** `.new_mass_send_dialog`，
    也不是 `.weui-desktop-dialog__wrp` —— 2026-10-19 实测这三种容器在同一个
    页面同时存在。只认发表那两个容器会**完全错过**扫码框，现象是
    「点了继续发表，然后什么都没发生」，轮询 180s 后报 timeout。

    ⚠⚠ 另一个被实测推翻的判断：**发布模式也要扫码**。
    曾据「探针里发布链路没弹二维码」断定发布不扫码 —— 那是错的：探针把
    check 系列请求的 send() 拦掉了，流程根本没走到扫码那一步。
    真实流程是 check 全过（can_mass_send=1）之后才弹「微信验证」。
    群发与发布的区别只有「推不推送粉丝、占不占每日额度」，**扫码都要**。
    """
    js = (
        "(function(){"
        "var ds=document.querySelectorAll('.dialog_wrp');"
        "for(var i=0;i<ds.length;i++){"
        "  if(getComputedStyle(ds[i]).display==='none')continue;"
        "  var t=(ds[i].innerText||'').trim();"
        "  if(t.indexOf('扫码')>=0)return t.slice(0,200);"
        "} return null;})()")
    raw = cdp.eval(js, refresh_context=True)
    return raw if isinstance(raw, str) and raw.strip() else None


def _list_from_home(cdp, home_tid, url):
    """在 home tab 里发同步 XHR 拉列表，返回解析后的 JSON 或 None。

    ⚠ 为什么不在编辑器 tab 里拉（2026-10-19 实测）：编辑器页的同步 XHR
    返回空字符串（页面把同步请求拦了），`wechat_sync._xhr_json` 解析失败
    返回 None ⇒ 草稿箱被读成空的 ⇒ 「草稿已消失」永远为假 ⇒
    发表结果永远判不出来。home tab 里同样的请求正常返回。
    """
    if home_tid:
        try:
            cdp.connect_target(home_tid)
        except Exception:
            pass
    import wechat_sync as ws
    return ws._xhr_json(cdp, url)


def evaluate_send(before_ids, after_items, draft_gone, want_title, norm=None):
    """判定「发表」有没有落库。**纯函数**，所有输入都可造假，便于回归。

    before_ids   发表前已发表列表里的 appmsg_id 集合
    after_items  发表后的条目 [{appmsg_id,title,publish_type}]
    draft_gone   草稿箱里那条草稿是否已消失
    want_title   期望标题（归一化后精确比对）
    norm         标题归一化函数（默认 wechat_sync.norm_title）

    返回 (status, new_id, reason)：
      ok          已发表列表出现新记录（标题对得上，或草稿已消失）
      unverified  有新记录但草稿还在 / 草稿没了但列表没读到
                  —— 两种都**不敢**记 published，交给人看
      pending     两边都还没有变化，继续等

    为什么两条证据都要看：只信「草稿消失」不够（列表接口被限流时会
    误判）；只信「有新记录」也不够（万一是别人刚发的一篇）。两条同时
    成立才落账 —— 和 wechat_sync 「远端是唯一真相源」是同一条纪律。
    """
    if norm is None:
        import wechat_sync as ws
        norm = ws.norm_title
    before = {str(x) for x in (before_ids or set())}
    new = [it for it in (after_items or [])
           if str(it.get("appmsg_id") or it.get("appmsgid") or "") not in before]
    want = norm(want_title or "")
    if new:
        hit = [it for it in new if want and norm(it.get("title") or "") == want]
        if hit:
            return ("ok", str(hit[0].get("appmsg_id") or ""),
                    "已发表列表出现新记录，标题一致")
        if draft_gone:
            return ("ok", str(new[0].get("appmsg_id") or ""),
                    "草稿已消失且出现新记录（标题未比上，以新记录为准）")
        return ("unverified", str(new[0].get("appmsg_id") or ""),
                "已发表列表有新记录，但草稿还在 —— 可能没发对，不敢记 published")
    if draft_gone:
        return ("unverified", "",
                "草稿消失了但已发表列表没读到新记录（列表可能被限流/截断）")
    return ("pending", "", "两边都还没有变化")


def _open_draft_editor_for_send(cdp, appmsgid, tok, timeout=30):
    """打开发表用的草稿编辑页，并把「打不开」的**两种原因**分开返回。

    为什么不能直接用 `_open_draft_editor(strict=True)`（2026-10-19 实测）：
    它超时就把 tab 关掉抛异常，于是「此草稿已发表」和「此草稿已被删除」
    两种处理方式完全不同的情况，共享同一句「编辑器未就绪」。
      · 已发表 ⇒ **后台已经发过**（台账该修 status，绝不该重发）
      · 已删除 ⇒ 台账记的是历史残留（该跑 sync 对账）
    页面原文（用户截图 + 页面 HTML 双重确认）：
      「此草稿已被删除」+「此草稿已发表，可前往发表记录查看」

    返回 (state, tid, page_html)：
      ready / published / deleted / timeout
    """
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit"
           "&action=edit&reprint_confirm=0&type=77&appmsgid=%s&token=%s"
           "&lang=zh_CN" % (appmsgid, tok))
    tid = cdp.new_target(url)["id"]
    cdp.connect_target(tid)
    text = ""
    for _ in range(timeout):
        time.sleep(1)
        if _editor_ready(cdp):
            return "ready", tid, ""
        try:
            text = str(cdp.eval(
                "document.documentElement?document.documentElement.outerHTML:''",
                refresh_context=True) or "")
        except Exception:
            text = ""
        # ⚠ 顺序不能反：「已发表」那句里也含「已被删除」四个字
        #   （原文是「此草稿已被删除／此草稿已发表，可前往发表记录查看」），
        #   先判删除会把已发表的误判成删除。
        if "已发表" in text:
            return "published", tid, text
        if "已被删除" in text:
            return "deleted", tid, text
    return "timeout", tid, text


def send_one(cdp, c, appmsgid, tok, home_tid=None, dry=False, notify=False,
             timeout=300):
    """把一条草稿发表出去。返回 {status, appmsgid(新), publish_type, reason}。

    完整链路见本节开头的注释。status：
      ok / unverified / timeout / dry / already_published
      （error 由调用方接异常）
    already_published：编辑器页面显示「此草稿已发表」⇒ 后台发过，
      **不重发**，返回该状态让调用方修台账（重发会造出重复文章）。
    """
    import wechat_sync as ws
    title = make_wechat_title(c)
    mode = "群发" if notify else "发布"
    print("\n→ %s — 《%s》[%s模式] 草稿 appmsgid=%s"
          % (c["id"], title, mode, appmsgid))
    if dry:
        print("  [dry] 不打开浏览器。将走：顶栏发表 → %s两层确认 → "
              "check → 弹「微信验证」二维码（等你扫）→ 轮询落库"
              % ("群发通知(开) + " if notify else "群发通知(关) + "))
        return {"status": "dry", "appmsgid": "", "publish_type": mode}

    # 发表前快照：已发表列表的 appmsg_id 集合（判定「有没有多发一条」的基线）
    def _pub_list():
        out, ok = [], False
        for begin in range(0, ws.PAGE * ws.MAX_PAGES, ws.PAGE):
            url = ("https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list"
                   "&search_field=null&begin=%d&count=%d&token=%s"
                   "&lang=zh_CN&f=json&ajax=1" % (begin, ws.PAGE, tok))
            d = _list_from_home(cdp, home_tid, url)
            if not isinstance(d, dict):
                if begin == 0:
                    return [], False
                break
            pp = d.get("publish_page")
            if isinstance(pp, str):
                try:
                    pp = json.loads(pp)
                except ValueError:
                    return [], False
            if not isinstance(pp, dict):
                if begin == 0:
                    return [], False
                break
            lst = pp.get("publish_list") or []
            if not lst:
                break
            for it in lst:
                out.extend(ws._flatten_publish_item(it))
            if len(lst) < ws.PAGE:
                break
        return out, True

    def _draft_ids():
        out, ok = {}, False
        for begin in range(0, ws.PAGE * ws.MAX_PAGES, ws.PAGE):
            url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_ex"
                   "&type=77&sub=all&begin=%d&count=%d&token=%s&lang=zh_CN"
                   "&f=json&ajax=1&random=0.7" % (begin, ws.PAGE, tok))
            d = _list_from_home(cdp, home_tid, url)
            if not isinstance(d, dict):
                if begin == 0:
                    return {}, False
                break
            items = d.get("app_msg_list") or []
            if not items:
                break
            for it in items:
                aid = it.get("appmsgid")
                if aid is not None and str(aid) != "":
                    out[str(aid)] = it
            if len(items) < ws.PAGE:
                break
        return out, True

    before, bok = _pub_list()
    if not bok:
        raise RuntimeError("发表前读不到已发表列表，没有基线就没法判定发没发成功")
    before_ids = {str(it.get("appmsg_id") or "") for it in before}

    state, tid, page = _open_draft_editor_for_send(cdp, appmsgid, tok)
    if state == "published":
        # 后台已经发表过这篇（用户在后台点过「发表」）。**绝不重发** ——
        # 重发会制造一篇重复文章。正确动作：把台账修成 published。
        # 关联新 id：先试标题精确相等；标题被后台改过就退产品名包含，
        # 并在 note 里标明是模糊关联（2026-10-19 visualizee-ai 实测：
        # 后台把标题从「…效果图渲染工具」改成了「…一次转型跳到 $8.6K」，
        # 精确匹配必败，所以这条退路不是摆设）。
        try:
            cdp.close_target(tid)
        except Exception:
            pass
        after, aok = _pub_list()
        new_id, how = "", ""
        if aok:
            want = ws.norm_title(title)
            for it in after:
                if ws.norm_title(it.get("title") or "") == want:
                    new_id, how = str(it.get("appmsg_id") or ""), "标题精确"
                    break
            if not new_id:
                name = ws.norm_title(c.get("name") or c["id"])
                if len(name) >= 4:
                    for it in after:
                        if name and name in ws.norm_title(it.get("title") or ""):
                            new_id = str(it.get("appmsg_id") or "")
                            how = "产品名包含（标题被后台改过，需人工确认）"
                            break
        print("  ⚠ 后台已发表过这篇（编辑器页面：「此草稿已发表，"
              "可前往发表记录查看」）。不重发，改为修正台账。")
        return {"status": "already_published", "appmsgid": new_id,
                "publish_type": "unknown", "reason":
                    "远端编辑器显示此草稿已发表%s" % (
                        "；关联到已发表记录 %s（%s）" % (new_id, how) if new_id
                        else "；已发表列表里没关联上（标题可能被改过）")}
    if state != "ready":
        try:
            cdp.close_target(tid)
        except Exception:
            pass
        raise RuntimeError(
            "打不开草稿编辑页（appmsgid=%s，页面状态=%s）。最常见的原因："
            "这条草稿在远端已被删掉，台账里记的是历史残留 —— 先跑 "
            "`wechat_publish.py sync` 对账" % (appmsgid, state))
    try:
        # ① 顶栏「发表」
        r = _click_by_text(cdp, "发表")
        if not (isinstance(r, str) and r.startswith("CLICKED")):
            raise RuntimeError("点顶栏「发表」失败：%s" % r)
        if not _wait_send_dialog(cdp):
            raise RuntimeError("点了发表但弹窗没出来（%ds）" % 15)
        # ② 设开关 + 点弹窗里的「发表」
        b, a = _set_notify(cdp, on=notify)
        print("  群发通知开关：%s → %s" % (b, a))
        if a != notify:
            raise RuntimeError("开关没设成目标态（要 %s，实际 %s）" % (notify, a))
        r = _click_in_dialog(cdp, "发表")
        if not (isinstance(r, str) and r.startswith("CLICKED")):
            raise RuntimeError("点弹窗「发表」失败：%s" % r)
        print("  已确认：%s" % r)

        # ③ 轮询等落库。途中只可能有这两种弹窗：
        #   · 第二层确认（.new_mass_send_dialog 换文案）⇒ 点它的主按钮；
        #   · 「微信验证」扫码框（.dialog_wrp）⇒ **打印提示然后等**。
        #     扫码是物理动作，自动化只能到这儿；扫完发布会自己继续。
        deadline = time.time() + timeout
        clicks, hinted, last_nag = 0, False, 0.0
        last = "还没动静"
        while time.time() < deadline:
            time.sleep(4)
            scan = _scan_dialog_text(cdp)
            if scan:
                now = time.time()
                if not hinted:
                    hinted = True
                    print("  [等待扫码] 页面弹出「微信验证」：%s"
                          % scan.replace("\n", " "))
                    print("  ⇒ 请用**管理员/运营者微信**扫页面上的二维码；"
                          "非管理员微信扫了还要管理员那边通过。"
                          "扫完发布自动继续，我在这儿等着。")
                if now - last_nag > 60:              # 每分钟提醒一次还剩多久
                    last_nag = now
                    print("    …还在等扫码（还剩 %ds）"
                          % int(deadline - now))
                continue
            if _send_dialog_text(cdp) is not None and clicks < 2:
                for txt in ("继续发表", "发表", "确认"):
                    rr = _click_in_dialog(cdp, txt)
                    if isinstance(rr, str) and rr.startswith("CLICKED"):
                        clicks += 1
                        print("  确认链第 %d 环：%s" % (clicks, rr))
                        break
                continue
            drafts, dok = _draft_ids()
            after, aok = _pub_list()
            if not aok:
                last = "已发表列表读取失败，继续重试"
                continue
            draft_gone = dok and str(appmsgid) not in drafts
            status, new_id, reason = evaluate_send(
                before_ids, after, draft_gone, title)
            last = reason
            if status in ("ok", "unverified"):
                return {"status": status, "appmsgid": new_id,
                        "publish_type": mode, "reason": reason}
        return {"status": "timeout", "appmsgid": "", "publish_type": mode,
                "reason": "%ds 内没判定出来：%s%s"
                          % (timeout, last,
                             "（一直在等扫码？）" if hinted else "")}
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


def build_send_todo(published, issues=None, limit=0):
    """`send --all` 的待办清单：[(cid, appmsgid)]，按**篇号**升序。

    ⚠ 按篇号排，不是账本插入顺序（2026-10-21 修）：账本是只追加的，任何
    后补/重存的草稿都会被追加到尾部，「续发下一条」的编号顺序口径就悄悄
    失效（当前数据碰巧升序是运气，不是保证）。没有篇号的沉到最后，彼此
    之间保持账本原序。

    `issues` 可注入篇号表（测试用）；默认读注册表。
    """
    if issues is None:
        issues = load_issue_numbers()
    pool = []
    for i, (cid, rec) in enumerate(published.items()):
        rec = rec or {}
        if rec.get("status") not in ("draft", "partial"):
            continue                     # 已发表的不重复发
        if rec.get("missing"):
            continue                     # 远端都没了，发了也白发
        if not rec.get("appmsgid"):
            continue
        pool.append((issues.get(cid), i, cid, str(rec["appmsgid"])))
    pool.sort(key=lambda t: (t[0] is None,
                             t[0] if t[0] is not None else 0, t[1]))
    todo = [(cid, aid) for _n, _i, cid, aid in pool]
    return todo[:limit] if limit else todo


def cmd_send(args):
    """把草稿箱里的稿子**发表**出去。**两种模式都要管理员扫码**。

    模式区别只在推送与额度（2026-10-19 实测）：
      · 默认「发布」：不推送粉丝、不占订阅号每日 1 次群发额度
      · `--notify`「群发」：推送粉丝、占额度
    扫码逃不掉：点完两层确认、check 全过之后，页面弹「微信验证」二维码，
    必须管理员/运营者微信扫。`send` 自动化的是扫码之前（开草稿、设开关、
    过确认）和之后（校验落库、记账）的所有步骤；扫码时它会停下来提示并等待。

    用法：
        python wechat_publish.py send --case bustem      # 发表单条（发布模式）
        python wechat_publish.py send --appmsgid 100000546
        python wechat_publish.py send --all --limit 3    # 台账里 draft 的前 3 条
        python wechat_publish.py send --case bustem --notify   # 群发（也需扫码）
        python wechat_publish.py send --case bustem --dry
    """
    all_cases = load_cases()
    by_id = {c["id"]: c for c in all_cases}
    published = load_published()

    todo = []                       # [(cid, draft_appmsgid)]
    if args.appmsgid:
        by_aid = {}
        for cid, rec in published.items():
            if rec.get("appmsgid"):
                by_aid[str(rec["appmsgid"])] = cid
        for aid in (x.strip() for x in args.appmsgid.split(",") if x.strip()):
            cid = by_aid.get(aid)
            if not cid:
                print("✗ 台账里找不到 appmsgid=%s 的记录" % aid)
                return
            todo.append((cid, aid))
    elif args.case:
        for cid in (x.strip() for x in args.case.split(",") if x.strip()):
            rec = published.get(cid) or {}
            if not rec.get("appmsgid"):
                print("✗ %s 没有 appmsgid（从没存过草稿？先跑 publish）" % cid)
                return
            todo.append((cid, str(rec["appmsgid"])))
    else:
        todo = build_send_todo(published, limit=args.limit)

    if not todo:
        print("没有可发表的草稿（台账里没有 status=draft 且带 appmsgid 的记录）")
        return
    mode = "群发（推送粉丝、占每日 1 次额度）" if args.notify \
        else "发布（不推送、不占每日群发额度）"
    print("模式：%s（两者都需管理员扫码，见下）" % mode)
    print("待发表 %d 条：%s" % (len(todo), "、".join(c for c, _ in todo)))
    if args.dry:
        for cid, aid in todo:
            t = make_wechat_title(by_id[cid]) if cid in by_id else "?"
            print("  [dry] %s ← 草稿 %s《%s》" % (cid, aid, t))
        return

    cdp = CDP(args.port)
    home_tid, tok = _connect_mp(cdp)
    if not tok:
        raise SystemExit("✗ 拿不到 token：确认调试窗口里 mp.weixin.qq.com 已登录")

    ok_n = 0
    sent_n = already_n = 0
    for cid, aid in todo:
        c = by_id.get(cid)
        if not c:
            print("  [skip] %s 不在 cases.json 里" % cid)
            continue
        rec = published.get(cid) or {}
        if rec.get("status") == "partial":
            print("  [注意] %s 是 partial（封面/原创可能没做完），照样发表" % cid)
        try:
            r = send_one(cdp, c, aid, tok, home_tid=home_tid,
                         notify=args.notify, timeout=args.timeout)
        except Exception as e:
            print("  [error] %s 发表失败：%s" % (cid, e))
            continue
        if r["status"] == "already_published":
            # 后台已经发表过（用户在后台点过）。绝不重发；把台账修成
            # published —— 远端是唯一真相源，本地记着 draft 会让下次
            # refresh 去刷一条已不在草稿箱的文章（wechat_sync 模块头
            # 记着这个坑）。
            rec = published.get(cid) or {}
            rec["status"] = "published"
            rec.setdefault("published_at", time.strftime("%Y-%m-%d %H:%M"))
            rec["publish_type"] = "unknown_backend"
            rec["sent_via"] = "backend_manual"     # 不是本脚本发的
            rec["send_reason"] = r.get("reason", "")
            if r.get("appmsgid"):
                rec["published_appmsgid"] = r["appmsgid"]
            rec.setdefault("title", make_wechat_title(c))
            published[cid] = rec
            save_published(published)
            try:
                import ledger as ld
                ld.mark("wechat", cid, ld.PUBLISHED,
                        remote_id=rec.get("appmsgid") or "",
                        title=make_wechat_title(c),
                        evidence="editor_says_published",
                        note="｜后台已发表（编辑器页面确认），本地由 send 修正")
            except Exception as e:                              # noqa: BLE001
                print("    ⚠ 统一台账写入失败（不影响发布记录）：%s" % str(e)[:80])
            print("  ✓ %s 后台早已发表，台账已修正为 published"
                  "（未重发）%s" % (cid, r.get("reason", "")))
            ok_n += 1
            already_n += 1
            continue
        if r["status"] != "ok":
            print("  [warn] %s 没敢记 published：%s（%s）"
                  % (cid, r["status"], r.get("reason", "")))
            continue
        # ---- 记账：follow wechat_sync 的约定 ----
        # appmsgid **不覆盖**（那是草稿 id，refresh/fix-cover 靠它定位草稿）；
        # 发表后的新 id 记到 published_appmsgid。群发/发布后 id 必变。
        rec["status"] = "published"
        rec["published_at"] = time.strftime("%Y-%m-%d %H:%M")
        rec["publish_type"] = "masssend" if args.notify else "publish"
        rec["published_appmsgid"] = r["appmsgid"]
        rec["sent_via"] = "send"            # 留痕：自动发表，区别于后台手点
        rec["send_reason"] = r.get("reason", "")
        rec.setdefault("title", make_wechat_title(c))
        published[cid] = rec
        save_published(published)           # 逐篇落库，中途被杀也不丢
        try:
            import ledger as ld
            ld.mark("wechat", cid, ld.PUBLISHED, remote_id=aid,
                    title=make_wechat_title(c), evidence="send_readback",
                    note="｜%s模式自动发表，新 appmsgid=%s"
                         % (rec["publish_type"], r["appmsgid"]))
        except Exception as e:                              # noqa: BLE001
            print("    ⚠ 统一台账写入失败（不影响发布记录）：%s" % str(e)[:80])
        print("  ✓ 已发表：%s（新 appmsgid=%s，%s）"
              % (cid, r["appmsgid"], r.get("reason", "")))
        ok_n += 1
        sent_n += 1
    print("\n完成：新发表 %d 条、后台早已发表（仅修台账）%d 条，共 %d/%d"
          % (sent_n, already_n, sent_n + already_n, len(todo)))
    if sent_n + already_n < len(todo):
        print("⚠ 没成功的**没有**被记成 published，重跑 send 会再试它们")


BROKEN_DRAFTS = os.path.join(ROOT, "data", "wechat_broken_drafts.json")


def load_broken():
    """读「服务端打不开的草稿」黑名单（case id -> {appmsgid, 首次发现时间}）。

    为什么要有这个：`系统错误(320003)` 有两种含义 —— 限流（暂时，重试能好）
    和**记录级损坏**（永久，这条草稿在服务端已不可访问）。后者会让 refresh
    在它上面白白耗掉两轮打开 + 退避，单条能吃掉几分钟，批量看起来就是
    「卡住不动」。交替测试确认是损坏后写进这里，之后每轮自动跳过。
    """
    try:
        with open(BROKEN_DRAFTS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_broken(d):
    tmp = BROKEN_DRAFTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BROKEN_DRAFTS)


def _parse_when(s):
    """'2026-10-01 20:04' -> unix 秒。给 --skip-refreshed-after 用。"""
    return int(time.mktime(time.strptime(s.strip(), "%Y-%m-%d %H:%M")))


def _reconcile_remote(cdp, tok, published, cases_by_id):
    """拉远端草稿箱 + 已发表列表，与本地台账三方对账。**不落盘**。

    封装 `wechat_sync.run_sync`（那边有全套判据：分页拉取、三层 JSON 解析、
    标题归一化认亲、四态判定）。返回 (diffs, meta)。

    为什么 refresh 要用它（2026-10-16 实战）：visualizee-ai 的草稿从服务端
    消失后，refresh 只报「编辑器未就绪(320003)」—— 限流、你群发过、草稿被删
    这三种成因在页面上长得一模一样。人肉排查花了半小时（拉 48 条草稿列表 +
    标题关键词 0 命中 + 探针 + 问用户），这个函数把整套排查固化成一次对账。
    """
    import wechat_sync as ws

    def _tmatch(cid, c):
        try:
            return make_wechat_title(c)
        except Exception:
            return (published.get(cid) or {}).get("title")

    return ws.run_sync(cdp, tok, local=published, cases_by_id=cases_by_id,
                       title_matcher=_tmatch)


def _verdict_for(diag, cid):
    """从对账结果里取某条 case 的本质结论。返回 (verdict, detail)。

    verdict 四种（别把后两种混成前两种）：
      PUBLISHED  草稿箱无 + 已发表列表有同标题 ⇒ **你后台群发过**，
                 台账该标 published（有双侧证据，可回写）
      MISSING    两侧接口都读成功、且都没有 ⇒ 草稿真没了（死篇号），
                 只能重建（publish --case X），重试无用
      RATE_LIMIT 草稿还在箱里、只是编辑器打不开 ⇒ 限流或记录损坏，
                 等几分钟重试
      UNKNOWN    任一侧接口没读准 ⇒ **不下任何结论**（宁可重跑也不要误判）

    ⚠ 比 `wechat_sync.classify` 更严一档：classify 的 DRAFT_GONE_BUT_PUBLISHED
    只看「已发表有同标题」，不看草稿箱读没读成；这里要求**双侧都读成功**
    才敢判 PUBLISHED（草稿箱读失败时远端为空，不能当「草稿箱无」的证据）。
    """
    if not diag:
        return "UNKNOWN", "对账没跑成（异常），重跑本命令即可"
    diffs, meta = diag
    if not (meta.get("draft_ok") and meta.get("pub_ok")):
        side = "草稿箱" if not meta.get("draft_ok") else "已发表列表"
        return "UNKNOWN", ("%s没读到 ⇒ 无法区分「真没了」和「没读到」，"
                           "不下结论" % side)
    for lvl, code, c, detail in diffs:
        if c != cid:
            continue
        if code == "DRAFT_GONE_BUT_PUBLISHED":
            return "PUBLISHED", detail
        if code == "REMOTE_MISSING":
            return "MISSING", detail
    # 没有这条的 diff ⇒ classify 认为草稿还在箱里（编辑器打不开是另一回事）
    return "RATE_LIMIT", "草稿箱里还有这条 ⇒ 限流或记录级损坏，等几分钟重试"


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
    cases_by_id = {c["id"]: c for c in cases}
    published = load_published()
    issues = issue_registry(cases)
    only = set(x.strip() for x in (args.case or "").split(",") if x.strip())
    skip_set = set(x.strip() for x in
                   (getattr(args, "exclude_case", "") or "").split(",") if x.strip())
    broken = load_broken()
    if broken and not args.dry:
        print("已跳过 %d 条服务端损坏草稿（%s；--include-broken 可强制重试）"
              % (len(broken), "、".join(sorted(broken)[:8])
                 + ("…" if len(broken) > 8 else "")))

    todo = []
    for c in cases:
        cid = c["id"]
        if only and cid not in only:
            continue
        if cid in skip_set:
            print("  [skip] %s 被 --exclude-case 排除" % cid)
            continue
        if cid in broken and not getattr(args, "include_broken", False):
            print("  [skip] %s 服务端草稿已损坏（黑名单）" % cid)
            continue
        rec = published.get(cid)
        if not rec:
            continue                      # 没发过，无需刷新
        if rec.get("status") == "published":
            # 2026-09-24：已群发的文章不在草稿箱里，refresh 刷不到（会白等几分钟
            # 然后报找不到）。发表后把 status 改成 published 就不会再排进来。
            print("  [skip] %s 已发表，不在草稿箱" % cid)
            continue
        # --skip-refreshed-after：续跑时把本轮已经刷成功的排掉。中断过的批次
        # 重新排队会从头再刷一遍，每条 1-2 分钟，白等几十分钟；refresh 本身幂等，
        # 但**没必要**。判据用 `refreshed_at`（服务端那一刻的本地时间）。
        if getattr(args, "skip_refreshed_after", ""):
            try:
                if rec.get("refreshed_at") and _parse_when(
                        rec["refreshed_at"]) >= _parse_when(args.skip_refreshed_after):
                    print("  [skip] %s 本轮已刷过（%s）" % (cid, rec["refreshed_at"]))
                    continue
            except ValueError:
                pass
        art = find_article(cid, c.get("name"))
        if not art:
            print("  [skip] %s 缺发布就绪 HTML" % cid)
            continue
        todo.append((cid, art, rec))
    if args.limit:
        todo = todo[:args.limit]

    # ---- 排序：草稿箱显示次序 = 服务端 update_time 倒序（微信无手动排序功能）
    # ⚠⚠ **默认按 cases.json 原序刷，会把草稿箱刷得更乱**（2026-10-05 用户反馈）。
    # 实测：草稿箱 30 条的 update_time 有 3 处逆序，且与排期分毫无关系 ——
    # 排期第 4 位的 shipfast 掉到第 29 位、排期第 41 位的 uplinked 排第 9。
    # 根因：`load_cases()` 返回 cases.json 的**录入顺序**，不是排期序。
    # ⇒ `--order rank` 显式按排期分排（复用 make_article 的判据，避免两处漂移）。
    # ⇒⚠ **刷新顺序与期望显示顺序相反**：最后刷的 update_time 最新、排最前。
    #    所以要让草稿箱从上到下 = 排期 1,2,3...，必须**倒序刷**
    #    （排期第 N 条先刷，第 1 条最后刷）。
    order = (getattr(args, "order", "") or "").strip().lower()
    if order in ("rank", "schedule"):
        import make_article as _MA
        by_id = {c["id"]: c for c in cases}
        todo = [t for t in todo if t[0] in by_id]
        # 降序= 分高在前。倒着刷 ⇒ 最后刷的是分最高的 ⇒ 它排最前。
        todo.sort(key=lambda t: _MA.rank_key(by_id[t[0]]), reverse=True)
        print("排序：按排期分倒序刷（最后刷的排最前）")
    elif order:
        print("[!] 未知 --order %r（可选：rank）" % order)
        return
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
    editor_errors = []          # 编辑器压根没打开的条目，收尾时统一报
    consecutive_errors = 0
    # 编辑器打不开时的服务端对账缓存：整批只跑一次（拉两侧列表不便宜），
    # 之后每条失败都从这份缓存里取结论。None=还没跑；False=跑失败过。
    diag = None
    ledger_fixes = []           # 对账判定「你群发过」的 case，收尾回写台账
    for idx, (cid, art, rec) in enumerate(todo):
        aid = rec.get("appmsgid") or by_title.get((rec.get("title") or "").strip())
        if not aid:
            print("\n→ %-18s [skip] 查不到 appmsgid（标题没在草稿列表里匹配上）" % cid)
            skip_n += 1
            continue
        ex = extract_article(art)
        body = pm_safe_body(ex["body_html"])
        c = next((x for x in cases if x["id"] == cid), None)
        print("\n→ %-18s appmsgid=%s 正文 %d 字" % (cid, aid, len(strip_tags(body))))
        # 上一条连续失败 → 先退避，别在限流窗口里继续加压（320003 多为高频
        # 打开编辑器触发）。退避后重试一次：限流是**暂时**的，记录级损坏才是
        # 永久的 —— 重试一次就能把这两类分开，不必事后逐条复现。
        if consecutive_errors >= 2:
            wait = min(20 + 10 * (consecutive_errors - 2), 90)
            print("  [backoff] 连续失败 %d 次，等待 %ds 再试" % (consecutive_errors, wait))
            time.sleep(wait)
        tid = None
        for attempt in (1, 2):
            try:
                tid = _open_draft_editor(cdp, aid, tok, strict=True)
                break
            except DraftEditorError as e:
                print("  [editor] 第%d次打开失败: %s" % (attempt, e))
                if attempt == 1:
                    time.sleep(20)
        if tid is None:
            # 编辑器没打开 = 正文一个字都没改。必须单列出来：这是**服务端问题**，
            # 不是「脚本写错了」，混进 fail_n 会让人一直查选择器。
            print("  [skip] 编辑器未就绪，本条未改动")
            # 2026-10-16：别再只报一句「未就绪」—— 限流 / 你群发过 / 草稿被删
            # 这三种成因页面上长得一模一样（都是 320003 错误页），人肉排查要
            # 半小时还得问用户。这里自动对账服务端，一次分清（见 _verdict_for）。
            # ⚠ 编辑器 tab 已被 strict=True 关掉，先连回一个活着的 mp 页面，
            #   对账的同步 XHR 才能带上登录 cookie。
            if diag is None:
                print("  [diag] 对账服务端（草稿箱 + 已发表列表，整批只跑一次）…")
                try:
                    _tdiag, tdiag_tok = _connect_mp(cdp)
                    diag = _reconcile_remote(cdp, tdiag_tok, published,
                                             cases_by_id)
                except Exception as _e:                              # noqa: BLE001
                    print("  [diag] 对账本身失败: %s: %s"
                          % (type(_e).__name__, _e))
                    diag = False
            verdict, detail = _verdict_for(diag, cid)
            _vlabel = {"PUBLISHED": "你群发过它",
                       "MISSING": "草稿已不存在（死篇号）",
                       "RATE_LIMIT": "草稿还在箱里（限流/记录损坏）",
                       "UNKNOWN": "证据不足，不下结论"}[verdict]
            print("  [diag] %s ⇒ %s" % (_vlabel, detail))
            if verdict == "PUBLISHED":
                ledger_fixes.append(cid)
            elif verdict == "MISSING":
                print("        重建：python wechat_publish.py publish --case %s"
                      % cid)
            editor_errors.append("%s[%s]" % (cid, verdict))
            consecutive_errors += 1
            fail_n += 1
            continue
        try:
            # 标题/摘要/作者一起同步：草稿箱列表上显示的是**标题字段**，只刷正文
            # 不刷标题，列表里就还挂着旧文案（2026-09-24 用户截图里那条正是：
            # 正文改了，标题还写着「客服类产品（具体定位未获取）」）。
            if c is not None:
                t = make_wechat_title(c)
                rt = _set_editor(cdp, 0, t, mode="text") or ""
                print("  标题: %s [%s]" % (t[:34], rt.split("(")[0]))
                _fill_input(cdp, "#author", "万物解释者")
                summ = ex["summary"] or c.get("one_liner", "")
                if summ:
                    _fill_input(cdp, "#js_description", summ[:120])
                time.sleep(1)
            r = _set_body(cdp, body)
            print("  set_body: %s" % r)
            time.sleep(2)
            # --cover：正文替换会把正文里那张封面图一并抹掉，封面字段却还指着
            # 旧图。所以要重画一张、重新上传（同时进素材库），再强制换封面。
            # 顺序不能反：必须先把图插进正文，`_set_cover_from_body` 才有首图可选。
            if getattr(args, "cover", False) and c is not None:
                cover_path = os.path.join(ROOT, "tmp", "covers", "%s.png" % cid)
                try:
                    gen = _gen_cover(cdp, c, issues.get(cid, 0), cover_path)
                    if isinstance(gen, str) and gen.endswith(".png"):
                        print("  封面重画: %s" % gen)
                        up = _upload_cover(cdp, gen)
                        print("  封面入素材库: %s" % up)
                        time.sleep(2)
                        if getattr(args, "cover_api", False):
                            # 官方 JS API 路线：_upload_cover 只回传 URL 前 50
                            # 字符，这里从 DOM 取全量，去 query 后调
                            # mp_editor_change_cover（实测 2 秒、保存后确认落库）
                            iu = _first_body_img_url(cdp)
                            sc = _set_cover_via_jsapi(cdp, iu)
                            print("  换封面(API): %s (img=...%s)"
                                  % (sc, (iu or "")[-36:]))
                        else:
                            sc = _set_cover_from_body(cdp, force=True)
                            print("  换封面: %s" % sc)
                    else:
                        print("  [warn] 封面重画失败: %s" % gen)
                except Exception as e:                              # noqa: BLE001
                    print("  [warn] 封面处理异常: %s" % e)
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
                consecutive_errors = 0
                # 顺手把 appmsgid 补进记录：以后不必再靠标题反查
                rec["appmsgid"] = str(aid)
                # 标题也一起写回 —— 记录里存的是**服务端现在是什么**。
                # 只刷正文不刷标题字段的话，这份记录会一直挂着旧文案
                # （2026-09-24：voklit 记录里还写着「客服类产品（具体定位未获取）」，
                #   而线上草稿早就改成云通信了，对账时以为没刷成功）。
                if c is not None:
                    rec["title"] = make_wechat_title(c)
                rec["refreshed_at"] = time.strftime("%Y-%m-%d %H:%M")
                save_published(published)
            else:
                fail_n += 1
                consecutive_errors += 1
        except Exception as e:
            print("  [error] %s" % e)
            fail_n += 1
            consecutive_errors += 1
        finally:
            if tid:
                try:
                    cdp.close_target(tid)
                except Exception:
                    pass
            # 条与条之间留呼吸时间：连续开编辑器是限流的主要触发条件。
            if idx + 1 < len(todo):
                time.sleep(6)
    print("\n完成：刷新 %d 篇，失败 %d 篇，跳过 %d 篇" % (ok_n, fail_n, skip_n))
    if editor_errors:
        print("编辑器未就绪（本条未改动）：%s" % "、".join(editor_errors))
    if ledger_fixes:
        # 有双侧证据才回写：草稿箱没有 + 已发表列表有同标题（群发后服务端会
        # 换发新 appmsg_id，所以已发表侧只能靠标题认亲——判据与 sync 完全一致）。
        # 复用 wechat_sync.apply_fixes：**唯一的台账写入口**，自动先备份 .bak，
        # 且只改 status，不动 title/appmsgid（那些可能是你在后台改过的）。
        # ⚠ 只回写本批涉及的 case：--case 过滤时不该顺手改别条。
        import wechat_sync as ws
        fix_diffs = [d for d in (diag[0] if diag else [])
                     if d[0] == "FIX" and d[1] == "DRAFT_GONE_BUT_PUBLISHED"
                     and d[2] in set(ledger_fixes)]
        n_pub, _n_missing = ws.apply_fixes(fix_diffs, published)
        if n_pub:
            save_published(published)
            print("\n[ledger] %d 条已改判 published（你后台群发过：草稿箱已无 + "
                  "已发表列表有同标题；备份 .bak 已留）：%s"
                  % (n_pub, "、".join(ledger_fixes)))
            print("        下次 refresh 不会再排它们（已发表不在草稿箱）。")
        else:
            print("\n[ledger] 判定「已群发」但回写 0 条 —— 反常，请人工核对：%s"
                  % "、".join(ledger_fixes))
    if editor_errors:
        print("\n提示：全量对账（只读报告）跑 `python wechat_publish.py sync`；"
              "`sync --apply` 可随时按远端真相修正台账。")


# 本进程自动拉起的 Chrome（退出时清理，避免孤儿进程堆积）
_AUTOSTART_PROC = None


def _kill_autostart_chrome(*_a):
    """退出时收掉**本进程**拉起的 Chrome。

    2026-10-16：此前 Popen 起的 Chrome 从不清收 —— 脚本正常退出或被杀后
    Chrome 变孤儿，多轮重跑堆积几十个 chrome.exe，内存压力反过来让新任务
    更早被回收（恶性循环）。用户当场发现「退出为什么不杀 chrome 进程」。
    只杀**自己起的那个**（记 pid），不动用户手动开的 Chrome。
    """
    global _AUTOSTART_PROC
    p = _AUTOSTART_PROC
    if p is None:
        return
    _AUTOSTART_PROC = None
    try:
        if p.poll() is None:
            # Windows：taskkill /T 连渲染/GPU 子进程一起收（只 terminate 主进程
            # 会留下一堆子进程继续占内存）
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
    except Exception:                                          # noqa: BLE001
        try:
            p.kill()
        except Exception:                                      # noqa: BLE001
            pass


atexit.register(_kill_autostart_chrome)


# ======================================================================
# CLI
# ======================================================================
def _autostart_chrome_if_needed(port=CDP_PORT, profile=None):
    """CDP 连不上时自动拉起 Chrome（带 `--no-sandbox`）。

    ⚠ 本机 Chrome 不带 `--no-sandbox` 会在 2 秒内自杀（exit code 3 =
    Chromium `RESULT_CODE_KILLED_BAD_MESSAGE`），DevTools 端口根本不开。
    所以这里统一代劳，不再要求用户先手工启动 Chrome。

    ⚠⚠ **`profile` 必须按平台给对**（2026-10-02 实测，本机登录态是分裂的）：
        - 公众号 → `%LOCALAPPDATA%\\Google\\ChromeCDP`（9222）
        - 头条 / B站 / 小红书 → `%USERPROFILE%\\chrome-debug-profile`（9223）
      拿错 profile 的症状**极具误导性**：CDP 连得上、页面也能开，
      但被重定向到登录页 ⇒ 看着像「掉登录了」，实际是**开错浏览器**。
      ⇒ 头条/B站/小红书 传 `profile=L.PLAT_PROFILE, port=L.PLAT_PORT`。

    返回 True 表示 CDP 可用（已有实例，或已自动拉起）。
    """
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:%d/json/version" % port
    try:
        op.open(url, timeout=2).read()
        return True                      # 已有实例在跑
    except Exception:
        pass

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import chrome_cdp_launch as L
    L.clear_proxy()
    profile = os.path.expandvars(profile or L.PROFILE)
    chrome, label = L.pick_chrome()
    if chrome is None:
        return False
    L.remove_lock(profile)
    headless = not str(os.environ.get("CHROME_HEADED", "")).lower() \
        in ("1", "true", "yes")
    global _AUTOSTART_PROC
    proc = subprocess.Popen(L.build_args(chrome, profile, port=port),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    _AUTOSTART_PROC = proc          # 记下来，退出时收掉（见 _kill_autostart_chrome）
    if not L.wait_cdp(proc, port=port):
        raise SystemExit(
            "✗ Chrome 启动失败（exit=%s）。\n"
            "  若为 exit 3，多半漏了 --no-sandbox。" % proc.poll())
    print("[autostart] Chrome 已启动（%s, --no-sandbox, %s）port=%d profile=%s"
          % (label, "headless" if headless else "headed", port, profile))
    return True


def main():
    ap = argparse.ArgumentParser(description="公众号拆解自动发布管线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="生成缺的发布就绪 HTML").add_argument(
        "--case", help="只重建这一条（避免全量重写，用于刚发过稿的场景）")
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
    pf.add_argument("--force", action="store_true",
                    help="封面已有也重设（用于换封面，如篇号画错的那批）")
    pr = sub.add_parser("refresh", help="把本地重生成过的正文就地灌回已有草稿")
    pr.add_argument("--port", type=int, default=CDP_PORT)
    pr.add_argument("--case", help="只刷指定 case id（逗号分隔）")
    pr.add_argument("--exclude-case", default="",
                    help="排除指定 case id（逗号分隔），临时绕开服务端打不开的草稿")
    pr.add_argument("--include-broken", action="store_true",
                    help="连黑名单里的损坏草稿也强制重试（确认服务端已恢复时用）")
    pr.add_argument("--skip-refreshed-after", default="",
                    help="跳过 refreshed_at 晚于该时间的条目，如 2026-10-01-20:04，"
                         "用于中断后从断点续跑")
    pr.add_argument("--limit", type=int, default=0, help="只刷前 N 篇")
    pr.add_argument("--order", default="",
                    help="刷新顺序。默认 cases.json 原序；"
                         "rank=按排期分倒序刷（这样草稿箱显示次序=排期序）。"
                         "⚠ 草稿箱次序由服务端 update_time 决定，微信无手动排序，"
                         "只能靠刷新顺序控序")
    pr.add_argument("--dry", action="store_true", help="只打印计划不打开浏览器")
    pr.add_argument("--cover", action="store_true",
                    help="连封面一起重画（篇号/主题变了必须带这个）")
    pr.add_argument("--cover-api", action="store_true",
                    help="封面改用官方 JS API mp_editor_change_cover 设置"
                         "（绕开 UI 裁剪弹窗：单篇省约 3 分钟，且 headless 可用；"
                         "需同时带 --cover）")
    pdel = sub.add_parser("delete", help="按 appmsgid 删除草稿箱里的草稿")
    pdel.add_argument("--port", type=int, default=CDP_PORT)
    pdel.add_argument("--appmsgid", help="要删的草稿 id（逗号分隔）")
    pdel.add_argument("--dry", action="store_true", help="只打印待删标题不真删")
    ps = sub.add_parser(
        "sync",
        help="远端⇄本地对账：读草稿箱 + 已发表列表，修正本地台账的 status")
    ps.add_argument("--port", type=int, default=CDP_PORT)
    ps.add_argument("--case", help="只对账指定 case id（逗号分隔）")
    ps.add_argument("--apply", action="store_true",
                    help="**回写** data/wechat_published.json（默认只读报告）。"
                         "自动先备份 .bak。WARN/UNKNOWN 一律不动")
    psend = sub.add_parser(
        "send",
        help="把草稿箱里的稿子**发表**出去（默认发布模式：不推送、不占额度；"
             "--notify 走群发。两种模式都要管理员扫码，脚本扫码前全自动、"
             "扫码时等待、扫码后自动校验落账）")
    psend.add_argument("--port", type=int, default=CDP_PORT)
    psend.add_argument("--case", help="只发表指定 case id（逗号分隔）")
    psend.add_argument("--appmsgid", help="只发表指定草稿 appmsgid（逗号分隔）")
    psend.add_argument("--all", action="store_true",
                       help="台账里 status=draft/partial 且带 appmsgid 的全发")
    psend.add_argument("--limit", type=int, default=0, help="只发前 N 条")
    psend.add_argument("--notify", action="store_true",
                       help="群发模式：推送粉丝、占每日 1 次额度。不加就是发布模式。"
                            "⚠ 两种模式都要管理员扫码")
    psend.add_argument("--timeout", type=int, default=300,
                       help="单条等待落库的秒数。扫码是人工动作，默认给 300s；"
                            "扫码框出现后会每分钟提醒剩余时间")
    psend.add_argument("--dry", action="store_true",
                       help="只打印将发表哪些，不打开浏览器")
    args = ap.parse_args()

    # 127.0.0.1 必须绕开系统代理，否则 websocket 连 Chrome 调试端口会被掐（WinError 10053）
    for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(_k, None)
    ensure_no_proxy_for_localhost()  # 幂等，import 时已跑过，这里再保一次

    # 所有需要 CDP 的子命令共用这一个入口：CDP 连不上就自动拉起 Chrome。
    # ⚠ 本机 Chrome 不带 `--no-sandbox` 会在 2 秒内自杀（exit code 3 =
    # Chromium RESULT_CODE_KILLED_BAD_MESSAGE），端口根本不开，故统一代劳。
    if args.cmd in ("publish", "refresh", "delete", "fix-cover", "inspect",
                    "sync", "send"):
        try:
            _autostart_chrome_if_needed()
        except SystemExit:
            raise
        except Exception as _e:
            print("[warn] 自动启动 Chrome 失败：%s: %s"
                  % (type(_e).__name__, _e))

    if args.cmd == "build":
        # 2026-10-16：原先这里写 cmd_build() 不传 args → --case 被静默忽略、
        # 每次都跑全量 build（255 条）。--case 的本意就是「只重建这一条」。
        cmd_build(args)
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
    elif args.cmd == "delete":
        cmd_delete(args)
    elif args.cmd == "sync":
        cmd_sync(args)
    elif args.cmd == "send":
        cmd_send(args)


if __name__ == "__main__":
    main()
