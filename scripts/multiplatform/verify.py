#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远端回读校验：清单说发过了，草稿箱里到底有没有？

**为什么必须做。** `state.py` 记的 `draft_saved` 只是「子进程退出码 0」，
而这轮批量发版已经出现过两次假成功：

* 头条封面上传返回 ok，但编辑器里的 `img.src` 根本没变（后来改成前后比对
  `img.src` 才看出来）；
* 本地记录 37 条、草稿箱只有 35 条，`kibu` / `pieter-levels` 去哪了说不清
  ——最后发现是它们已从草稿箱消失（发布或删除），而 records.json 仍留着。

所以这里提供**只读**的远端核对：读草稿箱标题列表，跟清单对账，差异分三类
报出来（清单说发了但远端没有 / 远端有但清单没记 / 状态矛盾）。

⚠️ 只读。不点任何按钮、不改任何状态。核对完请人工判断差异原因。
"""

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from multiplatform import state as S                # noqa: E402
from multiplatform import article as A              # noqa: E402

CDP_PORT = 9222


def _norm(t):
    """标题归一化：去空白、统一大小写、去书名号引号，用于宽松匹配。

    大小写必须归一：id/键一律小写（`coral`），而平台标题可能是
    `Coral AI` —— 不 lower 就匹配不上，差异会被误报成「远端少了这条」。
    中文标题无大小写，不受影响。
    """
    t = re.sub(r"[\s\u3000]+", "", (t or ""))
    return t.strip("《》\"'“”‘’").lower()


def _match(title, keys):
    """草稿卡片标题 → case id。用多级候选键（踩过的坑：标题与 id 不同）。"""
    tn = _norm(title)
    if not tn:
        return None
    for k in keys:
        if _norm(k) and _norm(k) in tn:
            return k
    return None


# ---- 头条 -----------------------------------------------------------------
TT_DRAFT = "https://mp.toutiao.com/profile_v4/manage/draft"
TT_DRAFT_JS = """
(() => {
  const items = document.querySelectorAll(
    '.draft-list-item, .publish-card, [class*="draft"] [class*="item"]');
  return Array.from(items).map(el => {
    const t = el.querySelector('h3, .title, [class*="title"]');
    return t ? (t.innerText||'').trim() : '';
  }).filter(Boolean).slice(0, 200);
})()
"""
TT_LOADMORE_JS = """
(() => {
  const b = document.querySelector('button, div, span');
  const el = Array.from(document.querySelectorAll('*')).find(e =>
    e.children.length === 0 && /加载更多|查看更多/.test(e.innerText||''));
  if (!el) return 'none';
  const r = el.getBoundingClientRect();
  if (r.bottom < 0 || r.top > innerHeight) { el.scrollIntoView(); return 'hidden'; }
  return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2});
})()
"""


def read_toutiao_drafts(port=CDP_PORT, load_more=True, max_rounds=40):
    """返回头条草稿箱的标题列表（只读）。失败抛异常。"""
    import wechat_publish as wp
    c = wp.CDP(port)
    tid = c.new_target(TT_DRAFT)
    try:
        time.sleep(6)
        if load_more:
            for _ in range(max_rounds):
                pos = c.eval(TT_LOADMORE_JS)
                if pos in (None, "none", ""):
                    break
                if pos == "hidden":
                    time.sleep(1.5)
                    continue
                xy = json.loads(pos)
                c.click(xy["x"], xy["y"])
                time.sleep(1.8)
        out = c.eval(TT_DRAFT_JS) or []
        return [s for s in out if isinstance(s, str) and s.strip()]
    finally:
        try:
            c.close_target(tid)
        except Exception:                              # noqa: BLE001
            pass


# ---- B站 ------------------------------------------------------------------
BILI_DRAFT = "https://member.bilibili.com/read/draft"
BILI_DRAFT_JS = """
(() => {
  const f = document.querySelector('iframe');
  const d = f && f.contentDocument ? f.contentDocument : document;
  const items = d.querySelectorAll('.draft-card, .draft-item, [class*="draft"] li');
  return Array.from(items).map(el => {
    const t = el.querySelector('h3, .draft-title, [class*="title"]');
    return t ? (t.innerText||'').trim() : '';
  }).filter(Boolean).slice(0, 200);
})()
"""


def read_bilibili_drafts(port=CDP_PORT):
    """返回 B站草稿箱的标题列表（只读）。"""
    import wechat_publish as wp
    c = wp.CDP(port)
    tid = c.new_target(BILI_DRAFT)
    try:
        time.sleep(8)
        out = c.eval(BILI_DRAFT_JS) or []
        return [s for s in out if isinstance(s, str) and s.strip()]
    finally:
        try:
            c.close_target(tid)
        except Exception:                              # noqa: BLE001
            pass


READERS = {"toutiao": read_toutiao_drafts, "bilibili": read_bilibili_drafts}


# ---- 对账 -----------------------------------------------------------------
def reconcile(plat, remote_titles, m=None, verbose=True):
    """把远端标题与清单对账，返回差异字典。"""
    m = m or S.Manifest()
    cases = {c.get("id"): c.get("name", "") for c in A.load_cases()}

    # 候选键 = id / 变体 / 平台精修标题 / 案例名
    def keys_for(cid):
        up = cid.upper().replace("_", "-")
        ks = [cid, up, up.split("-")[0]]
        for plat2 in ("toutiao", "bili", "xhs", "wechat"):
            t = A.platform_title(cid, plat2)
            if t:
                ks.append(t)
        if cases.get(cid):
            ks.append(cases[cid])
        return ks

    matched, unmatched_titles = {}, []
    for t in remote_titles:
        hit = None
        for cid in m.cids() or cases:
            if hit:
                break
            if _match(t, keys_for(cid)):
                hit = cid
        if hit:
            matched[hit] = t
        else:
            unmatched_titles.append(t)

    claimed = [c for c in (m.cids() or []) if m.is_done(c, plat)]
    missing = [c for c in claimed if c not in matched]      # 说发了，远端没有
    extra = [c for c in matched if c not in claimed]      # 远端有，清单没记

    if verbose:
        print("\n%s 对账：远端 %d 条标题，清单声称已发 %d 条"
              % (plat, len(remote_titles), len(claimed)))
        print("  匹配上 %d 条" % len(matched))
        if missing:
            print("\n  ⚠ 清单说已发、远端草稿箱找不到（%d）：" % len(missing))
            for c in missing:
                print("      %-24s %s" % (c, m.title_of(c, plat) or
                                          m.error_of(c, plat)))
            print("     → 多半是「已发布后从草稿箱消失」或「被人删了」。")
            print("     → 人工去作品管理核对；确认已发布就把清单改成 published：")
            print("        python scripts/publish_multi.py mark %s --case <id> "
                  "--state published" % plat)
        if extra:
            print("\n  ⚠ 远端有、清单没记（%d）：%s"
                  % (len(extra), ", ".join(extra[:12])))
            print("     → 清单漏记（可能上次崩在写盘前）：")
            print("        python scripts/publish_multi.py sync --platforms %s" % plat)
        if not missing and not extra:
            print("  ✓ 完全一致")
    return {"matched": matched, "missing": missing, "extra": extra,
            "unmatched_titles": unmatched_titles}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="远端回读校验：清单 vs 各平台草稿箱（只读）")
    ap.add_argument("platform", choices=sorted(READERS))
    args = ap.parse_args(argv)
    try:
        titles = READERS[args.platform]()
    except Exception as e:                              # noqa: BLE001
        print("读取 %s 草稿箱失败：%s：%s"
              % (args.platform, type(e).__name__, e))
        print("（确认调试 Chrome 9222 起着、且已登录该平台）")
        return 2
    reconcile(args.platform, titles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
