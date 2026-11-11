#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""头条 12 条草稿清注记：`save_draft_api` 带 pgc_id 就地更新（只读干跑默认）。

为什么不再试「DOM 删除 + 自动保存」
----------------------------------
实测（11-11，headless 与 **有头** 都试过）：编辑器页 footer 一直是
「草稿保存中...」，`_wait_autosave` 40s 内拿不到「已保存」，关页重开注记仍在。
⇒ 页面自己的自动保存链路不通，`execCommand('delete')` 改了 DOM 却不落库。
项目里唯一验证过的写路径是 `save_draft_api`（`cover_one_api` 同款）：
取 `tt-anti-token` → form-urlencoded 全量重提 → 带 `pgc_id` 更新同一篇。

怎么保证「只删注记，不改文案」
------------------------------
1. 正文 HTML **取自平台编辑器自己的 innerHTML**，只把含注记的那个
   `<blockquote>` 的 outerHTML 整段抠掉；不拿本地母版重建。
2. 标题 / 摘要 / 封面从 `draft_list` 原样回填 —— `save_draft_api` 是全量
   20 字段提交，不回填封面会把 `pgc_feed_covers` 冲空（docstring 已记）。
3. 验收：① 接口返回 pgc_id 必须 == 原 gid 且 msg 不是 `code=`；
   ② **关页重开**独立回读编辑器正文，注记消失且 `dele==0 / ins<=2`；
   ③ 全批前后 `draft_list` 条数 + 每篇 `cover_image.image_uri` 不变。

⚠ `tt-anti-token` 必须是**有头 Chrome** 生成的（headless 恒 7050），
   启动前先 `CHROME_HEADED=1 python scripts/_launch_9223.py`。
   `grab_anti_token` 会建一条 `__probe_tok*` 探针草稿，`finally` 里必清。

用法
----
    python -X utf8 tmp/clean_toutiao_drafts_api.py --only chatbase           # 干跑
    python -X utf8 tmp/clean_toutiao_drafts_api.py --go --limit 3
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
EXCLUDE = {"boothie-ai-photo-editor", "storyshort-ai"}

