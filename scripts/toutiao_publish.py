#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条号（mp.toutiao.com）发布链路 —— 与公众号 / 小红书同一条 CDP 路子。

为什么只能走浏览器自动化
------------------------
查过官方与第三方资料（2026-09）：头条号**没有面向个人创作者的图文发布 API**。
字节的内容发布接口挂在开放平台体系下，要求认证主体（企业/媒体/粉丝≥1000 的
个人认证号）+ 单独开通「文章发布」「图片上传」权限；普通未认证账号即使走完
OAuth 也会返回「权限不足」。所以这里跟公众号、小红书一样：复用本机已登录
Chrome 的登录态，用 CDP 把稿子填进发文页，默认**只存草稿**。

前置（一次性）
------------
在本机调试 Chrome（9222，用户目录 C:\\Users\\DELL\\chrome-debug-profile）里
登录一次头条号：mp.toutiao.com → 扫码 / 抖音 / 微信 / QQ 登录。登录态在
profile 里，之后脚本一直能用；清缓存会掉，掉了重新扫码。

子命令
------
  build     生成 out/toutiao/<id>/{article.html, meta.json}（正文 = 长文改写）
  queue     待发队列（不连浏览器）
  publish   打开发文页填标题 + 正文，默认存草稿（--yes 才点「发布」）
  drafts    从「内容管理」草稿列表反查，重建 data/toutiao_drafts.json
  probe     导出发文页的 input / contenteditable / 按钮文本，用来校准选择器

⚠️ 发文页选择器是**实验性**的：头条后台改版频繁，登录后用 `probe` 校一遍再跑
publish。首次建议 `publish --case <id> --dry`（只在编辑页填好，不点任何按钮）。
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402  复用 CDP / cases 加载 / 公众号标题
import xhs_publish as x      # noqa: E402  复用模糊匹配

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "toutiao")
ARTICLES_DIR = os.path.join(ROOT, "out", "articles")
XHS_DIR = os.path.join(ROOT, "out", "xhs")       # 卡片图在这儿，封面直接复用
# 头条封面位是横版裁切（列表缩略图 ≈16:9），小红书竖版 card-1（3:4）会被
# 裁到只剩中间一条留白 → 必须用专门的横版封面（2026-09-27 实测）。
TT_COVER_W, TT_COVER_H = 1920, 1080              # 16:9，截图时 scale=2 → 3840x2160
TITLE_OVERRIDES_PATH = os.path.join(ROOT, "data", "xhs_title_overrides.json")
DRAFT_FILE = os.path.join(ROOT, "data", "toutiao_drafts.json")
PUB_FILE = os.path.join(ROOT, "data", "toutiao_published.json")

# ⚠ 端口必须与 profile 配对：**9222 是公众号的 ChromeCDP**，
# 头条的登录态在 `chrome-debug-profile` ⇒ 用 9223。
# 抢同一个端口会连上别人的浏览器，症状是「被重定向到登录页」。
CDP_PORT = int(os.environ.get("CDP_TOUTIAO_PORT", 9223))

TITLE_MAX = 30          # 头条标题硬上限（实测后台 30 字，超了会被截断/拦）
BODY_MIN = 300          # 头条推荐 1000+ 字，低于这个数建议别发

TT_HOME = "https://mp.toutiao.com/"
# 发文页（创作端「写文章」）。2026-09-27 实测：新版创作端的发文页是
#   /profile_v4/graphic/publish
# 老写法 /profile_v4/graphic/articles 是**文章列表**（只有搜索框，没有编辑器），
# 用它会静默「填不进去」。另有 /creator/publish、/publish/article 等候选均无编辑器。
# 页面上有：标题 = textarea[placeholder*=文章标题]，正文 = .ProseMirror[contenteditable=true]
TT_EDITOR = "https://mp.toutiao.com/profile_v4/graphic/publish"
# 草稿箱列表页。2026-09-27 实测只有 /profile_v4/manage/draft 能出列表，
# manage/content、graphic/draft、draft 等落点都是空壳（只有导航菜单）。
TT_DRAFT = "https://mp.toutiao.com/profile_v4/manage/draft"
# 作品管理（已发布/已发内容都在这）——草稿箱里没有的稿走这里改
TT_WORKS = "https://mp.toutiao.com/profile_v4/manage/content/all?enter_from=left_menu"
# 「文章」tab 落点（已发布图文列表页）。2026-10-16 P1 实证：该 tab 的
# 列表数据走 /api/feed/mp_provider/v1/（见 ARTICLE_API_TPL），页面本身
# 是懒加载 + JS 路由（卡片 a 无 href），UI 收集只能逐页翻。
TT_ARTICLES_PAGE = "https://mp.toutiao.com/profile_v4/graphic/articles"
# 已发布图文列表 API（2026-10-16 P1 实证，用户提供 URL）。
# ⚠ 只能浏览器导航式访问（见 _article_api_url docstring）。
# @OFF@=offset（0/10/20...）@PG@=page_index（同步递增）
ARTICLE_API_TPL = (
    "https://mp.toutiao.com/api/feed/mp_provider/v1/"
    "?provider_type=mp_provider&aid=13&app_name=news_article"
    "&category=mp_article&channel=&stream_api_version=88"
    "&genre_type_switch=%7B%22repost%22%3A1%2C%22small_video%22%3A1"
    "%2C%22toutiao_graphic%22%3A1%2C%22weitoutiao%22%3A1"
    "%2C%22xigua_video%22%3A1%7D"
    "&device_platform=pc&platform_id=0&visited_uid=3986156894822883"
    "&offset=@OFF@&count=10&keyword="
    "&client_extra_params=%7B%22category%22%3A%22mp_article%22"
    "%2C%22real_app_id%22%3A%221231%22%2C%22need_forward%22%3A%22true%22"
    "%2C%22offset_mode%22%3A%221%22%2C%22page_index%22%3A%22@PG@%22"
    "%2C%22status%22%3A%220%22%2C%22source%22%3A%220%22%7D"
    "&app_id=1231")

# 草稿箱整页是否已展开（进程内状态，见 open_draft_editor 里的说明）。
# 只在「本次进程的草稿箱 tab 上」成立，换页/刷新后要手动清。
_draft_box_expanded = {"v": False}

# ---- 选择器（实验性，probe 后可改这里）-------------------------------------
SEL = {
    "title": ("textarea[placeholder*='文章标题'], "
              "input[placeholder*='文章标题'], "
              "textarea[placeholder*='标题'], input[placeholder*='标题']"),
    "body": (".ProseMirror[contenteditable='true'], "
             "[contenteditable='true'], .ProseMirror"),
}


# ======================================================================
# 纯函数区（可单测，不碰浏览器）
# ======================================================================
def load_cases():
    return wp.load_cases()


def find_case_fuzzy(q):
    return x.find_case_fuzzy(q)


def _title_override(cid):
    """AI 改写过的短标题（小红书那份），头条复用它再补信息，≤20 字。"""
    try:
        with open(TITLE_OVERRIDES_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                                 # noqa: BLE001
        return ""
    v = d.get(cid)
    if isinstance(v, list):
        v = v[0] if v else ""
    return (v or "").strip()


_TITLE_TAIL_JUNK = re.compile(r"[\s—–:：,，、.。!！?？|/\\-]+$")


def _truncate_title(title, limit):
    """按头条 30 字上限截断，且不留「——一」式残渣。

    2026-10-17：旧逻辑 `cand[:TITLE_MAX]` 硬切，实测把
    「…转成 WordPress——一键迁移」切成「…WordPress——一」
    （切在破折号后一个字），服务端原样存成丑标题留在草稿箱。
    两条规则：
      ① 截断面落在孤立标点/空白上 ⇒ 清掉；
      ② 截断面落在连接号（——/—/-）后一两个字上 ⇒ 连接号带残字一起清，
         干净收在前半句（仅当原标题真的超 limit，不误伤完整的「A——B」）。
    """
    t = (title or "")[:limit]
    t = _TITLE_TAIL_JUNK.sub("", t)                    # ①
    if len(title or "") > limit:                       # ②
        m = re.search(r"[-—–]{1,2}.{0,2}$", t)
        if m:
            t = t[:m.start()]
    return t or (title or "").strip()[:limit]


def make_toutiao_title(c, hits_out=None):
    """头条标题：≤30 字，优先 AI 改写过的短标题，否则公众号标题，再兜名字。

    头条的推荐逻辑偏好「信息量 + 具体数字」，所以能带上金额就带。
    `hits_out` 是个可选列表，-neutralize 换掉的敏感词会填进去（给 build 提示用）。
    """
    cid = c.get("id", "")
    cand = _title_override(cid)
    if not cand:
        try:
            cand = (wp.make_wechat_title(c) or "").strip()
        except Exception:                             # noqa: BLE001
            cand = ""
    if not cand:
        cand = "%s：%s" % ((c.get("name") or "").strip(),
                          (c.get("one_liner") or "").strip())
    cand = re.sub(r"\s+", " ", cand).strip(" ：:-—|")
    cand = x.correct_title_metric(cand, c)        # ARR 误标「月收」→「年收」
    cand, hits = neutralize_risk(cand)
    if hits_out is not None:
        hits_out.extend(hits)
    return _truncate_title(cand, TITLE_MAX)


def find_case_for_title(title):
    """线上草稿标题 → 本地 case（产品名前缀匹配，refresh 子命令用）。

    只取「第一个中/英文冒号前的串」与 case name 全称或首词比对。
    🚨 **不能按空格切**：「Pet Plus Us：…」按空格切会碎成 "pet" 而匹配不上
    （2026-10-17 实测踩过，害得 6 条无主稿人工认领）。
    匹配不上返 None（调用方按无主稿单列，别硬猜）。
    """
    t = (title or "").strip()
    if not t:
        return None
    head = re.split(r"[：:]", t, maxsplit=1)[0].strip().lower()
    if not head:
        return None
    cases = load_cases()
    for c in cases:
        if (c.get("name") or "").strip().lower() == head:
            return c
    for c in cases:
        first = re.split(r"[\s.\-–—]+",
                         (c.get("name") or "").strip().lower(), maxsplit=1)[0]
        if len(first) >= 5 and first == head:
            return c
    return None


def fix_arr_wording(title, c):
    """兼容旧名：口径纠正委托给 xhs_publish.correct_title_metric。"""
    return x.correct_title_metric(title, c)


def amount_warn(c, title):
    """标题金额与 metrics 对不上时返回提示字符串，否则 None。"""
    m = c.get("metrics") or {}
    g = re.search(r"\$([\d.,]+)([KM]?)", title or "")
    if not g:
        return None
    try:
        v = float(g.group(1).replace(",", "")) * {"": 1.0, "K": 1e3, "M": 1e6}[
            g.group(2).upper()]
    except (ValueError, KeyError):
        return None
    # 金额在 headline/growth 原文里出现过 = 口径对得上（很多案例只有文字口径，
    # 数字字段是另一回事，比如 comp-ai：MRR $12K，但单月流水 $540K）。
    blob = " ".join(str(m.get(k) or "") for k in
                    ("headline", "growth", "metric_note"))
    if g.group(0).lstrip("$") in blob:
        return None
    cand = [m.get(k) for k in ("mrr", "last_30d_revenue", "all_time", "arr")]
    cand = [x for x in cand if isinstance(x, (int, float)) and x > 0]
    if not cand:
        return None
    if not any(abs(v - x) <= max(1.0, abs(x) * 0.05) for x in cand):
        return "标题金额 $%s 与 metrics 对不上（%s）" % (
            g.group(1) + g.group(2), (m.get("headline") or "")[:40])
    return None


_URL_RE = re.compile(r"https?://\S+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+$")       # |---|---| 表格对齐行
_STAR_RE = re.compile(r"(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)", re.S)
_KV_RE = re.compile(r"^([^：:]{1,12}[：:])\s*(.*)$", re.S)

# 表格文本超过这个长度就不进表格（2026-10-09）。
# 数据分布：735 个单元格里 735 个标签列 ≤12 字；值列 622 个 ≤12 字、
# 92 个 13–60 字、**21 个 >60 字（最长 184）**。超长的几乎全是
# 「备注」行的口径说明段落（「TrustMRR 直连验证。MRR 为当月经常性收入…」），
# 挤进窄表格会把行高撑得参差、窄列里中文长句折行很难看。
# ⇒ 阈值行整行搬出去，渲成表格后面的普通段落（实测 41 篇共 21 处）。
# 60 是按分布定的：13–30 字（40 个）留在表里正好一行，31–60 字（52 个）
# 多数也能一行，只有真正的说明段落（>60）才搬走。
CELL_LONG = 60

# 占位表头标签 —— 整行丢掉（见 build_table）。
# 全库119 张表的表头都是「维度 | 内容」（`make_article._flush_md_rows` 生成的）。
# ⚠ 别把「客户」「团队」「增长」「备注」这些**真数据行的标签**放进来。
_HEADER_LABELS = ("维度", "内容", "项目", "指标")


def strip_md(s):
    """去掉 markdown 强调符号，用于纯文本派生（字数统计 / 敏感词扫描）。"""
    return _STAR_RE.sub(r"\1", (s or "").replace("`", ""))


def clean_line(s):
    """去掉外链 / markdown 链接 —— 头条正文里外链会被拦或降权。

    注意：**别删 `**` 加粗标记**，它在 blocks_to_html 里要转成 <strong>；
    需要纯文本时用 strip_md()。
    """
    s = _MD_LINK_RE.sub(r"\1", s or "")
    s = _URL_RE.sub("", s)
    s = s.replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def _flush_para(blocks, para):
    """把攒着的段落行收成一块；空则什么都不做（原地清空 para）。"""
    if para:
        blocks.append(("p", clean_line(" ".join(para))))
        para.clear()


def _table_rows(line):
    """一行 markdown 表格 → 单元格列表；不是表格行返回 None。

    空单元格被丢掉（`|a||b|`），所以**各行列数可能不齐** ——
    `build_table` 按最长行补齐，别假设整齐。
    """
    s = (line or "").strip()
    if not s.startswith("|"):
        return None
    cells = [c.strip() for c in s.strip("|").split("|")]
    cells = [c for c in cells if c]
    return cells or None


def build_table(rows):
    """连续表格行 → ("table", {...}) 载荷；没有有效表格行返回 None。

    产出 `{"rows": [[c, c], ...], "long": [(标签, 值), ...]}`：
      · `rows`  —— **短值**行，进真表格（已剥掉表头）；
      · `long`  —— 超长值行（见 `CELL_LONG`），转成表格后面的段落。

    ⚠ **表头行被丢掉**：全库 119 张表的表头都是 `| 维度 | 内容 |`
    （旧代码也用 `cells[0] != "维度"` 硬丢），这两列名对读者零信息量，
    留在表里就是一行废话。`<th>` 实测也会被降级成 `<td>`，所以不损失样式。

    单列行不算表格（`|备注长文……|` 这种），返回 None 交回给段落路径。
    """
    rows = [r for r in rows if r]
    if not rows:
        return None
    # 剥表头：第一行是「维度 | 内容」这类占位列名时丢掉。
    # ⚠ 只认**恰好**是这两个词或这组词，别把「客户 | 团队」这种真数据行误伤。
    if rows[0][0] in _HEADER_LABELS:
        rows = rows[1:]
    rows = [r for r in rows if r]
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    if ncol < 2:
        return None
    short, long_rows = [], []
    for r in rows:
        r = list(r) + [""] * (ncol - len(r))
        if max(len(c) for c in r) > CELL_LONG:
            # 整行都是长文（如「备注」的口径说明段落）→ 不挤进窄表格
            long_rows.append((r[0], "".join(r[1:]).strip()))
            continue
        short.append(r)
    return {"rows": short, "long": long_rows}


def md_to_blocks(md):
    """公众号长文 markdown → [(kind, text)]，kind ∈ h2 / p / ul / table / quote。

    **表格保留成真表格**（2026-10-09 改）。此前这里把表格行降级成
    「标签：值」再由 `blocks_to_html` 渲成 `<ul><li>`，理由写的是
    「头条编辑器不支持 markdown 表格」—— **那个结论是错的**，
    它来自一次只扫 title/aria-label/innerText 的探针，漏了 class：

        syl-toolbar-tool table static table-menu-button-before
        syl-table-menu-wrapper / -list / -size-tip …

    头条工具栏**有**表格按钮（还带行列选择菜单）。实测 `insertHTML`
    灌 `<table>` 会被完整保留，并规范化成
    `<div class="tableWrapper"><table><colgroup>…<td><p>…</p></td>`；
    **存草稿重开后 12 个单元格一个不少**（2026-10-09 实测）。
    ⇒ 真表格可用，别再降级成列表。
    """
    blocks = []
    para = []
    trows = []                                    # 正在攒的连续表格行
    in_comment = False                            # 多行 <!-- ... --> 注释块
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        # ---- 表格行：先攒着，遇非表格行才结算 ----
        if s.startswith("|"):
            if _TABLE_SEP_RE.match(s) and "-" in s:       # 对齐行吃掉，不算数据
                continue
            cells = _table_rows(s)
            if cells:
                trows.append(cells)
            continue
        if trows:                                    # 攒到这儿就结算
            _flush_para(blocks, para)
            tb = build_table(trows)
            if tb:
                blocks.append(("table", tb))
            trows = []
        if not s:
            _flush_para(blocks, para)
            continue
        if s.startswith("<!--"):                  # 模板注释，整块丢掉
            # 单行 <!-- x --> 一次跳过；跨行的要一直吃到 --> 为止，
            # 否则「核对用来源（发布前逐个点开确认，别进正文）：…」会漏进正文。
            if "-->" not in s:
                in_comment = True
            continue
        if s.startswith("# "):                    # h1 = 标题，正文里不要
            continue
        if s.startswith("## "):
            _flush_para(blocks, para)
            blocks.append(("h2", clean_line(s[3:])))
            continue
        if _HR_RE.match(s):                       # --- 分隔线（区别于表格对齐行）
            _flush_para(blocks, para)
            blocks.append(("hr", ""))
            continue
        if s.startswith(">"):
            _flush_para(blocks, para)
            blocks.append(("quote", clean_line(s.lstrip("> "))))
            continue
        if re.match(r"^[-*]\s+", s):
            _flush_para(blocks, para)
            blocks.append(("ul", clean_line(re.sub(r"^[-*]\s+", "", s))))
            continue
        para.append(s)
    if trows:                                       # 撞到文件末尾的表格
        _flush_para(blocks, para)
        tb = build_table(trows)
        if tb:
            blocks.append(("table", tb))
    _flush_para(blocks, para)
    # hr 没有文本内容，不能被下面的「去空文本」过滤器吃掉
    return [b for b in blocks if b[1] or b[0] == "hr"]


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;")


def inline(s):
    """行内 markdown → 头条认得的 HTML：**加粗** → <strong>。

    头条编辑器实测（2026-09-28）**不支持 em / u**，所以 `*斜体*` 一律
    降级成纯文字（只去符号，不套标签），免得生成一堆被丢掉的标签。
    """
    s = s or ""
    out = []
    last = 0
    for m in _STAR_RE.finditer(s):
        if m.start() > last:
            out.append(esc(strip_md(s[last:m.start()])))
        out.append("<strong>%s</strong>" % esc(strip_md(m.group(1))))
        last = m.end()
    tail = esc(strip_md(s[last:]))
    if tail:
        out.append(tail)
    return "".join(out)


def split_kv(text):
    """「付费意愿：3/5」 → ("付费意愿：", "3/5")；没有冒号返回 ("", text)。"""
    m = _KV_RE.match(text or "")
    return (m.group(1), m.group(2)) if m else ("", text)


FOOTER_NOTE = "关于本栏目"
FOOTER_TEXT = ("「拆解海外」逐个拆海外小生意：它干什么、钱从哪来、做到多大、"
               "为什么能成、哪些能搬回国内。数据均来自公开披露，收入口径按原文"
               "照实标注（MRR 就写 MRR，只有流水就写近 30 天收入），不做换算夸大。")


def table_to_html(tb):
    """表格载荷 → 头条认得的真表格 HTML。

    ⚠ 写法是**照着实测落地结果反推的**，不是想当然：
    - 外层 `div.tableWrapper` —— 编辑器自己就是这么包的；
    - `<tbody>` 必带，`<th>` 会被降级成 `<td>`（实测），所以表头不用；
    - `<td>` 里是 `<p>`，裸文本也会被规范化成 `<p>`，但写出来更稳；
    - `colgroup` / `style` / `data-colwidth` **一律不给** —— 编辑器按内容
      自己算列宽（实测它会补`min-width` 和 `data-colwidth`），给了反而多一份
      要被清洗的样式。

    超长值行不在这里 —— 它们由 `build_table` 拆出去，
    由 `blocks_to_html` 紧跟表格渲成普通段落。
    """
    rows = tb.get("rows") or []
    if not rows:
        return ""
    trs = []
    for r in rows:
        tds = "".join("<td><p>%s</p></td>" % inline(c) for c in r)
        trs.append("<tr>%s</tr>" % tds)
    return '<div class="tableWrapper"><table><tbody>%s</tbody></table></div>' \
           % "".join(trs)


def blocks_to_html(blocks, footer=True, footer_text=None):
    """块 → 头条编辑器的富文本 HTML。

    头条 ProseMirror schema 实测（2026-09-28 探针+ 2026-10-09 补测）：
      ✔ h1/h2/h3 → 统一渲染成 <h1 class="pgc-h-forward-slash"> 装饰标题
      ✔ ul+li / ol+li / li 内 <strong> / blockquote / hr
      ✔ table（2026-10-09 补：工具栏 `syl-toolbar-tool table`，见 `md_to_blocks`）
      ✘ em、u —— 会被静默丢掉，别用

    所以这里**把 markdown 里本来就有、但被之前版本拍平的结构还原回来**：
      二级标题 → <h2>；列表 → 真 <ul><li>；引用 → <blockquote>；
      连续的表格行 → 一整张 `<table>`（超长行拆成后面的段落）；
      --- → <hr>。连续的同类块会被合并成一个 <ul>，视觉上是一组而不是 N 段。

    ⚠⚠ **块之间必须用 `""` 拼，不能用换行**（2026-10-09 实测）：
    Tiptap 把标签间的 `\n` 当成额外空段落 —— 同一份内容
    `"\n".join` 渲出 3 个空 `<p>`，`"".join` 只有 2 个（首尾那两个是
    ProseMirror 自带的，无法消除）。B站那边早就有同样的结论
    （`>[ \t\r\n]+<` → `><`，见 docs/MULTIPLATFORM.md），头条之前漏了。
    """
    out = []
    i, n = 0, len(blocks)
    while i < n:
        kind, text = blocks[i]
        if kind == "h2":
            out.append("<h2>%s</h2>" % inline(text))
            i += 1
        elif kind == "hr":
            out.append("<hr>")
            i += 1
        elif kind == "ul":
            items = []
            while i < n and blocks[i][0] == "ul":
                items.append("<li>%s</li>" % inline(blocks[i][1]))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
        elif kind == "table":
            html = table_to_html(text)
            if html:
                out.append(html)
            else:
                out.append("")                # 全是长行 → 只剩下面的段落
            # 表格后面紧跟被拆出来的长值段落（「备注：…」）
            for lab, val in (text.get("long") or []):
                out.append("<p><strong>%s</strong>%s</p>"
                           % (esc("%s：" % lab), inline(val)))
            i += 1
        elif kind == "quote":
            t = inline(text)
            # 原文自带「」就别套两层
            out.append("<blockquote>%s</blockquote>"
                       % (t if (text or "").startswith("「") else "「%s」" % t))
            i += 1
        else:
            out.append("<p>%s</p>" % inline(text))
            i += 1
    if footer:
        out.append("<hr>")
        out.append("<h2>%s</h2>" % FOOTER_NOTE)
        out.append("<p>%s</p>" % inline(footer_text or FOOTER_TEXT))
    # ⚠ 空串不能留 —— 它在"".join 里无害，但会让下面这个判断失真
    out = [o for o in out if o]
    #⚠ "" 拼接，不是 "\n"（见 docstring：换行会变空段落）
    return "".join(out)

# 头条审核/推荐的敏感词 → 中性替身。**标题命中就自动换掉**，不留人工判断：
# 「躺着收租」这类词看着有味道，但头条推荐会把「躺」系关键词当低质收益噱头压权。
# 长词排前面，避免「稳赚不赔」被换成「稳赚」后残留。
RISK_REPLACE = {
    "躺赚": "被动收入", "躺收": "被动收入", "躺着收租": "收租", "躺着": "自动化",
    "暴利": "高利润", "稳赚不赔": "长期稳定", "稳赚": "长期稳定",
    "月入过万": "月收入过万", "零成本": "低成本", "无脑": "轻松",
    "震惊": "意外", "必看": "值得看", "第一": "领先", "最全": "完整", "最强": "成熟",
}
RISK_WORDS = sorted(RISK_REPLACE, key=len, reverse=True)
# 正文里这些词多半是原文引用（「业界第一」「零成本工具」），只在 build 时提示。
BODY_RISK_WORDS = [w for w in RISK_WORDS if w not in ("第一", "最全", "最强")]


def risk_words(text, words=None):
    return [w for w in (words or RISK_WORDS) if w in (text or "")]


def neutralize_risk(title):
    """标题敏感词中性化。返回 (新标题, 换掉的词列表)。"""
    out, hits = title or "", []
    for w in RISK_WORDS:
        if w in out:
            out = out.replace(w, RISK_REPLACE[w])
            hits.append(w)
    return out, hits


def blocks_plain_text(blocks):
    """块 → 纯文本（字数统计 / 敏感词扫描用）。

    ⚠ 表格块的载荷是 **dict** 不是 str（2026-10-09 改），
    直接 `"\n".join(t for _, t in blocks)` 会 TypeError。
    表格按「标签 值」逐行摊平，单元格之间用空格拼，量级与视觉一致。
    """
    parts = []
    for kind, t in blocks:
        if kind == "table":
            for r in (t.get("rows") or []):
                parts.append(" ".join(c for c in r if c))
            for lab, val in (t.get("long") or []):
                parts.append("%s %s" % (lab, val) if lab else val)
        else:
            parts.append(t)
    return "\n".join(p for p in parts if p)


def build_article(c):
    """把一个 case 变成头条稿。返回 meta dict（不写盘）。"""
    cid = c["id"]
    md_path = os.path.join(ARTICLES_DIR, cid + ".md")
    md = ""
    if os.path.isfile(md_path):
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
    blocks = md_to_blocks(md)
    plain = strip_md(blocks_plain_text(blocks))
    hits = []                                     # 被自动中性化的敏感词
    title = make_toutiao_title(c, hits_out=hits)
    # 2026-10-01：栏目页脚覆盖。
    # 默认页脚是「「拆解海外」逐个拆海外小生意：它干什么、钱从哪来、做到多大…」——
    # 对工具推广/使用类内容是错的（这篇不是海外小生意，还教读者「数据来自公开披露」，
    # 而实测数据来自公开接口）。不填则逐字节保持原样（39 条不受影响）。
    html = blocks_to_html(blocks, footer_text=c.get("footer_text"))
    return {
        "id": cid,
        "name": c.get("name", ""),
        "title": title,
        "risk_fixed": hits,
        "body_risk": risk_words(plain, BODY_RISK_WORDS),
        "chars": len(plain),
        "blocks": len(blocks),
        "html": html,
        "plain": plain,
        "cover": os.path.join(ROOT, "out", "xhs", cid, "card-1.png"),
    }


def write_article(meta, outdir=None):
    d = outdir or os.path.join(OUT, meta["id"])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "article.html"), "w", encoding="utf-8") as f:
        f.write("<h1>%s</h1>\n%s\n" % (esc(meta["title"]), meta["html"]))
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in meta.items() if k != "html"},
                  f, ensure_ascii=False, indent=2)
    return d


