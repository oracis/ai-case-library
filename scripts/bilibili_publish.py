#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站专栏发布链路（试点版）。

形态确认（2026-09-29）：B站「专栏」是图文长文载体——标题 + 富文本正文 + 封面，
和今日头条图文同构（不像小红书 9 图卡片）。所以：
  · 架构复用 xhs_publish.py 的 CDP（开 9222 调试 Chrome、真实鼠标点击、跳确认框）
  · 投稿逻辑复用 toutiao_publish.py（富文本长文 + 横版封面）

内容源：out/articles/<id>.html（公众号长文，纯文字无图）→ 清洗成 B站专栏富文本。
封面：复用 out/toutiao/<id>/cover.png（横版 16:9，已生成，不另造）。
合规：B站对站外导流零容忍（二维码 / 账号 / 第三方链接 / 公众号 CTA），比小红书更严，
      全部剥光；且发布后不可编辑、每天限 5 篇 → 只存草稿、人工终审。

用法：
  python scripts/bilibili_publish.py build --all            # 全量生成 B站版稿
  python scripts/bilibili_publish.py build --case prosp     # 单条
  python scripts/bilibili_publish.py probe                  # 连 9222 dump 投稿页 DOM，校准 SEL
  python scripts/bilibili_publish.py publish --case prosp [--dry]   # 存草稿（--dry 只探入口）
  python scripts/bilibili_publish.py publish --all          # 批量存草稿箱（草稿不限 5 篇/天）
  python scripts/bilibili_publish.py cover --case prosp     # 补封面（复用头条 cover.png）
  python scripts/bilibili_publish.py cover --all            # 批量补封面
  python scripts/bilibili_publish.py cover-status           # 回读远端 37 条 banner_url 对账

SEL 已按 2026-09-29 实测填定（read-draft → 新的创作 → read-editor 编辑器）。
投稿页 DOM 随 B站改版会漂，若 publish 连续失败先重跑 `probe` 校准。
封面上传链路见 cover_one()，2026-09-29 实测跑通。
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402  复用 CDP / 案例加载
import bili_draft_api as bili_api  # noqa: E402  草稿箱接口层（读/写/删）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "bili")
OUT_ART = os.path.join(ROOT, "out", "articles")
OUT_TT = os.path.join(ROOT, "out", "toutiao")
CASES = os.path.join(ROOT, "data", "cases.json")
# ⚠ 端口必须与 profile 配对：**9222 是公众号的 ChromeCDP**，
# B站的登录态在 `chrome-debug-profile` ⇒ 用 9223。
# 抢同一个端口会连上别人的浏览器，症状是「被重定向到登录页」。
PUB_PORT = int(os.environ.get("CDP_BILI_PORT", 9223))

# ---- 标题去金额（2026-10-02）-------------------------------------------
# 封面已经统一成「项目名 + 一句话介绍」，标题再挂着「月收$xxx」就自相矛盾；
# 而且金额是抓取当天的快照，站点自己会变，印在标题里等于印一个会过期的数字。
BILI_TITLE_OVERRIDES = os.path.join(ROOT, "data", "bili_title_overrides.json")
# 判据要「有货币符号」或「明确营收语境」，不能只看数字 ——
# 「B2B 团队的 AI GTM」里的 B2、「盯 5 万个企业招聘页」里的 5 万都不是金额。
TITLE_MONEY_RE = re.compile(
    r"[\$￥€£¥]"                       # 货币符号
    r"|(?:月收|年收|成交|流水|营收)"                # 营收语境词
    r"|\d[\d.,]*\s*(?:万|亿)(?![个位条件件人台次份])"    # 30万 / 1.5亿，排除量词
)


def _bili_title_overrides():
    """读 data/bili_title_overrides.json；缺失或坏JSON 一律退回空表。

    刻意不做「自动去金额」—— 剥掉金额后的残句读起来像机翻
    （「从的 AI 博客生成器，15 个月变成30 万的自动化 SEO 引擎」），
    所以标题是人工精修后落盘，这里只负责读。
    """
    try:
        with open(BILI_TITLE_OVERRIDES, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}

# 专栏投稿入口（登录后确认；可能跳创作中心新版 URL）
BILI_ARTICLE = "https://member.bilibili.com/platform/upload/text"

# ---- 选择器（2026-09-29 实测：投稿页结构） -------------------------------
# 投稿入口 /platform/upload/text/new-article → 同域 read-draft iframe 显示
# 「创作指南」，点「新的创作」(.new-creation_button) 后进 read-editor iframe
# （同域、in-process，可从主文档 context 直接操作其 contentDocument）：
#   · 标题：<textarea class="title-input__inner">
#   · 正文：<div class="tiptap ProseMirror eva3-editor" contenteditable>
#            （Tiptap/ProseMirror，与头条同构 → execCommand('insertHTML') 灌入）
#   · 存草稿：按钮文本「保存为草稿」
# 封面/分区在发布设置里，草稿不强制；试点先不填，人工核对。
SEL = {
    "new_creation": ".new-creation_button",   # read-draft iframe 内「新的创作」
    "title": ".title-input__inner",           # read-editor iframe 内 标题 textarea
    "body": ".tiptap.ProseMirror.eva3-editor",  # read-editor iframe 内 正文编辑区
    # 存草稿按钮按文本点（无稳定 id），见 _click_by_text
    "draft_text": "保存为草稿",
    "publish_text": "发布",
    # 封面（2026-09-29 实测）：「发布设置」区「自定义封面」是**开关**，
    # 默认关；打开后才渲染 .select-cover .upload-button「添加封面」
    # （要求 ≥600x336 的 .jpg/.png，本地头条封面 3840x2160 直接够用）。
    # 点它会动态创建 hidden input[type=file]（父 .select-method，
    # accept=".jpg,.jpeg,.png"），赋值后弹裁剪框「选择封面的截取位置」。
    "cover_switch": ".publish-settings input.vui_switch-input",
    "cover_upload": ".select-cover .upload-button",
}

# 必须就位的选择器
REQUIRED_SEL = ("new_creation", "title", "body", "draft_text")

# ---- 合规：B站对站外导流零容忍 ------------------------------------------
# 只拦「导流 CTA 句式」；正文里对微信/公众号/私信等平台的**事实性提及**
# （如「预约流程必须接微信小程序」「公众号的发布接口不开放」）不算导流，
# 不拦 —— 2026-09-29 逐条核过 12 处报警全是前者。
RISK_PATTERNS = [
    "万物解释者", "拆解海外", "案例库", "完整核实记录", "每天筛",
    "加微信", "微信号", "加我", "扫码", "二维码",
    "关注公众号", "公众号：", "私信我", "后台私信", "联系我",
]

# 结尾导流段关键词（整段删除）
LEADOUT_KW = ["案例库", "社群", "每天筛", "完整核实记录", "核实记录", "我每天筛"]


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def load_cases():
    if not os.path.isfile(CASES):
        return {}
    with open(CASES, encoding="utf-8") as f:
        d = json.load(f)
    # cases.json 可能是 {cases:[...]} 或 [...]
    if isinstance(d, dict):
        return d.get("cases", d)
    return d


def list_ids():
    """out/articles 下所有 <id>.html。"""
    if not os.path.isdir(OUT_ART):
        return []
    return sorted(re.sub(r"\.html$", "", f) for f in os.listdir(OUT_ART)
                 if f.endswith(".html"))


def _js(cdp, expr):
    r = cdp.eval(expr, refresh_context=True)
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except (ValueError, TypeError):
            pass
    return r


# --------------------------------------------------------------------------
# 内容转换：公众号长文 → B站专栏富文本
# --------------------------------------------------------------------------
def load_article_html(cid):
    p = os.path.join(OUT_ART, cid + ".html")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def extract_title(html):
    """从 <h1> 取标题文本。"""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def make_title(cid, html):
    """B站标题≤40 字（建议 ≤30）。

    ⚠ **2026-10-02 用户要求：标题也不许带金额**（封面已去金额，标题还带
    金额会自相矛盾）。优先级：
      1. `data/bili_title_overrides.json` —— 人工/AI 精修的无金额标题
      2. 头条 `out/toutiao/<id>/meta.json` —— **仅当它不含金额**才用
      3. 文章 h1（数据串，只能截断兜底）

    ⚠ 头条 meta.json 的标题是「月收$xxx：钩子」口径（37/39 条带金额），
    直接复用会把金额带进B站标题 —— 所以第2 级必须过金额正则。
    """
    ov = _bili_title_overrides().get(cid or "")
    if ov:
        return ov[:30]

    mp = os.path.join(OUT_TT, cid, "meta.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8") as f:
                t = (json.load(f).get("title") or "").strip()
            # 只有不含金额的标题才允许复用
            if t and not TITLE_MONEY_RE.search(t):
                return t[:30]
        except (ValueError, OSError):
            pass
    t = extract_title(html)
    if not t:
        t = cid
    # 去掉会占字数的括号数据说明，优先保留核心名
    t = re.sub(r"[（(].*?[)）]", "", t).strip()
    if len(t) > 30:
        t = t[:30]
    return t


def _heading_to_bold(body):
    """把 <h2>/<h3> 小标题转成加粗段落。

    为什么不用 h2（2026-09-29 实测，37 篇全量核对）：
      · B站编辑器**没有小标题功能**，工具栏无任何标题按钮；
      · 它的 CSS 把 h2 的浏览器默认样式清空
        （`.ProseMirror :where(p,h1..h6,…) { margin:0; padding:0;
        font-size:inherit; line-height:inherit }`）；
      · 唯一生效的 h2 规则是 `font-weight:500; margin-top:36px;
        font-size:18px` → 视觉上「18px 不加粗 + 上方空 36px + 下方 0」，
        正是用户说的「小标题没有样式，和下面内容隔了太多行」；
      · `style` 属性活不下来：insertHTML 时被 Tiptap 剥掉，灌完用 JS
        补上也会在存草稿时被序列化洗掉。
    加粗段落是 B站作者圈的实际做法：`strong` 能存活（存草稿重开后仍是
    `<strong>`），字号随正文 17px，间距由 B站统一的 24px 段落 margin 管。

    为什么要加 `▶ ` 前缀：正文里本来就有大量**句中** `**加粗**`
    （如「**实际情况**：该值是…」），它们和标题都是加粗段落，不加区分
    的话读者分不清哪个是章节。`▶` 是 B站作者圈约定的小标题标记
    （B站编辑器不支持多级标题，大家就用符号模拟层级）。
    已有序号的（`一、` `1.` `①`）保持原样 —— 序号本身就是区分。
    """
    def _one(m):
        inner = m.group(2).strip()
        if not inner:
            return ""
        if "<strong" in inner.lower():
            return "<p>%s</p>" % inner
        # 去掉内层标签后判断是否已有序号前缀
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        if re.match(r"^[0-9①-⑩一二三四五六七八九十（(【]", plain):
            return "<p><strong>%s</strong></p>" % inner
        return "<p><strong>▶ %s</strong></p>" % inner

    body = re.sub(r"<h([23])[^>]*>(.*?)</h\1>", _one, body, flags=re.S | re.I)
    return body


