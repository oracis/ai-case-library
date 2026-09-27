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
TITLE_OVERRIDES_PATH = os.path.join(ROOT, "data", "xhs_title_overrides.json")
DRAFT_FILE = os.path.join(ROOT, "data", "toutiao_drafts.json")
PUB_FILE = os.path.join(ROOT, "data", "toutiao_published.json")

CDP_PORT = int(os.environ.get("CDP_PORT", 9222))

TITLE_MAX = 30          # 头条标题硬上限（实测后台 30 字，超了会被截断/拦）
BODY_MIN = 300          # 头条推荐 1000+ 字，低于这个数建议别发

TT_HOME = "https://mp.toutiao.com/"
# 发文页（创作端「写文章」）。老版 /profile_v4/graphic/articles，新版创作端可能变，
# 登录后用 probe 校准：脚本会先打开 TT_EDITOR，若被重定向到首页则退到 TT_HOME。
TT_EDITOR = "https://mp.toutiao.com/profile_v4/graphic/articles"
TT_CONTENT = "https://mp.toutiao.com/profile_v4/manage/content"   # 内容管理/草稿

# ---- 选择器（实验性，probe 后可改这里）-------------------------------------
SEL = {
    "title": ("input[placeholder*='标题'], textarea[placeholder*='标题'], "
              ".article-title input, .title-input input, #article-title"),
    "body": ("[contenteditable='true'], .ProseMirror, "
             ".article-content [contenteditable]"),
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


def make_toutiao_title(c):
    """头条标题：≤30 字，优先 AI 改写过的短标题，否则公众号标题，再兜名字。

    头条的推荐逻辑偏好「信息量 + 具体数字」，所以能带上金额就带。
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
    cand = fix_arr_wording(cand, c)
    return cand[:TITLE_MAX]


def fix_arr_wording(title, c):
    """只有年化（ARR）数据的案例，标题里的「月收」要改成「年收」。

    xhs_title_overrides.json 是照「月收 $X」批量改写的，但 sierra / genius-ai
    这类只有 ARR（年化）数据的案例被写成「月收 $200M」，口径整整差 12 倍。
    头条标题字更少、更显眼，这里必须按 metrics 里实际有的字段纠正。
    """
    m = c.get("metrics") or {}
    has_monthly = any(m.get(k) is not None
                      for k in ("mrr", "last_30d_revenue"))
    if has_monthly or m.get("arr") is None:
        return title
    return title.replace("月收", "年收").replace("月入", "年入")


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

# 头条审核/推荐的敏感词（夸张收益、绝对化用语）。命中只在 build 时提示，不自动改。
RISK_WORDS = ["躺赚", "躺收", "躺着", "暴利", "稳赚", "稳赚不赔", "月入过万",
              "第一", "最全", "最强", "震惊", "必看", "零成本", "无脑"]


def risk_words(text):
    return [w for w in RISK_WORDS if w in (text or "")]


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
    title = make_toutiao_title(c)
    html = blocks_to_html(blocks)
    return {
        "id": cid,
        "name": c.get("name", ""),
        "title": title,
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


def publish_one(c, dry=False, yes=False):
    """填一稿进发文页。dry = 只填不点；yes = 点「发布」，否则点「存草稿」。"""
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
        if dry:
            print("    [dry] 不点按钮，编辑页已填好，自己看一眼再手动发布")
            return True
        btn = _click_text(cdp, ["发布", "立即发布"] if yes else ["存草稿", "保存草稿", "草稿"])
        print("    点击：%s" % btn)
        if btn == "notfound":
            print("    [warn] 没点到按钮（选择器要校准），记录不写")
            return False
        time.sleep(4)
        if yes:
            mark_published(c["id"])
        else:
            mark_drafted(c["id"])
        return True
    finally:
        if not dry:
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
            publish_one(c, dry=args.dry, yes=args.yes)
        except SystemExit:
            raise
        except Exception as e:                        # noqa: BLE001
            print("    [异常] %s：%s" % (type(e).__name__, e))


def cmd_drafts(args):
    """从「内容管理」草稿列表反查，重建已发记录。"""
    cdp, t = _open(TT_CONTENT, wait=8)
    try:
        _check_login(cdp)
        items = cdp.eval("""(function(){
          var out=[];
          [].slice.call(document.querySelectorAll('li,tr,div')).forEach(function(e){
            var s=(e.innerText||'').trim();
            if(s && s.length>4 && s.length<120 && /草稿|编辑中|未发布/.test(s)) out.push(s);
          });
          return JSON.stringify(out.slice(0,80));
        })()""")
        arr = json.loads(items or "[]")
        print("读到 %d 条候选（含噪声，人工看一眼）：" % len(arr))
        for s in arr:
            print("  · " + s.replace("\n", " | ")[:100])
        if args.write:
            hit = 0
            for c in load_cases():
                title = make_toutiao_title(c)
                if any(title and title[:12] in s for s in arr):
                    mark_drafted(c["id"])
                    hit += 1
            print("\n已写 %d 条进 data/toutiao_drafts.json" % hit)
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
    p.set_defaults(fn=cmd_publish)
    pr = sub.add_parser("probe", help="登录后导出发文页 DOM，校准选择器")
    pr.add_argument("--url")
    pr.add_argument("--keep", action="store_true")
    pr.set_defaults(fn=cmd_probe)
    d = sub.add_parser("drafts")
    d.add_argument("--write", action="store_true")
    d.set_defaults(fn=cmd_drafts)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