# ======================================================================
# 记录 / 队列
# ======================================================================
def _ids(path):
    if not os.path.isfile(path):
        return []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:                                 # noqa: BLE001
        return []
    if isinstance(d, dict):
        return list(d.keys())
    return [x.get("id") for x in d if isinstance(x, dict) and x.get("id")]


def _mark(path, cid):
    """把案例追加进台账（草稿 / 已发布共用）。

    ⚠⚠ 2026-10-14 修「台账历史被毁」：旧实现 `_ids()` 只取 id、
       丢掉每条原有的 `at`，追加一条就把**整本台账的发布时间**
       全部重写成当前时间（实测：追 1 条 macwall，9 条 9-28 的
       老记录全变成当天时间）⇒ 现在保留旧条目原值，只给新条目
       打时间戳。
    ⚠ 行尾按原文件走：`toutiao_published.json` 是 LF、
       `toutiao_drafts.json` 是 CRLF（本仓库两种并存），
       写错行尾 = git 里多一份全文件 diff。
    ⚠ `newline=""` 必须显式给：Windows 文本模式会把 \n 翻成
       \r\n，行尾控制权就没了。
    """
    raw = b""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            raw = f.read()
    lf = raw.count(b"\n") - raw.count(b"\r\n")
    crlf = raw.count(b"\r\n")
    nl = "\r\n" if crlf > lf else "\n"
    try:
        entries = json.loads(raw.decode("utf-8")) if raw.strip() else []
    except Exception:                                # noqa: BLE001
        entries = []
    if not isinstance(entries, list):
        entries = []
    now = time.strftime("%Y-%m-%d %H:%M")
    norm = []
    for e in entries:
        if isinstance(e, dict) and e.get("id"):
            norm.append({"id": e["id"],
                         "at": e.get("at") or now})
        elif isinstance(e, str) and e:
            norm.append({"id": e, "at": now})
    if any(e["id"] == cid for e in norm):
        return
    norm.append({"id": cid, "at": now})
    text = json.dumps(norm, ensure_ascii=False, indent=2)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", nl))


def mark_drafted(cid):
    _mark(DRAFT_FILE, cid)


def mark_published(cid):
    _mark(PUB_FILE, cid)


def pending_cases(need_built=True):
    """待发队列：没进过头条的案例，库里最新的排最前。"""
    skip = set(_ids(DRAFT_FILE)) | set(_ids(PUB_FILE)) | {"voklit"}
    built = set()
    if os.path.isdir(OUT):
        for d in os.listdir(OUT):
            if os.path.isfile(os.path.join(OUT, d, "meta.json")):
                built.add(d)
    todo = [c for c in load_cases() if c["id"] not in skip]
    if need_built:
        todo = [c for c in todo if c["id"] in built]
    return todo[::-1]


# ======================================================================
# 浏览器侧
# ======================================================================
LOGIN_HINT = """
✗ 这个 Chrome 里没有头条号登录态（页面被重定向到登录页）。
  请在调试 Chrome（9222）里打开 https://mp.toutiao.com/ 手动登录一次
  （扫码 / 抖音 / 微信 / QQ 都行），登录态会留在 profile 里，之后脚本一直能用。
  起调试 Chrome：
    "C:/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9222 ^
      --user-data-dir="C:\\Users\\DELL\\chrome-debug-profile" --no-first-run ^
      --no-default-browser-check --remote-allow-origins=*
"""


def _open(url, wait=8):
    cdp = wp.CDP(CDP_PORT)
    t = cdp.new_target(url)
    if not cdp.connect_target(t["id"]):
        raise SystemExit("连不上新标签页：%s" % url)
    time.sleep(wait)
    return cdp, t


def _check_login(cdp):
    href = cdp.eval("location.href") or ""
    if "/auth/page/login" in href or "login" in href.lower():
        raise SystemExit(LOGIN_HINT)
    return href


def _focus_and_type(cdp, sel, text):
    """点中元素 → insertText（Vue/React 受控输入框也认这种真输入）。"""
    r = cdp.eval("""(function(){
      var el = document.querySelector(%s);
      if (!el) return 'notfound';
      el.scrollIntoView({block:'center'});
      var b = el.getBoundingClientRect();
      return JSON.stringify({x: b.x + b.width/2, y: b.y + b.height/2});
    })()""" % json.dumps(sel))
    if not r or r == "notfound":
        return False
    pos = json.loads(r)
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
              "button": "left", "clickCount": 1})
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
              "button": "left", "clickCount": 1})
    time.sleep(0.5)
    cdp.send("Input.insertText", {"text": text})
    time.sleep(0.8)
    return True


_NUM_BUDGET = 12   # 封面大数字最大长度（字符数），超了逐段丢弃


def tt_cover_num(headline):
    """封面大数字：超预算时分段丢弃（;/，/斜杠都算分段符），不出半截文案。

    修掉旧版 `_clip` 硬截留下的「…$4,985,」尾逗号；大数去千分位逗号再试。
    """
    h = (headline or "").split("（")[0].strip()
    if not h:
        return ""
    n = x.cover_headline(h, budget=_NUM_BUDGET)
    if n:
        return n
    segs = [s.strip() for s in re.split(r"[;；，/／]", h) if s.strip()]
    out = ""
    for s in segs:
        cand = (out + " / " + s) if out else s
        if len(cand) <= _NUM_BUDGET:
            out = cand
    if out:
        return out
    for s in segs:                     # 大数去掉千分位逗号再试（$3,569,654→$3569654）
        s2 = s.replace(",", "")
        if len(s2) <= _NUM_BUDGET:
            return s2
    return re.sub(r"[，、；,;/／\s]+$", "", x._clip(h, _NUM_BUDGET))


def tt_cover_desc(c):
    """转发到 wechat_publish.cover_desc()（公共实现，见那里的取值优先级）。

    保留这个别名是因为 toutiao_publish 内部多处按 `tt_cover_*` 命名调用，
    改名会散落到很多处；实质逻辑只有一份。
    """
    return wp.cover_desc(c)


def tt_cover_html(c):
    """头条专用横版封面（16:9）：**项目名 + 一句话介绍**，不含金额。

    设计目标「缩略图可读」：草稿箱/推荐频道缩略图只有 ~100px 宽（源图的
    1/20），2026-09-29 二次重做 —— 旧版用主题深色底 + 亮色特大数字 + 白色
    特大产品名 + 顶部「万物解释者·拆解海外」品牌行 + 一句话介绍，但头条推
    荐卡片是横版缩略图，会按一定比例裁切：左边距、左对齐的文字会被切掉。
    新版把两行文字**水平居中**，缩字号，砍掉在缩略图下不可见的小字装
    饰——无论怎么裁都可读。

    2026-10-02：原先两行是「金额 + 产品名」，去掉金额。金额是抓取快照，
    会过期；产品名 + 它是干什么的才是稳定信息（与公众号封面口径一致）。
    """
    base, band, ac = wp._cover_theme(c.get("id", ""), "toutiao")
    name = (c.get("name") or c.get("id") or "").strip()
    desc = tt_cover_desc(c)
    # 2026-10-01 的 cover_kv 覆盖机制保留（非营收类内容用），
    # 但 num 语义已变：现在承载的是「介绍」而非金额。
    ck = c.get("cover_kv") or {}
    if ck.get("num"):
        desc = ck["num"]
    if ck.get("name"):
        name = ck["name"]
    # 没有介绍就不渲染这一行（不是渲染空 div —— 那样会留下多余行间距）
    desc_block = ('<div class="desc fit">%s</div>' % esc(desc)) if desc else ""
    # 字号按文案长度自适应 —— 固定字号在缩略图里要么看不清、要么被
    # max-height 静默截断（实测「108px 项目名 + 46px 描述」在 1920 宽画布
    # 的缩略图里项目名几乎看不见，而 57 字描述在 46px×2.9em 下会溢出）。
    # 目标：项目名在缩略图里仍是第一眼，描述完整可读不截断。
    # 字号按文案长度自适应。目标（按 1920×1080 画布、列表缩略图 ~100px 宽）：
    #   · 项目名在缩略图里是第一眼，不能因为描述长就退到看不清
    #   · 描述完整可读、不被 max-height 静默截断
    # 实测教训：先固定 108/46，短文案（voklit 6 字名）看着空、
    # 长文案（aeo-engine 57 字）描述被压到 34px，两头都不理想。
    n_len = len(name)
    d_len = len(desc)
    # 无描述时：大字单行
    desc_size, gap = 46, 26
    if not desc:
        name_size = 168 if n_len <= 8 else (144 if n_len <= 14 else 120)
    else:
        # 有描述：项目名仍是主视觉。短描述给大字，长描述才降级。
        name_size = 150 if n_len <= 8 else (132 if n_len <= 14 else 112)
        # 描述按长度选字号（可用宽 1920-180=1740px，两行内放下）
        if d_len <= 12:
            desc_size = 62
        elif d_len <= 20:
            desc_size = 54
        elif d_len <= 30:
            desc_size = 46
        elif d_len <= 42:
            desc_size = 39
        else:
            desc_size = 33
        # 字越大行间距越松
        gap = 34 if name_size >= 140 else 26
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "*{margin:0;padding:0;box-sizing:border-box;}"
        "html,body{width:%(W)dpx;height:%(H)dpx;}"
        'body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;'
        "background:%(BASE)s;color:#fff;display:flex;flex-direction:column;"
        "padding:0 90px;position:relative;overflow:hidden;}"
        ".glow{position:absolute;right:-260px;top:-260px;width:720px;"
        "height:720px;border-radius:50%%;background:%(BAND)s;opacity:.55;}"
        ".main{flex:1;display:flex;flex-direction:column;justify-content:center;"
        "align-items:center;text-align:center;position:relative;min-height:0;"
        "gap:%(GAP)dpx;}"
        ".name{font-size:%(NSIZE)dpx;font-weight:900;color:#fff;line-height:1.22;"
        "max-height:2.5em;overflow:hidden;letter-spacing:-1px;}"
        ".desc{font-size:%(DSIZE)dpx;font-weight:500;color:%(AC)s;line-height:1.45;"
        "max-height:2.95em;overflow:hidden;}"
        "</style></head><body>"
        '<div class="glow"></div>'
        '<div class="main">'
        '<div class="name fit">%(NAME)s</div>'
        "%(DESCBLOCK)s"
        "</div>"
        "</body></html>"
        % {"W": TT_COVER_W, "H": TT_COVER_H, "BASE": base, "BAND": band,
           "AC": ac, "NAME": esc(name), "NSIZE": name_size,
           "DSIZE": desc_size, "GAP": gap,
           "DESCBLOCK": desc_block})


