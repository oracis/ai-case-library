#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条 16 条存量草稿：只读诊断「平台正文 vs 本地母版」。

前置事实（11-10 实测，别再猜）
-----------------------------
* `draft_list` **只回标题/封面/时间，没有 content** ⇒ 正文只能读编辑器 DOM。
* 草稿箱列表点「编辑」要滚动 + 等新 tab，单条 60–170s（`open_draft_editor`）。
  但编辑页 URL 是 `/profile_v4/graphic/publish?pgc_id=<gid>`，**可以直接开**，
  先用 `draft_list` 拿 `gid` 再导航，一条 ~10s。
* 头条的母版是 `out/articles/<cid>.md`（`cmd_draft` 走
  `strip_source_links` → `md_to_blocks` → `blocks_to_html`），
  **不是 `.html`**（那 36 个 html 至今还带「我去核了一遍」注记）。

判据：同 B站/公众号 —— `diff_stats(母版, 平台) -> (dele, ins)`，
`dele==0` 表示平台不缺母版内容；`MARK in 平台` 表示带内部注记。

用法
----
    python -X utf8 tmp/diag_toutiao_16.py              # 只读
    python -X utf8 tmp/diag_toutiao_16.py --limit 4    # 分块（前台硬限）
"""
import argparse
import difflib
import html as H
import json
import os
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

MARK = "我去核了一遍"
DUMP = os.path.join(ROOT, "tmp", "tt16")
DONE = os.path.join(DUMP, "_done.json")
DRAFT_BOX = "https://mp.toutiao.com/profile_v4/manage/draft"
PUB_TPL = "https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=%s"

# ⚠ JS 里的选择器必须写成**一个字符串**（或显式 + 拼接）。11-11 踩过：
#   照 Python 习惯写「相邻字符串自动拼接」⇒ JS `missing ) after argument list`，
#   而 CDP.eval 的 exceptionDetails 判定漏了外层 result ⇒ 返 None 被当成
#   「页面没加载完」，白等 198s。选择器一行写完，别分成多行字面量。
JS_READ = """(function(){
  var out={url:location.href,title:'',body:'',ed:''};
  var sel="textarea[placeholder*='文章标题'],input[placeholder*='文章标题'],textarea[placeholder*='标题'],input[placeholder*='标题']";
  var ta=document.querySelector(sel);
  out.title = ta ? (''+ta.value) : '';
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return a.getBoundingClientRect().top-b.getBoundingClientRect().top});
  if(eds.length){ out.body = ''+eds[0].innerText; out.ed = 'pm'; }
  else {
    var alt=document.querySelector("[contenteditable='true']");
    if(alt){ out.body=''+alt.innerText; out.ed='ce'; }
  }
  return JSON.stringify(out);
})()"""


def base(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", H.unescape(t)).strip()


def diff_stats(a, b):
    dele = ins = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            dele += i2 - i1
        if tag in ("insert", "replace"):
            ins += j2 - j1
    return dele, ins


def master_text(cid):
    p = os.path.join(ROOT, "out", "articles", cid + ".md")
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8") as f:
        md = f.read()
    md, _ = tt.strip_source_links(md)
    return base(tt.blocks_to_html(tt.md_to_blocks(md), footer=False))


def targets():
    log = json.load(open(os.path.join(ROOT, "tmp", "batch_clean_log.json"),
                         encoding="utf-8"))
    changed = [os.path.splitext(os.path.basename(r[0]))[0]
               for r in log["results"] if r[1] != "skip"]
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(changed))
    cur.execute("SELECT case_id FROM platform_posts WHERE platform='toutiao' "
                "AND state='draft' AND case_id IN (%s) ORDER BY case_id" % q,
                changed)
    cids = [r[0] for r in cur.fetchall()]
    conn.close()
    return cids


def load_json(p, dflt):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                          # noqa: BLE001
        return dflt


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args(argv)

    cids = targets()
    if a.only:
        cids = [c for c in cids if c == a.only]
    done = load_json(DONE, {})
    if not a.redo:
        cids = [c for c in cids if c not in done]
    print("待查 %d 条（已查 %d）" % (len(cids), len(done)))
    if not cids:
        return 0

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=tt.CDP_PORT, profile=None)

    # ① 先开草稿箱页（draft_list 是同源 fetch，必须在 mp.toutiao.com 上下文）
    cdp, box = tt._open(DRAFT_BOX, wait=8)
    tt._check_login(cdp)
    raw = tt.fetch_draft_api_raw(cdp) or []
    print("draft_list %d 条" % len(raw))
    by_cid = {}
    for item in raw:
        c = tt.find_case_for_title(item.get("title") or "")
        if c:
            by_cid.setdefault(c["id"], []).append(item)
    # 标题里没有产品名的稿（augora / le19emetrou 这类论点式标题）`find_case_for_title`
    # 认不出 ⇒ 用人工核过的 case→gid 表兜底（tmp/tt16_map.json）。
    manual = load_json(os.path.join(ROOT, "tmp", "tt16_map.json"), {})
    for cid, gid in manual.items():
        if by_cid.get(cid):
            continue
        hit = [x for x in raw if (x.get("gid") or "") == gid]
        if hit:
            by_cid[cid] = hit
    print("认出 case %d 个（其中人工表 %d 个）\n"
          % (len(by_cid), len([c for c in manual if c in by_cid])))

    os.makedirs(DUMP, exist_ok=True)
    n = 0
    for cid in cids:
        if a.limit and n >= a.limit:
            print("… 已达 --limit %d" % a.limit)
            break
        n += 1
        hits = by_cid.get(cid) or []
        if not hits:
            print("%-26s ✗ 草稿箱里没这张卡（台账有 draft 行、箱里无稿）" % cid)
            done[cid] = {"status": "notfound", "at": time.strftime("%H:%M")}
            save_json(DONE, done)
            continue
        gid = hits[0].get("gid") or hits[0].get("pgc_id") or ""
        if not gid:
            print("%-26s ✗ 无 gid 字段：%s" % (cid, list(hits[0].keys())[:12]))
            continue
        t0 = time.time()
        # ⚠⚠ 每条必须**新建 CDP 对象**：草稿箱那个已 connect 到 draft tab，
        #    复用它去 connect 新 tab 时执行上下文会串 ⇒ eval 恒空、白等 348s
        #    （11-11 实测）。probe_tt_preview 用独立对象 2s 就读到 1364 字。
        tab = wp.CDP(tt.CDP_PORT)
        t = tab.new_target(PUB_TPL % gid)
        if not t or not tab.connect_target(t["id"]):
            print("%-26s ✗ 开不了编辑页" % cid)
            continue
        try:
            got = {}
            for _ in range(14):
                time.sleep(1.5)
                try:
                    r = json.loads(tab.eval(JS_READ, refresh_context=True) or "{}")
                except Exception:                              # noqa: BLE001
                    continue
                if r.get("body") and len(r["body"]) > 80:
                    got = r
                    break
            if not got:
                print("%-26s ✗ 编辑页读不到正文（%.0fs）" % (cid, time.time() - t0))
                continue
            body, title = got["body"], got["title"]
            with open(os.path.join(DUMP, cid + ".txt"), "w", encoding="utf-8") as f:
                f.write(body)
            p = base(body)
            m = master_text(cid)
            dele, ins = diff_stats(m, p)
            tag = []
            if MARK in body:
                tag.append("带注记")
            if dele > 20:
                tag.append("平台缺%4d字" % dele)
            if ins > 40:
                tag.append("平台多%4d字" % ins)
            print("%-26s 平台 %4d / 母版 %4d ｜ 缺%4d 多%4d ｜ %s（%.0fs）"
                  % (cid, len(p), len(m), dele, ins,
                     "、".join(tag) or "干净", time.time() - t0))
            done[cid] = {"gid": gid, "len": len(p), "title": title[:60],
                         "at": time.strftime("%H:%M")}
            save_json(DONE, done)
        finally:
            try:
                tab.close_target(t["id"])
            except Exception:                                  # noqa: BLE001
                pass
    print("\n本轮 %d 条落盘 → tmp/tt16/" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
