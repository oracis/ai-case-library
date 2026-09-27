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


def clean_line(s):
    """去掉外链 / markdown 链接 / 加粗标记 —— 头条正文里外链会被拦或降权。"""
    s = _MD_LINK_RE.sub(r"\1", s or "")
    s = _URL_RE.sub("", s)
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def md_to_blocks(md):
    """公众号长文 markdown → [(kind, text)]，kind ∈ h2 / p / ul / kv / quote。

    头条编辑器不支持 markdown 表格，表格行转「标签：值」；引用转引号段落。
    """
    blocks = []
    para = []
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            continue
        if s.startswith("<!--"):                      # 模板注释，全部丢掉
            continue
        if s.startswith("# "):                        # h1 = 标题，正文里不要
            continue
        if s.startswith("## "):
            if para:
                blocks.append(("p", clean_line(" ".join(para))))
                para = []
            blocks.append(("h2", clean_line(s[3:])))
            continue
        if re.match(r"^\|?[\s:\-|]+$", s) and "-" in s:   # 表格分隔行 |---|
            continue
        if s.startswith("|"):                         # 表格行 → key：value
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
    return [b for b in blocks if b[1]]


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;")


def blocks_to_html(blocks, footer=True):
    """块 → 头条编辑器能吃的极简 HTML（只有 p / strong，不依赖 class）。"""
    out = []
    for kind, text in blocks:
        t = esc(text)
        if kind == "h2":
            out.append("<p><strong>%s</strong></p>" % t)
        elif kind == "ul":
            out.append("<p>· %s</p>" % t)
        elif kind == "quote":
            # 原文自带「」就别套两层
            out.append("<p>%s</p>" % t if t.startswith("「")
                       else "<p>「%s」</p>" % t)
        else:
            out.append("<p>%s</p>" % t)
    if footer:
        out.append("<p><strong>关于本栏目</strong></p>")
        out.append("<p>「拆解海外」逐个拆海外小生意：它干什么、钱从哪来、做到多大、"
                   "为什么能成、哪些能搬回国内。数据均来自公开披露，收入口径按原文"
                   "照实标注（MRR 就写 MRR，只有流水就写近 30 天收入），不做换算夸大。</p>")
    return "\n".join(out)


FOOTER_NOTE = "关于本栏目"

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
    plain = "\n".join(t for _, t in blocks)
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


def cover_path(cid):
    """这篇头条稿的封面图（直接复用小红书那 5 张卡片图的第 1 张）。"""
    for name in ("card-1.png", "card-1.jpg", "card-1.jpeg"):
        p = os.path.join(XHS_DIR, cid, name)
        if os.path.isfile(p):
            return p
    return ""


def _file_input_node(cdp):
    """DOM 域里查 file input 的 nodeId（封面 file input 是点开才动态创建的）。"""
    try:
        r = cdp.send("DOM.getDocument", {"depth": 0})
        root = r.get("result", {}).get("root", {}).get("nodeId")
        if not root:
            return 0
        r2 = cdp.send("DOM.querySelector", {"nodeId": root,
                                            "selector": "input[type=file]"})
        return r2.get("result", {}).get("nodeId", 0)
    except Exception:                                 # noqa: BLE001
        return 0


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