def render_tt_cover(port, c):
    """渲染一条案例的横版封面到 out/toutiao/<id>/cover.png，返回路径或空串。"""
    html = tt_cover_html(c)
    data_url = "data:text/html;charset=utf-8;base64," + base64.b64encode(
        html.encode("utf-8")).decode("ascii")
    browser = sub = tid = None
    try:
        browser = wp.CDP(port)
        tid = browser.new_target(data_url)["id"]
        sub = wp.CDP(port)
        sub.connect_target(tid)
        # 视口钉死成画布尺寸（同 xhs 卡片实测教训：不钉会按小视口塌陷）。
        sub.send("Emulation.setDeviceMetricsOverride",
                 {"width": TT_COVER_W, "height": TT_COVER_H,
                  "deviceScaleFactor": 1, "mobile": False})
        time.sleep(1.0)
        sub.eval(x.AUTOFIT_JS, refresh_context=True)   # 长名/长句自动缩字号
        r = sub.send("Page.captureScreenshot", {
            "format": "png", "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": TT_COVER_W,
                     "height": TT_COVER_H, "scale": 2}})
        data = (r.get("result") or {}).get("data", "")
        if not data:
            return ""
        out_dir = os.path.join(OUT, c["id"])
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "cover.png")
        with open(p, "wb") as f:
            f.write(base64.b64decode(data))
        return p
    finally:
        if tid and browser is not None:
            try:
                browser.close_target(tid)
            except Exception:                         # noqa: BLE001
                pass
        for conn in (sub, browser):
            if conn is not None and getattr(conn, "bws", None) is not None:
                try:
                    conn.bws.close()
                except Exception:                     # noqa: BLE001
                    pass


def cover_path(cid):
    """这篇头条稿的封面图：优先横版专用封面，退回小红书 card-1。"""
    p = os.path.join(OUT, cid, "cover.png")
    if os.path.isfile(p):
        return p
    for name in ("card-1.png", "card-1.jpg", "card-1.jpeg"):
        p = os.path.join(XHS_DIR, cid, name)
        if os.path.isfile(p):
            return p
    return ""


def _set_files_via_js(cdp, path):
    """把本地图片转 base64 → 塞进 file input → 派发 change。

    CDP 的 DOM.setFileInputFiles 在头条（Byte 系上传组件）上被忽略，
    img 不会进封面区；改成在页面里自己造 File + DataTransfer，实测能上。
    """
    import base64
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    mime = "image/%s" % ("jpeg" if ext in ("jpg", "jpeg") else ext)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    name = os.path.basename(path)
    js = ("""(function(){
      var b64 = '%s';
      var bin = atob(b64);
      var arr = new Uint8Array(bin.length);
      for(var i=0;i<bin.length;i++) arr[i] = bin.charCodeAt(i);
      var file = new File([arr], '%s', {type: '%s'});
      var dt = new DataTransfer();
      dt.items.add(file);
      var inputs = [].slice.call(document.querySelectorAll('input[type=file]'));
      if(!inputs.length) return 'no-input';
      var input = inputs[0];
      try{ input.files = dt.files; }catch(e){ return 'set-failed:'+e.message; }
      input.dispatchEvent(new Event('change', {bubbles: true}));
      return 'ok:' + inputs.length;
    })()""" % (b64, name, mime))
    return cdp.eval(js)


def _click_pos(cdp, pos, pause=0.3):
    for kind in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": kind, "x": pos["x"], "y": pos["y"],
                  "button": "left", "clickCount": 1})
    time.sleep(pause)


def _click_selector(cdp, sel, pause=0.8, timeout=8):
    """按 CSS 选择器点元素中心；点不到就一直重试（元素往往是点开后才出现）。"""
    end = time.time() + timeout
    while time.time() < end:
        r = cdp.eval("""(function(sel){
          var el = document.querySelector(sel);
          if(!el) return 'none';
          el.scrollIntoView({block:'center'});
          var b = el.getBoundingClientRect();
          if(b.width <= 0 || b.height <= 0) return 'none';
          return JSON.stringify({x: b.x+b.width/2, y: b.y+b.height/2});
        })(%s)""" % json.dumps(sel))
        if r and r != "none":
            try:
                _click_pos(cdp, json.loads(r), pause)
                return True
            except Exception:                         # noqa: BLE001
                pass
        time.sleep(0.5)
    return False


_COVER_STATE = """(function(){
  var panel = document.querySelector('.upload-image-panel');
  var add = document.querySelector('.article-cover-add');
  var list = document.querySelector('.upload-image-panel .image-list');
  return JSON.stringify({
    panel: !!panel,
    panelVisible: panel ? (getComputedStyle(panel).display !== 'none') : false,
    add: !!add,
    imgs: list ? list.children.length : -1
  });
})()"""


def upload_cover(cdp, paths, timeout=60):
    """给发文页传封面 —— 走「图库面板」这条链路（2026-09-27 实测跑通）。

    为什么之前一直失败
    ----------------
    封面块 `.article-cover-add` **不是**上传入口，点了它只会造出一个隐藏的
    file input（组件卡在 .byte-spin 里，图传到 CDN 了却没人把它设成封面）。
    真正的流程是：点封面块会弹出一个图库面板 `.upload-image-panel`，里面才有
      · 本地上传按钮 button.upload-btn（file input 藏在其 .btn-upload-handle 里）
      · 已上传图片列表 ul.image-list > li.pic-select-image-item-wrap
      · 底部「取消 / 确定」
    所以必须：上传 → 在列表里**点选**那张图 → 点**确定**，封面才成立。

    实测结论（省得再踩）：CDP 的 DOM.setFileInputFiles 在这条链路上**是有效的**，
    不用 base64/DataTransfer 那套（_set_files_via_js 保留但不再走）。
    """
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
    except Exception:                                 # noqa: BLE001
        pass

    st = json.loads(cdp.eval(_COVER_STATE) or "{}")
    if not st.get("panel"):
        if not _click_selector(cdp, ".article-cover-add", 2.0, 6):
            # 已有封面的草稿没有 add 占位块 —— 走封面图 hover 菜单里的「替换」
            # （.article-cover-img-menu 在 DOM 里常驻，无需真 hover 即可点）
            if not _click_selector(cdp, ".article-cover-img-replace", 2.0, 6):
                return "no-cover-slot"
    # 等面板真的可见（点一下封面块可能要过一会儿才渲染）
    end = time.time() + 10
    while time.time() < end:
        st = json.loads(cdp.eval(_COVER_STATE) or "{}")
        if st.get("panelVisible"):
            break
        time.sleep(0.5)
    if not st.get("panelVisible"):
        return "panel-not-open"

    before = st.get("imgs", -1)
    # 1) 面板里的 file input（藏在「本地上传」按钮里）
    node = _panel_file_input(cdp)
    if not node:
        return "no-upload-input"
    # 顺便点一下「本地上传」按钮，让组件进入待上传态
    _click_selector(cdp, ".upload-image-panel button.upload-btn", 1.0, 4)
    node = _panel_file_input(cdp) or node
    cdp.send("DOM.setFileInputFiles",
             {"files": [os.path.abspath(p) for p in paths], "nodeId": node})

    # 2) 等 image-list 多出一张
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        time.sleep(2)
        st = json.loads(cdp.eval(_COVER_STATE) or "{}")
        last = str(st.get("imgs"))
        if st.get("imgs", -1) > before:
            break

    # 3) 点选刚上传那张（列表最后一项）
    if not _click_selector(cdp,
                           ".upload-image-panel .image-list li.pic-select-image-item-wrap",
                           1.5, 8):
        return "pick-failed:" + last

    # 4) 确定
    if not _click_selector(cdp, ".upload-image-panel .confirm-btns button"
                                 ".byte-btn-primary", 2.0, 8):
        return "confirm-failed:" + last

    # 5) 验证封面区真的出图
    js = """(function(){
      var box = document.querySelector('.article-cover-images');
      if(!box) return 'no-box';
      var srcs = [].slice.call(box.querySelectorAll('img')).map(function(e){
        return e.getAttribute('src')||'';});
      return JSON.stringify({add: !!document.querySelector('.article-cover-add'),
                             srcs: srcs.slice(0,2)});
    })()"""
    end = time.time() + 20
    while time.time() < end:
        time.sleep(2)
        s = cdp.eval(js) or ""
        try:
            d = json.loads(s)
        except Exception:                             # noqa: BLE001
            continue
        if not d.get("add") and d.get("srcs"):
            return "ok"
    return "uploaded-but-not-confirmed:" + s[:60]


def _panel_file_input(cdp):
    """取图库面板里「本地上传」按钮内那个 file input 的 nodeId。"""
    try:
        r = cdp.send("DOM.getDocument", {"depth": 0})
        root = r.get("result", {}).get("root", {}).get("nodeId")
        if not root:
            return 0
        for sel in (".upload-image-panel .btn-upload-handle input[type=file]",
                    ".upload-image-panel input[type=file]"):
            r2 = cdp.send("DOM.querySelector", {"nodeId": root, "selector": sel})
            nid = r2.get("result", {}).get("nodeId", 0)
            if nid:
                return nid
    except Exception:                                 # noqa: BLE001
        pass
    return 0


def _clear_editor(cdp):
    """把发文页的标题和正文清空（--dry 用，避免留下草稿）。"""
    js = """(function(){
      var ta = document.querySelector("textarea[placeholder*='文章标题'],"
                                     "input[placeholder*='文章标题']");
      if(ta){
        var proto = ta.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
        Object.getOwnPropertyDescriptor(proto.prototype,'value').set.call(ta,'');
        ta.dispatchEvent(new Event('input',{bubbles:true}));
      }
      var ed = document.querySelector(".ProseMirror[contenteditable='true']");
      if(ed){
        ed.focus();
        try{ document.execCommand('selectAll'); document.execCommand('delete'); }catch(e){}
      }
      return 'ok';
    })()"""
    try:
        cdp.eval(js)
    except Exception:                                 # noqa: BLE001
        pass
    time.sleep(1.5)


def _wait_autosave(cdp, timeout=45):
    """等发文页的草稿自动保存（头条没「存草稿」按钮，靠页面自己存）。

    页面提示语有「草稿将自动保存」「已保存」「保存成功」几种写法，
    命中任一即算成功；超时返回 False（调用方按草稿箱复核）。
    """
    js = ("(function(){return (document.body.innerText||'');})()")
    end = time.time() + timeout
    seen = set()
    while time.time() < end:
        try:
            txt = cdp.eval(js) or ""
        except Exception:                             # noqa: BLE001
            time.sleep(2)
            continue
        for kw in ("保存成功", "已保存", "草稿已保存"):
            if kw in txt and kw not in seen:
                seen.add(kw)
                return True
        time.sleep(2)
    return False


def _click_text(cdp, texts):
    """按可见文本点按钮（「保存草稿」「存草稿」「发布」）。"""
    js = ("(function(){var want=%s;"
          "var es=[].slice.call(document.querySelectorAll('button,a,span,div'));"
          "for(var i=0;i<es.length;i++){var t=(es[i].innerText||'').trim();"
          "if(!t||t.length>12) continue;"
          "for(var j=0;j<want.length;j++){if(t===want[j]||t.indexOf(want[j])>=0){"
          "var b=es[i].getBoundingClientRect();if(b.width<=0)continue;"
          "es[i].click();return t;}}}return 'notfound';})()"
          % json.dumps(texts, ensure_ascii=False))
    return cdp.eval(js)


def cmd_login(args):
    """登录态自检：已登录返回 0，被踢到登录页返回 1（供 publish_both 预检）。

    --open：不检测，而是在调试 Chrome 里**开一个登录页并一直留着**，
    你自己扫完码按回车（或直接等 --timeout 秒），脚本回报登录结果。
    登录页容易被覆盖/关掉，需要重新登录时用它。
    """
    if args.open:
        return _open_login_page(args.timeout)
    cdp, t = _open(TT_HOME, wait=6)
    try:
        href = cdp.eval("location.href") or ""
        if "/auth/page/login" in href:
            print(LOGIN_HINT)
            return 1
        print("头条号已登录：%s（%s）" % (href, cdp.eval("document.title")))
        return 0
    finally:
        cdp.close_target(t["id"])


def _open_login_page(timeout=600):
    cdp = wp.CDP(CDP_PORT)
    t = cdp.new_target(TT_HOME)
    if not cdp.connect_target(t["id"]):
        print("连不上调试 Chrome（9222），先起一个：")
        print(LOGIN_HINT)
        return 1
    print("已开一个头条号登录页，请在这个 Chrome 窗口里扫码登录…")
    print("（登录页会一直开着，扫完不用管它，下面自动检测）")
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        time.sleep(6)
        try:
            href = cdp.eval("location.href") or ""
        except Exception:
            continue
        if href != last:
            last = href
            print("  · 当前：%s" % href[:90])
        if "/auth/page/login" not in href:
            print("✓ 登录成功：%s（%s）"
                  % (href, cdp.eval("document.title")))
            return 0
    print("等了 %d 秒还没登录，登录页还开着，你自己扫一下再跑一次本命令的检测"
          % timeout)
    return 1


def cmd_probe(args):
    """登录后在发文页跑这个，把可选元素导出来校准 SEL。"""
    cdp, t = _open(args.url or TT_EDITOR)
    try:
        href = cdp.eval("location.href")
        print("当前页面：%s" % href)
        if "/auth/page/login" in (href or ""):
            print(LOGIN_HINT)
            return
        info = cdp.eval("""(function(){
          var out = {title: [], editable: [], buttons: []};
          [].slice.call(document.querySelectorAll('input,textarea')).forEach(function(e){
            out.title.push((e.tagName||'') + ' | ' + (e.placeholder||'') + ' | ' +
                           (e.className||'').toString().slice(0,60));
          });
          [].slice.call(document.querySelectorAll('[contenteditable]')).forEach(function(e){
            out.editable.push((e.className||'').toString().slice(0,80) + ' | ce=' +
                              e.getAttribute('contenteditable'));
          });
          [].slice.call(document.querySelectorAll('button,a,span')).forEach(function(e){
            var s=(e.innerText||'').trim();
            if(s && s.length<=10) out.buttons.push(s);
          });
          return JSON.stringify(out);
        })()""")
        d = json.loads(info or "{}")
        print("\n--- input / textarea（找标题框）---")
        for s in (d.get("title") or [])[:20]:
            print("  " + s)
        print("\n--- contenteditable（找正文区）---")
        for s in (d.get("editable") or [])[:20]:
            print("  " + s)
        print("\n--- 按钮文本 ---")
        print("  " + " / ".join(sorted(set(d.get("buttons") or []))[:60]))
    finally:
        if not args.keep:
            cdp.close_target(t["id"])


def publish_one(c, dry=False, yes=False, cover=True):
    """填一稿进发文页。dry = 只填不点；yes = 点「发布」，否则靠自动存草稿。

    cover = 顺手把小红书卡片图第 1 张传成封面（头条没封面推荐会弱）。
    """
    meta_path = os.path.join(OUT, c["id"], "meta.json")
    if not os.path.isfile(meta_path):
        meta = build_article(c)
        write_article(meta)
    else:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    html_path = os.path.join(OUT, c["id"], "article.html")
    with open(html_path, encoding="utf-8") as f:
        full = f.read()
    body_html = full.split("\n", 1)[1] if full.startswith("<h1>") else full

    cdp, t = _open(TT_EDITOR, wait=10)
    try:
        _check_login(cdp)
        if dry:
            # 头条是**边填边自动存草稿**的：只要往编辑器打了字（哪怕随后清空），
            # 草稿箱里就会留下一条带标题的草稿（实测：两次 dry 试封面 = 两条重复）。
            # 所以 dry 一律不打字，只做只读校验。
            has_t = cdp.eval("(function(){return document.querySelector(%s)?1:0;})()"
                             % json.dumps(SEL["title"]))
            has_b = cdp.eval("(function(){return document.querySelector(%s)?1:0;})()"
                             % json.dumps(SEL["body"]))
            cp = cover_path(c["id"]) if cover else None
            print("    [dry] 不填内容（头条边填边存，试填会留草稿，去重用 dedup 子命令）")
            print("    [dry] 标题框：%s / 正文区：%s（《%s》）"
                  % ("✓" if has_t else "✗ 没找到", "✓" if has_b else "✗ 没找到",
                     meta["title"]))
            print("    [dry] 封面图：%s"
                  % (os.path.basename(cp) if cp else "没找到卡片图"))
            return bool(has_t and has_b)
        ok_t = _focus_and_type(cdp, SEL["title"], meta["title"])
        print("    标题框：%s（《%s》%d 字）"
              % ("已填" if ok_t else "没找到", meta["title"], len(meta["title"])))
        body_sel = SEL["body"]
        got = cdp.eval("""(function(){
          var el = document.querySelector(%s);
          if(!el) return 'notfound';
          el.focus();
          return 'ok';
        })()""" % json.dumps(body_sel))
        if got != "ok":
            print("    正文区：没找到（跑 publish --probe 校准选择器）")
            return False
        cdp.send("Runtime.evaluate", {
            "expression": ("document.execCommand('insertHTML', false, %s)"
                           % json.dumps(body_html, ensure_ascii=False)),
            "returnByValue": True})
        time.sleep(1.5)
        n = cdp.eval("(function(){var e=document.querySelector(%s);"
                     "return (e&&(e.innerText||'').length)||0;})()"
                     % json.dumps(body_sel))
        print("    正文：%s 字" % n)
        if cover:
            cp = cover_path(c["id"])
            if cp:
                r = upload_cover(cdp, [cp])
                print("    封面：%s（%s）" % (r, os.path.basename(cp)))
            else:
                print("    封面：没找到卡片图（跳过，草稿会显示「没封面」）")
        if not yes:
            # 头条发文页**没有「存草稿」按钮**（底部只有 预览 / 定时发布 / 预览并发布），
            # 草稿是填完自动保存的（页面上有「草稿将自动保存」提示）。
            # 所以「存草稿」= 填完等它自己存，别点任何按钮。
            ok = _wait_autosave(cdp)
            print("    自动保存：%s" % ("已保存" if ok else "没等到保存提示，稍后去草稿箱确认"))
            if not ok:
                return False
        else:
            btn = _click_text(cdp, ["预览并发布", "发布文章", "立即发布"])
            if btn == "notfound":
                print("    [warn] 没点到发布按钮，记录不写")
                return False
        time.sleep(4)
        if yes:
            mark_published(c["id"])
        else:
            mark_drafted(c["id"])
        return True
    finally:
        cdp.close_target(t["id"])


