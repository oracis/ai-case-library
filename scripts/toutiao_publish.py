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

CDP_PORT = int(os.environ.get("CDP_PORT", 9222))

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
    return cand[:TITLE_MAX]


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


def md_to_blocks(md):
    """公众号长文 markdown → [(kind, text)]，kind ∈ h2 / p / ul / kv / quote。

    头条编辑器不支持 markdown 表格，表格行转「标签：值」；引用转引号段落。
    """
    blocks = []
    para = []
    in_comment = False                            # 多行 <!-- ... --> 注释块
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if not s:
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
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
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            blocks.append(("h2", clean_line(s[3:])))
            continue
        if _HR_RE.match(s):                       # --- 分隔线（区别于表格对齐行）
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            blocks.append(("hr", ""))
            continue
        if _TABLE_SEP_RE.match(s) and "-" in s:   # 表格分隔行 |---|
            continue
        if s.startswith("|"):                     # 表格行 → key：value
            cells = [c.strip() for c in s.strip("|").split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 2 and cells[0] != "维度":
                blocks.append(("kv", "%s：%s" % (cells[0], " ".join(cells[1:]))))
            continue
        if s.startswith(">"):
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            blocks.append(("quote", clean_line(s.lstrip("> "))))
            continue
        if re.match(r"^[-*]\s+", s):
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            blocks.append(("ul", clean_line(re.sub(r"^[-*]\s+", "", s))))
            continue
        para.append(s)
    if para:
        blocks.append(("p", clean_line(" ".join(para))))
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


def blocks_to_html(blocks, footer=True):
    """块 → 头条编辑器的富文本 HTML。

    头条 ProseMirror schema 实测（2026-09-28 探针）：
      ✔ h1/h2/h3 → 统一渲染成 <h1 class="pgc-h-forward-slash"> 装饰标题
      ✔ ul+li / ol+li / li 内 <strong> / blockquote / hr
      ✘ em、u —— 会被静默丢掉，别用

    所以这里**把 markdown 里本来就有、但被之前版本拍平的结构还原回来**：
      二级标题 → <h2>；列表 → 真 <ul><li>；引用 → <blockquote>；
      连续的表格行 → 一整组 <ul><li><strong>标签：</strong>值</li>；
      --- → <hr>。连续的同类块会被合并成一个 <ul>，视觉上是一组而不是 N 段。
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
        elif kind == "kv":
            items = []
            while i < n and blocks[i][0] == "kv":
                lab, val = split_kv(blocks[i][1])
                items.append(
                    "<li><strong>%s</strong>%s</li>" % (esc(lab), inline(val))
                    if lab else "<li>%s</li>" % inline(blocks[i][1]))
                i += 1
            out.append("<ul>%s</ul>" % "".join(items))
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
        out.append("<p>%s</p>" % inline(FOOTER_TEXT))
    return "\n".join(out)

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


def build_article(c):
    """把一个 case 变成头条稿。返回 meta dict（不写盘）。"""
    cid = c["id"]
    md_path = os.path.join(ARTICLES_DIR, cid + ".md")
    md = ""
    if os.path.isfile(md_path):
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
    blocks = md_to_blocks(md)
    plain = strip_md("\n".join(t for _, t in blocks))
    hits = []                                     # 被自动中性化的敏感词
    title = make_toutiao_title(c, hits_out=hits)
    html = blocks_to_html(blocks)
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
    ids = _ids(path)
    if cid in ids:
        return
    ids.append(cid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"id": i, "at": time.strftime("%Y-%m-%d %H:%M")} for i in ids],
                  f, ensure_ascii=False, indent=2)


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


def tt_cover_html(c):
    """头条专用横版封面（16:9）。

    设计目标「缩略图可读」：草稿箱/推荐频道缩略图只有 ~100px 宽（源图的
    1/20），2026-09-29 二次重做 —— 旧版用主题深色底 + 亮色特大数字 + 白色
    特大产品名 + 顶部「万物解释者·拆解海外」品牌行 + 一句话介绍，但头条推
    荐卡片是横版缩略图，会按一定比例裁切：左边距、左对齐的文字会被切掉。
    新版把 MRR+产品名**水平居中**，缩字号，砍掉在缩略图下不可见的小字装
    饰（顶部品牌行/一句话介绍）——只剩两个色块、两个大字，无论怎么裁都
    可读。
    """
    base, band, ac = wp._cover_theme(c.get("id", ""), "toutiao")
    name = c.get("name") or c.get("id") or ""
    headline = (c.get("metrics") or {}).get("headline") or ""
    num = tt_cover_num(headline)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "*{margin:0;padding:0;box-sizing:border-box;}"
        "html,body{width:%(W)dpx;height:%(H)dpx;}"
        'body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;'
        "background:%(BASE)s;color:#fff;display:flex;flex-direction:column;"
        "padding:60px 90px;position:relative;overflow:hidden;}"
        ".glow{position:absolute;right:-260px;top:-260px;width:720px;"
        "height:720px;border-radius:50%%;background:%(BAND)s;opacity:.55;}"
        ".main{flex:1;display:flex;flex-direction:column;justify-content:center;"
        "align-items:center;text-align:center;position:relative;min-height:0;}"
        ".num{font-size:150px;font-weight:900;color:%(AC)s;line-height:1.3;"
        "max-height:1.55em;overflow:hidden;letter-spacing:-2px;}"
        ".name{font-size:108px;font-weight:900;color:#fff;line-height:1.3;"
        "margin-top:18px;max-height:2.85em;overflow:hidden;}"
        "</style></head><body>"
        '<div class="glow"></div>'
        '<div class="main">'
        '<div class="num fit">%(NUM)s</div>'
        '<div class="name fit">%(NAME)s</div>'
        "</div>"
        "</body></html>"
        % {"W": TT_COVER_W, "H": TT_COVER_H, "BASE": base, "BAND": band,
           "AC": ac, "NAME": esc(name), "NUM": esc(num)})


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

_DRAFT_ITEM_COUNT_JS = ("document.querySelectorAll('.article-draft-item, "
                        ".draft-item').length")


def _load_all_drafts(cdp, max_rounds=40, pause=2.0):
    """点「加载更多」把草稿箱整页展开，返回渲染出的条目数。"""
    for _ in range(max_rounds):
        pos = cdp.eval(_DRAFT_LOADMORE_JS) or "none"
        if pos == "none":
            break
        if pos == "hidden":
            time.sleep(pause)
            continue
        _click_pos(cdp, json.loads(pos), 3)
        time.sleep(pause)
    try:
        return int(cdp.eval(_DRAFT_ITEM_COUNT_JS) or 0)
    except Exception:                                 # noqa: BLE001
        return 0


def _page_target_ids():
    out = set()
    try:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("type") == "page":
                out.add(t["id"])
    except Exception:                                 # noqa: BLE001
        pass
    return out


def _open_published_editor(title, wait=25):
    """草稿箱里没有这篇时，去「作品管理」点它的「修改」。

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
    # 作品管理有 495 条（2026-09-29 实测），目标可能排在几十页之后，
    # 每轮滚一屏 → 120 轮足够到底；先按标题扫，扫到就停。
    for _ in range(120):
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
    # 草稿箱初次只渲染前几条，目标卡在第 10 条开外时 _DRAFT_SCROLL_TO
    # 会一直 'none' 空转超时（2026-09-29 实测）。先把整页展开。
    #
    # 2026-09-30：整页展开是单条耗时的最大头（168s 里约 100s 在这）。
    # 展开是**幂等**的 —— 同一页反复展开，第二遍起 _DRAFT_LOADMORE_JS 直接
    # 返回 'none'，一轮就 break。所以别在每条前都跑满 20 轮，先探一次：
    # 已经有足够多的卡片就跳过。但**只探不点**是有代价的 —— 页面刚打开时
    # 可能只渲染 8 条而真值是 39，所以判据用「已展开」标记而不是数卡片。
    if not _draft_box_expanded.get("v"):
        _load_all_drafts(cdp, max_rounds=20, pause=1.5)
        # 展开后卡片数应接近草稿总数；明显更少说明还没展开完。
        try:
            n = int(cdp.eval(_DRAFT_ITEM_COUNT_JS) or 0)
        except Exception:                             # noqa: BLE001
            n = 0
        if n >= 20:
            _draft_box_expanded["v"] = True
    pos = ""
    for attempt in range(6):
        r = cdp.eval(_DRAFT_SCROLL_TO % json.dumps(title))
        if r == "none":
            time.sleep(1.5)
            continue                                   # 列表还没渲染出这张卡
        time.sleep(1.2)                                # 等scroll稳定
        pos = cdp.eval(_DRAFT_EDIT_POS % json.dumps(title))
        if pos and pos not in ("none", "zero", "offscreen"):
            break
        time.sleep(1.5)
        pos = ""
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

    end = time.time() + wait
    newt = None
    while time.time() < end:
        for t in wp.CDP(CDP_PORT).list_targets():
            if t.get("id") in before or t.get("type") != "page":
                continue
            if "/graphic/publish" in t.get("url", ""):
                newt = t
                break
        if newt:
            break
        time.sleep(1)
    if not newt:
        return None, None, "点了编辑但没等到新开编辑页 tab"
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
    for i, c in enumerate(todo, 1):
        print("[%d/%d] %s —— %s" % (i, len(todo), c["id"], c.get("name", "")))
        try:
            publish_one(c, dry=args.dry, yes=args.yes,
                        cover=not args.no_cover)
        except SystemExit:
            raise
        except Exception as e:                        # noqa: BLE001
            print("    [异常] %s：%s" % (type(e).__name__, e))


def fetch_draft_titles(cdp, wait=10):
    """读头条草稿箱页面，返回 [(标题, 另一行信息)]。

    列表行长这样（innerText，按块）：
        <标题> 刚刚 编辑删除 / <标题> 4 分钟前 编辑删除
    把整页 innerText 按「编辑删除」切段，每段第一行就是标题。
    """
    time.sleep(wait)
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
    """从草稿箱列表反查，重建已发记录。"""
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        arr = fetch_draft_titles(cdp)
        print("读到 %d 条草稿：" % len(arr))
        for title, info in arr:
            print("  · %s   [%s]" % (title[:40], info[:30]))
        if args.write:
            hit = 0
            for c in load_cases():
                title = make_toutiao_title(c)
                if any(title and title[:12] in a for a, _ in arr):
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
    """一次开页、一次展开，把所有重复标题逐个删到只剩最新一条。"""
    cdp, t = _open(TT_DRAFT, wait=10)
    try:
        _check_login(cdp)
        time.sleep(4)
        titles = _draft_all_titles(cdp)
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
    finally:
        cdp.close_target(t["id"])


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
    p.set_defaults(fn=cmd_publish)
    cv = sub.add_parser("covers",
                        help="批量生成头条横版封面 out/toutiao/<id>/cover.png")
    cv.add_argument("--case")
    cv.add_argument("--all", action="store_true")
    cv.add_argument("--port", type=int, default=9222)
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
    rc = args.fn(args)
    if isinstance(rc, int) and rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
