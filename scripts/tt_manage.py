#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条号 · 作品管理：查「未通过」内容 / 删除它们。

为什么要单独一支：已发表列表接口（`toutiao_publish._article_api_url`）只回
`status=0` 的已发布内容，**未通过/审核中的压根不在里面**，`unpublish` 那条路
（按 gid 调删除 API）对它们无效 ⇒ 只能走作品管理 UI。

实测（2026-11-03 凌晨，CDP 9223，有头 Chrome）：

- 作品管理页 `https://mp.toutiao.com/profile_v4/manage/content/all`
  顶部状态筛选：`全部 / 已发布 / 审核中 / 未通过 / 仅我可见`，右侧有 `共 N 条内容`。
  ⚠ 这里的条数含音频/视频/微头条，不止文章（全部 ≈ 598 条）。
- 删除路径：卡片「更多」→ 菜单「删除作品」→ 弹窗「确定删除此内容？」
  （副文案「内容删除后将无法恢复，请慎重考虑」）→ `确定` / `取消`。
  不同体裁按钮不同：文章卡是 `修改 / 更多`，音频卡是 `修改 / 分享 / 删除作品`。
- 🚨 **坐标必须在视口内**：卡片 y 常 > innerHeight（907），此时
  `Input.dispatchMouseEvent` 打在视口外 = 空操作，症状是「点了没反应、计数不变」，
  很像风控其实是没点到。正确顺序：`scrollIntoView({block:'center'})` → 等 0.8s
  → **重新**取 `getBoundingClientRect` → 再按坐标点。
- 🚨 **菜单项不能 scrollIntoView**：一滚菜单就收起，接着点空。菜单本来就在视口里。
- 删除后 `共 N 条内容` 要重新导航才准（当场读到的常是旧值，别据此判失败）。

用法：
    python scripts/tt_manage.py rejected            # 只列「未通过」内容
    python scripts/tt_manage.py rejected --json
    python scripts/tt_manage.py delete-rejected     # 逐条删（每步打印）
    python scripts/tt_manage.py delete-rejected --dry
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as tp  # noqa: E402

URL = tp.TT_WORKS
FILTER = "未通过"

CARDS = r"""
JSON.stringify((function(){
  const out = [];
  for (const e of document.querySelectorAll('li,tr,div')) {
    if (e.offsetParent === null) continue;
    const t = (e.innerText || '');
    if (!/未通过/.test(t)) continue;
    if (t.length > 130) continue;
    if (/状态全部|共\s*\d+\s*条内容/.test(t)) continue;   // 列表容器，不是卡片
    if (e.querySelector('li,tr')) continue;
    const btns = [...e.querySelectorAll('a,button,span')]
      .filter(b => b.offsetParent !== null
        && /^(修改|删除作品|更多|删除|分享)$/.test((b.innerText||'').trim()))
      .map(b => { const r = b.getBoundingClientRect();
        return {txt: (b.innerText||'').trim(),
                x: Math.round(r.x + r.width/2),
                y: Math.round(r.y + r.height/2),
                w: Math.round(r.width)}; })
      .filter(b => b.w > 8);
    if (!btns.length) continue;
    out.push({txt: t.replace(/\s+/g, ' ').trim().slice(0, 90), btns: btns});
  }
  const seen = new Set(), uniq = [];
  for (const c of out.sort((a, b) => a.btns.length - b.btns.length)) {
    const k = c.txt + '|' + c.btns.map(b => b.txt).join(',');
    if (seen.has(k)) continue;
    seen.add(k); uniq.push(c);
  }
  return uniq;
})())
"""

COUNT = r"""
(function(){
  const m = (document.body.innerText||'').match(/共\s*(\d+)\s*条内容/);
  return m ? m[1] : '0';
})()
"""

MENU = r"""
JSON.stringify([...document.querySelectorAll('li,a,span,div')]
  .filter(e => e.offsetParent !== null
    && /^(删除作品|删除|撤回)$/.test((e.innerText||'').trim()))
  .map(e => { const r = e.getBoundingClientRect();
    return {txt: (e.innerText||'').trim(), x: Math.round(r.x+r.width/2),
            y: Math.round(r.y+r.height/2), w: Math.round(r.width)}; })
  .filter(e => e.w > 8))
"""

CONFIRM = r"""
JSON.stringify([...document.querySelectorAll('button,a,span')]
  .filter(e => e.offsetParent !== null
    && /^(确定|确认|确定删除|删除|取消)$/.test((e.innerText||'').trim()))
  .map(e => { const r = e.getBoundingClientRect();
    return {txt: (e.innerText||'').trim(), x: Math.round(r.x+r.width/2),
            y: Math.round(r.y+r.height/2), w: Math.round(r.width)}; })
  .filter(e => e.w > 8))
"""