def publish_api_one(c, cdp=None, anti_token=None, verify=True, with_cover=True,
                    rebuild=False):
    """建一篇草稿，**纯 API 路线**（2026-10-17 实测 code:0 全通，含封面）。

    与 `publish_one`（UI 路线）的差别：
      · 不开编辑器、不点任何按钮、不填 DOM —— 只有 2~3 次 fetch：
        ① 传封面 `upload_cover_api`（`/spice/image` 两级）
        ② 存草稿 `save_draft_api`（`article/publish`，`save=0`）
      · 自动存草稿靠 `save=0`（实证值，不是发布）。

    ⚠ **anti_token 必须新鲜**（这是唯一约束）。自己取会留一条探针草稿，
    所以：优先用调用方传进来的（批量时取一次用一批）；没给就现取 +
    事后清理。取不到就返回 None，让调用方退 UI 路线，**不静默失败**。

    🚨 `rebuild=True` 用于**存量草稿刷新**（2026-10-17 实测踩坑）：
      默认行为是「meta.json 不在才 build」—— 存量刷新的 case meta 都在，
      于是建出来的稿子用的是 meta 里的**旧标题**（API 照样返「保存成功」，
      极具误导性）。刷新场景（标题逻辑修过、override 补过）必须先
      `build_article` 重建 meta 再建稿，也就是传 rebuild=True。
    """
    meta_path = os.path.join(OUT, c["id"], "meta.json")
    # ⚠ 用 os.path.isfile 不是 is_file —— 后者是 pathlib 的方法，
    #   os.path(=ntpath) 上没有，实测 AttributeError。
    if rebuild or not os.path.isfile(meta_path):
        meta = build_article(c)
        write_article(meta)
    else:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    html_path = os.path.join(OUT, c["id"], "article.html")
    with open(html_path, encoding="utf-8") as f:
        full = f.read()
    body_html = full.split("\n", 1)[1] if full.startswith("<h1>") else full

    own = False
    box = cdp
    tid = None
    if box is None:
        sess = open_api_session()
        if not sess:
            return None
        box, tid = sess
    try:
        if not anti_token:
            print("    取 tt-anti-token（会让页面自己保存一次探针稿）…")
            anti_token = grab_anti_token(box)
            own = bool(anti_token)
            if not anti_token:
                print("    ✗ 没拿到 token（掉登录或无头不触发自动保存）"
                      " ⇒ 退 UI 路线")
                return None
        cover = None
        if with_cover:
            cp = cover_path(c["id"])
            # 🚨 `cover.png`（3840x2160 横版）才是头条封面该用的图；
            #   `cover_path` 找不到时会退回小红书 card-1.png（3:4 竖图），
            #   塞进去会被裁得很难看 —— 所以缺横版就现渲染一张。
            if not os.path.isfile(os.path.join(OUT, c["id"], "cover.png")):
                try:
                    made = render_tt_cover(box.port, c)
                    if made:
                        print("    横版封面现渲染：%s"
                              % os.path.basename(made))
                        cp = made
                except Exception as e:                       # noqa: BLE001
                    print("    [warn] 渲染封面失败：%s" % str(e)[:60])
            if not cp or not os.path.isfile(cp):
                print("    [warn] 没有本地封面（%s）⇒ 这篇不带封面"
                      % (cp or "未渲染"))
            else:
                cu, curi, cmsg, wh = upload_cover_api(box, cp, anti_token)
                if cu:
                    print("    封面上传：✓ %s（%dx%d）"
                          % (cu[-42:], wh[0], wh[1]))
                    cover = (cu, curi, cmsg, wh)
                else:
                    # 封面失败**不阻断建稿** —— 先把正文存上，封面可后补
                    # （`cover` 子命令能单独补），别让一篇稿子白建。
                    print("    [warn] 封面上传失败（%s）⇒ 先建无封面草稿"
                          % cmsg)
        pgcid, msg = save_draft_api(box, meta["title"], body_html,
                                    anti_token=anti_token, cover=cover)
        print("    API 存草稿：%s（pgc_id=%s）" % (msg, pgcid or "无"))
        if not pgcid:
            return None
        if verify:
            # 「API 返 OK ≠ 真存上了」—— 头条 delete 对假 gid 也返 code:0，
            # 所以回读 draft_list 核对标题在不在，是唯一可信判据。
            # ⚠ 但 draft_list 也是被 argus 记账的请求：**批量时逐条回读会
            #   把后续 publish 顶进风控**（2026-10-17 实测）⇒ 批量调用方
            #   应传 verify=False，改为**整批存完统一核对一次**。
            try:
                raw = fetch_draft_api_raw(box) or []
                hit = [x for x in raw if (x.get("title") or "") == meta["title"]]
                if not hit:
                    print("    ✗ 回读校验失败：草稿箱里没有这篇 ⇒ 不写台账")
                    return None
                print("    回读校验：✓ 草稿箱命中（gid=%s）"
                      % hit[0].get("gid"))
                if cover:
                    st, det = verify_draft_cover(box, meta["title"], raw=raw)
                    print("    封面校验：%s%s"
                          % ({"OK": "✓ 封面已落库",
                              "NO_COVER": "✗ 没落封面",
                              "NOT_FOUND": "✗ 找不到这篇"}[st],
                             ("（%s）" % det) if det else ""))
                    if st != "OK":
                        return None
            except Exception as e:                           # noqa: BLE001
                print("    [warn] 回读校验异常（%s），按服务端 code:0 记"
                      % str(e)[:60])
        mark_drafted(c["id"])
        return pgcid
    finally:
        if own:
            n = cleanup_probe_draft(box)
            print("    清理探针草稿 %d 条" % n)
        if cdp is None:
            try:
                box.close_target(tid)
            except Exception:                               # noqa: BLE001
                pass


# 草稿箱列表会长，靠下的卡片「编辑」按钮在视口外 —— 点不到。
# 所以分两步：先把卡片滚进视口，等一拍，再量坐标（并校验确实在可视区内）。
_DRAFT_SCROLL_TO = """(function(t){
  var links = [].slice.call(document.querySelectorAll('.op-button, [class*=operation] a'));
  for(var i=0;i<links.length;i++){
    var e = links[i];
    if((e.innerText||'').trim() !== '编辑') continue;
    var card = e.closest('[class*=draft-item]') || e.parentElement;
    if(card && (card.textContent||'').indexOf(t) >= 0){
      try{ card.scrollIntoView({block:'center', inline:'center'}); }catch(err){}
      return 'scrolled';
    }
  }
  return 'none';
})(%s)"""


_DRAFT_EDIT_POS = """(function(t){
  var links = [].slice.call(document.querySelectorAll('.op-button, [class*=operation] a'));
  for(var i=0;i<links.length;i++){
    var e = links[i];
    if((e.innerText||'').trim() !== '编辑') continue;
    var card = e.closest('[class*=draft-item]') || e.parentElement;
    if(card && (card.textContent||'').indexOf(t) >= 0){
      var b = e.getBoundingClientRect();
      if(b.width <= 0 || b.height <= 0) return 'zero';
      var vh = window.innerHeight || 1000;
      if(b.y < 0 || b.y > vh) return 'offscreen';
      return JSON.stringify({x: b.x+b.width/2, y: b.y+b.height/2});
    }
  }
  return 'none';
})(%s)"""


# 草稿箱是**分页渲染**：首屏只出 20 条 `article-draft-item`，其余藏在
# 「加载更多」按钮后面（页头会写「共 N 条内容」）。不点开它，同标题重复
# 一律统计不到（dedup 曾因此全部报「0 条」假阴性）。
#
# ⚠⚠ 2026-10-09 实测：`_DRAFT_LOADMORE_JS` 返回的坐标**点不动**（两个坑叠加）：
#   ① 按钮在文档 y=3433 处而视口只有 907 高，且页面有隐藏滚动上下文，
#      里面的 `scrollIntoView` **不**更新 `getBoundingClientRect()`
#      ⇒ 派发的坐标事件落在视口外 = 点空气；
#   ② 就算把窗口滚对了坐标，点 `.common-load-more-wrap` 本身也**不触发**
#      加载 —— 真正挂 onClick 的是它里面**最深的那个 SPAN**。
#   实测：策略 A（scrollIntoView+旧坐标）恒 20；
#        策略 B（window.scrollTo+重取坐标+坐标派发）恒 20，偶尔掉到 0；
#        策略 C（JS .click() 容器本身）恒 20；
#        ✅ 策略 D（JS .click() **最深的 SPAN**）20 → 29 一次到位。
# ⇒ 结论：用 `_DRAFT_LOADMORE_CLICK_JS`，别再用坐标派发。
_DRAFT_LOADMORE_JS = """(function(){
  var ws = document.querySelectorAll('.common-load-more-wrap, .common-load-more-footer');
  for(var i=0;i<ws.length;i++){
    var w = ws[i];
    if((w.innerText||'').indexOf('加载更多') < 0) continue;
    var r = w.getBoundingClientRect();
    if(r.width <= 0 || r.height <= 0) return 'hidden';
    w.scrollIntoView({block:'center'});
    return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
  }
  return 'none';
})()"""

# ✅ 真正管用的点法（2026-10-09 实测 20 → 29）：直接 JS .click()「加载更多」
#    容器里**最深的**可点子节点，从后往前找第一个 cursor:pointer/BUTTON/SPAN。
#    ⚠ 别改回坐标派发 —— 上面三个坑全在坐标上。
_DRAFT_LOADMORE_CLICK_JS = """(function(){
  var ws = document.querySelectorAll(
      '.common-load-more-wrap, .common-load-more-footer');
  for (var i = 0; i < ws.length; i++) {
    var w = ws[i];
    if ((w.innerText||'').indexOf('加载更多') < 0) continue;
    var all = [].slice.call(w.querySelectorAll('*'));
    for (var j = all.length - 1; j >= 0; j--) {
      var e = all[j];
      var cs = getComputedStyle(e);
      if (cs.cursor === 'pointer' || e.tagName === 'BUTTON' ||
          e.tagName === 'SPAN') {
        e.click();
        return 'clicked:' + e.tagName;
      }
    }
    w.click();
    return 'clicked:wrap';
  }
  return 'none';
})()"""

# 页头声明的草稿总数（页头「共 N 条内容」）—— 展开判据用它，别只数卡片
_DRAFT_TOTAL_JS = """(function(){
  var m = (document.body.innerText||'').match(/共\\s*(\\d+)\\s*条/);
  return m ? m[1] : '';
})()"""

_DRAFT_ITEM_COUNT_JS = ("document.querySelectorAll('.article-draft-item, "
                        ".draft-item').length")


def _draft_total(cdp):
    """页头声明的草稿总数（int）；页头没写返回 0。

    ⚠ 判「草稿箱是否已展开完」必须用它，不能只数卡片：首屏恒 20 条，
    数卡片在没展开时也给 20 ⇒ 假阴性（2026-10-09 踩过）。
    """
    try:
        return int(cdp.eval(_DRAFT_TOTAL_JS) or 0)
    except (TypeError, ValueError, RuntimeError):
        return 0


def _load_all_drafts(cdp, max_rounds=40, pause=2.0):
    """点「加载更多」把草稿箱整页展开，返回渲染出的条目数。

    ⚠ 2026-10-09 重写。原来用 `_DRAFT_LOADMORE_JS` 取坐标 + 坐标派发，
      那条路**从来没生效过**（恒停在首屏 20 条），害得 dedup / drafts 反查
      全是假阴性。改用 `_DRAFT_LOADMORE_CLICK_JS`（JS 点最深 SPAN）。

    ⚠⚠ 2026-10-09 再修：**光靠「按钮消失」会提前退出，恒停在 20 条。**
      实测时序（草稿箱 29 条）：
        t+0s  点击 → `clicked:SPAN`，卡片 20，按钮 none
        t+2s  卡片**仍是 20**（接口还没回来）
        t+4s  卡片 **29** ← 数据这才到
      按钮在**请求发出瞬间**就被隐藏了，比响应早 2–4 秒 ⇒
      「`state == "none"` 就 break」等于在数据到达前就退出。
      后果：`tt_audit_drafts` 只读到 20 条 → 标题映射漏 9 条
      （含 `trustmrr` / `viktor` / `coral`）→ `--all` 批量刷新会**静默漏刷**。

      ⇒ 停止判据改成「**卡片数 ≥ 页头声明总数**」，按钮消失只当辅助信号：
        按钮还在 → 点它，等pause；
        按钮没了但卡片没到总数 → **继续等**（`settle` 轮），别急着退出。
    """
    def _count():
        try:
            return int(cdp.eval(_DRAFT_ITEM_COUNT_JS) or 0)
        except (TypeError, ValueError, RuntimeError):
            return 0

    def _total():
        return _draft_total(cdp)

    settle = 0                      # 按钮已消失但卡片没到总数的连续轮数
    rounds = 0
    # ⚠ 2026-10-14 修无限循环：原 `while settle < 5` 在「加载更多」按钮被
    # 永久探测到（元素残留 / 点击不触发加载）时，state 永远 != "none" ⇒
    # settle 永远到不了 5 ⇒ 死循环（实测 _tt_verify 卡死 1h42m）。加硬上限
    # max_rounds 兜底，到顶直接返回已加载条数（不丢已加载的）。
    while settle < 5 and rounds < max_rounds:
        rounds += 1
        total = _total()
        n = _count()
        if total and n >= total:    # 真的加载全了
            return n
        # ⚠ 页头总数读不到（total=0）⇒ 没有判据可依。此时只能靠按钮状态收敛，
        #   但**不能**直接 return 0 —— 前面 `_count()` 拿到的条数才是真实信息
        #   （实测首屏就有 20 条，全读不到就等于把已加载的也丢了）。
        state = cdp.eval(_DRAFT_LOADMORE_CLICK_JS) or "none"
        if state == "none":
            # 按钮没了：可能正在等响应（实测延迟 2–4s），也可能是真到底了。
            # 再等一轮看卡片数涨不涨；连续 5 轮不涨才认输。
            settle += 1
        else:
            settle = 0
            time.sleep(pause)
    return _count()


def _page_target_ids():
    out = set()
    try:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("type") == "page":
                out.add(t["id"])
    except Exception:                                 # noqa: BLE001
        pass
    return out


def _pgc_id_of(target):
    """从 target 的 URL 里取 `pgc_id`（草稿 ID）；取不到返回 ""。

    ⚠ 这是「头条复用了编辑页 tab」的判据：同一个 tab 从草稿 A导航到
      草稿 B 时，tab id 不变但 `pgc_id` 会变 ⇒ 用它区分「加载中/没动」
      与「真的换到下一篇」。
    """
    u = target.get("url") or ""
    m = re.search(r"[?&]pgc_id=(\d+)", u)
    return m.group(1) if m else ""


def _current_pgc_id(cdp):
    """当前**草稿箱**页所在 tab 的 pgc_id（用来记「点编辑前」的值）。"""
    try:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("type") == "page" \
                    and "/manage/draft" in t.get("url", "") \
                    and t.get("webSocketDebuggerUrl"):
                return _pgc_id_of(t)
    except Exception:                                 # noqa: BLE001
        pass
    return ""


def _open_published_editor(title, wait=25, max_seconds=90):
    """草稿箱里没有这篇时，去「作品管理」点它的「修改」。

    ⚠⚠ 2026-10-09 加 `max_seconds` 上限。原因：作品管理列表有几百条，
    原来固定滚 120 屏 × 1.2s ≈ **144 秒**才放弃。批量刷 41 条时，
    每条不在草稿箱的案例都要付这144 秒 —— 实测`--all` 跑到第 1 条
    就卡了 15 分钟没进展（当时页面正停在 manage/content/all）。
    ⇒ 时间预算到就返回「没找到」，让调用方跳过，别让批量任务被拖死。
    草稿箱里有的案例**根本不会走这里**，所以调小不影响正常刷新。

    头条已发布文章可编辑（能补封面），路径：作品管理 /profile_v4/manage/content/all
    → 找到标题卡 → 点「修改」→ 新开 tab /graphic/publish?pgc_id=...
    返回 (cdp, target, err)；找不到返回 (None, None, 原因)。
    """
    cdp = wp.CDP(CDP_PORT)
    tid = None
    for t in cdp.list_targets():
        if "/manage/content/all" in t.get("url", ""):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(TT_WORKS)["id"]
    box = wp.CDP(CDP_PORT)
    box.connect_target(tid)
    time.sleep(6)
    box.send("Page.navigate", {"url": TT_WORKS})
    time.sleep(8)

    before = _page_target_ids()
    # 作品管理也是懒加载，滚几轮把列表拉出来
    scroll_js = """(function(){
      var cands=[].slice.call(document.querySelectorAll('div,main,section'))
        .filter(function(e){
          return e.scrollHeight>e.clientHeight+80 && e.clientHeight>300;});
      var el=cands.sort(function(a,b){return b.clientHeight-a.clientHeight;})[0];
      if(!el) return 'none';
      el.scrollTop=el.scrollHeight;
      return String(el.scrollTop);
    })()"""
    pos = ""
    # 作品管理有 495 条（2026-09-29 实测），目标可能排在几十页之后。
    # ⚠ 上限同时受 `max_seconds` 约束 —— 原来死跑 120 轮 ≈ 144 秒，
    #   批量场景会把整批拖死（见上面 docstring）。
    deadline = time.time() + max_seconds
    for _ in range(120):
        if time.time() > deadline:
            return None, None, "《%s》作品管理搜索超时（%ds），跳过" % (
                title, max_seconds)
        box.eval(scroll_js)
        time.sleep(1.0)
        # 只认**已发布**那条卡（作品管理里同名可能有「由文章生成」的衍生条目，
        # 2026-09-29 实测 kibu 就有两条同名），优先带「已发布」标记的卡。
        r = box.eval("""(function(t){
          var cards=[].slice.call(document.querySelectorAll('li,tr,div'))
            .filter(function(e){
              var x=(e.innerText||'');
              return x.indexOf(t)>=0 && x.length<400 &&
                     x.indexOf('修改')>=0;});
          if(!cards.length) return 'no-card';
          // 最短包含者 = 卡片本身
          cards.sort(function(a,b){
            return a.innerText.length-b.innerText.length;});
          var card=cards[0];
          for(var i=0;i<cards.length;i++){
            if((cards[i].innerText||'').indexOf('已发布')>=0){card=cards[i];break;}
          }
          card.scrollIntoView({block:'center'});
          var btn=[].slice.call(card.querySelectorAll('a,button,span'))
            .find(function(b){return (b.innerText||'').trim()==='修改';});
          if(!btn) return 'no-btn';
          var b=btn.getBoundingClientRect();
          if(b.width<=0) return 'zero';
          return JSON.stringify({x:Math.round(b.x+b.width/2),
                                 y:Math.round(b.y+b.height/2)});
        })(""" + json.dumps(title))
        if r and r not in ("none", "no-btn", "zero"):
            pos = r
            break
        time.sleep(1.2)
    if not pos:
        return None, None, "作品管理里也没找到《%s》" % title

    _click_pos(box, json.loads(pos), 2.5)
    time.sleep(4)
    newt = None
    end = time.time() + wait
    while time.time() < end:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("id") in before or t.get("type") != "page":
                continue
            if "/graphic/publish" in t.get("url", ""):
                newt = t
                break
        if newt:
            break
        time.sleep(1.0)
    if not newt:
        return None, None, "点了修改但没等到编辑页"
    sub = wp.CDP(CDP_PORT)
    sub.connect_target(newt["id"])
    time.sleep(3)
    return sub, newt, ""