# 读编辑器正文 HTML + 注记节点 outerHTML（只读，不改 DOM）
JS_GRAB = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  if(!eds.length) return JSON.stringify({ok:false,why:'NO_EDITOR'});
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  var el=eds[0], ih=el.innerHTML||'', text=''+(el.innerText||'');
  var cand=[].slice.call(el.querySelectorAll('blockquote,p,div,section,li'))
    .filter(function(n){return (n.innerText||'').indexOf('我去核了一遍')>=0});
  if(!cand.length) return JSON.stringify({ok:false,why:'NO_NOTE',text:text});
  cand.sort(function(a,b){return (a.innerText||'').length-(b.innerText||'').length});
  return JSON.stringify({ok:true, html:ih, note_html:cand[0].outerHTML||'',
                         note_text:''+(cand[0].innerText||''), text:text,
                         title:(document.querySelector("textarea[placeholder*='文章标题']")
                                ||{}).value||''});
})()"""

JS_READ = r"""(function(){
  var eds=[].slice.call(document.querySelectorAll(".ProseMirror[contenteditable='true']"));
  eds.sort(function(a,b){return (b.innerText||'').length-(a.innerText||'').length});
  return JSON.stringify({body: eds.length?(''+eds[0].innerText):''});
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

    # 基线：草稿箱全量元数据（标题/摘要/封面）
    box, box_tid = tt._open(tt.TT_DRAFT, wait=8)
    raw0 = tt.fetch_draft_api_raw(box) or []
    by_gid = {str(x.get("gid")): x for x in raw0}
    covers0 = {g: ((x.get("cover_image") or {}).get("image_uri") or "")
               for g, x in by_gid.items()}
    box.close_target(box_tid)
    print("草稿箱基线 %d 条\n" % len(raw0), flush=True)

    token = None
    sess = None
    if a.go:
        ua = ""
        try:
            probe = wp.CDP(tt.CDP_PORT)
            ua = str(probe.eval("navigator.userAgent") or "")
        except Exception:                                      # noqa: BLE001
            pass
        # 上面那个 probe 没连到页面，改成从已存在的 page target 读 UA
        try:
            t0 = [x for x in wp.CDP(tt.CDP_PORT).list_targets()
                  if x.get("type") == "page" and x.get("webSocketDebuggerUrl")][0]
            cc = wp.CDP(tt.CDP_PORT)
            cc.connect_target(t0["id"])
            ua = str(cc.eval("navigator.userAgent") or "")
        except Exception:                                      # noqa: BLE001
            pass
        print("UA: %s" % ua[:90], flush=True)
        if "HeadlessChrome" in ua:
            raise SystemExit("✗ 当前 9223 是 headless，tt-anti-token 服务端不认（7050）。"
                             "先用 CHROME_HEADED=1 python scripts/_launch_9223.py 起有头实例。")
        sess = tt.open_api_session()
        if not sess:
            raise SystemExit("✗ 开不了 API 编辑器会话")
        sess_cdp, sess_tid = sess
        token = tt.grab_anti_token(sess_cdp)
        print("anti-token: %s\n" % ("已取 %d 字符" % len(token) if token else "没取到"),
              flush=True)
        if not token:
            raise SystemExit("✗ 没拿到 token，拒绝写入")

    n = ok = 0
    try:
        for cid, gid in rows:
            if a.limit and n >= a.limit:
                print("… 已达 --limit %d" % a.limit, flush=True)
                break
            n += 1
            t0 = time.time()
            meta = by_gid.get(gid) or {}
            title = (meta.get("title") or "").strip()
            abstract = meta.get("abstract") or ""
            ci = meta.get("cover_image") or {}
            # ⚠ 不能把 draft_list 里那张**带签名的旧直链**原样回提：实测
            #   `code=5009 发布失败，请重试`。`cover_one_api` 的已验证做法是
            #   每次重新上传本地 cover.png 拿新鲜 url/uri ⇒ 照抄。
            cover = ((ci.get("image_url") or ""), (ci.get("image_uri") or ""), "",
                     (ci.get("width") or 0, ci.get("height") or 0))
            if a.go:
                cp = os.path.join(ROOT, "out", "toutiao", cid, "cover.png")
                if not os.path.isfile(cp):
                    print("%-26s ✗ 缺本地封面 %s，拒绝提交（会冲掉线上封面）"
                          % (cid, cp), flush=True)
                    continue
                fresh_cover = tt.upload_cover_api(sess_cdp, cp, token)
                if not fresh_cover or not fresh_cover[0]:
                    print("%-26s ✗ 封面重传失败：%s" % (cid, fresh_cover),
                          flush=True)
                    continue
                cover = fresh_cover
            if not title:
                print("%-26s ✗ draft_list 里没有这条元数据" % cid, flush=True)
                continue
            c, tid = open_editor(gid)
            try:
                d = json.loads(c.eval(JS_GRAB, refresh_context=True) or "{}")
            finally:
                c.close_target(tid)
            if not d.get("ok"):
                print("%-26s ✗ 读编辑器失败：%s" % (cid, d.get("why")), flush=True)
                continue
            ih, nhtml = d["html"], d["note_html"]
            if MARK not in ih or nhtml not in ih:
                print("%-26s ✗ 注记 outerHTML 不在正文里，拒绝猜" % cid, flush=True)
                continue
            clean_html = ih.replace(nhtml, "", 1)
            expected = norm(d["text"]).replace(norm(d["note_text"]), "", 1)
            if MARK in clean_html or len(ih) - len(clean_html) != len(nhtml):
                print("%-26s ✗ HTML 抠除校验不过" % cid, flush=True)
                continue
            check = norm(clean_html)
            dele0, ins0 = diff_stats(expected, check)
            if dele0 > 0 or ins0 > 2:
                print("%-26s ✗ 净化 HTML 与期望文本不符：缺%d 多%d"
                      % (cid, dele0, ins0), flush=True)
                continue
            print("%-26s 净化 ✓ 抠掉 %d 字符 HTML ｜ 文本 %d→%d ｜ 缺%d 多%d"
                  % (cid, len(nhtml), len(norm(d["text"])), len(check),
                     dele0, ins0), flush=True)
            if not a.go:
                print("    [干跑] 未提交，草稿未改动", flush=True)
                continue
            pid, msg = tt.save_draft_api(sess_cdp, title, clean_html, pgc_id=gid,
                                         abstract_text=abstract,
                                         anti_token=token, cover=cover)
            if str(msg).startswith("code=") or not pid:
                print("    ✗ 存稿被拒：pid=%s msg=%s" % (pid, msg), flush=True)
                continue
            if str(pid) != str(gid):
                print("    ✗ 更新落到别的稿：期望 %s 实际 %s" % (gid, pid), flush=True)
                continue
            print("    API 返回 pgc_id=%s（同一篇）" % pid, flush=True)
            # ② 独立回读：关页重开，只信服务端重新渲染的正文
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
            done[cid] = {"gid": gid, "len": len(p2),
                         "note_html": len(nhtml), "at": time.strftime("%H:%M")}
            save_json(DONE, done)
            ok += 1
    finally:
        if sess:
            try:
                tt.cleanup_probe_draft(sess[0])
                print("\n探针草稿已清理", flush=True)
            except Exception as e:                             # noqa: BLE001
                print("\n探针草稿清理失败（需手清）：%s" % e, flush=True)
            try:
                sess[0].close_target(sess[1])
            except Exception:                                  # noqa: BLE001
                pass

    # ③ 收尾：条数 + 封面 uri 不变
    if a.go:
        try:
            box2, t2 = tt._open(tt.TT_DRAFT, wait=8)
            raw1 = tt.fetch_draft_api_raw(box2) or []
            covers1 = {str(x.get("gid")): ((x.get("cover_image") or {}).get("image_uri") or "")
                       for x in raw1}
            box2.close_target(t2)
            diff = [g for g in covers0 if g in covers1 and covers0[g] != covers1[g]]
            print("草稿箱 %d → %d 条 ｜ 封面 uri 变化 %d 个（应为 0）"
                  % (len(raw0), len(raw1), len(diff)), flush=True)
        except Exception as e:                                 # noqa: BLE001
            print("（收尾回读失败：%s）" % e, flush=True)
    print("\n本轮 %d 条 ｜ 成功 %d ｜ 已完成累计 %d" % (n, ok, len(done)),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
