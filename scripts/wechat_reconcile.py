"""**公众号草稿对账**：远端草稿正文vs 本地 out/articles/<id>.html。

回答一个具体问题：草稿箱里那些「日期很旧」的条目，是
  (a) 不需要改（本地也没变）
  (b) 该改但没改到
  (c) 改的时候出错了

⚠ **不能用文件 mtime 判断** —— 2026-09-30 晚上`make_article.py --all`
   把39 个 out/articles/*.html 一次性全重生成，mtime 全是同一时刻，
   跟「内容是否真的改过」无关。唯一可靠的判据是**逐条比对正文文本**。

用法：
    python scripts/wechat_reconcile.py            # 打印对账表
    python scripts/wechat_reconcile.py --json     # 机器可读
    python scripts/wechat_reconcile.py --fetch    # 顺便把远端正文抓下来缓存
"""
import html as _html
import json
import os
import re
import sys
import time
import datetime

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp  # noqa: E402
import bilibili_publish as bp# noqa: E402  make_title 住在这

ART = os.path.join(ROOT, "out", "articles")
CACHE = os.path.join(ROOT, ".cache", "wechat_drafts.json")
PORT = 9222


def plain(html, drop_title=None):
    """HTML → 纯文本，压缩空白（用于比对，容忍排版差异）。

    ⚠ **必须剥掉两处本地才有、远端正文没有的前缀**（2026-10-01 踩过）：
      1. 题头行 `<p>拆解海外 · 第 15 篇</p>` 和副题头
         `<p>— 万物解释者 · 拆解海外 —</p>`；
      2. `<h1>标题</h1>` —— 公众号的**标题是独立字段**，不进正文，
         远端正文里没有它。
      不剥的话 32 条**全部**报「不一致」假阳性（实测）。

    `drop_title`：显式给出要剥掉的标题文本（自动从 h1 取）。
    """
    s = re.sub(r"<(script|style)\b.*?</\1>", "", html or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|section|li|h[1-6])>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    # 副题头「— 万物解释者 · 拆解海外 —」与题头「拆解海外 · 第 15 篇」
    s = re.sub(r"^[—\-–\s]*万物解释者[·・\s]*拆解海外[—\-–\s]*", "", s)
    s = re.sub(r"^[—\-–\s]*拆解海外\s*[·・]?\s*第\s*\d+\s*篇\s*", "", s)
    s = re.sub(r"^标题?[:：]\s*", "", s)
    if drop_title:
        t = re.sub(r"[\s\u3000]+", "", drop_title)
        if t and s.startswith(t):
            s = s[len(t):]
    s = re.sub(r"[\s\u3000]+", "", s)          # 全空白（含全角空格）全去掉
    return s.strip()


def _h1(html):
    """取h1 文本（= 公众号标题，独立字段，正文里没有）。"""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
    if not m:
        return None
    return _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()