SCROLL_EL = r"""
(function(txt){
  const el = [...document.querySelectorAll('a,li,span,div')]
    .find(e => e.offsetParent !== null && (e.textContent||'').trim() === txt);
  if (!el) return 'no';
  el.scrollIntoView({block: 'center'});
  return 'ok';
})
"""

RECT_EL = r"""
(function(txt){
  const el = [...document.querySelectorAll('a,li,span,div')]
    .find(e => e.offsetParent !== null && (e.textContent||'').trim() === txt);
  if (!el) return 'no';
  const r = el.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x + r.width/2),
                         y: Math.round(r.y + r.height/2),
                         inView: r.y > 0 && r.y < (window.innerHeight - 8)});
})
"""


def click(box, x, y):
    for t in ("mouseMoved", "mousePressed", "mouseReleased"):
        box.send("Input.dispatchMouseEvent",
                 {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})


def open_filter(box):
    """打开作品管理页并切到「未通过」。坐标同样要先滚进视口。"""
    box.send("Page.navigate", {"url": URL})
    time.sleep(9)
    box.eval(SCROLL_EL + "(%s)" % json.dumps(FILTER))
    time.sleep(0.8)
    raw = box.eval(RECT_EL + "(%s)" % json.dumps(FILTER))
    if str(raw).startswith("{"):
        pos = json.loads(raw)
        click(box, pos["x"], pos["y"])
    time.sleep(4)


def cmd_rejected(box, args):
    rows = json.loads(box.eval(CARDS) or "[]")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("「未通过」共 %s 条" % box.eval(COUNT))
    for r in rows:
        print("  %s" % r["txt"])
        print("     %s" % [(b["txt"], b["x"], b["y"]) for b in r["btns"]])
    return 0


def cmd_delete(box, args):
    cards = json.loads(box.eval(CARDS) or "[]")
    print("「未通过」共 %s 条，待删 %d 张卡" % (box.eval(COUNT), len(cards)))
    for c in cards:
        print("  %s" % c["txt"])
    if args.dry or not cards:
        return 0

    keys = [c["txt"][:14] for c in cards]
    for key in keys:
        # 每轮重新取卡片：删掉一条后 DOM 重排，旧坐标全失效
        cur = json.loads(box.eval(CARDS) or "[]")
        hit = [c for c in cur if c["txt"].startswith(key)]
        if not hit:
            print("\n== %s ==\n  已不在列表" % key)
            continue
        c = hit[0]
        tgt = [b for b in c["btns"] if b["txt"] in ("删除作品", "更多")]
        if not tgt:
            print("\n== %s ==\n  ✗ 没有删除入口" % key)
            continue
        b = tgt[0]
        print("\n== 删除：%s ==" % key)

        if b["txt"] == "更多":
            box.eval(SCROLL_EL + "(%s)" % json.dumps("更多"))
            time.sleep(0.8)
            pos = json.loads(box.eval(RECT_EL + "(%s)" % json.dumps("更多")))
            click(box, pos["x"], pos["y"])
            time.sleep(1.5)
            menu = json.loads(box.eval(MENU) or "[]")
            if not menu:
                print("  ✗ 菜单没出来")
                continue
            click(box, menu[0]["x"], menu[0]["y"])       # 🚨 别 scroll，菜单会收
        else:
            box.eval(SCROLL_EL + "(%s)" % json.dumps("删除作品"))
            time.sleep(0.8)
            pos = json.loads(box.eval(RECT_EL + "(%s)" % json.dumps("删除作品")))
            click(box, pos["x"], pos["y"])

        time.sleep(2.5)
        btns = json.loads(box.eval(CONFIRM) or "[]")
        ok = [x for x in btns if x["txt"] in ("确定", "确认", "确定删除")]
        if not ok:
            print("  ✗ 没弹确认框（按钮：%s）"
                  % [(x["txt"], x["y"]) for x in btns])
            continue
        click(box, ok[0]["x"], ok[0]["y"])
        time.sleep(3.0)
        print("  已点确定")

    # 🚨 删后计数必须重新导航才准；就地读常是旧值
    open_filter(box)
    left = box.eval(COUNT)
    print("\n删除结束：剩余「未通过」%s 条" % left)
    return 0 if str(left) in ("0", "") else 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=tp.CDP_PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("rejected", help="列「未通过」内容")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_rejected)

    d = sub.add_parser("delete-rejected", help="删除「未通过」内容（不可恢复）")
    d.add_argument("--dry", action="store_true")
    d.set_defaults(fn=cmd_delete)

    args = ap.parse_args()
    tp.CDP_PORT = args.port
    box = tp.wp.CDP(args.port)
    tid = box.new_target(URL)["id"]
    box.connect_target(tid)
    try:
        open_filter(box)
        return args.fn(box, args)
    finally:
        try:
            box.send("Target.closeTarget", {"targetId": tid})
        except Exception:                              # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