def open_draft_editor(title, wait=25):
    """在草稿箱里按标题点「编辑」，返回 (cdp, target)。

    点编辑不会在当前页跳转，而是**新开一个 tab**，URL 带 ?pgc_id=<草稿ID>，
    原文内容已经在里面了。所以这里要盯着列表找出这个新 tab 再连上去。
    """
    cdp = wp.CDP(CDP_PORT)
    box = None
    for t in cdp.list_targets():
        if "/manage/draft" in t.get("url", "") and t.get("webSocketDebuggerUrl"):
            box = t
            break
    if not box:
        cdp.new_target(TT_DRAFT)
        time.sleep(6)
        cdp.connect_target(cdp.list_targets()[-1]["id"])
        box = cdp
        cdp = wp.CDP(CDP_PORT)
        for t in cdp.list_targets():
            if "/manage/draft" in t.get("url", "") and t.get("webSocketDebuggerUrl"):
                cdp.connect_target(t["id"])
                box = t
                break
    else:
        cdp.connect_target(box["id"])
    time.sleep(2)

    before = _page_target_ids()
    # 草稿箱初次只渲染前 20 条，目标卡在第 20 条开外时 _DRAFT_SCROLL_TO
    # 会一直 'none' 空转超时（2026-09-29 实测）。先把整页展开。
    #
    # 2026-09-30：整页展开是单条耗时的最大头（168s 里约 100s 在这）。
    # 展开是**幂等**的 —— 同一页反复展开，第二遍起 _DRAFT_LOADMORE_CLICK_JS
    # 直接返回 'none'，一轮就 break。所以别在每条前都跑满 20 轮，先探一次。
    #
    # ⚠⚠ 2026-10-09 修判据：原来用 `n >= 20` 当「已展开」，而首屏**恒是**
    #    20 条 ⇒ 永远第一轮就判定「已展开」，第 21 条开外的稿（实测草稿共
    #    29 条）**永远搜不到** ⇒ open_draft_editor 直接掉进作品管理兜底。
    #    现在拿页头声明的总数当基准：有总数就比总数，没总数才退回 ≥20。
    if not _draft_box_expanded.get("v"):
        n = _load_all_drafts(cdp, max_rounds=20, pause=1.5)
        total = _draft_total(cdp)
        if total and n >= total:
            _draft_box_expanded["v"] = True
        elif not total and n >= 20:
            _draft_box_expanded["v"] = True
    # ⚠⚠ 2026-10-09 再修：找不到卡时**必须让展开状态失效并重试**。
    #   原bug：`_draft_box_expanded` 一旦置True 就永不复查。实测批量跑到
    #   第 22 条起连续 9 条报「草稿箱：列表里没这张卡」，而这 9 条全都
    #   实实在在在草稿箱清单里（第 18-28 条）。根因是草稿箱列表会**回缩**
    #   （关tab / 路由刷新 / 列表虚拟化）回首屏 20 条，而缓存说「已展开」，
    #   于是第 21 条开外的卡永远搜不到，每次都掉进作品管理兜底空等 90s。
    #   ⇒ 失败时重置缓存并重新展开，最多试 2轮。
    pos = ""
    for round_ in range(2):
        for attempt in range(6):
            r = cdp.eval(_DRAFT_SCROLL_TO % json.dumps(title))
            if r == "none":
                time.sleep(1.5)
                continue                               # 列表还没渲染出这张卡
            time.sleep(1.2)                                # 等scroll稳定
            pos = cdp.eval(_DRAFT_EDIT_POS % json.dumps(title))
            if pos and pos not in ("none", "zero", "offscreen"):
                break
            time.sleep(1.5)
            pos = ""
        if pos:
            break
        # 没找到 ⇒ 假设列表已回缩，作废「已展开」缓存，下一轮强制重新展开
        if round_ == 0:
            _draft_box_expanded["v"] = False
            print("    (列表可能已回缩，重新展开草稿箱后重试)")
            n = _load_all_drafts(cdp, max_rounds=20, pause=1.5)
            total = _draft_total(cdp)
            if total and n >= total:
                _draft_box_expanded["v"] = True
            elif not total and n >= 20:
                _draft_box_expanded["v"] = True
    if not pos:
        # 草稿箱里没有这张卡 → 可能已经发布/被删。头条已发布文章也能编辑，
        # 转「内容管理」列表页找（2026-09-29 实测：kibu / pieter-levels
        # 已不在草稿箱，37 条发布记录 vs 草稿箱只剩 35 条）。
        alt = _open_published_editor(title)
        if alt and alt[0]:
            return alt
        why = (alt[2] if alt else "作品管理页没打开")
        return None, None, "《%s》的编辑按钮点不到（草稿箱：%s；作品管理：%s）" % (
            title, pos or "列表里没这张卡", why)
    _click_pos(cdp, json.loads(pos), 2.5)
    time.sleep(4)

    # ⚠⚠ 2026-10-09 修：头条**会复用已有的 publish tab**，不是每次都新开。
    #   原来只认「新开 tab」（`t["id"] in before` 直接continue），实测批量
    #   重试时 9 条全部报「点了编辑但没等到新开编辑页 tab」，而列表里明明有
    #   一个 `/graphic/publish?pgc_id=...` 的 tab —— 头条把上一条残留的编辑页
    #   **原地导航**到下一条草稿了，tab id 没变。
    #   ⇒ 两段式：先等「新 tab」；等不到再回落「已在publish 页但 pgc_id 变了」。
    end = time.time() + wait
    newt = None
    before_pgc = _current_pgc_id(cdp)
    while time.time() < end:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("type") != "page":
                continue
            if "/graphic/publish" not in t.get("url", ""):
                continue
            if t.get("id") not in before:              # 首选：新开的 tab
                newt = t
                break
            if _pgc_id_of(t) not in ("", before_pgc):   # 回落：复用的 tab
                newt = t
                break
        if newt:
            break
        time.sleep(1)
    if not newt:
        return None, None, ("点了编辑但没等到编辑页 tab"
                            "（新 tab 没开出来，复用 tab 也没换 pgc_id）")
    c = wp.CDP(CDP_PORT)
    if not c.connect_target(newt["id"]):
        return None, None, "连不上新编辑页 tab"
    # 等编辑器（标题框）出现
    end = time.time() + 25
    while time.time() < end:
        if cdp.eval("!!document.querySelector(%s)" % json.dumps(SEL["title"])):
            break
        time.sleep(1)
    return c, newt, ""


def cover_one(c, dry=False, force=False):
    """给**已存在的草稿**原地补/换封面（不重发、不产生重复草稿）。

    做法：草稿箱 → 点该草稿的「编辑」（新开 tab，原文已加载）→ 走图库面板上传。
    force=True 时草稿已有封面也强制换图（走封面 hover 菜单的「替换」入口）。
    """
    if dry:
        return "dry"
    meta_path = os.path.join(OUT, c["id"], "meta.json")
    if not os.path.isfile(meta_path):
        return "没有 meta.json，先跑 build"
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    cp = cover_path(c["id"])
    if not cp:
        return "没找到卡片图"
    cdp, t, err = open_draft_editor(meta["title"])
    if not cdp:
        return err
    try:
        has_cover = cdp.eval("!!document.querySelector('.article-cover-add')")
        if not has_cover and not force:
            return "这张草稿本来就有封面（没有占位块）；要换新图加 --force"
        old_src = cdp.eval(
            "(function(){var i=document.querySelector("
            "'.article-cover-images img');return i?i.src:'';})()") or ""
        r = upload_cover(cdp, [cp])
        if r != "ok":
            return "封面失败：%s" % r
        ok = _wait_autosave(cdp)
        # 换图场景：确认编辑器里的封面 src 真的变了（add 块始终不存在，
        # upload_cover 的内置验证在替换模式下会立刻误判 ok）
        if old_src:
            new_src = cdp.eval(
                "(function(){var i=document.querySelector("
                "'.article-cover-images img');return i?i.src:'';})()") or ""
            if new_src == old_src:
                return "封面已传，但编辑器里的图没变（替换可能没生效）"
        return "ok" + ("" if ok else "（封面已传，但没等到保存提示）")
    finally:
        cdp.close_target(t["id"])


def cmd_cover(args):
    """给已有的头条草稿补封面（原地改，不重发）。"""
    cases = []
    if args.case:
        c = find_case_fuzzy(args.case)
        if not c:
            raise SystemExit("找不到案例：%s" % args.case)
        cases.append(c)
    else:
        cases = list(load_cases())
    if not args.all:
        cases = cases[:1]
    print("将为 %d 篇草稿补封面%s" % (len(cases), "（dry-run）" if args.dry else ""))
    ok = 0
    t_all = time.time()
    for i, c in enumerate(cases, 1):
        # 关键：每条都 flush。批量跑要 40+ 分钟，重定向到管道时 Python
        # 默认块缓冲（4~8KB），不刷就是「看起来卡住、Chrome 也没动静」。
        # 2026-09-30 用户两次问「还在更新吗」就是被这个坑到的。
        print("[%d/%d] %-22s 《%s》" % (i, len(cases), c["id"],
                                        find_title(c)), flush=True)
        r = None
        t0 = time.time()
        for attempt in (1, 2):                    # CDP 偶发断连，单条重试一次
            try:
                r = cover_one(c, dry=args.dry, force=getattr(args, "force", False))
                break
            except Exception as e:                # noqa: BLE001
                r = "%s: %s（attempt %d）" % (type(e).__name__, e, attempt)
                time.sleep(3)
        print("    → %s（%.0fs，本条累计 %.1f 分钟）"
              % (r, time.time() - t0, (time.time() - t_all) / 60.0), flush=True)
        if r.startswith("ok"):
            ok += 1
    print("\n完成：%d/%d" % (ok, len(cases)))


def find_title(c):
    mp = os.path.join(OUT, c["id"], "meta.json")
    if os.path.isfile(mp):
        with open(mp, encoding="utf-8") as f:
            return json.load(f).get("title", c["id"])
    return c["id"]


def cmd_build(args):
    cases = [find_case_fuzzy(args.case)] if args.case else list(load_cases())
    for i, c in enumerate(cases, 1):
        meta = build_article(c)
        d = write_article(meta)
        flags = []
        if meta["chars"] < BODY_MIN:
            flags.append("正文偏短，建议别发")
        w = amount_warn(c, meta["title"])
        if w:
            flags.append(w)
        rw = risk_words(meta["title"])
        if rw:
            flags.append("标题有头条敏感词：%s" % "/".join(rw))
        if meta.get("risk_fixed"):
            flags.append("敏感词已自动中性化：%s" % "/".join(meta["risk_fixed"]))
        if meta.get("body_risk"):
            flags.append("正文敏感词（原文引用，自行判断）：%s" % "/".join(meta["body_risk"]))
        print("[%d/%d] %-22s 《%s》 %d 字 · %d 段%s"
              % (i, len(cases), c["id"], meta["title"], meta["chars"],
                 meta["blocks"], ("  [!] " + "；".join(flags)) if flags else ""))


