#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把头条草稿正文**幂等灌成**本地 `article.html` 的内容（恢复 / 修复通用）。

⚠⚠ 存在的理由（2026-10-09 事故）：`tt_table_repair.py` 的清空校验写成
   `text <= 1`，但**正文被清空后 ProseMirror 会显示占位符**
   `<span class="syl-placeholder">请输入正文</span>`，innerText 长度是 **7**
   ⇒ 校验判定「没清空」→ 脚本直接放弃 → **草稿正文留空且已被自动保存**。
   ⇒ 「清空成功」与「编辑器为空」是两件事，本工具用**占位符识别**判空。

幂等：已经是目标内容就什么都不做（不会重复插入）。

用法：
    python -X utf8 scripts/tt_body_fill.py --case aeo-engine
    python -X utf8 scripts/tt_body_fill.py --all
    python -X utf8 scripts/tt_body_fill.py --case X --dry
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402

SEL = json.dumps(tp.SEL["body"])
PLACEHOLDER = "请输入正文"

# 正文是否「实质为空」—— 占位符不算内容
_IS_EMPTY = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'noeditor';
  var ph = e.querySelectorAll('.syl-placeholder, [class*=placeholder]');
  for (var i = 0; i < ph.length; i++) { ph[i].remove(); }
  var t = (e.innerText || '').trim();
  return JSON.stringify({len: t.length, empty: t === ''});
})(%s)"""

_READ = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'NOT FOUND';
  return JSON.stringify({
    tables: e.querySelectorAll('table').length,
    cells: e.querySelectorAll('td, th').length,
    text: (e.innerText || '').length
  });
})(%s)"""

_CLEAR = """(function(){
  var e = document.querySelector(%s);
  if (!e) return 'no editor';
  e.focus();
  var r = document.createRange(); r.selectNodeContents(e);
  var s = getSelection(); s.removeAllRanges(); s.addRange(r);
  return 'selected';
})(%s)"""


def is_empty(cdp):
    """正文是否为空（忽略占位符文本）。⚠ 占位符会被移除，副作用无害。"""
    raw = cdp.eval(_IS_EMPTY % (SEL, SEL))
    try:
        return bool(json.loads(raw).get("empty")) if raw and raw.startswith("{") \
            else None
    except ValueError:
        return None


def read_body(cdp):
    raw = cdp.eval(_READ % (SEL, SEL))
    try:
        return json.loads(raw) if raw and raw.startswith("{") else {}
    except ValueError:
        return {}


def body_html(cid):
    p = os.path.join(tp.OUT, cid, "article.html")
    with open(p, encoding="utf-8") as f:
        full = f.read()
    return full.split("\n", 1)[1] if full.startswith("<h1>") else full


def clear_body(cdp):
    """清空正文，走真实按键管线。返回 True/False/None(未知)。"""
    if cdp.eval(_CLEAR % (SEL, SEL)) != "selected":
        return False
    time.sleep(0.4)
    for ty in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": ty, "key": "a", "code": "KeyA",
            "modifiers": 2, "windowsVirtualKeyCode": 65})
    time.sleep(0.4)
    for ty in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": ty, "key": "Delete", "code": "Delete",
            "windowsVirtualKeyCode": 46})
    time.sleep(1.2)
    return is_empty(cdp)          # True = 真的空了；False = 还有内容


def fill_one(c, dry=False, force=False, remote_title=None):
    """把本地 article.html 灌进该案例的远端草稿。

    ⚠ `remote_title`：草稿箱里实际存在的标题。**有 3 条案例的远端标题是
       旧标题**（本地后来改过），按本地标题去草稿箱点「编辑」会找不到卡：
         harperai      远端「把英国公开抵押记录连成一张网，然后卖给贷款经纪」
         quran-unlock远端「Quran Unlock：移动 app」
         mort远端「MORT：AI 产品」
       ⇒ 批量刷新必须传**远端标题**。不传则用本地 meta.json 的标题。
    """
    cid = c["id"]
    mp = os.path.join(tp.OUT, cid, "meta.json")
    if not os.path.isfile(mp):
        return "没有 meta.json"
    with open(mp, encoding="utf-8") as f:
        meta = json.load(f)
    body = body_html(cid)
    want_t, want_c = body.count("<table"), body.count("<td>")
    if not want_t:
        return "本地稿无表格"

    box_title = remote_title or meta["title"]
    cdp, t, err = tp.open_draft_editor(box_title)
    if not cdp:
        return err or "打不开草稿"
    try:
        # ⚠ 先等编辑器把原文加载完（否则会读到半空的编辑器就动手）
        ok = False
        for _ in range(10):
            time.sleep(2)
            st = is_empty(cdp)
            if st is not None:
                ok = True
                break
        if not ok:
            return "编辑器一直没就绪，不敢动手"
        before = read_body(cdp)
        if (before.get("tables") or 0) == want_t \
                and (before.get("cells") or 0) == want_c:
            return "skip：已是目标内容（%d 表/%d 格）" % (want_t, want_c)
        if dry:
            return "dry：当前 %d 表/%d 格 → 目标 %d 表/%d 格" % (
                before.get("tables") or 0, before.get("cells") or 0,
                want_t, want_c)

        cleared = clear_body(cdp)
        if cleared is False:
            return "清空没生效（编辑器还有内容），已放弃"
        cdp.eval("(function(){var e=document.querySelector(%s);"
                 "if(e){e.focus();} return 'ok';})()" % SEL)
        cdp.send("Runtime.evaluate", {
            "expression": ("document.execCommand('insertHTML', false, %s)"
                           % json.dumps(body, ensure_ascii=False)),
            "returnByValue": True})
        time.sleep(2.5)
        mid = read_body(cdp)
        if (mid.get("tables") or 0) != want_t:
            return ("灌完表格数不对（%d != %d），**未保存**，草稿仍是空正文，"
                    "需重跑本命令" % (mid.get("tables") or 0, want_t))
        ok_save = tp._wait_autosave(cdp)
        after = read_body(cdp)
        good = ((after.get("tables") or 0) == want_t
                and (after.get("cells") or 0) == want_c)
        return ("ok：%d 表/%d 格%s" % (want_t, want_c,
                                       "" if ok_save else "（没等到保存提示）")
                if good else
                "保存后回读不符：%d 表/%d 格" % (after.get("tables") or 0,
                                             after.get("cells") or 0))
    finally:
        cdp.close_target(t["id"])