def _display_width(s):
    """按「终端/等宽」口径算显示宽度：中日韩全角算 2，其余算 1。

    中文在 B站正文里是等宽的（17px 中文字体），所以按这个口径补齐空格
    能让「标签列」对齐。标签全是中文短词（实测 17 种，2–5 字），假设很稳。
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in s)


# 匹配**连续一组** flex 伪表格行。必须是连续的：同一张表的行在源 HTML 里
# 是挨着的，中间不会有别的段落。
#
# ⚠ 2026-10-09 复核：这两个正则**原本是对的**，曾被误判为「跨标签吃结构 /
# 捕获组错位导致输出末尾多一个 `}`」。真实原因只是 return 语句里字面量多写了
# 一个 `}`（已修）。`.*?` 在 re.S 下虽是惰性匹配，但 `<p[^>]*>` 的 `[^>]*`
# 不会跨 `>`，而 flex 行内不含嵌套 `<p>`，所以第一个 `</p>` 就是本行的正确闭合。
# 保持原样，别再"加固"——加了 `(?!<p[ >])` 只会让正则更慢更难读，且掩盖真 bug。
_FLEX_BLOCK_RE = re.compile(
    r"(?:<p[^>]*display:\s*flex[^>]*justify-content:\s*space-between"
    r"[^>]*>.*?</p>\s*)+", re.S | re.I)
_FLEX_ROW_RE = re.compile(
    r"<p[^>]*display:\s*flex[^>]*justify-content:\s*space-between[^>]*>"
    r"(.*?)</p>", re.S | re.I)


def _flex_rows_to_kv_block(body):
    """把公众号的「flex 伪表格」转成 B站能看的键值块。

    **问题**（2026-09-30 实测，37/37 全中，每篇 11–16 行）：
      公众号模板里那些灰底圆角的「数据表格」其实不是 `<table>`，而是
      `<p style="display:flex;justify-content:space-between">` 里放两个
      `<span>`（标签 + 值）两端对齐。clean_body 剥 style 又剥裸 span 之后，
      标签和值直接粘成一团：
          `<p>官方口径$3,150 MRR</p>`  ← 用户看到的「表格样式全丢了」
      全角空格都没留一个，读起来像乱码。

    **为什么不能用真 <table>**（2026-10-09 复核，结论仍是「不能」）：
      · B站工具栏没有任何表格按钮（这次**连 class 一起扫**，
        `scripts/bili_table_probe.py`，零命中 —— 与头条不同，B站这条
        旧结论经复核**是对的**，不是「只扫可读属性」的漏判）；
      · `draft/add` 的 `para_type` 枚举只有 1/2/4/5/6/9
        （文本/图片/引用/有序/无序/标题），**结构上就没有表格类型**；
      · insertHTML 灌 `<table>` 会被整体降级成一个 `<p>`，单元格粘成一串。

    **方案**：连续一组伪表格行 → 一个 `<blockquote>`，每行 `<p>标签 ｜ 值</p>`。
      blockquote 的选择理由（实测三行样本的高度对比）：
        纯段落           135px，无视觉分组
        整组一个 blockquote  **135px**（一样高！）+ 22px 左缩进 → 整组成块 ✓
        每行单独 blockquote  159px，更高且碎
      单个 blockquote 的 margin-top 就是 24px（和 p 一样），**不会额外撑高**。
      ⚠ 别用「每行一个 blockquote」：连续多个会各自带 24px 边距，
      正是之前「每段分得太开」的成因。

    ⚠⚠ **对齐方式 2026-10-09 换掉：全角空格 → 分隔符 + 右对齐槽位**

    旧做法按 `_display_width()` 算标签宽度、补全角空格让标签列右端对齐。
    **这在 B站是错的**：B站正文是**比例字体**（`font_size:17`），
    全角空格的渲染宽度≠ 2 个半角 ≠ 一个中文字宽，而`_display_width()`
    假设「全角=2、半角=1」的等宽模型 ⇒ 标签列必然参差不齐。
    用户截图就是这个现象（`付费意愿 3/5` / `支付可达 3/5` 值列对不齐）。

    现在改成**标签右对齐到固定槽位 + 分隔符**：
        `标签` + 补空格到 SLOT 字宽 + `｜ ` + `值`
    分隔符把两列**视觉锚定**开，值列起点只由补空格数决定 ——
    即使字体宽度不完全理想，肉眼也能看到整齐的一列「｜」。
    而且分隔符不依赖标签列宽度，标签长短都不影响值列对齐。

    槽位宽度取组内最长标签（上限 12，避免超长标签把行撑太宽）。
    全库分布（实测 41篇 / 526 行）：标签 2–5 字占 98%
    （4字 338 / 5字 78 / 2字 71 / 3字 39），值 82% 是 `x/5` 定长评分。
    """

    def _one(m):
        rows = _FLEX_ROW_RE.findall(m.group(0))
        kv = []
        for rw in rows:
            sp = re.findall(r"<span[^>]*>(.*?)</span>", rw, re.S | re.I)
            if len(sp) < 2:
                continue
            k = re.sub(r"<[^>]+>", "", sp[0]).strip()
            v = re.sub(r"<[^>]+>", "", sp[-1]).strip()
            if k or v:
                kv.append((k, v))
        if not kv:
            return ""
        lines = []
        # 分隔符：视觉锚点，把标签列和值列切开（正文里很醒目，不喧宾夺主）
        SEP = "｜"
        # 槽位 = 组内最长标签宽度（半角口径），封顶 12 防超长标签撑爆行宽
        slot = min(12, max(_display_width(k) for k, _ in kv))
        if len(kv) == 1:
            # 只有一行就别包块了（一个 blockquote 包单行没意义）
            k, v = kv[0]
            return "<p>%s ｜ <strong>%s</strong></p>" % (k, v) if v \
                else "<p>%s</p>" % k
        for k, v in kv:
            # 标签**右对齐到槽位**：补半角空格到 slot 宽。
            # ⚠ 下限必须是 0 而不是 1 —— 下限 1 会让「标签宽度已经等于 slot」
            #   的那一行（每组都至少有一行）凭空多出 1 格，
            #   ｜ 就比别的行右移一点，正是要消灭的那种参差。
            #   最短的一行照样是 slot 宽，分隔符前的那个空格保证还有间隔。
            # ⚠ 用半角空格（U+0020）不用全角 —— 全角在比例字体下宽度不定，
            #   反而让错位更明显；半角是拉丁字体的确定宽度。
            # 分隔符 ｜ 让两列视觉对齐，读者看的是「整齐的一列竖线」。
            pad = " " * max(0, slot - _display_width(k))
            lines.append("<p>%s%s ｜ <strong>%s</strong></p>"
                         % (k, pad, v) if v else "<p>%s</p>" % k)
        return "<blockquote>" + "".join(lines) + "</blockquote>"

    return _FLEX_BLOCK_RE.sub(_one, body)


def clean_body(html):
    """清洗成 B站专栏富文本片段（直接 insertHTML 进编辑器）。

    · 取 <section> 内正文
    · 删 HTML 注释
    · 删结尾站外导流段——**逐段匹配**：关键词只允许出现在单个 <p>/<blockquote>
      内部。早期版本用 `<p>.*?关键词.*?</p>` + re.S，`.*?` 会从正文第一个 <p>
      一路吞到关键词所在段，1lookup 1808 字被吃剩 241 字（2026-09-29 实测）。
    · **x** → <strong>x</strong>
    · 去 style 属性（B站用自己的样式，微信灰字 style 会显脏）
    · 剥裸 <span>：公众号模板用 `<span>标签</span>` 做灰色小标签，
      style 被剥掉后 span 成了无意义包裹（Tiptap 会原样保留）。实测 37 篇
      共 1162 个，全是裸 span（有 class/style 的一个都没有），剥掉文字不变。
    · 保留 h2/p/blockquote/hr
    · **合并连续 blockquote**（2026-09-29 补）：公众号模板把「引用原话」和
      「核实过程」拆成相邻两个 blockquote。B站 Tiptap 给每个 blockquote
      上下大边距，连着两块看起来就是「每段分得太开」。实测 37 篇里 26 篇
      有连续 blockquote。合并成一块（中间换行）后视觉上是一段引文。
    · **h2 小标题降级成加粗段落**（2026-09-29 补，用户反馈「小标题没有样式，
      和下面内容隔了太多行」）。实测 B站编辑器**没有小标题功能**：
        - 工具栏里没有任何「标题」按钮（2026-09-29 全 DOM 扫描 title/
          aria-label 找「标题/heading」零命中）；
        - CSS 里 `.ProseMirror :where(p, h1..h6, blockquote…) { margin:0;
          padding:0; font-size:inherit; line-height:inherit }` 把 h2 的
          浏览器默认样式全清掉；
        - 唯一给 h2 设样式的规则是 `.ProseMirror h2[data-eva3-scoped]
          { font-weight:500; margin-top:36px; font-size:18px }` ——
          结果就是「18px 不加粗 + 上方 36px + 下方 0」，看起来既不像标题
          又把正文推得很远；
        - `insertHTML` 灌进去的 `style` 属性会被 Tiptap 剥掉（实测
          h2 的 style 被丢，文字被包进 `<span style="color:var(--Ga10)">`）；
          灌完再用 JS 加 style，**存草稿后也会被洗掉**（重开是干净的
          `<h2 data-eva3-scoped>`）。所以样式这条路走不通。
      → 只能用 B站作者圈的实际做法：`<p><strong>标题</strong></p>`。
      `strong` 能存活（实测存草稿重开后仍是 `<strong>`），字号跟着正文
      17px 走，段落间距由 B站统一的 24px margin 管，比 h2 的 36px/0 协调。
      `## 一、xxx` 这种带序号前缀会保留 —— 序号本来就该有。
    · **flex 伪表格 → 键值 blockquote**（2026-09-30 补，用户反馈「表格形式的
      数据样式都丢了」）。公众号的「数据表格」是 `<p style="display:flex;
      justify-content:space-between">` + 两个 `<span>`（标签/值）伪装出来的，
      不是真 `<table>`。剥 style + 剥裸 span 会把两者粘成一团
      （`官方口径$3,150 MRR`）。必须在**剥 style 之前**识别并转成
      「整组一个 blockquote + 每行 `标签　值`」，详见 `_flex_rows_to_kv_block`。
      ⚠ 顺序敏感：这一步必须在「去 style」和「剥裸 span」**之前**，
      否则识别特征（style 里的 flex、span 分列）已经被抹掉了。
    """
    m = re.search(r"<section[^>]*>(.*)</section>", html, re.S | re.I)
    body = m.group(1) if m else html
    # 标题单独填标题框，正文区不再留 <h1>（否则与标题框重复）
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, flags=re.S | re.I)
    # 删注释
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)

    def _drop_if_leadout(match):
        seg = match.group(0)
        return "" if any(kw in seg for kw in LEADOUT_KW) else seg

    # 逐段删导流段（</p> 内不会嵌 p，单段匹配不会跨界）
    body = re.sub(r"<p[^>]*>.*?</p>", _drop_if_leadout, body, flags=re.S)
    body = re.sub(r"<blockquote[^>]*>.*?</blockquote>", _drop_if_leadout,
                  body, flags=re.S)
    # **加粗** → strong
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    # flex 伪表格 → 键值 blockquote（**必须在去 style/剥 span 之前**，
    # 否则识别特征没了。37/37 全有这种表，详见函数 docstring）
    body = _flex_rows_to_kv_block(body)
    # h2/h3 小标题 → 加粗段落（B站没有标题功能，见 docstring）
    body = _heading_to_bold(body)
    # 去所有 style 属性
    # ⚠ 2026-10-09 复核：原来是 `r"\s+style=\"[^\"]*\""`，**没改回去**，
    # 它是对的。`clean_body` 不带 re.S 标志，`[^\"]*` 不跨行；且公众号模板
    # 渲染产物里 style 值不含 `"`。曾误判它「跨行吃掉 </p>」——真凶还是那个
    # `}`。`[^">]*` 在 style 值含 `>` 的极端情况下反而更糟（提前截断）。
    body = re.sub(r"\s+style=\"[^\"]*\"", "", body)
    # 剥裸 span（带属性的一律保留 —— 那些可能承载语义）
    body = re.sub(r"<span(?![^>]*\b(?:class|style|id)\s*=)[^>]*>(.*?)</span>",
                  r"\1", body, flags=re.S | re.I)
    # 去空段落/空引用块
    body = re.sub(r"<p[^>]*>\s*</p>", "", body)
    body = re.sub(r"<blockquote[^>]*>\s*</blockquote>", "", body)
    # 合并连续 blockquote：</blockquote>\s*<blockquote…> → 中间留 <br>
    body = re.sub(r"</blockquote>\s*<blockquote[^>]*>", "<br>", body,
                  flags=re.I)
    # **压掉标签间换行**（2026-09-30 补，用户反馈「每段分得太开」的真凶）。
    # B站 Tiptap 的 insertHTML 把 `>` 与下一个 `<` 之间的**空白文本节点**
    # 当成一个额外的空段落：`<p>A</p>\n<p>B</p>` 灌进去会变成 A、空段、B。
    # 实测 coral 灌入后全文高 5785px、39 个正常段之间夹了 20+ 个 58px 的
    # 空段（正常段只有 29px）；把标签间空白全压掉后同样的内容只剩
    # 2514px、段落间距回到 B站 统一的 24px。⚠ 只压标签**之间**的空白，
    # 段内文字之间的空白要留着（<p>a b</p> 中间那个空格是正文内容）。
    body = re.sub(r">[ \t\r\n]+<", "><", body)
    return body.strip()


def cover_path(cid):
    """横版封面复用头条已生成的 cover.png。"""
    p = os.path.join(OUT_TT, cid, "cover.png")
    return p if os.path.isfile(p) else None


def risk_check(text):
    return [w for w in RISK_PATTERNS if w in (text or "")]


# --------------------------------------------------------------------------
# build：生成 out/bili/<id>/{article.html, note.json}
# --------------------------------------------------------------------------
def build_one(cid):
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    os.makedirs(os.path.join(OUT, cid), exist_ok=True)
    title = make_title(cid, html)
    body = clean_body(html)
    cp = cover_path(cid)

    art = body  # 标题单独填标题框，正文区不含 <h1>
    with open(os.path.join(OUT, cid, "article.html"), "w", encoding="utf-8") as f:
        f.write(art)

    note = {
        "id": cid,
        "title": title,
        "body_len": len(re.sub(r"<[^>]+>", "", body)),
        "cover": os.path.basename(cp) if cp else None,
        "risk": risk_check(title + re.sub(r"<[^>]+>", "", body)),
    }
    with open(os.path.join(OUT, cid, "note.json"), "w", encoding="utf-8") as f:
        json.dump(note, f, ensure_ascii=False, indent=2)

    flag = ("  ⚠ 风险词:%s" % note["risk"]) if note["risk"] else ""
    print("  ✓ %s《%s》正文 %d 字 封面:%s%s"
          % (cid, title, note["body_len"], "有" if cp else "缺", flag))
    return True


def build_all():
    ids = list_ids()
    print("build 全部 %d 篇 → out/bili/" % len(ids))
    ok = 0
    for cid in ids:
        if build_one(cid):
            ok += 1
    print("完成 %d/%d" % (ok, len(ids)))


# --------------------------------------------------------------------------
# probe：连 9222 dump 投稿页 DOM，校准 SEL
# --------------------------------------------------------------------------
def probe():
    if None in SEL.values():
        print("（SEL 当前为占位，跑 probe 拿真实选择器后填 scripts/bilibili_publish.py）")
    cdp = wp.CDP(PUB_PORT)
    tid = None
    for t in cdp.list_targets():
        if t.get("type") == "page" and "bilibili.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(BILI_ARTICLE)["id"]
        time.sleep(6)
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    sub.send("Page.navigate", {"url": BILI_ARTICLE})
    time.sleep(15)
    ctxs = sub.collect_contexts(timeout=15)
    print("=== contexts ===")
    for c in ctxs:
        print("  ctx %s %s name=%s" % (c["id"], "default" if c["isDefault"] else "iframe", c.get("name")))
    for c in ctxs:
        cid = c["id"]
        try:
            d = sub.eval("""(function(){
              function cls(e){ return (e.className||'').toString().slice(0,60); }
              return {
                edits: [].slice.call(document.querySelectorAll('[contenteditable]')).map(function(e){
                  return {cls:cls(e), tag:e.tagName};
                }).slice(0,8),
                inputs: [].slice.call(document.querySelectorAll('textarea,input')).map(function(e){
                  return {ph:(e.getAttribute('placeholder')||''), cls:cls(e)};
                }).slice(0,8),
                btns: [].slice.call(document.querySelectorAll('button')).map(function(e){
                  return (e.innerText||'').trim();
                }).filter(Boolean).slice(0,20)
              };
            })()""", context_id=cid)
        except Exception as ex:
            d = "eval err: %s" % ex
        print("--- ctx %s (%s) ---" % (cid, "default" if c["isDefault"] else "iframe"))
        print(json.dumps(d, ensure_ascii=False))


# --------------------------------------------------------------------------
# publish：填一稿进专栏投稿页并存草稿
# --------------------------------------------------------------------------
BILI_NEW_ARTICLE = BILI_ARTICLE.rstrip("/") + "/new-edit"

# 同域 iframe 文档获取 + 编辑器就绪探测（在主文档 context 执行）
_JS_FDOC = """(function(kw){
  var f=[].slice.call(document.querySelectorAll('iframe')).find(
    function(f){return (f.src||'').indexOf(kw)>=0;});
  if(!f) return 'no-iframe';
  var d=f.contentDocument;
  if(!d) return 'no-doc';
  return d.body ? 'ready' : 'empty';
})"""


def _need_sel():
    miss = [k for k in REQUIRED_SEL if not SEL.get(k)]
    if miss:
        raise SystemExit("SEL 缺: %s —— 先 `probe` 拿真实选择器填进脚本" % miss)


def _open_editor_tab(cdp):
    """找/开 B站 tab，导航到新建专栏页，返回 (sub, tid)。"""
    tid = None
    for t in cdp.list_targets():
        if t.get("type") == "page" and "member.bilibili.com" in (t.get("url") or ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(BILI_NEW_ARTICLE)["id"]
    sub = wp.CDP(PUB_PORT)
    sub.connect_target(tid)
    if "new-edit" not in (sub.eval("location.href") or ""):
        sub.send("Page.navigate", {"url": BILI_NEW_ARTICLE})
        time.sleep(8)
    return sub, tid


def _key(cdp, key, code, vk, mods=0, text=None):
    """发一对 CDP 键盘事件（keyDown + keyUp）。

    为什么不用 execCommand：ProseMirror 只认**真实输入管线**上的beforeinput/
    keydown，execCommand('delete') 改的是 DOM，编辑器内部 state 不同步，
    下一次渲染会把内容恢复回去（2026-09-30 实测，见 publish_one 2b 注释）。
    mods 用 CDP 位掩码：1=Alt 2=Ctrl 4=Meta 8=Shift。
    """
    p = {"type": "keyDown", "key": key, "code": code,
         "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
         "modifiers": mods}
    if text:
        p["text"] = text
    cdp.send("Input.dispatchKeyEvent", p)
    cdp.send("Input.dispatchKeyEvent", dict(p, type="keyUp"))


def _wait_iframe_ready(sub, kw, probe_sel, timeout=25):
    """轮询等 <iframe src~kw> 里的文档渲染出 probe_sel。"""
    end = time.time() + timeout
    while time.time() < end:
        r = sub.eval(_JS_FDOC + "(" + json.dumps(kw) + ")")
        if r == "ready":
            # 再确认目标元素已渲染
            r2 = sub.eval("(function(kw,s){"
                          "var f=[].slice.call(document.querySelectorAll('iframe'))"
                          ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                          "return f&&f.contentDocument?"
                          "!!f.contentDocument.querySelector(s):false;})("
                          + json.dumps(kw) + "," + json.dumps(probe_sel) + ")")
            if r2 is True or r2 == "true" or r2 is True:
                return True
        time.sleep(1.5)
    return False


def _dry_api(cid):
    """`publish --dry` 的 API 路线版本：**只做本地校验，不发任何请求**。

    与 UI 路线 --dry 的语义不同（UI 版会开 tab 看选择器在不在），
    这里改成「正文能不能过闸门 + 会构造出几个段落」。
    """
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    title = make_title(cid, html)
    body = clean_body(html)
    plain = re.sub(r"<[^>]+>", "", body)
    risk = risk_check(title + plain)
    if risk:
        print("  ✗ 风险词未清干净：%s" % risk)
        return False
    arg = bili_api.build_arg(title, body)
    print("  [dry] 标题《%s》正文 %d 字 → 构造 %d 段，category_id=%d"
          % (title, len(plain),
             len(arg["opus"]["content"]["paragraphs"]), arg["category_id"]))
    print("  [dry] 未发请求（API 路线不发草稿箱列表，只读不写）")
    return len(plain) >= 200


def publish_one(cid, dry=False, replace=False, keep_tabs=False, use_api=True,
                cdp=None):
    """存一条草稿到 B站。

    `use_api=True`（**默认**）走 draft/add 接口，不开编辑器、秒级。
    `use_api=False` 走老 UI 路线（开 tab → 灌正文 → 点「保存为草稿」），
    保留它的唯一理由是**行内样式**（粗体/颜色/居中）——API 路线不构造
    word.style，会丢。
    """
    if use_api:
        if dry:
            return _dry_api(cid)
        return save_via_api(cid, replace=replace, cdp=cdp)

    _need_sel()
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    title = make_title(cid, html)
    body = clean_body(html)
    cp = cover_path(cid)
    plain = re.sub(r"<[^>]+>", "", body)
    risk = risk_check(title + plain)
    if risk:
        print("  ✗ 风险词未清干净：%s —— 先修 clean_body/LEADOUT_KW" % risk)
        return False
    if len(plain) < 200:
        print("  ✗ 正文仅 %d 字（B站专栏建议 ≥300），跳过" % len(plain))
        return False

    cdp = wp.CDP(PUB_PORT)
    aid, old_mtime = None, 0
    # replace 模式**不要**先开新建入口 tab —— 它完全用不上（直接按
    # article_id 打开已有草稿），留着纯粹白占一个 page target。批量 37 条
    # 时这些废 tab 会累积到把页面 WebSocket 拖超时（2026-09-30 实测：
    # 第 2 条就TimeoutError: timed out，栈在 _open_editor_tab 的
    # Page.navigate）。
    sub, tid = (None, None) if replace else _open_editor_tab(cdp)

    try:
        if dry:
            if sub is None:
                _close_stale_bili_tabs(cdp)
                lid0 = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
                cdp.connect_target(lid0)
                time.sleep(5)
                sub, tid = cdp, lid0
            ok1 = _wait_iframe_ready(sub, "read-draft", SEL["new_creation"], 20)
            print("  [dry] 入口 iframe 就绪: %s" % ("✓" if ok1 else "✗"))
            print("  [dry] 标题=%s 正文=%s 存草稿文本=%r（未实际填写）"
                  % (SEL["title"], SEL["body"], SEL["draft_text"]))
            return ok1

        # 0) replace：直接按 article_id 打开已有草稿
        #    不用草稿箱点卡片 —— 草稿箱 DOM 只渲染首屏 10 条且滚动无效
        #    （2026-09-29 实测，37 条里第 11 条之后就找不到卡片了）。
        #    走 /platform/upload/text/new-edit?aid=<id> 最可靠，
        #    article_id 从官方草稿接口拿（见 _pick_draft）。
        if replace:
            _close_stale_bili_tabs(cdp)
            lid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
            cdp.connect_target(lid)
            time.sleep(5)
            aid, _drafts = _pick_draft(cdp, lid, title)
            cdp.close_target(lid)
            if not aid:
                print("  ✗ 草稿箱接口里没找到《%s》" % title)
                return False
            old_mtime = 0
            for d in _drafts:
                if d.get("article_id") == aid:
                    old_mtime = d.get("mtime") or 0
                    break
            tid2 = cdp.new_target(BILI_EDIT_URL % aid)["id"]
            cdp.connect_target(tid2)
            sub, tid = cdp, tid2
            time.sleep(4)

        # 1) 非 replace：B站 2026-09-30 改版后「新的创作」按钮点击失效
        #    （read-draft 始终全屏、read-editor 不出现），改为直接走 new-edit
        #    编辑器入口——_open_editor_tab 已 navigate 到 new-edit，read-editor
        #    iframe 直接出现，下方标题/正文/封面的填充逻辑完全复用。
        else:
            pass

        # 2) 等编辑器 iframe（read-editor）就绪
        if not _wait_iframe_ready(sub, "read-editor", SEL["title"], 30):
            print("  ✗ 编辑器 iframe 没就绪（title-input 没出现）")
            return False

        # 2b) replace 模式：先清空原有标题/正文，否则新内容会叠加在旧草稿上
        #
        # ⚠ 必须用**真实键盘事件**清空，不能用 execCommand('selectAll')+delete。
        #   2026-09-30 实测：execCommand 版本在DOM 上看着删干净了，但 ProseMirror
        #   内部 state 没同步 —— 下一步读children 时旧内容**自己回来了**，
        #   随后 insertHTML 插不进去（静默无效）。结果整篇正文被塞进编辑器
        #   遗留的 <blockquote class="eva3-blockquote"> 里，一整块引用样式，
        #   段落间距全乱（就是用户截图里「样式丢失、段落分得太开」的成因）。
        #   CDP Input.dispatchKeyEvent 的 Ctrl+A → Delete 走的是 ProseMirror
        #   真实输入管线，删完是干净的 `<p class="is-empty is-editor-empty">`。
        if replace:
            cleared = sub.eval(
                "(function(kw,ts,bs){"
                "var f=[].slice.call(document.querySelectorAll('iframe'))"
                ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                "var d=f&&f.contentDocument?f.contentDocument:null;"
                "if(!d) return 'no-iframe';"
                "var t=d.querySelector(ts), b=d.querySelector(bs);"
                "if(t){t.focus();t.value='';"
                "t.dispatchEvent(new Event('input',{bubbles:true}));}"
                "if(b){b.focus(); return 'focused';}"
                "return 'no-body';})(" +
                json.dumps("read-editor") + "," + json.dumps(SEL["title"]) +
                "," + json.dumps(SEL["body"]) + ")", refresh_context=True)
            if cleared == "focused":
                _key(cdp, "a", "KeyA", 65, mods=2)      # Ctrl+A 全选正文
                time.sleep(0.4)
                _key(cdp, "Delete", "Delete", 46)      # Delete 删掉
                time.sleep(1.2)
            print("    清空旧内容: %s" % cleared)
            time.sleep(1.0)

        # 3) 填标题：聚焦 textarea → Input.insertText（真实键入，框架必收）
        focus_js = ("(function(kw,s){"
                    "var f=[].slice.call(document.querySelectorAll('iframe'))"
                    ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                    "var el=f&&f.contentDocument?"
                    "f.contentDocument.querySelector(s):null;"
                    "if(!el) return 'none';"
                    "el.focus();"
                    "return 'focused';"
                    "})(" + json.dumps("read-editor") + "," +
                    json.dumps(SEL["title"]) + ")")
        if sub.eval(focus_js) != "focused":
            print("  ✗ 标题框聚焦失败")
            return False
        sub.send("Input.insertText", {"text": title})
        time.sleep(1)

        # 4) 灌正文：聚焦 ProseMirror → execCommand('insertHTML')（同头条范式）
        #
        #⚠ 灌完必须校验「顶层子节点数 + 纯文本长度」：静默无效的情况真实
        #   发生过（清空没生效时 insertHTML 直接被丢弃，编辑器字数还是旧的）。
        #   只看 return 的 len 不够 —— len 是灌完立刻读的，可能是旧内容。
        insert_js = ("(function(kw,s,html){"
                     "var f=[].slice.call(document.querySelectorAll('iframe'))"
                     ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                     "var d=f.contentDocument;"
                     "var el=d.querySelector(s);"
                     "if(!el) return 'none';"
                     "el.focus();"
                     "d.execCommand('insertHTML', false, html);"
                     "return JSON.stringify({len:el.textContent.length,"
                     "kids:el.children.length});"
                     "})(" + json.dumps("read-editor") + "," +
                     json.dumps(SEL["body"]) + "," + json.dumps(body) + ")")
        r = sub.eval(insert_js)
        try:
            info = json.loads(r)
            blen, bkids = info.get("len", 0), info.get("kids", 0)
        except (ValueError, TypeError):
            print("  ✗ 正文灌入失败: %s" % str(r)[:80])
            return False
        # 灌完再等一拍让 ProseMirror 提交事务，然后复核
        time.sleep(2)
        r2 = sub.eval(_iframe_js(
            "var el=d.querySelector(%s);"
            "if(!el) return 'none';"
            "return JSON.stringify({len:el.textContent.length,"
            "kids:el.children.length});" % json.dumps(SEL["body"])))
        try:
            info2 = json.loads(r2)
            blen, bkids = info2.get("len", 0), info2.get("kids", 0)
        except (ValueError, TypeError):
            pass
        if bkids < 3 or blen < len(plain) * 0.9:
            print("  ✗ 正文疑似没灌进去（顶层块 %d，编辑器字数 %d，本地 %d）"
                  % (bkids, blen, len(plain)))
            return False
        time.sleep(1)

        # 5) 点「保存为草稿」
        save_js = ("(function(kw,txt){"
                   "var f=[].slice.call(document.querySelectorAll('iframe'))"
                   ".find(function(f){return (f.src||'').indexOf(kw)>=0;});"
                   "if(!f||!f.contentDocument) return 'no-iframe';"
                   "var btn=[].slice.call(f.contentDocument.querySelectorAll("
                   "'button, a, [role=button]')).find("
                   "function(b){return (b.innerText||'').trim()===txt;});"
                   "if(!btn) return 'none';"
                   "btn.click();"
                   "return 'clicked';})(" +
                   json.dumps("read-editor") + "," + json.dumps(SEL["draft_text"]) + ")")
        clicked = False
        end = time.time() + 10
        while time.time() < end:
            r = sub.eval(save_js)
            if r == "clicked":
                clicked = True
                break
            time.sleep(1.5)
        if not clicked:
            print("  ✗ 「保存为草稿」按钮没找到")
            return False
        time.sleep(4)  # 等保存请求发出/toast

        # 5b) **远端回读校验**（replace 模式）——「点了保存」不等于「存上了」。
        # 2026-09-30 实测踩到：旧版脚本只点按钮就报成功，实际远端草稿正文
        # 一个字没变（编辑器里还是 8 个 <h2>），而脚本已经写下"成功"。
        # 唯一可靠的判据是远端 draft 的 mtime 变化 + summary 前缀匹配。
        if replace and aid:
            got = _verify_saved(cdp, title, expect_head=plain[:40], old_mtime=old_mtime)
            if got == "ok":
                print("  ✓ 远端已回读确认：mtime 已更新、正文前缀匹配")
            else:
                print("  ⚠ 保存后回读不一致（%s）——草稿可能没真正更新，"
                      "请人工核对" % got)
                return False

        print("  ✓ 标题《%s》正文 %d 字（编辑器实测 %d 字 / %d 块）已存草稿%s"
              % (title, len(plain), blen, bkids,
                 "（封面未传，人工核对）" if not cp else ""))
        return True
    finally:
        # 单条跑完就清掉自己开的 B站 tab：批量时 tab 会累积，实测 20+ 个
        # 页面 target 会把页面 WebSocket 拖到 TimeoutError。非批量（人工
        # 核对）场景由 --keep-tabs 保留。
        if not keep_tabs:
            try:
                _close_stale_bili_tabs(cdp)
            except Exception:
                pass


def publish_all(replace=False, retries=1, only=None, use_api=True):
    ids = list_ids()
    if only:
        # 断点续传：只跑白名单里的 id（批量中途 CDP 断连/机器休眠后补跑用）。
        # 给出的是**全部 id 的序号**，方便直接复用上轮日志里的 [23/37] 编号。
        want = set(only)
        ids = [c for c in ids if c in want]
        print("仅处理指定 %d 条" % len(ids))
    print("publish %d 篇 → B站草稿箱%s%s%s"
          % (len(ids), "（覆盖已有草稿）" if replace else "",
             "（每条重试 %d 次）" % retries if retries else "",
             "" if use_api else "（UI 路线，要样式时用）"))
    ok = 0
    failed = []
    # API 路线不碰 tab，连接可复用 —— 批量时省掉每条重建 CDP 的开销
    cdp = None
    if use_api:
        try:
            cdp = wp.CDP(PUB_PORT)
            bili_api._bili_tab(cdp)
        except Exception as e:                # noqa: BLE001
            print("  ! 建 CDP 失败（%s），逐条重试" % str(e)[:60])
            cdp = None
    for i, cid in enumerate(ids, 1):
        print("[%d/%d] %s" % (i, len(ids), cid))
        done = False
        for attempt in range(retries + 1):
            if attempt:
                # 重试前先彻底清一遍 tab：上次残留的 target 是超时主因
                try:
                    if use_api:
                        bili_api._bili_tab(cdp or wp.CDP(PUB_PORT))
                    else:
                        _close_stale_bili_tabs(wp.CDP(PUB_PORT))
                except Exception:                # noqa: BLE001
                    pass
                time.sleep(4)
                print("    ↻ 重试第 %d 次" % attempt)
            try:
                if publish_one(cid, replace=replace, use_api=use_api, cdp=cdp):
                    done = True
                    break
            except Exception as e:                # noqa: BLE001
                # CDP 超时 / 页面崩了都不该中断整轮
                print("    ! %s: %s" % (type(e).__name__, str(e)[:90]))
        if done:
            ok += 1
        else:
            failed.append(cid)
        time.sleep(3)
    print("完成 %d/%d" % (ok, len(ids)))
    if failed:
        # 用**空格**分隔（不是逗号）：`--only` 的参数是逗号分隔，失败清单
        # 要能直接复制回 --only 补跑，两种分隔符混用会解析出错。
        print("失败 %d 条：%s" % (len(failed), " ".join(failed)))
    # 部分失败也要 rc≠0，让调用方（publish_multi 的 batch）能感知。
    # ⚠ 但调用方**不能只用 rc 判定**：rc=1 只说明"至少挂了一条"，
    #   已经成功那些必须靠 parse_batch 从 stdout 逐条确认，否则会被重发。
    return ok, failed


# --------------------------------------------------------------------------
# cover：给已存草稿补封面（2026-09-29 实测跑通）
#
# 为什么不用 Page.fileChooserOpened：在这个**同域 read-editor iframe** 上点
# 「添加封面」，fileChooserOpened 事件收不到（实测 drain 12s 一个事件都没有），
# 但 hidden input[type=file] 确实被创建出来了。改用官方支持的 DataTransfer
# 页面内赋值 + dispatchEvent('change')，稳定触发 Vue 的 change 监听。
# --------------------------------------------------------------------------
BILI_DRAFT_LIST_PAGE = "https://member.bilibili.com/opus/management/drafts"
BILI_DRAFT_API = ("https://api.bilibili.com/x/dynamic/feed/article/draft/list"
                  "?pn=1&ps=200&keyword=")
# 删草稿：路径是 draft/**delete** 不是 draft/remove —— 猜 remove 会返回
# 空 {}（连 {code,msg} 都没有），一度以为没权限。真实路径是从草稿箱页面
# 加载的 DraftList-*.js bundle 里正则抓出来的（2026-09-30）。
# article_id 从 draft/list 的 drafts[].article_id 拿。
BILI_DRAFT_REMOVE_API = "https://api.bilibili.com/x/dynamic/feed/article/draft/delete"
BILI_EDIT_URL = "https://member.bilibili.com/platform/upload/text/new-edit?aid=%s"


def _iframe_js(body, kw="read-editor"):
    """在 src 含 kw 的同域 iframe 文档里执行 body，返回它的返回值。

    body 是**函数体**（可含 return），本函数负责包成 IIFE 并调用。
    ⚠ 两个坑都踩过：
      1. 别在 body 里再套一层 `(function(){...})()` 后不 return —— 内层调用
         结果被丢弃，外层返回 undefined（表现为 `FAIL:None`，极难查）。
      2. body 必须以 `return ...;` 或裸 return 收尾，让**外层** IIFE 的
         返回值等于你要的结果；写成 `var t=(function(){})(a)` 会让整个
         表达式退化成 undefined。
    """
    return ("(function(){var f=[].slice.call("
            "document.querySelectorAll('iframe')).find("
            "function(x){return (x.src||'').indexOf(%s)>=0;});"
            "if(!f||!f.contentDocument) return {err:'no-iframe'};"
            "var d=f.contentDocument;" % json.dumps(kw) + body + "})()")


def _close_stale_bili_tabs(cdp):
    """关掉遗留的 B站 tab —— 堆积 20 个会把页面 WebSocket 拖到超时。"""
    try:
        targets = cdp.list_targets()
    except Exception:
        return
    for t in targets:
        if t.get("type") == "page" and "member.bilibili.com" in (t.get("url") or ""):
            try:
                cdp.close_target(t["id"])
            except Exception:
                pass


def _data_url(path):
    import base64
    import mimetypes
    mt = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mt, base64.b64encode(f.read()).decode())


# 往 hidden input 塞文件（dataurl/name 由调用方拼在后面当参数）。
# 为什么不用 Page.fileChooserOpened：在这个**同域 iframe** 上点「添加封面」，
# 该事件实测收不到（drain 12s 零事件），但 input 确实被创建了。
# DataTransfer 是 Chrome 官方支持的写法：input.files = dt.files 合法。
# ⚠ `_JS_SET_FILES` 必须是**带括号的函数表达式**：`(function(...){...})`。
#   裸写 `function(...){...}` 落在语句位置会被当函数声明 → SyntaxError:
#   Function statements require a function name。
# ⚠ 两个坑都踩过：
#   1. `dispatchEvent('change')` 是**同步**的 —— Vue 的 change 处理函数会在
#      派发返回前就把 input 清空（`inp.value=''`）。所以 files 的长度/大小
#      必须在派发**之前**取，派发后再读 `files[0]` 会是 undefined
#      （报错 "Cannot read properties of undefined (reading 'size')"）。
#   2. `_JS_SET_FILES` 是匿名函数**表达式**，且调用点必须写成
#      `return <fn>(args)`，让外层 IIFE 的 return 等于它的返回值；
#      写成 `var t=<fn>(a)` 会让整个表达式退化成 undefined。
_JS_SET_FILES = """(function(dataurl, name){
    var inp=d.querySelector('input[type=file]');
    if(!inp) return 'no-file-input';
    var parts=dataurl.split(',');
    var mime=parts[0].replace(/^data:/,'').split(';')[0];
    var bin=atob(parts[1]);
    var arr=new Uint8Array(bin.length);
    for(var i=0;i<bin.length;i++) arr[i]=bin.charCodeAt(i);
    var file=new File([arr], name, {type:mime});
    var dt=new DataTransfer();
    dt.items.add(file);
    if(!dt.files.length) return 'dt-empty';
    inp.files=dt.files;
    if(!inp.files.length) return 'assign-empty';
    /* 必须在派发前取值：change 处理函数是同步的，会立刻清空 input */
    var n=inp.files.length, sz=inp.files[0].size;
    inp.dispatchEvent(new Event('change', {bubbles:true}));
    return 'ok:'+n+':'+sz;
  })"""


def _verify_saved(cdp, title, expect_head, old_mtime):
    """保存后回读远端草稿，确认真的存上了。返回 "ok" 或失败原因。

    为什么必须有这一步（2026-09-30 实测）：旧版publish_one 点完「保存为草稿」
    就 return True，结果远端草稿正文一个字没变，脚本却报成功 —— 一整轮
    批量 37 条全部"成功"但远端是旧版，这类假成功最难查。

    判据（两个都要）：
      1. mtime 必须比保存前更新（B站只给秒级时间戳，轮询要给足窗口）；
      2. summary 前缀要匹配本地正文开头 —— summary 是 B站自己截的前 250 字，
         刚好够当"正文开头指纹"。⚠ 别指望 summary 全文对账：B站硬上限
         250 字（coral 本地 847 字，远端 summary 只有 250）。
    """
    norm = lambda s: re.sub(r"\s+", "", s or "")
    want = norm(expect_head)[:20]
    lid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
    cdp.connect_target(lid)
    try:
        time.sleep(4)
        end = time.time() + 30
        last = "no-draft"
        while time.time() < end:
            r = _fetch_drafts(cdp, lid)
            ds = (r.get("drafts") if isinstance(r, dict) else None) or []
            if not ds:
                last = "草稿接口返回空（%s）" % json.dumps(
                    r, ensure_ascii=False)[:80]
            hit = None
            for d in ds:
                if d.get("title") == title:
                    hit = d
                    break
            if not hit:
                last = "远端草稿列表里找不到该标题"
            elif (hit.get("mtime") or 0) <= (old_mtime or 0):
                last = "mtime 没变（仍为 %s）" % hit.get("mtime")
            else:
                got = norm(hit.get("summary") or "")[:20]
                if want and not got.startswith(want[:12]):
                    last = "summary 开头不匹配：远端 %r" % got[:20]
                else:
                    return "ok"
            time.sleep(4)
        return last
    finally:
        try:
            cdp.close_target(lid)
        except Exception:
            pass


def _fetch_drafts(cdp, tid):
    """在登录页上下文里调官方草稿接口，返回 drafts 列表。"""
    js = """(async () => {
      const r = await fetch(%s, {credentials: 'include'});
      const j = await r.json();
      if (j.code !== 0) return {err: j.code, msg: j.message};
      return {drafts: (j.data && j.data.drafts) || []};
    })()""" % json.dumps(BILI_DRAFT_API)
    return cdp.eval(js, refresh_context=True)


def _pick_draft(cdp, tid, title):
    """在草稿列表里找标题匹配的草稿，返回 article_id。

    标题是**当前**草稿的标题（publish 时填的精修版），不是本地 out/bili 的
    note.json —— note.json 可能与草稿箱里存的旧版不一致（改过文案没重存）。
    """
    r = _fetch_drafts(cdp, tid)
    if not isinstance(r, dict) or r.get("err") is not None:
        raise RuntimeError("草稿接口异常: %s" % json.dumps(r, ensure_ascii=False)[:200])
    drafts = r.get("drafts") or []
    if not title:
        return (drafts[0].get("article_id") if drafts else None), drafts
    norm = lambda s: re.sub(r"\s+", "", (s or "").lower())
    want = norm(title)
    for d in drafts:
        if norm(d.get("title")) == want:
            return d.get("article_id"), drafts
    # 退而求其次：前缀匹配（草稿箱截断/改版都可能动标题尾部）
    for d in drafts:
        t = norm(d.get("title"))
        if t and (t.startswith(want[:10]) or want.startswith(t[:10])):
            return d.get("article_id"), drafts
    return None, drafts


# --------------------------------------------------------------------------
# **API 存草稿**（2026-10-01 接通，取代开编辑器点按钮）
# --------------------------------------------------------------------------
#
# 为什么从 UI 换成 API：
#   UI 路线单条 ~70 秒（开 tab → 等 iframe → 清空 → 灌正文 → 点保存 →
#   回读），批量 37 条极易撞 CDP tab 累积超时；API 路线**不开编辑器、
#   不碰 tab**，秒级完成，且不丢封面（image_urls 从旧草稿原样带回）。
#
# ⚠ **代价：行内样式不保留**。API 路线只映射段落级（标题/引用/段落），
#   粗体/颜色/居中这些富文本格式在 word.style 里，本实现不构造。
#   要样式就用 --ui 走老路。
#
# 请求形状见 bili_draft_api 模块 docstring —— 2026-10-01 之前那版
# （form-urlencoded + csrf 在 body + {type,text} 节点）**全是错的**，
# 一律 -400。

def _api_tab(cdp):
    """接一个已登录的 B站 page；必要时新开草稿箱页。返回 target_id。"""
    return bili_api._bili_tab(cdp)


def save_via_api(cid, replace=False, cdp=None, title=None, body=None):
    """用 draft/add 存一条草稿（新建或按标题覆盖）。返回 True/False。

    这是 `publish_one` 的默认路径。流程全程不开编辑器：
        1. clean_body → build_arg
        2. replace 时先按标题找 article_id + 抄旧草稿的 image_urls
        3. draft/add（带 article_id 即更新）
        4. draft/view 回读校验 mtime 变了、正文非空

    `cdp` 可传入复用（批量时省连接）；不给就自己建。
    """
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    title = title or make_title(cid, html)
    body = body if body is not None else clean_body(html)
    plain = re.sub(r"<[^>]+>", "", body)
    risk = risk_check(title + plain)
    if risk:
        print("  ✗ 风险词未清干净：%s —— 先修 clean_body/LEADOUT_KW" % risk)
        return False
    if len(plain) < 200:
        print("  ✗ 正文仅 %d 字（B站专栏建议 ≥300），跳过" % len(plain))
        return False

    own = cdp is None
    cdp = cdp or wp.CDP(PUB_PORT)
    tid = None
    try:
        tid = _api_tab(cdp)
        aid, old = None, {}
        if replace:
            drafts = bili_api.list_drafts(cdp)
            norm = lambda s: re.sub(r"\s+", "", (s or "").lower())
            want = norm(title)
            for d in drafts:
                if norm(d.get("title")) == want:
                    aid = d.get("article_id")
                    break
            if aid is None:               # 前缀兜底，与 _pick_draft 同策略
                for d in drafts:
                    t = norm(d.get("title"))
                    if t and (t.startswith(want[:10]) or want.startswith(t[:10])):
                        aid = d.get("article_id")
                        break
            if not aid:
                print("  ✗ 草稿箱接口里没找到《%s》（现有 %d 条，"
                      "不确定是否已改标题）" % (title, len(drafts)))
                return False
            # 抄旧草稿里不想丢的字段（封面/可见性），build_arg 会原样带回
            try:
                old = bili_api.view_draft(cdp, aid)
            except Exception as e:                # noqa: BLE001
                print("    ⚠ 读旧草稿详情失败（%s），按默认值写" % str(e)[:60])
                old = {}
            old_mtime = old.get("mtime") or 0
        else:
            old_mtime = 0

        arg = bili_api.build_arg(
            title, body, article_id=aid,
            image_urls=old.get("origin_image_urls") or old.get("image_urls"),
            private_pub=old.get("private_pub", 2),
            original=old.get("original", 0),
            reprint=old.get("reprint", 1))
        out = bili_api.save_draft(cdp, arg)
        if not isinstance(out, dict) or out.get("code") != 0:
            print("  ✗ draft/add 失败：%s"
                  % json.dumps(out, ensure_ascii=False)[:200])
            return False
        new_aid = (out.get("data") or {}).get("article_id") or aid

        # 回读校验：「点了保存」≠「存上了」。UI 路线栽过这个坑（2026-09-30）。
        time.sleep(1.5)
        after = bili_api.view_draft(cdp, new_aid)
        got = (after.get("content") or "")
        ok = bool(got.strip())
        if replace and old_mtime and after.get("mtime") == old_mtime:
            ok = False
        tag = "更新" if aid else "新建"
        if ok:
            print("  ✓ API %s草稿 aid=%s《%s》正文 %d 字 / %d 段"
                  % (tag, new_aid, title, len(got),
                     len(arg["opus"]["content"]["paragraphs"])))
            if after.get("image_urls"):
                print("    封面保留: %s" % after["image_urls"][0][:70])
            else:
                print("    ⚠ 该草稿无封面（封面只能走 UI 上传，"
                      "draft/add 只认远端 URL）")
            return True
        print("  ✗ 回读校验失败：mtime=%s→%s，正文 %d 字"
              % (old_mtime, after.get("mtime"), len(got)))
        return False
    finally:
        if own and tid:
            try:
                cdp.close_target(tid)
            except Exception:                # noqa: BLE001
                pass


def _cover_present(cdp, tid):
    """当前草稿是否已有自定义封面（DOM 层面）。

    两种形态（2026-09-29 实测）：
      · 没有 → .select-cover .upload-button「添加封面」
      · 已有 → .selected-cover（picture > img[src*=article.biliimg.com]）
                + 「删除」「重新上传」两个按钮
    """
    r = cdp.eval(_iframe_js(
        "return !!d.querySelector('.selected-cover, .selected-cover img');"),
        refresh_context=True)
    return r is True or r == "true"


def _cover_img_url(cdp, tid):
    """从 DOM 里读当前封面图的 CDN 地址（有封面才读得到）。"""
    return cdp.eval(_iframe_js(
        "var m=d.querySelector('.selected-cover img, .selected-cover source');"
        "return m ? (m.getAttribute('src') || m.getAttribute('srcset') || '')"
        " : '';"),
        refresh_context=True)


def _open_cover_switch(cdp, tid):
    """确保「自定义封面」开关是开的。返回 'toggled' / 'already' / 'FAIL:...'。"""
    body = """
      var items=[].slice.call(d.querySelectorAll('.publish-settings .form-item'));
      var it=items.find(function(x){
        return (x.querySelector('.form-item-label')||{}).textContent==='自定义封面';});
      if(!it) return 'FAIL:找不到自定义封面项';
      var sw=it.querySelector('input.vui_switch-input');
      if(!sw) return 'FAIL:封面开关元素缺失';
      if(sw.checked) return 'already';
      sw.click();
      return 'toggled';
    """
    end = time.time() + 10
    r = None
    while time.time() < end:
        r = cdp.eval(_iframe_js(body), refresh_context=True)
        if r in ("toggled", "already"):
            if r == "toggled":
                time.sleep(2.5)      # 等 .select-cover 渲染
            return r
        time.sleep(1.2)
    return r if isinstance(r, str) and r else "FAIL:%r" % (r,)


def _drop_existing_cover(cdp, tid):
    """删掉已选的旧封面，为「重新上传」腾地方。返回 True/False。

    已有封面时 `.selected-action` 里有两个按钮：删除、重新上传。
    先删再传最稳 —— 不删的话裁剪框预填的是旧图，容易叠图。
    删不掉不算硬失败：_upload_cover_file 会退到「重新上传」路径。
    """
    end = time.time() + 12
    while time.time() < end:
        r = cdp.eval(_iframe_js(
            "var bs=[].slice.call("
            "  d.querySelectorAll('.selected-action button, .selected-action a'))"
            "  .filter(function(x){return (x.innerText||'').trim()==='删除';});"
            "if(!bs[0]) return 'no-btn'; bs[0].click(); return 'clicked';"),
            refresh_context=True)
        if r == "clicked":
            # 可能有二次确认
            time.sleep(1.5)
            cdp.eval(_iframe_js(
                "var b=[].slice.call("
                "  d.querySelectorAll('.vui-dialog button, .vui-modal button'))"
                "  .find(function(x){return (x.innerText||'').trim()==='确定';});"
                "if(b) b.click(); return 'ok';"),
                refresh_context=True)
            e2 = time.time() + 12
            while time.time() < e2:
                if not _cover_present(cdp, tid):
                    return True
                time.sleep(1.0)
            return False
        if r == "no-btn":
            # 已经不在「有封面」状态（可能自己就没了）
            return True
        time.sleep(1.0)
    return False


def _upload_cover_file(cdp, tid, img_path):
    """点「添加封面」造出 hidden input，用 DataTransfer 赋值触发上传。

    返回 'uploaded'（已进入裁剪弹窗）/ 'FAIL:...'。
    """
    # 点上传按钮把 hidden input 造出来。
    # 两种形态都要认：没有封面时是「添加封面」(.upload-button)，
    # 已有封面时是「重新上传」（.selected-action 里的第二个 .selected-btn）。
    clicked = cdp.eval(_iframe_js(
        "var b=d.querySelector('.select-cover .upload-button');"
        "if(!b){"
        "  var bs=[].slice.call(d.querySelectorAll('.selected-action button'))"
        "           .filter(function(x){return (x.innerText||'').trim()==='重新上传';});"
        "  b=bs[0];"
        "}"
        "if(!b) return 'no-btn'; b.click(); return 'clicked';"),
        refresh_context=True)
    if clicked != "clicked":
        return "FAIL:上传按钮点不到(%s)" % clicked

    end = time.time() + 10
    while time.time() < end:
        n = cdp.eval(_iframe_js(
            "return d.querySelectorAll('input[type=file]').length;"),
            refresh_context=True)
        if n:
            break
        time.sleep(1.0)
    else:
        return "FAIL:文件输入框没出现"

    url = _data_url(img_path)
    # 注意：body 里是 `return (function(...){...})(a, b)` —— 返回值是**外层
    # IIFE 的 return**，不是逗号表达式（写成 `var t=(function(){})(a)` 会
    # 让整个表达式退化成 undefined，看起来像「赋值失败(None)」）。
    r = cdp.eval(
        _iframe_js("return %s(%s,%s);" % (
            _JS_SET_FILES, json.dumps(url),
            json.dumps(os.path.basename(img_path)))),
        refresh_context=True)
    if not (isinstance(r, str) and r.startswith("ok:")):
        return "FAIL:赋值失败(%s)" % str(r)[:80]

    # 等裁剪弹窗（.vui_image-crop / 「选择封面的截取位置」）
    end = time.time() + 40
    while time.time() < end:
        has = cdp.eval(_iframe_js(
            "return !!d.querySelector('.vui_image-crop, .image-dialog');"),
            refresh_context=True)
        if has is True or has == "true":
            return "uploaded"
        time.sleep(1.5)
    return "FAIL:裁剪弹窗没出现"


def _crop_confirm(cdp, tid, timeout=25):
    """点裁剪弹窗的「确定」，等 .selected-cover 出现。

    「确定」不是终点 —— 点完还要等 Vue 把裁剪结果提交成封面
    （.select-cover 消失、.selected-cover 出现才算成）。
    """
    end = time.time() + timeout
    while time.time() < end:
        r = cdp.eval(_iframe_js(
            "var b=[].slice.call(d.querySelectorAll('button,[role=button]'))"
            ".find(function(x){return (x.innerText||'').trim()==='确定';});"
            "if(!b) return 'none'; b.click(); return 'clicked';"),
            refresh_context=True)
        if r == "clicked":
            # 等 .selected-cover 落地
            e2 = time.time() + 20
            while time.time() < e2:
                if _cover_present(cdp, tid):
                    return True
                time.sleep(1.0)
            return False
        time.sleep(1.2)
    return False


def _save_draft(cdp, tid, timeout=20):
    """点「保存为草稿」。返回 True/False。"""
    end = time.time() + timeout
    while time.time() < end:
        r = cdp.eval(_iframe_js(
            "var b=[].slice.call(d.querySelectorAll('button,[role=button],a'))"
            ".find(function(x){return (x.innerText||'').trim()==='保存为草稿';});"
            "if(!b) return 'none'; b.click(); return 'clicked';"),
            refresh_context=True)
        if r == "clicked":
            time.sleep(5)
            return True
        time.sleep(1.2)
    return False


def _cover_of(draft):
    """从草稿接口记录里取封面 URL（2026-09-29 实测定论）。

    ⚠ **不是 `banner_url`** —— 草稿接口的 `banner_url` 恒为 `""`，
    哪怕封面已经设好也一样。真正的封面落在：
      · `origin_image_urls` — 原始尺寸（正式封面用这个）
      · `image_urls`        — 缩略尺寸
    两者同源，`origin_image_urls` 优先。`banner_url` 另有用途（动态分发封面），
    拿它当「有没有封面」的判据会永远误判成没有。
    """
    for key in ("origin_image_urls", "image_urls"):
        v = draft.get(key)
        if isinstance(v, list) and v:
            return v[0]
        if isinstance(v, str) and v:
            return v
    return ""


def _banner_of(cdp, tid, aid):
    """回读某条草稿的封面 URL（空 = 没封面）。名字沿用旧叫法，实际取 image_urls。"""
    r = _fetch_drafts(cdp, tid)
    if not isinstance(r, dict) or r.get("err") is not None:
        return None
    for d in r.get("drafts") or []:
        if d.get("article_id") == aid:
            return _cover_of(d) or ""
    return None


def cover_one(cid, dry=False, cdp=None, tab_id=None, keep_tab=False,
              force=False):
    """给单条草稿补封面。返回 True/False。

    force=True 时**即使远端已有封面也重新上传**（配色改了要换图时用）。
    旧实现里 force 只影响「批量时跳不跳过」，cover_one 本身永远走
    「封面已存在 → 跳过上传」，所以 `cover --case X --force` 是假的。
    """
    html = load_article_html(cid)
    if not html:
        print("  ✗ 找不到 out/articles/%s.html" % cid)
        return False
    cp = cover_path(cid)
    if not cp:
        print("  ✗ 没有封面源图 out/toutiao/%s/cover.png" % cid)
        return False
    title = make_title(cid, html)

    own = cdp is None
    if own:
        _close_stale_bili_tabs(wp.CDP(PUB_PORT))
    cdp = cdp or wp.CDP(PUB_PORT)

    tid = None
    try:
        # 1) 列表页拿 article_id
        tid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
        cdp.connect_target(tid)
        time.sleep(5)
        aid, drafts = _pick_draft(cdp, tid, title)
        if not aid:
            print("  ✗ 草稿箱里没找到《%s》（现有 %d 条）" % (title, len(drafts)))
            return False
        if not keep_tab and not own:
            cdp.close_target(tid)
        if dry:
            print("  [dry] aid=%d 封面源=%s（未上传）" % (aid, os.path.basename(cp)))
            return True

        # 2) 编辑器页开封面开关 → 上传 → 裁剪确定 → 存草稿
        tid2 = cdp.new_target(BILI_EDIT_URL % aid)["id"]
        cdp.connect_target(tid2)
        ready = False
        # B站编辑器偶发加载慢（实测 35 条里约 1 条 iframe 30s 没起来）。
        # 重试一次：换新 tab 重新导航，而不是干等。
        for attempt in (1, 2):
            time.sleep(7 if attempt == 1 else 4)
            if _wait_iframe_ready(cdp, "read-editor", SEL["body"], 30):
                ready = True
                break
            print("    iframe 未就绪（第 %d 次），重试" % attempt)
            try:
                cdp.close_target(tid2)
            except Exception:
                pass
            time.sleep(2)
            tid2 = cdp.new_target(BILI_EDIT_URL % aid)["id"]
            cdp.connect_target(tid2)
        if not ready:
            cdp.close_target(tid2)
            print("  ✗ 编辑器 iframe 两次都没就绪")
            return False
        sw = _open_cover_switch(cdp, tid2)
        if sw.startswith("FAIL"):
            print("  ✗ %s" % sw)
            return False
        if _cover_present(cdp, tid2) and not force:
            # 已经传过一次（上次崩在存草稿前）：直接存草稿即可
            print("    封面已存在，跳过上传")
        else:
            if force and _cover_present(cdp, tid2):
                # 已有封面时上传按钮是「重新上传」，_upload_cover_file 已认。
                # 但 B站裁剪框预填的是**旧图**，不点「删除」的话新图可能
                # 叠在旧图上；这里先尝试删掉旧封面，删不掉也不阻断
                # （_upload_cover_file 会走「重新上传」兜底路径）。
                print("    强制重设：先删旧封面")
                _drop_existing_cover(cdp, tid2)
            up = _upload_cover_file(cdp, tid2, cp)
            if up.startswith("FAIL"):
                print("  ✗ %s" % up)
                return False
            if not _crop_confirm(cdp, tid2):
                print("  ✗ 裁剪弹窗没点上「确定」/封面没落地")
                return False
        dom_url = _cover_img_url(cdp, tid2) or ""
        if not _save_draft(cdp, tid2):
            print("  ✗ 「保存为草稿」点不到")
            return False

        # 3) 双重复验：DOM 有图 + 官方接口封面字段非空
        #    光看 DOM 不算 —— 必须确认已随草稿存到服务端。
        #    ⚠ 判据用 image_urls/origin_image_urls，**不是 banner_url**
        #    （后者在草稿接口里恒空，见 _cover_of 的说明）。
        back = _banner_of(cdp, tid2, aid)
        cdp.close_target(tid2)
        if back:
            print("  ✓ 《%s》aid=%d 封面已设 %s" % (title, aid, back[:70]))
            return True
        if dom_url:
            print("  ⚠ aid=%d 页面有封面图（%s）但接口仍无封面，存草稿可能没提交"
                  % (aid, dom_url[:50]))
            return False
        print("  ✗ aid=%d 上传后既无 DOM 封面也无接口封面" % aid)
        return False
    finally:
        if tid and not keep_tab and own:
            cdp.close_target(tid)


def cover_all(only_missing=True, cdp=None, force=False):
    """批量补封面。only_missing=True 时先回读远端，已设过的跳过。

    force=True 时**传下去给 cover_one**：连已有封面的也真重传（配色改了
    要换图）。否则 --force 只影响「跳不跳过」，不换图（那是旧行为，假的）。
    """
    ids = list_ids()
    print("cover 全部 %d 篇 → B站草稿%s" % (len(ids), "（只补没封面的）"
                                              if only_missing else "（全部重设）"))
    own = cdp is None
    if own:
        _close_stale_bili_tabs(wp.CDP(PUB_PORT))
    cdp = cdp or wp.CDP(PUB_PORT)

    skip = set()
    tid = None
    if only_missing:
        try:
            tid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
            cdp.connect_target(tid)
            time.sleep(5)
            r = _fetch_drafts(cdp, tid)
            if isinstance(r, dict) and r.get("err") is None:
                for d in r.get("drafts") or []:
                    if _cover_of(d):
                        skip.add(re.sub(r"\s+", "",
                                        (d.get("title") or "").lower()))
            print("  远端已有封面 %d 条（将跳过）" % len(skip))
            cdp.close_target(tid)
            tid = None
        except Exception as e:
            print("  ⚠ 读远端封面状态失败（%s），改为全量重设" % e)

    ok = 0
    failed = []
    todo = []
    for cid in ids:
        html = load_article_html(cid)
        t = make_title(cid, html) if html else ""
        if only_missing and re.sub(r"\s+", "", t.lower()) in skip:
            continue
        if not cover_path(cid):
            print("  – %s 跳过（无封面源图）" % cid)
            continue
        todo.append(cid)

    def _close_leftovers():
        """关掉本条开过的 tab —— 堆积 20 个 B站 page 会把 WS 拖到超时。"""
        try:
            cur = cdp.tid
        except Exception:
            cur = None
        for t in cdp.list_targets():
            if (t.get("type") == "page"
                    and "member.bilibili.com" in (t.get("url") or "")
                    and t.get("id") != cur):
                try:
                    cdp.close_target(t["id"])
                except Exception:
                    pass

    for i, cid in enumerate(todo, 1):
        print("[%d/%d] %s" % (i, len(todo), cid))
        good = False
        try:
            good = cover_one(cid, cdp=cdp, keep_tab=True, force=force)
        except Exception as e:
            print("  ✗ 异常: %s" % str(e)[:150])
        if good:
            ok += 1
        else:
            failed.append(cid)
        _close_leftovers()
        time.sleep(2)

    # 失败重试一轮：B站编辑器/网络偶发抽风（实测 35 条里约 1 条 iframe 超时）
    if failed:
        print("  ↻ 重试 %d 条失败项：%s" % (len(failed), "、".join(failed[:8])))
        retry = []
        for cid in failed:
            print("[重试] %s" % cid)
            good = False
            try:
                good = cover_one(cid, cdp=cdp, keep_tab=True, force=force)
            except Exception as e:
                print("  ✗ 异常: %s" % str(e)[:150])
            if good:
                ok += 1
            else:
                retry.append(cid)
            _close_leftovers()
            time.sleep(3)
        failed = retry

    print("完成 %d/%d" % (ok, len(todo)))
    if failed:
        print("仍失败 %d 条：%s" % (len(failed), "、".join(failed)))
    return ok, len(todo)


def _norm_title(s):
    return re.sub(r"\s+", "", (s or "").lower())


def find_duplicate_drafts(cdp, tid):
    """回读草稿箱，按标题分组，返回 {归一标题: [(article_id, 标题, raw), ...]}。

    只含重复的组。重复是怎么来的（2026-09-30 实测）：`publish --case X`
    单条重试、或 publish_all 中途被 SIGTERM 杀掉，都会在草稿箱里**再建
    一份** —— B站发布是「新建草稿」不是「更新」，所以重试必留重复。
    """
    r = _fetch_drafts(cdp, tid)
    if not isinstance(r, dict) or r.get("err") is not None:
        raise RuntimeError("草稿接口异常: %s"
                           % json.dumps(r, ensure_ascii=False)[:200])
    groups = {}
    for d in r.get("drafts") or []:
        groups.setdefault(_norm_title(d.get("title")), []).append(
            (d.get("article_id"), d.get("title") or "", d))
    return {k: v for k, v in groups.items() if len(v) > 1}


def _draft_remove(cdp, tid, article_id):
    """调官方接口删一条草稿，返回 {code, msg}。

    ⚠ CSRF：写操作必须带 `bili_jct`，否则 -111「CSRF 校验失败」
    （2026-09-30 实测）。它就在 cookie 里，但**不能**靠 fetch 的
    credentials 自动带 —— B站要求 csrf 字段出现在 body 里。
    """
    js = """(async () => {
      const m = document.cookie.match(/(?:^|;\\s*)bili_jct=([^;]+)/);
      const csrf = m ? m[1] : '';
      const r = await fetch(%s, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'article_id=' + %s + '&csrf=' + csrf
      });
      const j = await r.json();
      return JSON.stringify({code: j.code, msg: j.message});
    })()""" % (json.dumps(BILI_DRAFT_REMOVE_API), json.dumps(str(article_id)))
    out = cdp.eval(js, refresh_context=True)
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except Exception:                                # noqa: BLE001
            pass
    return out


def dedup(keep=1, dry=False, max_del=20):
    """把草稿箱里同标题的重复草稿删到剩 keep 条。

    保留策略：**优先留有封面的**（`image_urls`/`origin_image_urls` 非空），
    同为有封面或同为无封面时留 `article_id` 大的（后建的那份通常是重试时
    带最新正文存的）。⚠ 绝不要看 `banner_url` —— 草稿接口里它恒为 `""`，
    拿它判会永远误报成「都没封面」。

    2026-09-30 起**默认真删**（用户授权：删重复不用问）。`dry=True` 仍可
    用来先看清单。
    """
    _close_stale_bili_tabs(wp.CDP(PUB_PORT))
    cdp = wp.CDP(PUB_PORT)
    tid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
    cdp.connect_target(tid)
    time.sleep(6)
    try:
        dups = find_duplicate_drafts(cdp, tid)
        if not dups:
            print("✓ 草稿箱无同标题重复")
            return 0
        extra = sum(len(v) - keep for v in dups.values())
        print("发现 %d 组重复、共 %d 条多余草稿：" % (len(dups), extra))
        plan = []
        for _norm, items in sorted(dups.items()):
            # 保留策略：**有封面的优先留**，其次留 aid 大的（后建的，通常是
            # 重试成功那次，正文/封面更全）。2026-09-30 实测踩过反面：按
            # aid 最小留，结果留下 16:44 那条空白草稿，删掉了 18:01 那条
            # 带封面的 —— 封面白设了。
            items = sorted(
                items,
                key=lambda x: (0 if _cover_of(x[2]) else 1, -x[0]))
            keep_ids = [x[0] for x in items[:keep]]
            drop_ids = [x[0] for x in items[keep:]]
            print("  《%s》×%d → 保留 %s，删除 %s"
                  % (items[0][1][:40], len(items), keep_ids, drop_ids))
            for aid in keep_ids:
                d = next(x[2] for x in items if x[0] == aid)
                print("      保留 aid=%d %s"
                      % (aid, "有封面" if _cover_of(d) else "无封面"))
            plan += drop_ids
        if dry:
            print("\n[dry] 将删除 %d 条：%s" % (len(plan), plan[:20]))
            print("      确认无误后去掉 --dry 执行真删")
            return 0
        done, failed = [], []
        for aid in plan[:max_del]:
            out = _draft_remove(cdp, tid, aid)
            ok = isinstance(out, dict) and out.get("code") == 0
            print("  %s aid=%s %s" % ("✓" if ok else "✗", aid,
                                      json.dumps(out, ensure_ascii=False)[:120]))
            (done if ok else failed).append(aid)
            time.sleep(1.2)
        print("\n删除完成 %d / 失败 %d" % (len(done), len(failed)))
        if failed:
            print("失败 aid: %s" % failed)
        return 0 if not failed else 2
    finally:
        cdp.close_target(tid)


def cover_status():
    """回读远端 37 条封面状态，标出哪些没封面。"""
    _close_stale_bili_tabs(wp.CDP(PUB_PORT))
    cdp = wp.CDP(PUB_PORT)
    tid = cdp.new_target(BILI_DRAFT_LIST_PAGE)["id"]
    cdp.connect_target(tid)
    time.sleep(5)
    try:
        r = _fetch_drafts(cdp, tid)
        if not isinstance(r, dict) or r.get("err") is not None:
            print("✗ 草稿接口异常: %s" % json.dumps(r, ensure_ascii=False)[:200])
            return
        drafts = r.get("drafts") or []
        rows = [(d, _cover_of(d)) for d in drafts]
        have = [(d, u) for d, u in rows if u]
        missing = [(d, u) for d, u in rows if not u]
        print("远端 %d 条：✓有封面 %d / ✗无封面 %d"
              % (len(drafts), len(have), len(missing)))
        for d, u in have:
            print("  ✓ aid=%d %s" % (d.get("article_id"),
                                     (d.get("title") or "")[:44]))
        for d, u in missing:
            print("  ✗ aid=%d %s" % (d.get("article_id"),
                                     (d.get("title") or "")[:44]))
    finally:
        cdp.close_target(tid)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="B站专栏发布")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--all", action="store_true")
    b.add_argument("--case", default=None)
    sub.add_parser("probe")
    p = sub.add_parser("publish")
    p.add_argument("--all", action="store_true")
    p.add_argument("--case", default=None)
    p.add_argument("--dry", action="store_true")
    p.add_argument("--replace", action="store_true",
                   help="按 article_id 打开已有草稿覆盖重存（改文案后用）")
    p.add_argument("--retries", type=int, default=1,
                   help="单条失败重试次数（默认 1；批量时 CDP 超时很常见）")
    p.add_argument("--keep-tabs", action="store_true",
                   help="跑完保留浏览器 tab（人工核对用，默认清掉）")
    p.add_argument("--ui", action="store_true",
                   help="**改走 UI 路线**（开编辑器点按钮，慢但保留行内样式："
                        "粗体/颜色/居中。默认走 draft/add 接口，秒级）")
    p.add_argument("--only", default=None,
                   help="只跑这些 case id（逗号分隔），断点续传用")
    c = sub.add_parser("cover")
    c.add_argument("--all", action="store_true")
    c.add_argument("--case", default=None)
    c.add_argument("--dry", action="store_true")
    c.add_argument("--force", action="store_true",
                   help="连远端已有封面的也重设（默认只补没封面的）")
    sub.add_parser("cover-status")
    d = sub.add_parser("dedup", help="草稿箱同标题去重（默认直接删，只留清单）")
    d.add_argument("--delete", action="store_true",
                   help="兼容旧调用（现在默认就删，这个开关是冗余的）")
    d.add_argument("--dry", action="store_true",
                   help="只列清单不删。删草稿不可逆，确认清单时用")
    d.add_argument("--keep", type=int, default=1,
                   help="同标题保留几条（默认 1，即删到只剩一条）")
    d.add_argument("--max-del", type=int, default=20)
    args = ap.parse_args()

    # 127.0.0.1 必须绕开系统代理，否则 websocket 连 Chrome 调试端口会被掐
    # （WinError 10053）。本脚本直接 import wechat_publish 复用 wp.CDP，从不走
    # wp.main()，所以这里要自己清一遍——否则后台跑批次时本地 CDP 连接全挂。
    for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(_k, None)
    _np = os.environ.get("no_proxy", "")
    if "127.0.0.1" not in _np:
        os.environ["no_proxy"] = ("127.0.0.1,localhost"
                                   + ("," + _np if _np else ""))

    # ⚠ 走 CDP 的子命令共用这一个自动启动入口（清代理 → 删 LOCK →
    # 带 --no-sandbox 起 Chrome for Testing → 轮询端口）。
    # 不做这步的话，Chrome 没起时只会报「连不上 9222」让人自己去开浏览器。
    # `build` 是纯离线（只拼 HTML），不需要浏览器。
    if args.cmd != "build":
        try:
            # ⚠ B站的登录态在 `chrome-debug-profile`，**不是**公众号那个
            # ChromeCDP。拿错 profile 的症状：CDP 连得上、页面能开，
            # 但被重定向到登录页 ⇒ 看着像掉登录，实际是开错浏览器。
            import chrome_cdp_launch as _L
            wp._autostart_chrome_if_needed(PUB_PORT, profile=_L.PLAT_PROFILE)
        except SystemExit:
            raise
        except Exception as e:
            print("[warn] autostart 失败: %s" % e)

    if args.cmd == "build":
        if args.case:
            build_one(args.case)
        else:
            build_all()
    elif args.cmd == "probe":
        probe()
    elif args.cmd == "publish":
        use_api = not args.ui
        if args.case:
            publish_one(args.case, dry=args.dry, replace=args.replace,
                        keep_tabs=args.keep_tabs, use_api=use_api)
        else:
            only = ([x.strip() for x in args.only.split(",") if x.strip()]
                    if args.only else None)
            _ok, failed = publish_all(replace=args.replace,
                                      retries=args.retries, only=only,
                                      use_api=use_api)
            if failed:
                sys.exit(1)
    elif args.cmd == "cover":
        if args.case:
            cover_one(args.case, dry=args.dry, force=args.force)
        else:
            cover_all(only_missing=not args.force, force=args.force)
    elif args.cmd == "cover-status":
        cover_status()
    elif args.cmd == "dedup":
        # 2026-09-30 用户授权：删重复不用再问，默认直接删。--dry 仍保留
        # 用来「先看清单再删」，--delete 变成兼容旧调用的冗余开关。
        dedup(keep=args.keep, dry=args.dry, max_del=args.max_del)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