def cmd_preview(args):
    """out/toutiao/index.html：37 篇一页看完（标题 / 字数 / 正文链接）。"""
    rows = []
    for c in load_cases():
        mj = os.path.join(OUT, c["id"], "meta.json")
        if not os.path.isfile(mj):
            continue
        with open(mj, encoding="utf-8") as f:
            m = json.load(f)
        rows.append(m)
    rows.sort(key=lambda m: m["id"])
    li = "\n".join(
        "<li><a href='%s/article.html'>%s</a> "
        "<span class=m>%s · %d 字</span></li>" % (esc(m["id"]), esc(m["title"]),
                                                  esc(m["id"]), m["chars"])
        for m in rows)
    html = ("<meta charset='utf-8'><title>头条稿预览</title>"
            "<style>body{font:14px/1.7 -apple-system,'Microsoft YaHei',sans-serif;"
            "max-width:760px;margin:32px auto;padding:0 16px;color:#222}"
            "h1{font-size:20px}li{margin:6px 0}a{color:#1a73e8;text-decoration:none}"
            ".m{color:#888;margin-left:8px}</style>"
            "<h1>头条稿预览（%d 篇）</h1><ul>%s</ul>" % (len(rows), li))
    p = os.path.join(OUT, "index.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    print("预览页：%s" % p)


def cmd_queue(args):
    todo = pending_cases(need_built=not args.fresh)
    n = max(1, args.near)
    if not todo:
        print("待发队列是空的。")
        return
    print("待发 %d 条（最新的在最上），当前取前 %d 条：\n" % (len(todo), n))
    for c in todo[:n]:
        has = os.path.isfile(os.path.join(OUT, c["id"], "meta.json"))
        print("  %-24s %-28s %s" % (c["id"], c.get("name", ""),
                                    "" if has else "（没稿，会现造）"))
    print("\n要发就跑：python scripts/toutiao_publish.py publish --near %d" % n)


def _diagnose_failure(c, cdp=None):
    """publish_one 失败后跑一次四态对账，把「失败」翻译成本质结论。

    为什么需要（2026-10-17）：失败只说「标题框没找到」「自动保存没等到」，
    用户看不出该怎么办。真实成因常常是**这条已经发出去了**（你后台群发过
    ⇒ 重发会出重复稿）或**远端根本没有**（重试无用）。
    `reconcile_verdict` 把这套判据共用给四平台，这里只是接一下。

    诊断本身失败**绝不阻断**主流程 —— 它是锦上添花，不是发布的一环。
    """
    try:
        import reconcile_verdict as rv
        if cdp is None:
            cdp, t = _open(TT_HOME, wait=6)
            try:
                p = rv.probe(cdp, "toutiao")
            finally:
                cdp.close_target(t["id"])
        else:
            p = rv.probe(cdp, "toutiao")
        print(rv.explain("toutiao", c["id"], c, p,
                         title_maker=make_toutiao_title))
    except Exception as e:                                   # noqa: BLE001
        print("    [诊断跳过] %s（不影响发布结论）" % str(e)[:80])


def cmd_publish(args):
    if args.case:
        todo = [find_case_fuzzy(args.case)]
    else:
        pool = list(load_cases()) if args.force else pending_cases(need_built=False)
        if args.skip:
            pool = [c for c in pool
                    if c["id"] not in {s.strip() for s in args.skip.split(",")}]
        todo = pool if args.all else pool[:max(1, args.near)]
    if not todo:
        print("没有待发的案例。")
        return
    failed = []
    if args.api and not args.dry:
        # 批量走 API：token **取一次用一批**（唯一约束是要新鲜，
        # 逐条重取既慢又每次都留探针稿）。
        # 🚨 全流程同一个编辑器 tab（首页 tab 会被 argus 挡，见 open_api_session）。
        sess = open_api_session()
        token = None
        if not sess:
            print("[api] 编辑器页开不了（Chrome 没起来/掉登录）⇒ 退 UI 路线")
        else:
            cdp, tid = sess
            token = grab_anti_token(cdp)
            print("[api] tt-anti-token：%s"
                  % (("已取（%d 字符）" % len(token)) if token else "没取到"))
            if token:
                # 🚨🚨 **不要在 publish 之前调 cleanup_probe_draft**。
                # 2026-10-17 实测：cleanup 会连着发好几轮 draft_list + delete，
                # 紧接着的 publish 就被 argus 风控挡成 HTML（症状与缺 token
                # 不同 —— 缺 token 返 JSON 7050，argus 返 <!doctypehtml>）。
                # 对照实验：中间只插 3 次 delete 仍 code:0，但 cleanup 默认
                # rounds=3×pause=3s 的量级就会触发。
                # ⇒ 顺序固定为「取 token → 立刻连打 → **最后**统一清理」。
                for i, c in enumerate(todo, 1):
                    print("[%d/%d] %s —— %s"
                          % (i, len(todo), c["id"], c.get("name", "")))
                    try:
                        ok = bool(publish_api_one(c, cdp=cdp,
                                                  anti_token=token,
                                                  verify=False,
                                                  with_cover=not args.no_cover))
                    except SystemExit:
                        raise
                    except Exception as e:              # noqa: BLE001
                        ok = False
                        print("    [异常] %s：%s" % (type(e).__name__, e))
                    if not ok:
                        failed.append(c["id"])
                        _diagnose_failure(c, cdp=cdp)
                print("[api] 清理探针草稿 %d 条" % cleanup_probe_draft(cdp))
                # 整批存完后**统一核对一次**（而不是逐条）—— draft_list
                # 也是被 argus 记账的请求，逐条回读会把后续 publish 顶进风控。
                try:
                    raw = fetch_draft_api_raw(cdp) or []
                    titles = [x.get("title") or "" for x in raw]
                    missing = []
                    for c in todo:
                        try:
                            t = make_toutiao_title(c)
                        except Exception:              # noqa: BLE001
                            continue
                        if not draft_title_match(c["id"], c, titles):
                            missing.append(c["id"])
                    if missing:
                        print("[api] ⚠ 回读核对：%d 条草稿箱里没找到 %s"
                              % (len(missing), " ".join(missing)))
                        print("     （可能是同标题被服务端合并，也可能被风控吞）")
                    else:
                        print("[api] 回读核对：✓ %d 条全部命中草稿箱"
                              % len(todo))
                except Exception as e:                  # noqa: BLE001
                    print("[api] [warn] 回读核对异常：%s" % str(e)[:60])
            try:
                cdp.close_target(tid)
            except Exception:                           # noqa: BLE001
                pass
        if not token:
            print("[api] 退回 UI 路线")
        else:
            if failed:
                print("\n失败 %d 条：%s"
                      % (len(failed), " ".join(failed)))
                print("（每条的四态结论见上；MISSING 只能重建，"
                      "RATE_LIMIT 等几分钟重跑）")
            return
    for i, c in enumerate(todo, 1):
        print("[%d/%d] %s —— %s" % (i, len(todo), c["id"], c.get("name", "")))
        ok = True
        try:
            ok = bool(publish_one(c, dry=args.dry, yes=args.yes,
                                  cover=not args.no_cover))
        except SystemExit:
            raise
        except Exception as e:                        # noqa: BLE001
            ok = False
            print("    [异常] %s：%s" % (type(e).__name__, e))
        if not ok and not args.dry:
            failed.append(c["id"])
            _diagnose_failure(c)
    if failed and not args.dry:
        print("\n失败 %d 条：%s" % (len(failed), " ".join(failed)))
        print("（每条的四态结论见上；MISSING 只能重建，RATE_LIMIT 等几分钟重跑）")


def _refresh_plan(box, only_case=None):
    """扫草稿箱，列出「线上标题 ≠ 本地最新标题」的待刷新清单。

    返回 [(gid, case, online_title, local_title)]。已发表的跳过
    （群发后稿件离开草稿箱是正常行为，但**草稿箱里若躺着已发表的同篇**
    就是残留，不该刷新——交给 scan_pub_dupes 删）。
    """
    raw = fetch_draft_api_raw(box) or []
    try:
        pub = fetch_published_articles(box, max_page=3) or []
    except Exception:                              # noqa: BLE001
        pub = []
    pub_blob = [re.sub(r"\s+", "", a.get("title") or "") for a in pub]
    plan = []
    for x in raw:
        t = (x.get("title") or "").strip()
        if not t:
            continue
        c = find_case_for_title(t)
        if c is None:
            continue
        if only_case and c["id"] != only_case:
            continue
        # 线上标题与某篇已发表「互相包含」⇒ 这是已发表残留，不刷新
        nt = re.sub(r"\s+", "", t)
        if any(nt and (nt in p or p in nt) for p in pub_blob):
            continue
        try:
            local = make_toutiao_title(c)
        except Exception:                          # noqa: BLE001
            continue
        if local != t:
            plan.append((x.get("gid"), c, t, local))
    return plan


def cmd_refresh(args):
    """按最新逻辑刷新存量草稿（2026-10-17：17 篇残渣/旧标题批量刷新）。

    每条流程：重建 meta（含最新标题）→ 删旧稿 → 建新稿（含封面）。
    🚨 必须先重建 meta：`publish_api_one` 默认「meta 不在才 build」，
      存量刷新的 case meta 都在 ⇒ 不重建建出来的还是**旧标题**
      （API 照样返「保存成功」，2026-10-17 实测踩过）。
    🚨 需要**有头 Chrome**（`grab_anti_token` 靠编辑器自动保存触发，
      无头抓不到 token ⇒ CHROME_HEADED=1 起 9223）。
    """
    if args.case and args.all:
        print("--case 和 --all 二选一")
        return
    sess = open_api_session()
    if not sess:
        print("编辑器页开不了（Chrome 没起来/掉登录）")
        return
    box, tid = sess
    plan = _refresh_plan(box, only_case=args.case)
    if not plan:
        print("没有需要刷新的草稿（线上标题都已等于本地最新逻辑）。")
        return
    print("待刷新 %d 条：" % len(plan))
    for gid, c, online, local in plan:
        print("  %s [%s]\n    线上: %s\n    本地: %s" % (gid, c["id"],
                                                    online[:44], local[:44]))
    if args.dry:
        print("（--dry 只列清单，不动手）")
        return
    token = grab_anti_token(box)
    if not token:
        print("没取到 tt-anti-token（需要有头 Chrome：CHROME_HEADED=1）")
        return
    failed = []
    for i, (gid, c, online, local) in enumerate(plan, 1):
        print("[%d/%d] %s —— 删旧 %s" % (i, len(plan), c["id"], gid))
        st = delete_draft_api(box, gid, draft_type=2)
        if st != "OK":
            print("    删旧失败：%s（跳过，不建新——避免双稿）" % st)
            failed.append(c["id"])
            continue
        try:
            ok = bool(publish_api_one(c, cdp=box, anti_token=token,
                                      verify=False, with_cover=True,
                                      rebuild=True))
        except Exception as e:                      # noqa: BLE001
            ok = False
            print("    [异常] %s：%s" % (type(e).__name__, e))
        if not ok:
            failed.append(c["id"])
    print("清理探针草稿 %d 条" % cleanup_probe_draft(box))
    # 整批统一核对一次（逐条回读会把后续 publish 顶进风控）
    time.sleep(3)
    raw = fetch_draft_api_raw(box) or []
    online_titles = [x.get("title") or "" for x in raw]
    miss = []
    for gid, c, online, local in plan:
        if c["id"] in failed:
            continue
        if not draft_title_match(c["id"], c, online_titles):
            miss.append((c["id"], local))
    if miss:
        print("⚠ 回读核对：%d 条没命中 %s" % (len(miss), miss))
    else:
        print("回读核对：✓ %d 条全部命中草稿箱" % len(plan))
    if failed:
        print("失败 %d 条：%s" % (len(failed), " ".join(failed)))


def _name_keys(cid, c):
    """从 case id / name 提取产品名匹配键（小写）。

    对账专用：远端标题口径会变（2026-10-16 P0 实测三平台远端全是
    「产品名：描述」新口径，而 make_*_title 生成的是「月收$xK：…」
    旧口径），字面前 N 字符匹配必然失配 ⇒ 用产品名做子串匹配。
    id 去常见后缀（-io/-ai/-app/-9e3eaa 等），name 取冒号前段。
    """
    keys = set()
    core = re.sub(r"-(io|ai|app|co|com|us|b-v|net|9e3eaa|[0-9a-f]{6})$",
                  "", cid or "")
    if len(core) >= 4:
        keys.add(core.lower())
    nm = (c.get("name") or "").strip()
    if nm:
        head = re.split(r"[：:]", nm)[0].strip()
        if len(head) >= 4:
            keys.add(head.lower())
        if len(nm) >= 4:
            keys.add(nm.lower())
    if len(cid or "") >= 5:
        keys.add(cid.lower())
    return keys


def draft_title_match(cid, c, remote_titles):
    """本地 case 是否命中远端草稿标题列表（产品名优先，字面兜底）。"""
    keys = _name_keys(cid, c)
    for t in remote_titles:
        tl = (t or "").lower()
        if any(k in tl for k in keys):
            return t
    # 前缀互补兜底（P1 实测漏判）：远端标题用缩写/简称时子串匹配失效，
    # 如「PromptMon：盯 AI 里的品牌」vs id=promptmonitor-io、
    # 「Divine：给站长卖网站挂件」vs id=divine-widgets。
    # 判据：本地键与远端标题任一词互为前缀（长度≥5，防误伤）。
    for t in remote_titles:
        tl = (t or "").lower()
        twords = [w for w in re.split(r"[^a-z0-9]+", tl) if len(w) >= 5]
        for k in keys:
            for w in twords:
                if w.startswith(k) or k.startswith(w):
                    return t
    # 兜底：生成标题的前 12 字符（老口径，给没产品名的标题用）
    try:
        gt = make_toutiao_title(c)
    except Exception:
        gt = ""
    key = (gt or "")[:12]
    if len(key) >= 6:
        for t in remote_titles:
            if key in t:
                return t
    return None


def _article_api_url(offset, page, count=10):
    """已发布图文列表 API URL（2026-10-16 P1 实证，用户提供）。

    ⚠⚠ **只能浏览器导航式访问**（Page.navigate 到这个 URL，body.innerText
    就是 JSON）——同页面内 fetch 会被风控挡（返回 argus HTML），
    curl 带完整 cookie 也返 login_status:0 空数据。三种方式只通这一种。
    翻页：offset=0/10/20... + page_index 同步递增，has_more=false 止。
    """
    return ARTICLE_API_TPL.replace("@OFF@", str(offset)) \
                          .replace("@PG@", str(page))


def _extract_article_title(item):
    """从 data[i] 挖标题。路径（实测）：assembleCell.itemCell.articleBase.title。
    abstractText/content 字段为空 ⇒ 拿不到摘要，只能标题级匹配。"""
    try:
        ac = item.get("assembleCell") or {}
        ic = ac.get("itemCell") or {}
        ab = ic.get("articleBase") or {}
        return (ab.get("title") or "").strip()
    except Exception:
        return ""


def fetch_published_articles(cdp, max_page=5):
    """已发布图文列表（文章 tab 的 API 版，2026-10-16 P1 实证）。

    走 _article_api_url + 浏览器导航式读取，返回 [{title, url}]；
    失败/空返回 []（上层可退 UI 两页收集）。

    实测：本号 20 条（has_more 第 2 页即 false），与 UI 版逐条一致。
    """
    import json as _json
    out = []
    try:
        tid = cdp.new_target(TT_ARTICLES_PAGE)["id"]
    except Exception as e:
        print("[warn] fetch_published_articles 开不了页：%s" % e)
        return []
    box = wp.CDP(CDP_PORT)
    try:
        box.connect_target(tid)
        time.sleep(4)
        for pg in range(1, max_page + 1):
            off = (pg - 1) * 10
            try:
                box.send("Page.navigate",
                         {"url": _article_api_url(off, pg)})
                time.sleep(3.5)
                txt = str(box.eval("(document.body.innerText||'')") or "")
            except Exception as e:
                print("[warn] 第 %d 页读取失败：%s" % (pg, e))
                break
            if not txt.startswith("{"):
                break
            try:
                d = _json.loads(txt)
            except Exception:
                break
            data = d.get("data") or []
            for it in data:
                t = _extract_article_title(it)
                if t:
                    out.append({"title": t, "url": ""})
            if not d.get("has_more") or not data:
                break
    finally:
        try:
            box.close_target(tid)
        except Exception:
            pass
    return out


def _publish_api_url():
    """草稿保存/发布 API URL（2026-10-17 抓包实证 + JS 反编译交叉验证）。

    ⚠ **body 是 form-urlencoded，不是 JSON**（用 JSON 发会 HTTP200 但不生效
    —— 今天踩过：200 ≠ 存上了）。
    """
    return ("/mp/agw/article/publish?source=mp&type=article&aid=1231"
            "&mp_publish_ab_val=0")


def _cover_upload_urls():
    """封面/图片上传的两级接口（2026-10-17 hook 抓包实证，别猜）。

    ① POST ``/spice/image?upload_source=20020003&aid=1231&device_platform=web``
       —— multipart/form-data，字段名就是 ``image``（<blob>）。
       返图床 URL（``...~tplv-obj.image``）+ ``uri``（``tos-cn-i-xxx/<id>``）。
    ② POST 同上再加 ``&need_cover_url=1``，body 是 form 字段
       ``imageUrl=<①的URL>``，**必须带 tt-anti-token**。
       返真正的封面直链（``...~tplv-tt-cover-v2.image``）—— 这才是
       publish body 里 ``pgc_feed_covers[].url`` 要填的东西。
    """
    base = "/spice/image?upload_source=20020003&aid=1231&device_platform=web"
    return base, base + "&need_cover_url=1"


def upload_cover_api(cdp, png_path, anti_token=None, timeout=120):
    """纯 API 传一张本地封面，返回 `(cover_url, cover_uri, msg, (宽, 高))`。

    🚨 **不需要开编辑器、不点任何按钮** —— 图片以 base64 注入页面，
    在页面上下文里组 FormData 上传（页面本身就是最干净的签名环境）。

    与 `upload_cover`（UI 路线）的差别：UI 要点封面块 → 等图库面板 →
    上传 → 点选 → 确定，headless 下脆且慢；这里两次 fetch 搞定。

    🚨 **第①步返回的字段名是 `image_uri` / `image_url`（不是 `uri`/`url`）**
    （2026-10-17 实测）—— 抽错字段会得到空 uri，然后 `save_draft_api` 报
    `code:4009 第1个封面数据异常，请重新上传`。同理第②步的封面直链在
    `data.cover_url`（`data.image_url` 是 obj 原图，**不是**封面图）。

    返回 `(None, None, 原因, (0,0))` 表示失败 —— **不静默**，
    由调用方决定退不退 UI 路线。
    """
    fail = lambda m: (None, None, m, (0, 0))                  # noqa: E731
    if not png_path or not os.path.isfile(png_path):
        return fail("本地封面不存在：%s" % png_path)
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    up_url, cover_url_api = _cover_upload_urls()
    tok_hdr = (",'tt-anti-token':%s" % json.dumps(anti_token)
               if anti_token else "")
    # ⚠ 三个坑都在这行代码里，改的时候别动：
    #   ① 所有字符串都走 json.dumps —— 裸中文进 Runtime.evaluate 会坏（见
    #      save_draft_api 的对照实验：症状长得像 argus，其实是编码坏了）；
    #   ② 第②步要 tt-anti-token，缺了大概率报错（不是 7050，别混）；
    #   ③ 第①步是 multipart，不能手拼 content-type（得让浏览器自己带边界）。
    js = (
        "(async function(){"
        "var bin=atob(@B@);var u=new Uint8Array(bin.length);"
        "for(var i=0;i<bin.length;i++){u[i]=bin.charCodeAt(i);}"
        "var fd=new FormData();"
        "fd.append('image',new Blob([u],{type:'image/png'}),'cover.png');"
        "var r1=await fetch(@U1@,{method:'POST',credentials:'include',"
        "headers:{'Accept':'application/json, text/plain, */*'},body:fd});"
        "var t1=await r1.text();"
        "var j1=null;try{j1=JSON.parse(t1);}catch(e){}"
        "var d1=(j1&&j1.data)||{};"
        "var img=d1.image_url||d1.url||'';"
        "var uri=d1.image_uri||d1.uri||'';"
        "var w=d1.image_width||0,h=d1.image_height||0;"
        "var r2=await fetch(@U2@,{method:'POST',credentials:'include',"
        "headers:{'Accept':'application/json, text/plain, */*',"
        "'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'@TK@},"
        "body:'imageUrl='+encodeURIComponent(img)});"
        "var t2=await r2.text();"
        "var j2=null;try{j2=JSON.parse(t2);}catch(e){}"
        "var d2=(j2&&j2.data)||{};"
        "var cu=d2.cover_url||d2.url||'';"
        "if(!cu){try{var m2=t2.match(/\"cover_url\"\\s*:\\s*\"([^\"]+)\"/);"
        "if(m2)cu=m2[1];}catch(e){}}"
        "return JSON.stringify({s1:r1.status,s2:r2.status,"
        "img:img,uri:uri,w:w,h:h,cover:cu,r1:t1.slice(0,3000),"
        "r2:t2.slice(0,3000)});"
        "})()"
    ).replace("@B@", json.dumps(b64)).replace("@U1@", json.dumps(up_url)) \
        .replace("@U2@", json.dumps(cover_url_api)).replace("@TK@", tok_hdr)
    try:
        out = str(cdp.eval(js) or "")
    except Exception as e:                                        # noqa: BLE001
        return fail("上传异常：%s" % str(e)[:80])
    if not out.startswith("{"):
        if "argus" in out:
            return fail("上传被 argus 风控挡了（先取新鲜 token）")
        return fail("上传返回非 JSON：%s" % out[:120])
    try:
        d = json.loads(out)
    except ValueError:
        return fail("上传返回解析失败：%s" % out[:120])
    if os.environ.get("TT_DEBUG_COVER"):
        print("      [upload r1] %s" % (d.get("r1") or "")[:900])
        print("      [upload r2] %s" % (d.get("r2") or "")[:900])
    wh = (int(d.get("w") or 0), int(d.get("h") or 0))
    if not d.get("cover"):
        return fail("第②步没返封面直链 s1=%s s2=%s r2=%s"
                    % (d.get("s1"), d.get("s2"), d.get("r2")))
    if not d.get("uri"):
        return fail("第①步没返 image_uri（字段名变了？）r1=%s" % (d.get("r1") or "")[:200])
    return d["cover"], d["uri"], "ok", wh


def _publish_draft_body(title, content_html, pgc_id=None, abstract_text="",
                        cover_url="", word_cnt=None, cover_uri="",
                        thumb_w=3840, thumb_h=2160):
    """草稿保存 body（20 个 form 字段，2026-10-17 抓包实证逐字段对齐）。

    实证要点：
    - `save=0` 才是**存草稿**（不是发布！）——第一版猜反了；
    - 首次保存**不带** pgc_id（=创建），后续带（=更新）；
    - `extra.content_word_cnt` 由前端算，API 调用可直接留 0（服务端重算）；
    - `title_id` 形如 `<毫秒时间戳>_<16位随机>`，用当前时间生成即可；
    - 🚨 **封面走 `pgc_feed_covers`，不是 `cover_tos_url`**（2026-10-17
      带封面的真实抓包对齐，旧注释里的 `cover_tos_url` 是我的臆测，字段名
      根本不会被服务端认）。结构（真实抓包原文）：

          pgc_feed_covers=[{"id":"","url":"https://image-tt-private.toutiao.com
            /tos-cn-i-6w9my0ksvp/e266…~tplv-tt-cover-v2.image?…",
            "uri":"tos-cn-i-6w9my0ksvp/97ca…","ic_uri":"",
            "thumb_width":3840,"thumb_height":2160,
            "extra":{"from_content_uri":"","from_content":"0"}}]

      url 是 `/spice/image?…&need_cover_url=1` 的产物（`upload_cover_api`
      拿），uri 是第①步上传返回的原始对象 uri；缺 uri 时留空也能落库，
      但服务端可能因此重算裁剪 —— 实测带 uri 更稳。
    """
    extra = {
        "content_source": 100000000402,
        "content_word_cnt": word_cnt or 0,
        "is_multi_title": 0,
        "sub_titles": [],
        "gd_ext": {"entrance": "", "from_page": "publisher_mp",
                   "enter_from": "PC", "device_platform": "mp",
                   "is_message": 0},
        "tuwen_wtt_transfer_switch": "1",
    }
    search_info = {"searchTopOne": 0, "abstract": abstract_text or "",
                   "clue_id": ""}
    covers = []
    if cover_url:
        covers = [{"id": "", "url": cover_url, "uri": cover_uri or "",
                   "ic_uri": "", "thumb_width": thumb_w,
                   "thumb_height": thumb_h,
                   "extra": {"from_content_uri": "", "from_content": "0"}}]
    fields = {
        "source": "29",
        "title": title or "",
        "content": content_html or "",
        "save": "0",                      # 0 = 存草稿（实证）
        "timer_status": "0",
        "entrance": "",
        "activity_tag": "0",
        "article_ad_type": "3",
        "claim_exclusive": "0",
        "disable_praise": "0",
        "praise": "0",
        "govern_forward": "0",
        "is_fans_article": "0",
        "is_refute_rumor": "0",
        "tree_plan_article": "0",
        "trends_writing_tag": "0",
        "mp_editor_stat": "{}",
        "pgc_feed_covers": json.dumps(covers, ensure_ascii=False),
        "draft_form_data": json.dumps({"coverType": 2}, ensure_ascii=False),
        "search_creation_info": json.dumps(search_info, ensure_ascii=False),
        "extra": json.dumps(extra, ensure_ascii=False),
        "title_id": "%d_%d" % (int(time.time() * 1000),
                               int(time.time() * 1e6) % 10000000000000000),
    }
    if pgc_id:
        fields["pgc_id"] = str(pgc_id)
    return urlencode(fields)


# hook 抓「真实」publish 请求的 header。⚠ 三个 hook 分支（fetch / XHR
# open / XHR send）**都要记 send 的 body** —— 上一轮漏抓 body + header
# 导致返工三次（hook 三件套：URL / header / body，缺一不可）。
_HOOK_ANTI_TOKEN = r"""
(function(){
  if (window.__ttAntiHook) { return 'already'; }
  window.__ttAntiHook = true;
  window.__antiRec = [];
  var F = window.fetch;
  window.fetch = function(input, init){
    try {
      var u = (typeof input === 'string') ? input : (input && input.url);
      if (u && /article\/publish/.test(u)) {
        var h = (init && init.headers) || {};
        var flat = {};
        try {
          if (typeof Headers !== 'undefined' && h instanceof Headers) {
            h.forEach(function(v, k){ flat[k] = String(v); });
          } else { for (var k in h) { flat[k] = String(h[k]); } }
        } catch (e) { flat = {}; }
        if (flat['tt-anti-token']) {
          window.__antiRec.push(flat['tt-anti-token']);
        }
      }
    } catch (e) {}
    return F.apply(this, arguments);
  };
  var XO = XMLHttpRequest.prototype.open;
  var XS = XMLHttpRequest.prototype.send;
  var XSH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(m, u){
    this.__u = u; return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(k, v){
    try {
      if (!this.__h) { this.__h = {}; }
      this.__h[k] = String(v);
    } catch (e) {}
    return XSH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b){
    try {
      if (this.__u && /article\/publish/.test(this.__u) &&
          this.__h && this.__h['tt-anti-token']) {
        window.__antiRec.push(this.__h['tt-anti-token']);
      }
    } catch (e) {}
    return XS.apply(this, arguments);
  };
  return 'hooked';
})()
"""


def grab_anti_token(cdp, attempts=4, per_attempt=25, page=None):
    """从**真实**页面流量里捞一个新鲜的 `tt-anti-token`。

    ## 为什么这么做（2026-10-17 定案，纠正了上午的错误结论）

    上午的错误结论是：「token 带时效，所以纯 API 化需逆向其生成算法、不可行」。
    **那个结论是错的**，错在把「token 过期」和「token 缺失」的 7050 当成一回事
    （两者返回的报文完全一样，光看状态码根本分不开）。

    同会话对照实验（tmp/tt_fresh_replay.py，四段全部 code:0 保存成功）：
      A hook 抓真实请求头 → 只有 3 个：Accept / Content-Type / **tt-anti-token**
      B 同页即时重放「原 body + 抓到的头」 → code:0 ✅
      C 同一 token **换 body（换标题）**      → code:0 ✅
      D 我方 `_publish_draft_body()` + 抓到的头 → code:0 ✅（3 个 pgc_id 各不相同）

    ⇒ 三件事同时被证伪：① 无需逆向生成算法；② token **不绑定 body**
       （换标题照样过）；③ 我方 20 字段 body 形态本来就是对的。

    ### token 有效期实测（tmp/tt_token_ttl.py）

    取一次 token，间隔 0/3/6/10/20s 各打一次：**5/5 全部 code:0**
    （0s、6.5s、13.1s、19.7s、26.3s，各得一个不同 pgc_id）
    ⇒ 既**不短命**（>26s 有效）也**非一次性**（可重复用）。
    所以批量时「取一次用一批」完全成立，不必逐条重取。

    ⚠ 但有个**前置条件**：请求头必须**三个齐全**
    （`Accept` / `Content-Type` / `tt-anti-token`）。少 `Accept` 会被 argus
    风控挡成 HTML（症状与 token 问题完全不同，别混）。

    真正唯一的约束是：**token 必须新鲜**（抓完立刻用）。

    ## 怎么拿

    不用逆向 —— 让页面自己生成：在**编辑器页**挂 hook，再触发一次它自己的
    自动保存（填一下标题），token 就被记下来了。代价是**留一条探针草稿**，
    由调用方 `cleanup_probe_draft()` 删掉。

    🚨 **cdp 必须已连在编辑器页上**（用 `open_api_session()` 开），理由两条：
      · 首页 tab 里 `fetch('article/publish')` 被 argus 风控挡回来，返的不是
        JSON 而是 `<!doctypehtml>…argus…` 验证页（2026-10-17 实测）；
      · 本函数不再自己 connect/close target —— 那会把调用方的 `cdp.ws`
        切走又关掉，后续请求直接 WinError 10053（同日实测）。
        「取 token」和「存草稿」必须在**同一个编辑器 tab** 里跑完。

    ⚠ **必须是有头 Chrome**：无头不触发编辑器自己的自动保存 ⇒ 抓不到 token
    （2026-10-17 实测，`os.environ["CHROME_HEADED"]="1"` 才能起有头实例）。

    返回 token 字符串；拿不到返回 None（上层会退 UI 路线，不静默失败）。
    """
    if not os.environ.get("CHROME_HEADED"):
        print("    [warn] grab_anti_token 需要有头 Chrome（CHROME_HEADED=1）："
              "无头不触发自动保存 ⇒ 拿不到 token")
    # ⚠ 自动保存**不一定每次都触发**（实测约 40% 抓不到：可能是页面刚挂载
    #   时序问题、或标题框还没就绪、或那次保存被 argus 挡了没发出去）。
    #   所以要多轮重试：每轮换一个标题（避开去重），重新聚焦再打一次。
    for attempt in range(max(1, attempts)):
        try:
            cdp.eval(_HOOK_ANTI_TOKEN)
            _focus_and_type(cdp, SEL["title"], "__probe_tok%d__" % attempt)
            end = time.time() + per_attempt
            while time.time() < end:
                time.sleep(1.5)
                tok = cdp.eval("(window.__antiRec||[])[window.__antiRec.length-1]"
                               " || ''")
                if tok:
                    return str(tok)
        except Exception as e:                               # noqa: BLE001
            print("    [warn] 取 token 异常：%s" % str(e)[:70])
    print("    [warn] %d 轮都没抓到 token" % max(1, attempts))
    return None


def open_api_session(wait=10):
    """开一个**编辑器页会话**，返回 (cdp, tid)；连不上/掉登录返回 None。

    🚨 为什么必须全程待在编辑器页（2026-10-17 实测踩坑）：

    在**首页** tab 里 `fetch('article/publish')` 会被 argus 风控挡回来 ——
    返回的不是 JSON 而是 `<!doctypehtml><html>…argus…` 的验证页。
    同样请求在**编辑器页** tab 里 `code:0` 保存成功。
    原因：argus 的签名注入挂在编辑器自己的 chunk 上，只有那个页面上下文才有。
    所以「取 token → 存草稿 → 核对 → 清理」全流程必须同一个编辑器 tab。
    """
    cdp = wp.CDP(CDP_PORT)
    tid = cdp.new_target(TT_EDITOR)["id"]
    if not cdp.connect_target(tid):
        return None
    time.sleep(wait)
    if "/auth/page/login" in str(cdp.eval("location.href") or ""):
        try:
            cdp.close_target(tid)
        except Exception:                                   # noqa: BLE001
            pass
        return None
    return cdp, tid


def cleanup_probe_draft(cdp, title_prefix="__probe", rounds=3, pause=3.0):
    """删掉 grab_anti_token 留下的探针草稿（用 delete_draft_api）。

    ⚠ **必须重试**：hook 抓到 token 时，页面那次自动保存**可能还没落库**
    （2026-10-17 实测首轮 `draft_list` 里还没有，过几秒才出现）。
    只查一次会「删 0 条」然后留下垃圾稿 —— 而 `draft_list` 读到的条数
    是唯一判据，所以宁可多等几轮。
    """
    total = 0
    for _ in range(max(1, rounds)):
        try:
            raw = fetch_draft_api_raw(cdp) or []
        except Exception:                                   # noqa: BLE001
            return total
        victims = [x for x in raw
                   if (x.get("title") or "").startswith(title_prefix)]
        if not victims:
            if total:
                break          # 删过一轮且这轮空了 ⇒ 收工，别再轮询
            time.sleep(pause)  # 一条都没删过 ⇒ 多半是「还没落库」，再等
            continue
        for x in victims:
            if delete_draft_api(cdp, x.get("gid"),
                                x.get("draft_type") or 2) == "OK":
                total += 1
        time.sleep(pause)
    return total


def cleanup_titles(cdp, titles, rounds=3, pause=2.5):
    """按**精确标题**删草稿（探针脚本收尾专用）。

    为什么要有它（2026-10-17 实测教训）：`cleanup_probe_draft` 只匹配
    `__probe` 前缀 —— 探针脚本用中文测试标题时**全部漏删**（`tt_title.py`
    的「中文标题探针」等 7 条实留在草稿箱里，靠按 gid 手工删才清掉）。
    精确标题匹配不依赖命名约定，探针脚本删自己创建的那些标题即可。

    ⚠ 调用方负责保证标题**只属于测试稿**：同一个标题下可能躺着正稿
    （今天 3 条 TrustMRR 无封面测试稿与 10-15 有封面正稿同标题），
    精确匹配分不开它们 —— 那种情况按 gid 删，别用本函数。

    删后核对内置于 `delete_draft_api`（服务端对假 gid 也返 code:0，
    「API 返 OK ≠ 删掉」）。返回删除条数。
    """
    want = {t for t in (titles or []) if t}
    if not want:
        return 0
    total = 0
    for _ in range(max(1, rounds)):
        try:
            raw = fetch_draft_api_raw(cdp) or []
        except Exception:                                   # noqa: BLE001
            return total
        victims = [x for x in raw if (x.get("title") or "") in want]
        if not victims:
            if total:
                break          # 删过一轮且这轮空了 ⇒ 收工
            time.sleep(pause)  # 一条都没删过 ⇒ 多半还没落库，再等
            continue
        for x in victims:
            if delete_draft_api(cdp, x.get("gid"),
                                x.get("draft_type") or 2) == "OK":
                total += 1
        time.sleep(pause)
    return total


def save_draft_api(cdp, title, content_html, pgc_id=None, abstract_text="",
                   anti_token=None, cover=None):
    """新建/更新一篇头条草稿（**纯 API 路线**，2026-10-17 实测可用）。

    返回 (pgc_id, msg)：成功 pgc_id 非空；失败 msg 为原因。

    🚨 **`tt-anti-token` 是必需 header**（2026-10-17 实证，缺则恒 7050）：
      - 形如 `HOikR1wqgY-6a2d77a42…`（75~78 字符，前缀每次不同），页面运行时
        生成、**不在 HTML / window / cookie 里**（用户 2026-10-17 提供的完整
        cookie 清单里也没有它 —— 那份 cookie 只有 sessionid / csrf / odin_tt
        / ttwid 之类的**会话身份**，不含任何反滥用凭证）。
      - 它**不是** msToken（`mssdk.bytedance.com/web/r/token` 签发的是
        msToken，挂 `_msToken` cookie，服务内容**读取**接口的风控；两者别混）。
      - **只需新鲜，不必逆向**（见 `grab_anti_token` 的四段对照实验）。

    ⚠ 另有独立坑：body 必须 **form-urlencoded**，用 JSON 发 HTTP200 但
    不生效（服务端返 7050，不是宽容返 OK）。

    🚨 **两个独立的失败形态，别混**（2026-10-17 各实测踩过）：
      · 缺 `tt-anti-token` → `{"code":7050,"message":"保存失败"}`（JSON 正常返回）
      · 缺 `Accept` 头        → 返回 **argus 风控页**（`<!doctypehtml>…argus…`，
        不是 JSON ⇒ `save_draft_api` 里 `not out.startswith("{")` 那个分支
        报 `ERR:<!doctypehtml>…`）。这两个症状完全不同，别都当成 token 问题。
      真实请求头一共只有 3 个：`Accept` / `Content-Type` / `tt-anti-token`。

    anti_token=None 时不自己去找（保持纯函数），由 `publish_api_one` 负责取。

    `cover` 传 `upload_cover_api()` 的返回三元组（url, uri, msg）或 None；
    封面信息走 `pgc_feed_covers` 字段（见 `_publish_draft_body` 实证结构）。
    """
    cover_url, cover_uri = (cover[0], cover[1]) if cover else ("", "")
    wh = (cover[3] if cover and len(cover) > 3 else (0, 0))
    body = _publish_draft_body(title, content_html, pgc_id, abstract_text,
                               cover_url=cover_url, cover_uri=cover_uri,
                               thumb_w=wh[0] or 3840, thumb_h=wh[1] or 2160)
    hdr = {"Accept": "application/json, text/plain, */*",
           "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}
    if anti_token:
        hdr["tt-anti-token"] = anti_token
    # 🚨🚨 **必须 json.dumps 转义后再拼进 JS，不能原始字符串替换**
    # （2026-10-17 实测，同会话同 token 同 body 的对照）：
    #   · `body:'@B@'.replace("@B@", body)` 原始塞中文 → 🚫 argus 风控页
    #   · `body:%s` % json.dumps(body)      转义后 → ✅ code:0
    # 原因：原始替换把**裸 UTF-8 中文**直接写进 CDP `Runtime.evaluate`
    # 的表达式源码，CDP 通道传输/解析后内容被破坏（body 里 `extra`、
    # `search_creation_info` 等字段全是 JSON 串，一坏就整篇废掉）。
    # json.dumps 默认 ensure_ascii=True ⇒ 源码纯 ASCII，跨通道绝对安全。
    # ⚠ 这与「缺 token(7050)/缺 Accept(argus)」是三个独立故障，别混。
    js = ("(function(){"
          "return fetch(%s,{method:'POST',credentials:'include',"
          "headers:%s,body:%s})"
          ".then(function(r){return r.text();})"
          ".catch(function(e){return 'ERR:'+e.message;})})()"
          % (json.dumps(_publish_api_url()), json.dumps(hdr),
             json.dumps(body)))
    try:
        out = str(cdp.eval(js) or "")
    except Exception as e:
        return "", "ERR:%s" % e
    if not out.startswith("{"):
        return "", "ERR:%s" % out[:80]
    try:
        d = json.loads(out)
    except Exception:
        return "", "ERR:%s" % out[:80]
    if d.get("code") != 0:
        return "", "code=%s %s" % (d.get("code"), d.get("message"))
    data = d.get("data") or {}
    return str(data.get("pgc_id") or ""), d.get("message") or "ok"


def _delete_draft_api_url():
    """草稿删除 API URL（2026-10-16 P1 hook 抓包实证）。"""
    return "/mp/agw/creator_center/delete_draft?app_id=1231"


def _delete_draft_api_body(gid, draft_type=2):
    """删除 API 的 body（hook 抓真实流量，非猜测）。

    ⚠⚠ 真实形态是 **drafts 数组包装**：
        {"drafts":[{"draft_type":2,"gid":"<gid>"}]}
    猜过的错误形态：{"gid":...} / form gid=... —— 服务端对**任意 body**
    都返 {"code":0,"success}（幂等宽容，公众号 delete 同款坑），
    但形态不对就不真删 ⇒ 「API 返 OK ≠ 删掉」，必须删后核对。
    """
    return json.dumps({"drafts": [{"draft_type": draft_type,
                                   "gid": str(gid)}]},
                      ensure_ascii=False)


def delete_draft_api(cdp, gid, draft_type=2):
    """删一篇草稿（API 一击 + 删后核对，2026-10-16 P1 实证）。

    流程：POST delete_draft（_delete_draft_api_body 形态）→ 重拉
    draft_list 核对 gid 真消失。服务端宽容（假 gid 也 success），
    所以**核对是唯一可信判据**。

    返回 "OK"（已消失）/ "RET:<code>"（接口拒了）/ "STILL"（返 OK 但
    稿子还在 ⇒ 形态失效，上层转 UI）/ "ERR:<...>"。
    """
    js = ("(function(){"
          "return fetch('@URL@',{method:'POST',credentials:'include',"
          "headers:{'Content-Type':'application/json'},"
          "body:'@BODY@'})"
          ".then(function(r){return r.text();})"
          ".catch(function(e){return 'ERR:'+e.message;})})()"
          .replace("@URL@", _delete_draft_api_url())
          .replace("@BODY@", _delete_draft_api_body(gid, draft_type)))
    try:
        out = str(cdp.eval(js) or "")
    except Exception as e:
        return "ERR:%s" % e
    if not out.startswith("{"):
        return "ERR:%s" % out[:60]
    try:
        d = json.loads(out)
    except Exception:
        return "ERR:%s" % out[:60]
    if d.get("code") != 0:
        return "RET:%s(%s)" % (d.get("code"), d.get("message"))
    # 删后核对（draft_list 里 gid 消失才算数）
    dl = fetch_draft_api_raw(cdp)
    if dl is None:
        return "STILL"                     # 核对失败，保守判未删
    for x in dl:
        if str(x.get("gid")) == str(gid):
            return "STILL"
    return "OK"


def fetch_draft_api_raw(cdp):
    """draft_list 原始 dict 列表（不过滤，含空标题稿）。失败 None。"""
    js = ("(function(){return fetch("
          "'/mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231',"
          "{credentials:'include'})"
          ".then(function(r){return r.text();})"
          ".catch(function(e){return 'ERR:'+e.message;})})()")
    try:
        s = str(cdp.eval(js) or "")
    except Exception:
        return None
    if not s.startswith("{"):
        return None
    try:
        return json.loads(s).get("draft_list")
    except Exception:
        return None


def verify_draft_cover(cdp, title, raw=None):
    """回读 `draft_list` 看这篇的封面**有没有真落库**，返回 (状态, 详情)。

    状态 ∈ `OK` / `NO_COVER` / `NOT_FOUND`。

    为什么要单独一个函数（结果导向的老规矩）：`save_draft_api` 返 `code:0`
    只说明服务端收下了请求；封面这种字段历史上就出现过「接口返 OK、
    字段其实没落」的坑（头条 delete 对假 gid 也返 code:0）。

    判据（2026-10-17 实测）：`draft_list` 每条带
    `cover_image = {image_uri, image_url, width, height}`，
    且 `image_uri` 与 `upload_cover_api` 第①步返回的 uri **完全一致**
    —— 这比「有没有 src」更硬，能确认存的是我们刚传的那张。
    """
    if raw is None:
        raw = fetch_draft_api_raw(cdp) or []
    hit = [x for x in raw if (x.get("title") or "") == title]
    if not hit:
        return "NOT_FOUND", ""
    ci = hit[0].get("cover_image") or {}
    uri = ci.get("image_uri") or ""
    if not uri:
        return "NO_COVER", json.dumps(ci, ensure_ascii=False)[:160]
    return "OK", "%sx%s uri=%s" % (ci.get("width"), ci.get("height"), uri)


def fetch_draft_api(cdp):
    """草稿箱 JSON API（2026-10-16 P1 实证，用户提供接口 + 实测）。

        GET /mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231
        （需在 mp.toutiao.com 同源页面上下文里 fetch，自动带 cookie）

    返回 [(title, info)]（与 fetch_draft_titles 同格式，可直接替换）；
    失败返回 None（上层退 UI 滚动方案）。实测：
      - code=0，48 条全量一次到手（UI 滚动 ~90s 且只拿部分）
      - type=0 / type=2 都返同样 48 条（type 维度不影响图文草稿）
      - count=200 无翻页字段（draft_list 就是全量；>200 才会截断）
    字段：title / abstract / gid / update_time / cover_image...
    """
    js = ("(function(){return fetch("
          "'/mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231',"
          "{credentials:'include',"
          "headers:{'Accept':'application/json, text/plain, */*'}})"
          ".then(function(r){return r.text();})"
          ".catch(function(e){return 'ERR:'+e.message;})})()")
    try:
        s = str(cdp.eval(js) or "")
    except Exception:
        return None
    if not s.startswith("{"):
        return None
    try:
        d = json.loads(s)
    except Exception:
        return None
    dl = d.get("draft_list")
    if not isinstance(dl, list):
        return None
    out = []
    for x in dl:
        if not isinstance(x, dict):
            continue
        t = (x.get("title") or "").strip()
        if len(t) < 3:
            continue
        ut = x.get("update_time") or 0
        info = time.strftime("%m-%d %H:%M", time.localtime(ut)) if ut else ""
        out.append((t, info))
    return out


def fetch_draft_titles(cdp, wait=10, scroll=True, max_scroll=10):
    """读头条草稿箱页面，返回 [(标题, 另一行信息)]。

    列表行长这样（innerText，按块）：
        <标题> 刚刚 编辑删除 / <标题> 4 分钟前 编辑删除
    把整页 innerText 按「编辑删除」切段，每段第一行就是标题。

    ⚠⚠ **草稿箱是懒加载的**（2026-10-16 P0 实测踩坑）：首屏只有 ~21 条，
    页面自报「共 48 条内容」+「加载更多」按钮，URL 分页参数
    （?page=2 / ?begin=10&count=20）**无效**（都返回首屏）。
    不滚动到底就只读到一小部分 ⇒ `drafts --write` 重建出的台账一直
    是残缺的，`pending_cases` 的去重跟着失真。
    ⇒ 默认滚动到出现「没有更多数据了」或达到 max_scroll 次。
    """
    time.sleep(wait)
    if scroll:
        for _ in range(max_scroll):
            done = cdp.eval(
                "(function(){var t=document.body.innerText||'';"
                "return t.indexOf('没有更多')>=0;})()")
            if str(done) == "True":
                break
            cdp.eval("window.scrollTo(0,document.body.scrollHeight)")
            time.sleep(1.5)
    txt = cdp.eval("(function(){return document.body.innerText||'';})()") or ""
    out = []
    for seg in txt.split("编辑删除"):
        lines = [l.strip() for l in seg.split("\n") if l.strip()]
        if not lines:
            continue
        title = lines[0]
        if len(title) < 3 or not any(k in title for k in ("拆解", "：", "$")):
            continue
        info = " ".join(lines[1:3])
        out.append((title, info))
    return out


def cmd_drafts(args):
    """从草稿箱反查，重建已发记录。API 优先，UI 滚动兜底。"""
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        arr = fetch_draft_api(cdp)
        if arr is not None:
            print("读到 %d 条草稿（API draft_list）：" % len(arr))
        else:
            print("[api 不通，退 UI 滚动]")
            arr = fetch_draft_titles(cdp)
            print("读到 %d 条草稿：" % len(arr))
        for title, info in arr:
            print("  · %s   [%s]" % (title[:40], info[:30]))
        if args.write:
            hit = 0
            remote_titles = [a for a, _ in arr]
            for c in load_cases():
                if draft_title_match(c["id"], c, remote_titles):
                    mark_drafted(c["id"])
                    hit += 1
            print("\n已写 %d 条进 data/toutiao_drafts.json" % hit)
    finally:
        cdp.close_target(t["id"])


# 草稿删除确认弹窗（byte-modal）底部按钮文案
_CONFIRM_JS = """(function(){
  var f = document.querySelector('.byte-modal-footer');
  if(!f) return 'nomodal';
  var btns = [].slice.call(f.querySelectorAll('*')).filter(function(e){
    var r = e.getBoundingClientRect();
    if(r.width <= 0) return false;
    var direct = [].slice.call(e.childNodes).filter(function(n){
      return n.nodeType === 3;}).map(function(n){return n.textContent.trim();}).join('');
    return direct === '确定';
  });
  if(!btns.length) return 'nobtn';
  var b = btns[btns.length-1].getBoundingClientRect();
  return JSON.stringify({x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
})()"""


def _count_dup_and_delbtn(cdp, title, keep_newest=True, expand=True):
    """草稿箱里 title 的条数 + 待删那条的「删除」按钮坐标（没有则 None）。

    keep_newest：草稿箱按「最新在前」排，dedup 反复删会让**最后一条**活下来。
    旧行为删第一条 = 保留最旧那张（重发后会把没封面的旧稿留下）。
    所以这里默认返回**最后一张**的删除按钮，活下来的就是最新那条。

    调用前必须先把列表点「加载更多」展全，否则首屏 20 条之外的重复全漏。
    expand=False 用于批量模式（列表已展开，别重复点）。
    """
    if expand:
        _load_all_drafts(cdp)
    js = """(function(){
      var want = %s;
      var es = [].slice.call(document.querySelectorAll(
          '.article-draft-item, .draft-item'));
      var inner = es.filter(function(e){
        return ((e.innerText||'').indexOf(want) >= 0);
      });
      if(!inner.length) return JSON.stringify({n: 0, x: null, y: null});
      var del = null;
      // 草稿箱 **最新在前**（实测：首条 22:32，末条 15:22）。
      // keep_newest=true 时要删的是**最旧那条** = inner 的最后一个 → 反转后取 [0]。
      var target = (%s) ? inner.slice().reverse() : inner;
      target[0].scrollIntoView({block: 'center'});
      [].slice.call(target[0].querySelectorAll('span,button,a,div')).forEach(function(e2){
        var t2 = (e2.innerText||'').trim();
        var r2 = e2.getBoundingClientRect();
        if(t2 === '删除' && r2.width > 0 && r2.height > 0) del = e2;
      });
      if(del){
        var b = del.getBoundingClientRect();
        var vh = window.innerHeight || 1000;
        if(b.y >= 0 && b.y <= vh){
          return JSON.stringify({n: inner.length, ok: 1,
            x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
        }
        return JSON.stringify({n: inner.length, ok: 0,
          reason: 'offscreen', x: null, y: null});
      }
      return JSON.stringify({n: inner.length, ok: 0, reason: 'nodel',
                             x: null, y: null});
    })()""" % (json.dumps(title, ensure_ascii=False),
               "true" if keep_newest else "false")
    # 第一趟只负责把目标卡片滚进视口（列表展开后多数卡片在视口外，
    # 直接点坐标会落空），隔一拍再量坐标。
    cdp.eval(js)
    time.sleep(0.9)
    r = cdp.eval(js)
    d = json.loads(r or "{}")
    pos = (d["x"], d["y"]) if d.get("x") is not None and d.get("ok") else None
    return d.get("n", 0), pos


def cmd_covers(args):
    """批量生成头条横版封面 out/toutiao/<id>/cover.png。"""
    cases = [find_case_fuzzy(args.case)] if args.case else list(load_cases())
    if not cases:
        print("没有可处理的案例。")
        return 1
    ok = 0
    for i, c in enumerate(cases, 1):
        try:
            p = render_tt_cover(args.port, c)
        except Exception as e:                        # noqa: BLE001
            p, err = "", "%s: %s" % (type(e).__name__, e)
        else:
            err = ""
        print("[%d/%d] %s → %s%s" % (i, len(cases), c["id"],
                                     "ok" if p else "失败", (" " + err) if err else ""))
        ok += 1 if p else 0
    print("完成 %d/%d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 2


def _copy_args(args, **kw):
    d = vars(args).copy()
    d.update(kw)
    return argparse.Namespace(**d)


# 「标题 + 时间 + 编辑删除」一条草稿卡片的文本，用来从整体文本里切出纯标题。
# 注意「5 分钟前」中间**有空格**，时间单位前必须允许 \s*，否则刚发的草稿会被漏。
_DRAFT_TITLE_RE = re.compile(
    r"^(?P<t>.*?)\s(?:昨日|昨天|前天|今天|刚刚|\d+\s*秒前|\d+\s*分钟前|"
    r"\d+\s*小时前|\d{2}-\d{2})"
    r"\s*(?:\d{2}:\d{2})?\s+编辑删除$")

_DRAFT_ALL_TEXT_JS = """(function(){
  // 必须直接用卡片节点：通用 [div,li,tr,section] 会选中卡片内部的
  // .operations（文本只有「编辑删除」），导致取不到标题。
  var es = [].slice.call(document.querySelectorAll(
      '.article-draft-item, .draft-item'));
  return JSON.stringify(es.map(function(e){
    return (e.innerText||'').replace(/\\s+/g, ' ').trim();
  }));
})()"""


def _draft_all_titles(cdp, expand=True):
    """草稿箱（展开后）所有卡片的纯标题，按 DOM 顺序（**最新在前**）。"""
    if expand:
        _load_all_drafts(cdp)
    try:
        items = json.loads(cdp.eval(_DRAFT_ALL_TEXT_JS) or "[]")
    except Exception:                                 # noqa: BLE001
        items = []
    out = []
    for s in items:
        m = _DRAFT_TITLE_RE.match((s or "").strip())
        if m:
            out.append(m.group("t").strip())
    return out


def _confirm_delete(cdp, pos):
    """点删除 → 等 byte-modal → 点它的「确定」。成功返回 True。"""
    for kind in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": kind, "x": pos[0], "y": pos[1],
                  "button": "left", "clickCount": 1})
    time.sleep(2.2)
    conf = cdp.eval(_CONFIRM_JS)
    if conf in ("nomodal", "nobtn"):
        return False
    try:
        d = json.loads(conf)
    except Exception:                                 # noqa: BLE001
        return False
    for kind in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": kind, "x": d["x"], "y": d["y"],
                  "button": "left", "clickCount": 1})
    time.sleep(3.5)
    return True


