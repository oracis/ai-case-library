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


def _open_and_eval(c, url, wait, js):
    """开 tab → 连上 → 等页面渲染 → 求值。返回 JS 结果。

    ⚠️ `CDP.new_target()` 只返回 targetId，**不连 WebSocket**；
    漏掉 `connect_target` 会报「未连接页面 target」（2026-09-29 实测）。
    """
    tid = c.new_target(url)["id"]
    try:
        c.connect_target(tid)
        time.sleep(wait)
        return c.eval(js)
    finally:
        try:
            c.close_target(tid)
        except Exception:                              # noqa: BLE001
            pass


def read_toutiao_drafts(port=CDP_PORT, load_more=True, max_rounds=40):
    """返回头条草稿箱的标题列表（只读）。失败抛异常。"""
    import wechat_publish as wp
    c = wp.CDP(port)
    tid = c.new_target(TT_DRAFT)["id"]
    try:
        c.connect_target(tid)
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
# ⚠️ 三个坑（2026-09-29 踩完才对，别改回去）：
# 1. `/read/draft` 是**错误页**（document.title = "出错啦!"），读出 0 条会被
#    误判成「37 条草稿全丢了」。
# 2. `/platform/upload-manager/opus` 默认停在**「图文」tab**（已发布图文，
#    显示「全部 0」），草稿是同级另一个 tab，路由 `/opus/management/drafts`。
# 3. 那个页面是 iframe 套 iframe（列表在 `member.bilibili.com/opus/
#    management` 内），而且**只渲染首屏 10 条** —— 滚动无效、无分页控件。
#    所以最终走**官方列表接口**（从页面 resource 里抓到的），一次拿全：
#      /x/dynamic/feed/article/draft/list?pn=1&ps=<N>&keyword=
#    ps 就是每页条数，传 200 一次到位。
BILI_DRAFT = "https://member.bilibili.com/opus/management/drafts"
BILI_API = ("https://api.bilibili.com/x/dynamic/feed/article/draft/list"
            "?pn=1&ps=200&keyword=")
BILI_API_JS = r"""
(async () => {
  try {
    const r = await fetch(%(api)s, {credentials: 'include'});
    const j = await r.json();
    if (j.code !== 0) return {err: j.code + ' ' + (j.message || '')};
    // ⚠️ 列表字段是 `drafts`（不是 items/list）—— 2026-09-29 实测：
    // 写 items 会静默返回空数组，看起来像「草稿全没了」。
    const drafts = (j.data && j.data.drafts) || [];
    const titles = drafts.map(x => (x.title || '').trim()).filter(Boolean);
    return {n: titles.length, titles: titles};
  } catch (e) { return {err: String(e)}; }
})()
"""


def read_bilibili_drafts(port=CDP_PORT, ps=200):
    """返回 B站草稿箱的标题列表（只读，走官方接口所以能拿全）。

    在**已登录的页面上下文**里发 fetch —— 直接从 Python 发会缺 CSRF/cookie。
    """
    import wechat_publish as wp
    c = wp.CDP(port)
    url = BILI_API if ps == 200 else BILI_API.replace("ps=200", "ps=%d" % ps)
    js = BILI_API_JS % {"api": json.dumps(url)}
    r = _open_and_eval(c, BILI_DRAFT, 10, js)
    # JS 正常时返回 {n, titles}，异常时返回 {err} —— 靠 err 字段区分，
    # 不能靠 isinstance(dict)：正常结果**也是** dict（会误报成失败）。
    if not isinstance(r, dict) or r.get("err"):
        raise RuntimeError("B站草稿接口返回异常：%s"
                           % ((r or {}).get("err") if isinstance(r, dict) else r))
    return [s for s in (r.get("titles") or [])
            if isinstance(s, str) and s.strip()]


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
        # 「只读到首屏」和「草稿真的丢了」必须区分开，否则会误报。
        if claimed and len(remote_titles) < len(claimed) * 0.6:
            print("  ⚠ 远端读到的条数（%d）明显少于清单声称（%d）："
                  % (len(remote_titles), len(claimed)))
            print("     多半是**分页/懒加载没翻完**，不是草稿丢了。")
            print("     → 下面的 missing 先别当真，人工去草稿箱翻页确认。")
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