def _match_remote_titles(cases, cutoff=0.55):
    """只读草稿箱，把每个案例对到**远端实际标题**。

    ⚠ 为什么要这一步：`open_draft_editor(title)` 是按标题在草稿箱里找卡片，
      而有 3 条案例的远端标题是**旧标题**（本地后来改过），按本地标题去
      找会「列表里没这张卡」⇒ 直接掉进作品管理兜底，几十秒白等。
      批量前先只读一次草稿箱，建好映射，后面按映射点。

    返回 {cid: 远端标题}，匹配不上的 cid 不在字典里。
    """
    import difflib
    import tt_audit_drafts as ad

    cards = ad.read_draft_cards(verbose=True)
    if not cards:
        return {}
    box_titles = [c.get("t", "").strip() for c in cards]
    box_titles = [t for t in box_titles if t]

    mapping = {}
    for c in cases:
        meta_p = os.path.join(tp.OUT, c["id"], "meta.json")
        if not os.path.isfile(meta_p):
            continue
        with open(meta_p, encoding="utf-8") as f:
            local_title = json.load(f)["title"]
        if local_title in box_titles:                # 精确命中（多数）
            mapping[c["id"]] = local_title
            continue
        m = difflib.get_close_matches(local_title, box_titles,
                                      n=1, cutoff=cutoff)
        if m:
            mapping[c["id"]] = m[0]
            print("  ⚠ 标题漂移 %-16s 本地=%s" % (c["id"], local_title))
            print("  %-19s 远端=%s" % ("", m[0]))
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--remote-title", default=None,
                    help="配合 --case：直接指定草稿箱里的真实标题")
    args = ap.parse_args()
    if not args.case and not args.all:
        args.case = "aeo-engine"
    cases = ([tp.find_case_fuzzy(args.case)] if args.case else list(tp.load_cases()))
    cases = [c for c in cases if c]

    remote = {}
    if args.all and not args.case:
        print("== 只读草稿箱，建远端标题映射 ==", flush=True)
        remote = _match_remote_titles(cases)
        print("   匹配上 %d / %d" % (len(remote), len(cases)), flush=True)
        # ⚠⚠ 只刷草稿箱里**真实存在**的案例（2026-10-09 修）。
        #   原来不过滤 ⇒ 13 条不在草稿箱的也去跑，每条触发一次
        #   `open_draft_editor` 的**作品管理兜底搜索**（列表几百条、滚 120 屏
        #   ≈ 144 秒）⇒ 实测 `--all` 跑到第 1 条就卡住 15 分钟毫无进展。
        #   这些案例本来也无处可改，跳过才是对的。
        skipped = [c for c in cases if c["id"] not in remote]
        cases = [c for c in cases if c["id"] in remote]
        print("   跳过（草稿箱里没有）%d 条：%s\n" % (
            len(skipped), " ".join(c["id"] for c in skipped)), flush=True)
        if args.dry:
            print("--dry：只看范围不改内容")
            for c in cases:
                print("   %-20s ← %s" % (c["id"], remote.get(c["id"], "")))
            return 0

    ok = bad = skip = 0
    for i, c in enumerate(cases, 1):
        try:
            r = fill_one(c, dry=args.dry,
                         remote_title=(args.remote_title
                                       or remote.get(c["id"])))
        except Exception as e:                # noqa: BLE001
            r = "%s: %s" % (type(e).__name__, e)
        print("[%d/%d] %-20s %s" % (i, len(cases), c["id"], r), flush=True)
        ok += r.startswith("ok")
        skip += r.startswith("skip")
        bad += not (r.startswith("ok") or r.startswith("skip"))
    print("\n成功 %d / 跳过 %d / 失败 %d" % (ok, skip, bad))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())