def _dedup_batch(max_del=12, dry=False):
    """一次开页，把所有重复标题逐个删到只剩最新一条。

    2026-10-16 P1 起改 **API 优先**：draft_list 拿全量（含 gid /
    update_time）分组找重复 → 每组留 update_time 最大的（最新），
    其余走 delete_draft_api（hook 抓包实证形态）删；返 OK 但稿子
    还在（STILL）或接口拒（RET）时转 UI 点删兜底。
    """
    cdp, t = _open(TT_DRAFT, wait=10)
    try:
        _check_login(cdp)
        time.sleep(2)
        dl = fetch_draft_api_raw(cdp)
        if dl is None:
            print("[api 不通，退 UI 展开]")
            titles = _draft_all_titles(cdp)
            print("展开后 %d 条，唯一标题 %d 个"
                  % (len(titles), len(set(titles))))
            return _dedup_batch_ui(cdp, titles, max_del, dry)
        print("draft_list API：%d 条" % len(dl))
        # 按标题分组（空标题跳过——没有标题就无从判重）
        groups = {}
        for x in dl:
            ttl = (x.get("title") or "").strip()
            if len(ttl) < 3:
                continue
            groups.setdefault(ttl, []).append(x)
        dups = [(t2, xs) for t2, xs in groups.items() if len(xs) > 1]
        dups.sort(key=lambda kv: -len(kv[1]))
        n_del = sum(len(xs) - 1 for _, xs in dups)
        print("重复标题 %d 个，共需删 %d 条" % (len(dups), n_del))
        if dry:
            for t2, xs in dups:
                print("  [dry] x%d  %s" % (len(xs), t2))
            return 0
        done = 0
        bad = 0
        for t2, xs in dups:
            # 留 update_time 最大的（最新），其余按时间升序删
            xs = sorted(xs, key=lambda x: x.get("update_time") or 0)
            keep, todel = xs[-1], xs[:-1]
            for x in todel[:max_del]:
                gid = x.get("gid")
                dtype = x.get("draft_type", 2)
                st = delete_draft_api(cdp, gid, dtype)
                if st == "OK":
                    done += 1
                    continue
                # API 没删动（STILL/RET）→ 转 UI 兜底
                print("  [api %s，转 UI] 《%s》gid=%s"
                      % (st, t2[:30], gid))
                n, pos = _count_dup_and_delbtn(cdp, t2, expand=False)
                if not pos or not _confirm_delete(cdp, pos):
                    print("  [warn] 《%s》UI 也没删动，停" % t2[:30])
                    bad += 1
                    break
                done += 1
            print("  《%s》→ 留 gid=%s（%s）"
                  % (t2[:36], keep.get("gid"),
                     time.strftime("%m-%d %H:%M",
                                   time.localtime(keep.get("update_time")
                                                  or 0))))
        print("共删除 %d 条%s" % (done, ("，失败 %d 个标题" % bad) if bad else ""))
        return 0 if bad == 0 else 2
    finally:
        cdp.close_target(t["id"])