def upload_cover(cdp, paths, timeout=30):
    """给发文页传封面：点 .article-cover-add 出 file input，再塞图。

    注意 CDP 的 DOM.setFileInputFiles 在这里会被 Byte 上传组件忽略，
    得用 _set_files_via_js（base64 → File → input.files → change）。
    传完轮询封面区，直到出现真图（占位块 .article-cover-add 被替换掉）。
    """
    r = cdp.eval("""(function(){
      var el = document.querySelector('.article-cover-add');
      if(!el) return 'notfound';
      el.scrollIntoView({block:'center'});
      var b = el.getBoundingClientRect();
      if(b.width <= 0) return 'notfound';
      return JSON.stringify({x: b.x+b.width/2, y: b.y+b.height/2});
    })()""")
    if not r or r == "notfound":
        return "no-cover-slot"
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setInterceptFileChooserDialog", {"enabled": True})
    except Exception:                                 # noqa: BLE001
        pass
    _click_pos(cdp, json.loads(r), 2)
    # 封面块只负责造出 file input，不会自己弹框 → 用**真实鼠标点它**，
    # Chrome 弹文件选择框，CDP 拦截后喂文件（DOM.setFileInputFiles / 塞 File 都不灵）。
    fp = cdp.eval("""(function(){
      var ins = [].slice.call(document.querySelectorAll('input[type=file]'))
                 .filter(function(e){return e.getBoundingClientRect().width > 0;});
      if(!ins.length) return 'no-input';
      var el = ins[0]; el.scrollIntoView({block:'center'});
      var b = el.getBoundingClientRect();
      return JSON.stringify({x: b.x+b.width/2, y: b.y+b.height/2});
    })()""")
    got = False
    if fp and fp != "no-input":
        _click_pos(cdp, json.loads(fp), 1.5)
        end = time.time() + 8
        while time.time() < end:
            if not cdp._ws_readable(0.4):
                continue
            try:
                raw = cdp.ws.recv_text()
            except Exception:                         # noqa: BLE001
                break
            if "fileChooserOpened" in (raw or ""):
                got = True
                break
    if got:
        try:
            cdp.send("Page.handleFileChooser",
                     {"mode": "open",
                      "files": [os.path.abspath(p) for p in paths]})
        except Exception as e:                        # noqa: BLE001
            print("      handleFileChooser 失败：%s" % e)
    res = _set_files_via_js(cdp, paths[0])
    if not str(res).startswith("ok"):
        return str(res)[:40]
    js = """(function(){
      var box = document.querySelector('.article-cover-images');
      if(!box) return 'no-box';
      var add = !!document.querySelector('.article-cover-add');
      var srcs = [].slice.call(box.querySelectorAll('img')).map(function(e){
        return e.getAttribute('src')||'';});
      return JSON.stringify({add: add, srcs: srcs.slice(0,2)});
    })()"""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        time.sleep(2.5)
        last = cdp.eval(js) or ""
        try:
            d = json.loads(last)
        except Exception:                             # noqa: BLE001
            d = {}
        if not d.get("add") and d.get("srcs"):
            return "ok"
    return "uploaded-but-not-confirmed:" + last[:60]


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
        pool = pending_cases(need_built=False)
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


def _count_dup_and_delbtn(cdp, title):
    """草稿箱里 title 的条数 + 第一条的「删除」按钮坐标（没有则 None）。"""
    r = cdp.eval("""(function(){
      var want = %s;
      var es = [].slice.call(document.querySelectorAll('div,li,tr,section'));
      var cards = [];
      for(var i=0;i<es.length;i++){
        var s = (es[i].innerText||'');
        if(s.indexOf(want)>=0 && s.indexOf('编辑删除')>=0 && s.length < 400) cards.push(es[i]);
      }
      var inner = cards.filter(function(c){
        return !cards.some(function(o){ return o !== c && c.contains(o); });
      });
      var del = null;
      if(inner.length){
        [].slice.call(inner[0].querySelectorAll('span,button,a,div')).forEach(function(e2){
          var t2 = (e2.innerText||'').trim();
          if(t2 === '删除' && e2.getBoundingClientRect().width > 0) del = e2;
        });
      }
      if(del){
        del.scrollIntoView({block:'center'});
        var b = del.getBoundingClientRect();
        return JSON.stringify({n: inner.length,
          x: b.x+b.width/2, y: b.y+b.height/2});
      }
      return JSON.stringify({n: inner.length, x: null, y: null});
    })()""" % json.dumps(title, ensure_ascii=False))
    d = json.loads(r or "{}")
    pos = (d["x"], d["y"]) if d.get("x") is not None else None
    return d.get("n", 0), pos


def cmd_dedup(args):
    """把草稿箱里同标题的重复草稿删到只剩 1 条（试填/重发留下的）。"""
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        n, _ = _count_dup_and_delbtn(cdp, args.title)
        print("同标题草稿 %d 条" % n)
        if n <= 1:
            print("无重复，不动")
            return 0
        if args.dry:
            print("[dry] 会删 %d 条，保留 1 条" % (n - 1))
            return 0
        for i in range(min(n - 1, args.max_del)):
            n, pos = _count_dup_and_delbtn(cdp, args.title)
            if n <= 1 or not pos:
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
        return 0 if n == 1 else 2
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
    p.add_argument("--no-cover", action="store_true",
                   help="不传封面卡片图（默认会传小红书 card-1 当封面）")
    p.set_defaults(fn=cmd_publish)
    pr = sub.add_parser("probe", help="登录后导出发文页 DOM，校准选择器")
    pr.add_argument("--url")
    pr.add_argument("--keep", action="store_true")
    pr.set_defaults(fn=cmd_probe)
    d = sub.add_parser("drafts")
    d.add_argument("--write", action="store_true")
    d.set_defaults(fn=cmd_drafts)
    dd = sub.add_parser("dedup",
                        help="把草稿箱里同标题的重复草稿删到只剩 1 条")
    dd.add_argument("title", help="草稿标题（精确包含匹配）")
    dd.add_argument("--dry", action="store_true")
    dd.add_argument("--max-del", type=int, default=10)
    dd.set_defaults(fn=cmd_dedup)
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
