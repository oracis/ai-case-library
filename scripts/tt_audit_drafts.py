#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条远端状态**只读**审计（绝不点任何写操作按钮）。

回答三个问题：
  1. 草稿箱里现在到底有哪些稿（真实标题 + 时间 + 有无表格）？
  2. 每个本地案例在草稿箱/已发布区的存在状态。
  3. 重复标题、`[无标题]` 垃圾稿、测试残留（zz 开头）各有几条。

⚠ 只读约束：不点「编辑」「修改」「删除」「发布」，不 `execCommand`，
   不 `new_target` 发文页 —— 只读列表 DOM 文本 + 展开「加载更多」。
   因此可以安全地在跑批量修复**之前**先跑这个。

用法：
    python -X utf8 scripts/tt_audit_drafts.py            # 草稿箱清单 + 统计
    python -X utf8 scripts/tt_audit_drafts.py --find uplinked-b-v   # 查某条
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402
import wechat_publish as wp                                    # noqa: E402

# 每张草稿卡：标题（首个非空行）+ 其余信息行
_DRAFT_CARDS_JS = """(function(){
  var es = [].slice.call(document.querySelectorAll(
      '.article-draft-item, .draft-item'));
  return JSON.stringify(es.map(function(e){
    var lines = (e.innerText||'').split('\\n')
      .map(function(s){return s.trim();})
      .filter(function(s){return s;});
    return {t: lines[0]||'', info: lines.slice(1,3).join(' / ')};
  }));
})()"""

_TOTAL_JS = """(function(){
  var m = (document.body.innerText||'').match(/共\\s*(\\d+)\\s*条/);
  return m ? m[1] : '';
})()"""

_HEAD_NOISE = ("写文章", "草稿箱", "已发布", "作品管理", "数据", "创作",
               "设置", "首页", "登录")


def _connect(url_sub, timeout=60):
    """打开（或复用）一个包含 url_sub 的 tab，返回 (cdp, target_id)。"""
    cdp = wp.CDP(tp.CDP_PORT)
    tid = None
    for t in cdp.list_targets():
        if url_sub in t.get("url", "") and t.get("webSocketDebuggerUrl"):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(tp.TT_DRAFT)["id"]
    c = wp.CDP(tp.CDP_PORT)
    if not c.connect_target(tid):
        return None, None
    time.sleep(3)
    return c, tid


def read_draft_cards(verbose=True):
    """只读列出草稿箱全部卡片。返回 [{t, info}]。"""
    cdp, tid = _connect("/manage/draft")
    if not cdp:
        return None
    try:
        cdp.send("Page.navigate", {"url": tp.TT_DRAFT})
        time.sleep(9)
        # 展开「加载更多」—— 只是加载列表，不改任何数据。
        # ⚠ 必须用 tp._load_all_drafts（2026-10-09 已重写为 JS 点最深 SPAN，
        #   旧版坐标派发点不动，恒停在首屏 20 条 → 覆盖判断全是假阴性）。
        n = tp._load_all_drafts(cdp, max_rounds=30, pause=1.8)
        raw = cdp.eval(_DRAFT_CARDS_JS) or "[]"
        cards = json.loads(raw)
        if verbose:
            total = tp._draft_total(cdp)
            print("[草稿箱] 渲染卡片 %d 条，页面声明总数 %d%s" % (
                n, total,
                "" if total and n >= total else
                "  ⚠️ 未展开全（%s）" % ("卡片<总数" if total else "总数未知")))
        return cards
    finally:
        pass          # tab 留着不关，下一条复用


def looks_like_title(s):
    """草稿卡首行是不是真标题（滤掉 UI 噪声与占位符）。"""
    if not s or len(s) < 3:
        return False
    if s in ("[无标题]", "无标题", "未命名"):
        return False
    if s in _HEAD_NOISE:
        return False
    return True


def audit(cards):
    titles = [(c.get("t") or "").strip() for c in cards]
    infos = [(c.get("info") or "").strip() for c in cards]
    real = [(t, i) for t, i in zip(titles, infos) if looks_like_title(t)]
    untitled = [(t, i) for t, i in zip(titles, infos)
                if t in ("[无标题]", "无标题")]
    tests = [(t, i) for t, i in real if t.lower().startswith("zz")]
    seen, dups = {}, []
    for t, _ in real:
        if t in seen:
            dups.append(t)
        seen[t] = 1

    print("\n=== 草稿箱统计 ===")
    print("卡片总数      : %d" % len(cards))
    print("有效标题      : %d" % len(real))
    print("[无标题] 垃圾 : %d" % len(untitled))
    print("zz 测试残留   : %d" % len(tests))
    print("重复标题      : %d %s" % (len(dups), dups if dups else ""))
    for t, i in untitled:
        print("   [无标题] %s" % i)
    for t, i in tests:
        print("   [测试稿] %s  %s" % (t[:30], i))
    return real, untitled, tests, dups


def build_index(cards):
    """标题 → 本地案例 id。"""
    idx = {}
    for c in tp.load_cases():
        t = tp.make_toutiao_title(c)
        if t:
            idx[t] = c["id"]
    return idx


