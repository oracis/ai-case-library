#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公众号 15 条已有草稿：**只删平台正文里那一段核验注记**，其余一字不动。

为什么不用「母版覆盖」
----------------------
`build_one()` 重建出来的产物比平台草稿**少一段 59 字栏目尾巴**
（「这条案例的完整核实记录…我每天筛一批发在社群里」），这段是建稿时由
其它链路加上的、现在本地母版里已经没有。整篇覆盖会顺手删掉它 ⇒ 不是清注记，
是改文案。所以走**平台原位手术**：在编辑器里选中那个 `<blockquote>` 删掉，
其余 DOM / 图片 / 封面 / 标题 / 摘要全部原样保留。

实测依据（`tmp/probe_wechat_edit_existing.py`，appmsgid=100000428）
----------------------------------------------------------------
* 已有草稿编辑页同样有 `window.__MP_Editor_JSAPI__`；
* 两个 ProseMirror：标题 25 字、正文 1886 字（按 innerText 最长取正文）；
* 注记是独立 `<blockquote>`（56 字，内含 `<section><span leaf="">…`）；
* 可见按钮里只有「保存为草稿」，`继续保存`/`返回原草稿` 是隐藏的，无弹窗。

验收（不认 code=0、不认按钮点到了）
----------------------------------
① 页内：删完 `MARK` 消失，且 `norm(after) == norm(before)` 抠掉注记串，
   或者「缺 0 字、多 ≤2 字」⇒ 保证没顺手删掉别的段落；
② **独立回读**：另起连接开新编辑页 `read_draft_body`，必须 `MARK` 消失且
   与期望文本一致（页面关掉重开，只信服务端落库状态）；
③ 全批前后 `list_ex` 草稿总数不变（不许因为保存而多出一条草稿）。

用法
----
    python -X utf8 tmp/clean_wechat_drafts.py                 # 干跑（不保存）
    python -X utf8 tmp/clean_wechat_drafts.py --go --limit 3  # 真改（分块）
    python -X utf8 tmp/clean_wechat_drafts.py --only augora-ai --go
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

MARK = "我去核了一遍"
DONE = os.path.join(ROOT, "tmp", "wx15", "_clean_done.json")

JS_DELETE = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll('.ProseMirror[contenteditable="true"]'));
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

