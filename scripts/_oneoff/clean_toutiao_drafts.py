#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条 12 条已有草稿：**只删平台正文里的核验注记**，其余一字不动。

为什么不用 `save_draft_api` 全量重提
----------------------------------
`save_draft_api` 是全量 20 字段提交：不带 `pgc_feed_covers` 会把封面冲掉
（`cover_one_api` docstring 已记），还要重新抓 `tt-anti-token`（会留探针草稿）。
既然实测 `/profile_v4/graphic/publish?pgc_id=<gid>` 能直开编辑器、
且头条编辑器**自动保存**（页面出现「草稿保存中…/已保存」），
就走原位 DOM 手术：选中那个 `<blockquote>` → `execCommand('delete')`
→ 等自动保存 → 关页重开独立回读。标题 / 摘要 / 封面 / 其余段落全不动。

判据（与公众号同款）
--------------------
① 页内 `norm(after) == norm(before)` 抠掉注记串，或缺 0 字多 ≤2 字；
② **独立回读**：全新连接重开同一 gid 的编辑页，注记必须消失且正文逐字一致；
③ 全批前后 `draft_list` 条数与 12 条的 `cover_image.image_uri` 不变。

用法
----
    python -X utf8 tmp/clean_toutiao_drafts.py                  # 干跑
    python -X utf8 tmp/clean_toutiao_drafts.py --go --limit 4   # 真改
"""
import argparse
import difflib
import html as H
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

MARK = "我去核了一遍"
PUB_TPL = "https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id=%s"
MAP = os.path.join(ROOT, "tmp", "tt16_map.json")
DONE = os.path.join(ROOT, "tmp", "tt16", "_clean_done.json")
# 人工裁定：boothie 双向改写、storyshort 整篇不同版本 ⇒ 一律不碰
EXCLUDE = {"boothie-ai-photo-editor", "storyshort-ai"}

JS_DELETE = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  if(!eds.length) return JSON.stringify({ok:false,why:'NO_EDITOR'});
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  var el=eds[0], before=''+(el.innerText||'');
  var cand=[].slice.call(el.querySelectorAll('blockquote,p,div,section,li'))
    .filter(function(n){return (n.innerText||'').indexOf('我去核了一遍')>=0});
  if(!cand.length) return JSON.stringify({ok:false,why:'NO_NOTE',before:before});
  cand.sort(function(a,b){return (a.innerText||'').length-(b.innerText||'').length});
  var n=cand[0], note=''+(n.innerText||'');
  el.focus();
  var r=document.createRange(); r.selectNode(n);
  var s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
  var ok=document.execCommand('delete');
  return JSON.stringify({ok:!!ok, tag:n.tagName, before:before, note:note,
                         after:''+(el.innerText||'')});
})()"""