def _dedup_batch_ui(cdp, titles, max_del, dry):
    """_dedup_batch 的 UI 兜底版（API 不通时走老路）。"""
    print("展开后 %d 条，唯一标题 %d 个" % (len(titles), len(set(titles))))
    from collections import Counter
    cnt = Counter(titles)
    dups = sorted([(t2, c) for t2, c in cnt.items() if c > 1],
                  key=lambda kv: -kv[1])
    print("重复标题 %d 个，共需删 %d 条"
          % (len(dups), sum(c - 1 for _, c in dups)))
    if dry:
        for t2, c in dups:
            print("  [dry] x%d  %s" % (c, t2))
        return 0
    done = 0
    bad = 0
    for t2, c in dups:
        for i in range(min(c - 1, max_del)):
            n, pos = _count_dup_and_delbtn(cdp, t2, expand=False)
            if n <= 1 or not pos:
                break
            if not _confirm_delete(cdp, pos):
                print("  [warn] 《%s》第 %d 条没点到确认框，停" % (t2, i + 1))
                bad += 1
                break
            done += 1
        n, _ = _count_dup_and_delbtn(cdp, t2, expand=False)
        print("  《%s》→ 剩 %d 条" % (t2, n))
    print("共删除 %d 条%s" % (done, ("，失败 %d 个标题" % bad) if bad else ""))
    return 0 if bad == 0 else 2


def cmd_dedup(args):
    """把草稿箱里同标题的重复草稿删到只剩 1 条（试填/重发留下的）。
    --all：遍历 data/toutiao_drafts.json 里登记过的每条标题逐个去重。
    --batch：只**开一次**草稿箱、把列表展开到底，然后一次性扫出所有重复标题
            再逐条删（比 --all 快得多，也更不容易中途被浏览器掉线打断）。
    """
    if getattr(args, "batch", False):
        return _dedup_batch(max_del=args.max_del, dry=args.dry)
    if args.all and not args.title:
        byid = {c["id"]: c for c in load_cases()}
        try:
            with open(DRAFT_FILE, encoding="utf-8") as f:
                recs = json.load(f)
        except Exception:                             # noqa: BLE001
            recs = []
        bad = 0
        for i, r in enumerate(recs, 1):
            c = byid.get(r.get("id"))
            if not c:
                continue
            print("--- [%d/%d] %s" % (i, len(recs), r.get("id")))
            args.title = make_toutiao_title(c)
            bad += cmd_dedup(_copy_args(args, title=args.title)) or 0
            args.title = None
        return 0 if bad == 0 else 2
    if not args.title:
        print("需要指定标题或使用 --all")
        return 1
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        target = getattr(args, "to", 1)           # --to 0 = 删光（清测试稿）
        n, _ = _count_dup_and_delbtn(cdp, args.title)
        print("同标题草稿 %d 条（目标剩 %d）" % (n, target))
        if n <= target:
            print("无重复，不动")
            return 0
        if args.dry:
            print("[dry] 会删 %d 条，保留 %d 条" % (n - target, target))
            return 0
        for i in range(min(n - target, args.max_del)):
            n, pos = _count_dup_and_delbtn(cdp, args.title)
            if n <= target or not pos:
                break
            for kind in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent",
                         {"type": kind, "x": pos[0], "y": pos[1],
                          "button": "left", "clickCount": 1})
            time.sleep(2.2)
            conf = cdp.eval(_CONFIRM_JS)
            if conf in ("nomodal", "nobtn"):
                print("  第 %d 条：没等到确认弹窗（%s），停" % (i + 1, conf))
                return 2
            d = json.loads(conf)
            for kind in ("mousePressed", "mouseReleased"):
                cdp.send("Input.dispatchMouseEvent",
                         {"type": kind, "x": d["x"], "y": d["y"],
                          "button": "left", "clickCount": 1})
            time.sleep(4)
            print("  第 %d 条：删除 → 确定" % (i + 1))
        cdp.eval("location.reload()")
        time.sleep(9)
        n, _ = _count_dup_and_delbtn(cdp, args.title)
        print("现剩同标题 %d 条" % n)
        return 0 if n <= target else 2
    finally:
        cdp.close_target(t["id"])


def main():
    ap = argparse.ArgumentParser(description="头条号文章生成 / 发布（CDP）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--case")
    b.set_defaults(fn=cmd_build)
    pv = sub.add_parser("preview", help="生成 out/toutiao/index.html 预览页")
    pv.set_defaults(fn=cmd_preview)
    q = sub.add_parser("queue")
    q.add_argument("--near", type=int, default=5)
    q.add_argument("--fresh", action="store_true")
    q.set_defaults(fn=cmd_queue)
    p = sub.add_parser("publish")
    p.add_argument("--case")
    p.add_argument("--near", type=int, default=1)
    p.add_argument("--all", action="store_true")
    p.add_argument("--skip")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="忽略已发记录，全部重发（用于补封面等重做场景）")
    p.add_argument("--no-cover", action="store_true",
                   help="不传封面卡片图（默认会传小红书 card-1 当封面）")
    p.add_argument("--api", action="store_true",
                   help="纯 API 建稿（不开编辑器/不点按钮，2026-10-17 实测可用）。"
                        "token 取一次用一批；**含封面上传**（upload_cover_api 两级 "
                        "/spice/image，verify_draft_cover 回读核对；--no-cover 可关）")
    p.set_defaults(fn=cmd_publish)
    rf = sub.add_parser("refresh",
                        help="按最新逻辑刷新存量草稿（重建 meta→删旧→建新，"
                             "用于标题逻辑/override 变过之后的批量重刷）")
    rf.add_argument("--all", action="store_true",
                    help="自动扫草稿箱：线上标题≠本地最新逻辑的全刷")
    rf.add_argument("--case", help="只刷指定 case 对应的草稿")
    rf.add_argument("--dry", action="store_true", help="只列清单不动手")
    rf.set_defaults(fn=cmd_refresh)
    cv = sub.add_parser("covers",
                        help="批量生成头条横版封面 out/toutiao/<id>/cover.png")
    cv.add_argument("--case")
    cv.add_argument("--all", action="store_true")
    # 2026-10-02：原来写死 9222（公众号那份），与模块级 CDP_PORT 不一致 ——
    # `covers` 只渲染本地 PNG、不需要登录态，用哪份都行，但硬编码端口
    # 会在只起了 9223 时报「连不上 9222」。跟 CDP_PORT 走。
    cv.add_argument("--port", type=int, default=CDP_PORT)
    cv.set_defaults(fn=cmd_covers)
    pr = sub.add_parser("probe", help="登录后导出发文页 DOM，校准选择器")
    pr.add_argument("--url")
    pr.add_argument("--keep", action="store_true")
    pr.set_defaults(fn=cmd_probe)
    d = sub.add_parser("drafts")
    d.add_argument("--write", action="store_true")
    d.set_defaults(fn=cmd_drafts)
    dd = sub.add_parser("dedup",
                        help="把草稿箱里同标题的重复草稿删到只剩 1 条")
    dd.add_argument("title", nargs="?", default=None,
                    help="草稿标题（精确包含匹配）；--all 时可省略")
    dd.add_argument("--all", action="store_true",
                    help="遍历 data/toutiao_drafts.json 登记过的全部标题")
    dd.add_argument("--batch", action="store_true",
                    help="只开一次草稿箱、展开全部列表后一次性扫+删（推荐）")
    dd.add_argument("--dry", action="store_true")
    dd.add_argument("--max-del", type=int, default=10)
    dd.add_argument("--to", type=int, default=1,
                    help="删到剩几条为止（默认 1；--to 0 可把测试稿删光）")
    dd.set_defaults(fn=cmd_dedup)
    cv = sub.add_parser("cover",
                        help="给【已有的】草稿原地补封面（草稿箱 → 编辑 → 图库上传），"
                             "不重发、不会产生重复草稿。不写 --all 时只处理一条。")
    cv.add_argument("--case")
    cv.add_argument("--all", action="store_true")
    cv.add_argument("--dry", action="store_true")
    cv.add_argument("--force", action="store_true",
                    help="草稿已有封面也强制换图（重做封面后同步到草稿用）")
    cv.set_defaults(fn=cmd_cover)
    lg = sub.add_parser("login", help="检查登录态（退出码 0 = 已登录）")
    lg.add_argument("--open", action="store_true",
                    help="开一个登录页并留着，你自己扫码，脚本自动检测结果")
    lg.add_argument("--timeout", type=int, default=600,
                    help="--open 时最长等多少秒（默认 600）")
    lg.set_defaults(fn=cmd_login)
    args = ap.parse_args()
    #⚠ 走CDP 的子命令共用这一个自动启动入口（清代理 → 删 LOCK →
    # 带 --no-sandbox 起 Chrome for Testing → 轮询 9222）。
    # 不做这步的话，`drafts` / `cover` / `dedup` / `probe` 在 Chrome 没起时
    # 只会报一句「连不上 9222」然后让人自己去开浏览器（实测踩过）。
    # 纯离线子命令（build/queue/preview）不需要浏览器，别去启动。
    if getattr(args, "cmd", "") not in ("build", "queue", "preview"):
        try:
            # ⚠ 头条的登录态在 `chrome-debug-profile`，**不是**公众号那个
            # ChromeCDP。拿错 profile 的症状极具误导性：CDP 连得上、
            # 页面也能开，但被重定向到登录页 ⇒ 看着像「掉登录了」，
            # 实际是**开错浏览器**。
            import chrome_cdp_launch as _L
            wp._autostart_chrome_if_needed(
                CDP_PORT, profile=_L.PLAT_PROFILE)
        except SystemExit:
            raise
        except Exception as e:
            print("[warn] autostart 失败: %s" % e)
    rc = args.fn(args)
    if isinstance(rc, int) and rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