def report_coverage(real, idx):
    """每个本地案例在草稿箱的命中情况。

    ⚠ 必须分两级判：**精确标题** 与 **模糊/漂移标题**。
       实测草稿箱里有 4 条是旧标题（本地后来改过），只看精确匹配会把它们
       误报成「不在草稿箱」⇒ 又一次假阴性。
    """
    import difflib
    print("\n=== 案例 × 草稿箱命中 ===")
    exact, drifted, not_in = [], [], []
    box_titles = [t for t, _ in real]
    for c in tp.load_cases():
        t = tp.make_toutiao_title(c)
        if t in box_titles:
            exact.append((c["id"], t, ""))
            continue
        m = difflib.get_close_matches(
            t or "", box_titles, n=1, cutoff=0.55) if t else []
        if m:
            drifted.append((c["id"], t, m[0]))
        else:
            not_in.append((c["id"], t, ""))

    print("精确命中 %d / 标题漂移 %d / 真不在 %d / 共 %d" % (
        len(exact), len(drifted), len(not_in),
        len(exact) + len(drifted) + len(not_in)))
    if drifted:
        print("\n⚠ 标题漂移（草稿箱里是**旧标题**，批量刷新要按草稿标题匹配）：")
        for cid, new, old in drifted:
            print("   %-18s 本地=%s" % (cid, new))
            print("   %-18s 远端=%s" % ("", old))
    if not_in:
        print("\n真不在草稿箱（需走已发布区或从未发过）：")
        for cid, t, _ in not_in:
            print("   %-22s %s" % (cid, t))
    return exact, drifted, not_in


def find_in_works(title, max_rounds=120):
    """在作品管理（已发布区）只读找标题。返回 (info, '') 或 (None, '')。

    ⚠ 只滚动 + 读文本，**不点「修改」**。
    ⚠ 全程用**同一个** CDP 对象：踩过 `Page.navigate` 走 A、`eval` 走 B
       的坑，报「未连接页面 target」。
    """
    cdp = wp.CDP(tp.CDP_PORT)
    tid = None
    for t in cdp.list_targets():
        if "/manage/content/all" in t.get("url", "") and t.get("webSocketDebuggerUrl"):
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(tp.TT_WORKS)["id"]
    if not cdp.connect_target(tid):
        return None, ""
    cdp.send("Page.navigate", {"url": tp.TT_WORKS})
    time.sleep(9)

    scroll_js = """(function(){
      var cands=[].slice.call(document.querySelectorAll('div,main,section'))
        .filter(function(e){
          return e.scrollHeight>e.clientHeight+80 && e.clientHeight>300;});
      var el=cands.sort(function(a,b){return b.clientHeight-a.clientHeight;})[0];
      if(!el) return 'none';
      var before=el.scrollTop;
      el.scrollTop=el.scrollHeight;
      return (el.scrollTop===before) ? 'bottom' : String(el.scrollTop);
    })()"""
    hit_js = """(function(t){
      var cards=[].slice.call(document.querySelectorAll('li,tr,div'))
        .filter(function(e){
          var x=(e.innerText||'');
          return x.indexOf(t)>=0 && x.length<400 && x.indexOf('修改')>=0;});
      if(!cards.length) return 'no-card';
      cards.sort(function(a,b){return a.innerText.length-b.innerText.length;});
      var info=cards[0].innerText;
      for(var i=0;i<cards.length;i++){
        if((cards[i].innerText||'').indexOf('已发布')>=0){info=cards[i].innerText;break;}
      }
      return JSON.stringify({info:info});
    })(""" + json.dumps(title)

    for i in range(max_rounds):
        r = cdp.eval(hit_js)
        if r and r not in ("no-card", "none"):
            return json.loads(r).get("info", "").replace("\n", " / ")
        s = cdp.eval(scroll_js)
        if s == "bottom":
            break
        time.sleep(1.0)
    return None, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", default=None,
                    help="额外去已发布区只读查这个案例")
    args = ap.parse_args()

    cards = read_draft_cards()
    if cards is None:
        print("!! 连不上草稿箱（CDP %d）" % tp.CDP_PORT)
        return 2
    real, untitled, tests, dups = audit(cards)
    print("\n=== 草稿箱清单 ===")
    for i, (t, info) in enumerate(real, 1):
        print("%2d. %-34s %s" % (i, t[:34], info[:40]))

    exact, drifted, not_in = report_coverage(real, build_index(cards))

    if args.find:
        c = tp.find_case_fuzzy(args.find)
        if not c:
            print("\n!! 本地找不到案例 %s" % args.find)
            return 2
        title = tp.make_toutiao_title(c)
        box_titles = [t for t, _ in real]
        print("\n=== 只读查《%s》（%s）===" % (title, c["id"]))
        if title in box_titles:
            print("在草稿箱 ✔ 无需查作品管理")
        else:
            import difflib
            m = difflib.get_close_matches(title, box_titles, n=1,
                                          cutoff=0.55)
            if m:
                print("草稿箱里有**旧标题**《%s》✔" % m[0])
            else:
                print("已发布区查询中（最多滚 120 屏）…", flush=True)
                info, _ = find_in_works(title)
                print("找到了 ✔ 卡片信息：%s" % info if info else
                      "已发布区也没找到 ❌ —— 可能从未发过，需重新 publish 建稿")
    return 0


if __name__ == "__main__":
    sys.exit(main())