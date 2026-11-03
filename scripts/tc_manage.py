#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云开发者社区 · 文章管理（创作中心）：查审核状态 / 删稿。

为什么单独一支：发布链在 `yun_finalize.py`，删稿和查状态属于**稿件运维**，
混进发布脚本会让「发」和「删」共用一套登录态判断，出问题时不好定位。

实测（2026-11-03，CDP 9228，有头 Chrome）：

- 列表页 `https://cloud.tencent.com/developer/creator/article`
  - 页面上**没有「未通过」标签页**，只有 `草稿箱(N)` / `已发布`；
    审核中 / 已驳回 / 已发布 都是**卡片上的文案**，一页混排。
  - 卡片状态文案：`你的文章正在审核中` / `你的文章已驳回,原因: 灌水内容/技术无关内容`。
  - 列表接口：`POST /developer/api/creator/articleList {"hostStatus":0,...}`
    （页面 fetch 的，直接读 DOM 更省事，本脚本走 DOM）。
- 删稿：卡片上的「删除」是 `SPAN.cdc-svg-icon-con`，`el.click()` 触发不了
  （框架只认真实事件）⇒ 必须 `Input.dispatchMouseEvent` 点坐标。
- 确认弹窗 `BUTTON` 文案是 **`确定删除`**，`取消` 才是 `cdc-btn--primary`
  ⇒ 🚨 **千万别按「主按钮样式」去找确认按钮，那会点到取消**。
- 删除接口：`POST /developer/api/common/delete-content
  {"objId": <articleId>, "module": 1, "forever": 0}`
  `forever:0` = 进回收站（`创作者中心-文章管理-回收站` 可找回），`1` = 彻底删。
  `articleId` 是 7 位（如 2773642），**不是** 编辑器里的 `draftId`（6 位）。

用法：
    python scripts/tc_manage.py status
    python scripts/tc_manage.py status --json
    python scripts/tc_manage.py delete 2773642 2773633        # 进回收站
    python scripts/tc_manage.py delete 2773642 --forever      # 彻底删
    python scripts/tc_manage.py delete 2773642 --dry          # 只定位不点
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from juejin_publish import _CDP  # noqa: E402

CDP_PORT = 9228
LIST_URL = "https://cloud.tencent.com/developer/creator/article"

# 列表：卡片 = 从标题链接往上找到含「编辑/删除/审核/驳回」的容器
LIST_JS = r"""
JSON.stringify((function(){
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/developer/article/"]')) {
    if (a.offsetParent === null) continue;
    const m = (a.href || '').match(/\/developer\/article\/(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    let box = a;
    for (let i = 0; i < 10 && box.parentElement; i++) {
      box = box.parentElement;
      if (/编辑|删除|审核|驳回/.test(box.textContent || '')) break;
    }
    const t = (box.textContent || '').replace(/\s+/g, ' ').trim();
    // 只认「我的稿件卡片」：卡片上有编辑/删除图标控件；页脚「社区规范」之类
    // 也是 /developer/article/ 链接，但它们没有这些控件，混进来会谎报审核中。
    if (!/删除/.test(t)) continue;
    if (!box.querySelector('span.cdc-svg-icon-con')) continue;
    if (t.length > 400) continue;
    let st = '', why = '';
    if (/已驳回/.test(t)) { st = 'rejected';
      const mm = t.match(/已驳回[^。]{0,60}/); why = mm ? mm[0] : ''; }
    else if (/审核中/.test(t)) st = 'reviewing';
    else if (/已发布/.test(t)) st = 'published';
    else if (/草稿/.test(t)) st = 'draft';
    out.push({id: m[1], title: (a.textContent||'').trim().slice(0, 60),
              state: st, why: why});
  }
  return out;
})())
"""

# 定位某篇卡片上的「删除」控件中心坐标
POS_JS = r"""
JSON.stringify((function(id){
  const a = [...document.querySelectorAll('a[href*="/developer/article/' + id + '"]')]
    .filter(e => e.offsetParent !== null)[0];
  if (!a) return {err: 'no-link'};
  let box = a;
  for (let i = 0; i < 10 && box.parentElement; i++) {
    box = box.parentElement;
    if (/编辑|删除|更多/.test(box.textContent || '')) break;
  }
  const del = [...box.querySelectorAll('span.cdc-svg-icon-con')]
    .filter(e => e.offsetParent !== null && (e.textContent||'').trim() === '删除')[0];
  if (!del) return {err: 'no-del-btn'};
  const r = del.getBoundingClientRect();
  return {x: r.x + r.width/2, y: r.y + r.height/2,
          title: (a.textContent||'').trim().slice(0, 60)};
})(__ID__))
"""