JS_SAVE = r"""(function(){
  var bs=[].slice.call(document.querySelectorAll('button,a'));
  var b=bs.filter(function(x){
    var r=x.getBoundingClientRect();
    return (x.textContent||'').trim()==='保存为草稿' && r.width>0 && !x.disabled;
  })[0];
  if(!b) return 'NO_BTN';
  b.click(); return 'CLICKED';
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


def targets():
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    ids = [os.path.basename(f)[:-4]
           for f in sorted(os.listdir(os.path.join(ROOT, "tmp", "wx15")))
           if f.endswith(".txt")]
    q = ",".join("?" * len(ids))
    cur.execute("SELECT case_id, remote_id, draft_id, title FROM platform_posts "
                "WHERE platform='wechat' AND state='draft' AND case_id IN (%s) "
                "ORDER BY case_id" % q, ids)
    rows = [{"cid": r[0], "aid": (r[1] or r[2] or ""), "title": r[3] or ""}
            for r in cur.fetchall()]
    conn.close()
    return rows


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


def fresh(port=0):
    """每条都用全新连接：_open_draft_editor 会把 cdp 连到新 tab，
    用完关掉后该对象就废了，复用会让下一条 eval 恒空。"""
    port = port or wp.CDP_PORT
    c = wp.CDP(port)
    _tid, tok = wp._connect_mp(c)
    if not tok:
        raise RuntimeError("拿不到 token")
    return c, tok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--redo", action="store_true")
    a = ap.parse_args(argv)

    rows = targets()
    if a.only:
        rows = [r for r in rows if r["cid"] == a.only]
    done = load_json(DONE, {})
    if not a.redo:
        rows = [r for r in rows if r["cid"] not in done]
    print("待处理 %d 条（已完成 %d）｜模式 %s\n"
          % (len(rows), len(done), "真改" if a.go else "干跑"), flush=True)
    if not rows:
        return 0

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=wp.CDP_PORT)

    c0, tok0 = fresh()
    try:
        before_list = wp._draft_list(c0, tok0, count=50) or []
        print("草稿箱基线 %d 条\n" % len(before_list), flush=True)
    except Exception as e:                                     # noqa: BLE001
        print("（基线读取失败，不影响逐条验收：%s）" % e, flush=True)
        before_list = []

    n = ok = 0
    for r in rows:
        if a.limit and n >= a.limit:
            print("… 已达 --limit %d" % a.limit, flush=True)
            break
        n += 1
        cid, aid, title = r["cid"], r["aid"], r["title"]
        t0 = time.time()
        c, tok = fresh()
        tid = None
        try:
            tid = wp._open_draft_editor(c, aid, tok, timeout=45, strict=True)
            raw = c.eval(JS_DELETE, refresh_context=True)
            d = json.loads(raw or "{}")
            if not d.get("ok"):
                print("%-26s ✗ 删注记失败：%s" % (cid, d.get("why")), flush=True)
                continue
            before, after, note = d["before"], d["after"], d["note"]
            nb, na, nn = norm(before), norm(after), norm(note)
            expected = nb.replace(nn, "", 1)
            dele, ins = diff_stats(expected, na)
            if MARK in after or dele > 0 or ins > 2:
                print("%-26s ✗ 页内校验不过：注记残留=%s 缺%d 多%d（不保存）"
                      % (cid, MARK in after, dele, ins), flush=True)
                continue
            print("%-26s 页内 ✓ 删 %s %d 字 ｜ %d→%d ｜ 缺%d 多%d"
                  % (cid, d.get("tag"), len(nn), len(nb), len(na), dele, ins),
                  flush=True)
            if not a.go:
                print("    [干跑] 未保存，草稿未改动", flush=True)
                continue
            sv = c.eval(JS_SAVE, refresh_context=True)
            if sv != "CLICKED":
                print("    ✗ 保存按钮点不到：%s" % sv, flush=True)
                continue
            time.sleep(6)
            print("    已点「保存为草稿」，等落库…", flush=True)
        except Exception as e:                                 # noqa: BLE001
            print("%-26s ✗ 异常：%s" % (cid, e), flush=True)
            continue
        finally:
            if tid:
                try:
                    c.close_target(tid)
                except Exception:                              # noqa: BLE001
                    pass
        if not a.go:
            continue
        # ② 独立回读：全新连接 + 全新编辑页，只信服务端重新渲染的正文
        try:
            c2, tok2 = fresh()
            body2 = wp.read_draft_body(c2, tok2, aid, expect_title=title)
            if not body2:
                print("    ✗ 独立回读空正文（%s）" % aid, flush=True)
                continue
            p2 = norm(body2)
            d2, i2 = diff_stats(expected, p2)
            if MARK in body2 or d2 > 0 or i2 > 2:
                print("    ✗ 独立回读不过：注记残留=%s 缺%d 多%d"
                      % (MARK in body2, d2, i2), flush=True)
                continue
            print("    ✓ 独立回读通过：%d 字 ｜ 注记已清 ｜ 缺%d 多%d（%.0fs）"
                  % (len(p2), d2, i2, time.time() - t0), flush=True)
            done[cid] = {"aid": aid, "len": len(p2), "note_len": len(nn),
                         "at": time.strftime("%H:%M")}
            save_json(DONE, done)
            ok += 1
        except Exception as e:                                 # noqa: BLE001
            print("    ✗ 独立回读异常：%s" % e, flush=True)

    # ③ 全批前后草稿总数不变
    if a.go:
        try:
            c3, tok3 = fresh()
            after_list = wp._draft_list(c3, tok3, count=50) or []
            print("\n草稿箱 %d → %d 条（应相等，多出来说明误建了新稿）"
                  % (len(before_list), len(after_list)), flush=True)
        except Exception as e:                                 # noqa: BLE001
            print("（收尾列表读取失败：%s）" % e, flush=True)
    print("\n本轮 %d 条 ｜ 成功 %d ｜ 已完成累计 %d" % (n, ok, len(done)),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