def _fmt_ts(v):
    """草稿列表的 update_time 是**unix 秒**，不是格式化日期。

    直接印出来会是一个 10 位数字（1790182828），看着像乱码 ——
    实际是 2026-09-24 01:00。踩过这个坑。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v or "")
    if n < 10 ** 9:                # 已经是字符串日期
        return str(v)
    return datetime.datetime.fromtimestamp(n).strftime("%m-%d %H:%M")


def _read_body(cdp, tok, appmsgid):
    """打开一条远端草稿编辑器，读回正文纯文本。

    ⚠ 编辑器里有**多个** `.ProseMirror`（第0 个是标题栏），正文是最长的
       那个 —— 取最长的，不要取第一个（2026-09-30 踩过，读出来是 -1/标题）。
    """
    tid = wp._open_draft_editor(cdp, appmsgid, tok, timeout=30)
    try:
        for _ in range(20):
            time.sleep(1)
            r = cdp.eval(
                "(function(){"
                "var eds=[].slice.call(document.querySelectorAll("
                "'.ProseMirror[contenteditable=\"true\"]'))"
                ".filter(function(e){return (''+e.className)"
                ".indexOf('js_reprint')<0});"
                "if(!eds.length) return '';"
                "var best='';for(var i=0;i<eds.length;i++){"
                "var t=eds[i].innerText||eds[i].textContent||'';"
                "if(t.length>best.length) best=t;}"
                "return best;})()", refresh_context=True)
            if isinstance(r, str) and len(r) > 200:
                return r
        return r if isinstance(r, str) else ""
    finally:
        try:
            cdp.close_target(tid)
        except Exception:                      # noqa: BLE001
            pass


def _remote_bodies(cdp, tok, drafts, fetch=False):
    """读远端草稿正文。fetch=True 才逐条打开编辑器（慢，~8s/条）。"""
    if not fetch:
        return {}
    out = {}
    for i, d in enumerate(drafts, 1):
        aid = d.get("appmsgid")
        try:
            r = _read_body(cdp, tok, aid)
        except Exception as e:                 # noqa: BLE001
            print("  ! %s 读取失败: %s" % (aid, str(e)[:60]), flush=True)
            r = ""
        out[aid] = r or ""
        print("  [%d/%d] %s %d 字 %s"
              % (i, len(drafts), aid, len(out[aid]),
                 (d.get("title") or "")[:26]), flush=True)
    return out


def _local_titles():
    """case id → 公众号真实标题。

    ⚠ **不能用 `bilibili_publish.make_title`** —— 那是B站标题，
    截 30 字、优先取头条 meta.json；公众号标题是另一套（长得多、
    来源不同）。用错了 32 条只能反查到 1 条。

    正解：直接读**发布就绪的产物** `公众号-<id>拆解.html`（根目录），
    那是 build 完的真标题。
    """
    out = {}
    for fn in os.listdir(ROOT):
        m = re.match(r"^公众号-(.+?)拆解\.html$", fn)
        if m:
            out[m.group(1)] = fn
    return out


def _match_local(title):
    """草稿标题 → 本地 case id。"""
    norm = lambda s: re.sub(r"[\s\u3000]+", "", (s or "")).lower()
    want = norm(title)
    idx = {}
    for cid, fn in _local_titles().items():
        try:
            h = open(os.path.join(ROOT, fn), encoding="utf-8").read()
        except OSError:
            continue
        m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S | re.I)
        t = _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
        idx.setdefault(norm(t), cid)
    if want in idx:
        return idx[want]
    for k, v in idx.items():
        if k and want and (k.startswith(want[:12]) or want.startswith(k[:12])):
            return v
    return None


def main():
    fetch = "--fetch" in sys.argv
    as_json = "--json" in sys.argv

    cdp = wp.CDP(PORT)
    tid, tok = wp._connect_mp(cdp)
    if not tok:
        print("拿不到公众号 token，确认 Chrome 已登录 mp.weixin.qq.com")
        return 1
    drafts = wp._draft_list(cdp, tok, count=100)
    print("远端草稿 %d 条\n" % len(drafts))

    bodies = {}
    if fetch:
        print("抓取远端正文（~8s/条，%d 条约 %d 分钟）…"
              % (len(drafts), len(drafts) * 8 // 60 + 1), flush=True)
        bodies = _remote_bodies(cdp, tok, drafts, True)
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in bodies.items()}, f,
                      ensure_ascii=False)
        print()

    rows = []
    for d in drafts:
        title = d.get("title") or ""
        cid = _match_local(title)
        local_html = ""
        if cid:
            # 用**发布就绪的产物**（根目录 公众号-<id>拆解.html），不是
            # out/articles/<id>.html —— 后者是 make_article 的中间产物，
            # 与真正灌进公众号的不是同一份文件。
            p = os.path.join(ROOT, "公众号-%s拆解.html" % cid)
            if os.path.exists(p):
                local_html = open(p, encoding="utf-8").read()
        lp = plain(local_html, drop_title=_h1(local_html))
        rp = plain(bodies.get(d.get("appmsgid"), "")) if bodies else ""
        if not bodies:
            state = "?"          # 没抓正文，只标「能否对上本地」
        elif not lp:
            state = "NO_LOCAL"
        elif not rp:
            state = "EMPTY_REMOTE"
        elif lp == rp:
            state = "SAME"
        elif rp and rp in lp:
            state = "REMOTE_PREFIX"     # 远端是本地的前缀 → 旧版截断
        elif lp and lp in rp:
            state = "REMOTE_SUPER"      # 远端比本地多 → 本地被截短了
        else:
            state = "DIFF"
        rows.append({"appmsgid": d.get("appmsgid"), "title": title,
                     "cid": cid, "update_time": _fmt_ts(d.get("update_time")),
                     "state": state, "local_len": len(lp),
                     "remote_len": len(rp)})

    rows.sort(key=lambda r: (r["state"], r["title"]))
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    from collections import Counter
    cnt = Counter(r["state"] for r in rows)
    print("状态统计:", dict(cnt))
    print()
    print("%-14s %-16s %-9s %s" % ("状态", "远端更新", "远/本地字", "标题"))
    print("-" * 104)
    for r in rows:
        print("%-14s %-16s %4d/%-5d %s"
              % (r["state"], r["update_time"] or "-",
                 r["remote_len"], r["local_len"], r["title"][:44]))
    return 0


if __name__ == "__main__":
    sys.exit(main())