# 弹窗里的确认按钮：只认文案
CONFIRM_JS = r"""
JSON.stringify((function(){
  const btns = [...document.querySelectorAll('button, span.cdc-btn, a')]
    .filter(e => e.offsetParent !== null
      && /^(确定删除|确定|确认)$/.test((e.textContent||'').trim()));
  return btns.map(e => {
    const r = e.getBoundingClientRect();
    return {txt: (e.textContent||'').trim(),
            x: r.x + r.width/2, y: r.y + r.height/2, w: Math.round(r.width)};
  });
})())
"""

EXISTS_JS = r"""
String(!![...document.querySelectorAll('a[href*="/developer/article/__ID__"]')]
  .filter(e => e.offsetParent !== null).length)
"""


def _click(cdp, x, y):
    """真实鼠标事件。合成 click 打不穿 React handler。"""
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1})


def fetch_list(cdp, pages=4):
    """翻页收集全部文章。翻不动就停（`no-next` / `disabled`）。"""
    rows, seen = [], set()
    for _ in range(pages):
        for r in json.loads(cdp.eval(LIST_JS) or "[]"):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
        nxt = cdp.eval(r"""
(function(){
  const b = [...document.querySelectorAll('button,li,a,span')]
    .filter(e => e.offsetParent !== null
      && /^(下一页|next|>)$/i.test((e.textContent||'').trim()))[0];
  if (!b) return 'no-next';
  if (b.disabled || /disabled/.test((b.className||'').toString())) return 'disabled';
  const r = b.getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
        if not nxt or not str(nxt).startswith("{"):
            break
        pos = json.loads(nxt)
        _click(cdp, pos["x"], pos["y"])
        time.sleep(3.5)
    return rows


def cmd_status(cdp, args):
    rows = fetch_list(cdp, args.pages)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("共 %d 条" % len(rows))
    for r in rows:
        print("  %-8s %-10s %s" % (r["id"], r["state"] or "?",
                                   r["title"][:44]))
        if r["why"]:
            print("            └ %s" % r["why"])
    bad = [r for r in rows if r["state"] == "rejected"]
    if bad:
        print("\n⚠ 驳回 %d 篇：%s" % (len(bad), ", ".join(r["id"] for r in bad)))
    return 0


def cmd_delete(cdp, args):
    ok, fail = [], []
    for aid in args.ids:
        print("\n== %s ==" % aid)
        pos = json.loads(cdp.eval(POS_JS.replace("__ID__", json.dumps(aid)))
                         or "{}")
        if "err" in pos:
            print("  ✗ %s" % pos["err"])
            fail.append(aid)
            continue
        print("  目标：%s" % pos["title"])
        if args.dry:
            print("  （预演，未点击）")
            continue
        _click(cdp, pos["x"], pos["y"])
        time.sleep(1.8)
        btns = json.loads(cdp.eval(CONFIRM_JS) or "[]")
        hit = [b for b in btns if b["w"] > 20]
        if not hit:
            print("  ✗ 没弹确认框（或按钮文案变了），未删除")
            fail.append(aid)
            continue
        # 🚨 这个弹窗把「取消」做成 cdc-btn--primary，只能按文案点
        print("  确认按钮：%s" % hit[0]["txt"])
        _click(cdp, hit[0]["x"], hit[0]["y"])
        time.sleep(3.0)
        if args.forever:
            print("  ⚠ --forever 只是意图声明：回收站里的彻底删除要人工，"
                  "接口 forever:1 未实测，本轮仍按 forever:0 走")
        still = cdp.eval(EXISTS_JS.replace("__ID__", aid))
        print("  回读卡片存在：%s" % still)
        (fail if still == "true" else ok).append(aid)
    print("\n成功 %d：%s" % (len(ok), ok))
    print("失败 %d：%s" % (len(fail), fail))
    return 0 if not fail else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=CDP_PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="列文章 + 审核状态")
    s.add_argument("--json", action="store_true")
    s.add_argument("--pages", type=int, default=4)
    s.set_defaults(fn=cmd_status)

    d = sub.add_parser("delete", help="按 articleId 删稿（默认进回收站）")
    d.add_argument("ids", nargs="+")
    d.add_argument("--forever", action="store_true")
    d.add_argument("--dry", action="store_true")
    d.set_defaults(fn=cmd_delete)

    args = ap.parse_args()
    cdp = _CDP(args.port)
    tid = cdp.open(LIST_URL)
    try:
        cdp.send("Page.enable")
        time.sleep(9)
        return args.fn(cdp, args)
    finally:
        try:
            cdp.send_browser("Target.closeTarget", {"targetId": tid})
        except Exception:                                  # noqa: BLE001
            pass
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