JS_READ = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  return JSON.stringify({body: eds.length?(''+eds[0].innerText):'',
                         save:(document.body.innerText||'').slice(0,120)});
})()"""


def norm(s):
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


def load_json(p, dflt):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                          # noqa: BLE001
        return dflt


def save_json(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def open_editor(gid, settle=14):
    """每条都用全新 CDP 对象，避免和草稿箱/上一条的执行上下文串掉。"""
    c = wp.CDP(tt.CDP_PORT)
    t = c.new_target(PUB_TPL % gid)
    c.connect_target(t["id"])
    for _ in range(settle):
        time.sleep(1.5)
        try:
            o = json.loads(c.eval(JS_READ, refresh_context=True) or "{}")
        except Exception:                                      # noqa: BLE001
            continue
        if len(o.get("body") or "") > 80:
            return c, t["id"]
    return c, t["id"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args(argv)

    mp = load_json(MAP, {})
    rows = [(cid, str(gid)) for cid, gid in sorted(mp.items())
            if cid not in EXCLUDE]
    if a.only:
        rows = [r for r in rows if r[0] == a.only]
    done = load_json(DONE, {})
    if not a.redo:
        rows = [r for r in rows if r[0] not in done]
    print("待处理 %d 条（已完成 %d）｜模式 %s\n"
          % (len(rows), len(done), "真改" if a.go else "干跑"), flush=True)
    if not rows:
        return 0

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=tt.CDP_PORT, profile=None)

    # 基线：草稿箱条数 + 每篇封面 uri
    box, box_tid = tt._open(tt.TT_DRAFT, wait=8)
    raw0 = tt.fetch_draft_api_raw(box) or []
    covers0 = {str(x.get("gid")): ((x.get("cover_image") or {}).get("image_uri") or "")
               for x in raw0}
    print("草稿箱基线 %d 条\n" % len(raw0), flush=True)
    box.close_target(box_tid)

    n = ok = 0
    for cid, gid in rows:
        if a.limit and n >= a.limit:
            print("… 已达 --limit %d" % a.limit, flush=True)
            break
        n += 1
        t0 = time.time()
        c, tid = open_editor(gid)
        try:
            d = json.loads(c.eval(JS_DELETE, refresh_context=True) or "{}")
            if not d.get("ok"):
                print("%-26s ✗ 删注记失败：%s" % (cid, d.get("why")), flush=True)
                continue
            nb, na, nn = norm(d["before"]), norm(d["after"]), norm(d["note"])
            expected = nb.replace(nn, "", 1)
            dele, ins = diff_stats(expected, na)
            if MARK in d["after"] or dele > 0 or ins > 2:
                print("%-26s ✗ 页内校验不过：残留=%s 缺%d 多%d"
                      % (cid, MARK in d["after"], dele, ins), flush=True)
                continue
            print("%-26s 页内 ✓ 删 %s %d 字 ｜ %d→%d ｜ 缺%d 多%d"
                  % (cid, d.get("tag"), len(nn), len(nb), len(na), dele, ins),
                  flush=True)
            if not a.go:
                print("    [干跑] 未等待自动保存，草稿未改动", flush=True)
                continue
            saved = tt._wait_autosave(c, timeout=40)
            time.sleep(3)
            print("    自动保存提示=%s" % saved, flush=True)
        except Exception as e:                                 # noqa: BLE001
            print("%-26s ✗ 异常：%s" % (cid, e), flush=True)
            continue
        finally:
            try:
                c.close_target(tid)
            except Exception:                                  # noqa: BLE001
                pass
        if not a.go:
            continue
        # ② 独立回读：关页重开，只信服务端重新渲染的正文
        try:
            c2, tid2 = open_editor(gid)
            try:
                o = json.loads(c2.eval(JS_READ, refresh_context=True) or "{}")
            finally:
                c2.close_target(tid2)
            p2 = norm(o.get("body") or "")
            d2, i2 = diff_stats(expected, p2)
            if not p2 or MARK in p2 or d2 > 0 or i2 > 2:
                print("    ✗ 独立回读不过：空=%s 残留=%s 缺%d 多%d"
                      % (not p2, MARK in p2, d2, i2), flush=True)
                continue
            print("    ✓ 独立回读通过：%d 字 ｜ 注记已清 ｜ 缺%d 多%d（%.0fs）"
                  % (len(p2), d2, i2, time.time() - t0), flush=True)
            done[cid] = {"gid": gid, "len": len(p2), "note_len": len(nn),
                         "at": time.strftime("%H:%M")}
            save_json(DONE, done)
            ok += 1
        except Exception as e:                                 # noqa: BLE001
            print("    ✗ 独立回读异常：%s" % e, flush=True)

    # ③ 收尾：条数 + 封面 uri 不变
    if a.go:
        try:
            box2, t2 = tt._open(tt.TT_DRAFT, wait=8)
            raw1 = tt.fetch_draft_api_raw(box2) or []
            covers1 = {str(x.get("gid")): ((x.get("cover_image") or {}).get("image_uri") or "")
                       for x in raw1}
            box2.close_target(t2)
            diff = [g for g in covers0 if g in covers1 and covers0[g] != covers1[g]]
            print("\n草稿箱 %d → %d 条 ｜ 封面 uri 变化 %d 个（应为 0）"
                  % (len(raw0), len(raw1), len(diff)), flush=True)
        except Exception as e:                                 # noqa: BLE001
            print("（收尾回读失败：%s）" % e, flush=True)
    print("\n本轮 %d 条 ｜ 成功 %d ｜ 已完成累计 %d" % (n, ok, len(done)),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
