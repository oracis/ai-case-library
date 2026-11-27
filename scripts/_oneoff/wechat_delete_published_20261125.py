"""删除公众号「已发表」文章（按标题命中，只删一条）。

2026-11-25 14:2x 用户确认：撤下 `betting-tips`（博彩投注题材，标题含
「每日专家投注建议」）。账号本轮被安全风控限制过一次（需人脸验证），
这篇是公众号里唯一命中「赌博/投注」的已发稿 ⇒ 按平台政策撤干净。

⚠ 实测出来的四个坑（都别再踩）
1. 发表记录列表**不是草稿箱**：URL 是 `appmsgpublish?sub=list`，行容器
   `.weui-desktop-block`。删除入口**不在行内直接可见**，是行右上的
   `.more_icon button`（36×36）点开的 popover 里的 `<li>删除</li>`；
   草稿箱那套 `[data-appid]` + tooltip 在这里完全不适用。
2. 标题**不能**用 `querySelectorAll('*').textContent` 匹配：整页 HTML/BODY 的
   textContent 也含这些字（内联 JSON），会先在 SCRIPT 上命中。判据 =
   先在 `.weui-desktop-block` 上筛 innerText。
3. **必须一次跑完**：点 `more` → 重量 `删除` 坐标 → 点它 → 读弹窗。分两次
   进程跑会失败 —— popover 在失焦/重排后自动关闭，上一轮算的坐标点了个空
   （2026-11-25 实测：单独一次点 (1143,394) 无任何反应，`共发表了` 仍是 65）。
4. 点前 `scrollIntoView({block:'center'})` 再重算 rect（rect 是相对视口的）。

用法：
    python scripts/_oneoff/wechat_delete_published_20261125.py                 # 干跑
    python scripts/_oneoff/wechat_delete_published_20261125.py --yes           # 打开菜单+点删除，停在确认框
    python scripts/_oneoff/wechat_delete_published_20261125.py --yes --confirm # 连确认一起点
"""
import argparse
import base64
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cdp_core  # noqa: E402

PORT = 9222
SHOT_DIR = os.path.join(ROOT, "tmp")
LIST_URL = ("https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
            "?sub=list&begin=0&count=10&token=%s&lang=zh_CN")

JS_FIND = r"""
(function (kw) {
  var area = document.querySelector('.weui-desktop-layout__main__area');
  if (!area) return JSON.stringify({err: 'no-area'});
  var blocks = [].slice.call(area.querySelectorAll('.weui-desktop-block'));
  var out = [], pos = null;
  blocks.forEach(function (b, i) {
    var t = (b.innerText || '').replace(/\s+/g, ' ');
    if (t.indexOf(kw) < 0) return;
    var more = b.querySelector('.more_icon button') || b.querySelector('.more_icon');
    out.push({idx: i, text: t.slice(0, 200), hasMore: !!more});
    if (more && !pos) {
      more.scrollIntoView({block: 'center'});
      var r = more.getBoundingClientRect();
      pos = {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
    }
  });
  return JSON.stringify({n: blocks.length, hits: out, pos: pos});
})(%s)
"""

# 菜单里「删除」的位置（每次现算，别复用上一轮坐标）
# ⚠ 可见性只能用**尺寸**判断：`offsetParent` 对 `position:fixed` 的 popover 恒为
#   null，拿它当可见性判据会把真菜单当隐藏（页面里另有 10 个 0×0 的模板 li）。
JS_MENU_DEL = r"""
(function () {
  var best = null;
  document.querySelectorAll('li,span,a').forEach(function (x) {
    if ((x.innerText || '').trim() !== '删除') return;
    var r = x.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    if (!best) best = {x: Math.round(r.left + r.width / 2),
                       y: Math.round(r.top + r.height / 2)};
  });
  return JSON.stringify(best ? best : {err: 'no-del-item'});
})()
"""

JS_DIALOG = r"""
(function () {
  var out = [];
  document.querySelectorAll('div').forEach(function (d) {
    var r = d.getBoundingClientRect();
    if (r.width < 250 || r.height < 70) return;
    var cls = (d.className || '').toString();
    if (!/modal|dialog|mask|confirm/i.test(cls)) return;
    var t = (d.innerText || '').replace(/\s+/g, ' ').trim();
    if (!t) return;
    var btns = [];
    d.querySelectorAll('button,a').forEach(function (b) {
      var s = (b.innerText || '').trim();
      if (s) btns.push(s);
    });
    out.push({cls: cls.slice(0, 40), text: t.slice(0, 220), btns: btns.slice(0, 8)});
  });
  return JSON.stringify(out)
})()
"""

JS_CONFIRM_POS = r"""
(function () {
  var want = ['删除', '确定', '确认删除'];
  var best = null;
  document.querySelectorAll('button,a').forEach(function (b) {
    var s = (b.innerText || '').trim();
    if (want.indexOf(s) < 0) return;
    var r = b.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    if (!best) best = {x: Math.round(r.left + r.width / 2),
                       y: Math.round(r.top + r.height / 2), txt: s};
  });
  return JSON.stringify(best ? best : {err: 'no-confirm-btn'});
})()
"""

JS_COUNT = r"""
(function () {
  var body = document.body.innerText || '';
  var m = body.match(/共发表了\s*(\d+)\s*次/);
  return JSON.stringify({count: m ? m[1] : null,
                         hasTitle: body.indexOf(%s) >= 0});
})()
"""


def shot(cdp, name):
    r = cdp.send("Page.captureScreenshot", {"format": "png"})
    p = os.path.join(SHOT_DIR, name)
    with open(p, "wb") as f:
        f.write(base64.b64decode(r["result"]["data"]))
    print("  📷 %s" % os.path.relpath(p, ROOT))


def click_at(cdp, x, y):
    """⚠ 必须先 `mouseMoved` 再按 —— `.more_icon` 是 hover 才启用的触发器，
    只发 press/release 时框架收不到 hover 态，菜单不弹（2026-11-25 实测：
    单纯点 (1155,262) 无反应，补一个 mouseMoved 立刻出现菜单）。"""
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(0.2)
    for t in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": t, "x": x, "y": y, "button": "left",
                  "clickCount": 1, "modifiers": 0})
        time.sleep(0.15)


def main():
    ap = argparse.ArgumentParser(description="删除公众号已发表文章（按标题命中）")
    ap.add_argument("--title", default="Betting Tips")
    ap.add_argument("--token", default="399814951")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()

    cdp = cdp_core.CDP(a.port)
    tg = [x for x in cdp.list_targets()
          if x.get("type") == "page" and "mp.weixin" in (x.get("url") or "")]
    if not tg:
        raise SystemExit("✗ %d 上没有 mp 标签页" % a.port)
    cdp.connect_target(tg[0]["id"])
    cdp.send("Page.navigate", {"url": LIST_URL % a.token})
    time.sleep(8)

    found = json.loads(cdp.eval(JS_FIND % json.dumps(a.title), refresh_context=True) or "{}")
    if found.get("err"):
        raise SystemExit("✗ %s" % found["err"])
    print("列表块=%s，命中 %d 条" % (found.get("n"), len(found.get("hits") or [])))
    for h in found.get("hits") or []:
        print("  · idx=%s hasMore=%s | %s" % (h["idx"], h["hasMore"], h["text"][:90]))
    if not found.get("hits"):
        raise SystemExit("✗ 没命中「%s」" % a.title)
    if len(found["hits"]) > 1:
        raise SystemExit("✗ 命中多条，不自动挑")
    if not a.yes:
        print("\n（干跑）加 --yes 才真点")
        return 0

    pos = found.get("pos")
    if not pos:
        raise SystemExit("✗ 该行没有 more 入口")

    # 菜单可能要点两次才出（首次点击落在字体/布局刚生效、handler 还没绑定的时候，
    # 实测「导航后固定 sleep 8s 再点」有概率点了没反应 ⇒ 改成点完轮询，最多补点 2 次）
    mp = {}
    for attempt in range(3):
        print("① 点 more (%s, %s) 第 %d 次" % (pos["x"], pos["y"], attempt + 1))
        click_at(cdp, pos["x"], pos["y"])
        for _ in range(6):
            time.sleep(1.0)
            # ⚠ 轮询里**不要** refresh_context：每次重整上下文要几秒，
            #   10×3 轮直接把一条命令拖过 2 分钟超时（2026-11-25 实测被 SIGTERM）。
            mp = json.loads(cdp.eval(JS_MENU_DEL) or "{}")
            if not mp.get("err"):
                break
        if not mp.get("err"):
            break
    if mp.get("err"):
        shot(cdp, "wechat_del_nomenu_%s.png" % time.strftime("%H%M%S"))
        raise SystemExit("✗ 菜单里没找到「删除」：%s" % mp["err"])
    print("② 点菜单「删除」 (%s, %s)" % (mp["x"], mp["y"]))
    click_at(cdp, mp["x"], mp["y"])
    time.sleep(3)

    dlg = json.loads(cdp.eval(JS_DIALOG, refresh_context=True) or "[]")
    print("③ 弹层 %d 个" % len(dlg))
    for d in dlg:
        print("  · %s | 按钮 %s" % (d["text"][:170], d["btns"]))
    shot(cdp, "wechat_del_dialog_%s.png" % time.strftime("%H%M%S"))

    if not a.confirm:
        print("\n已停在确认框（没确认）。看截图后加 --confirm。")
        return 0

    cp = json.loads(cdp.eval(JS_CONFIRM_POS, refresh_context=True) or "{}")
    if cp.get("err"):
        print("✗ 确认按钮没找到：%s（可能弹出扫码验证，看截图）" % cp["err"])
        shot(cdp, "wechat_del_needscan_%s.png" % time.strftime("%H%M%S"))
        return 2
    print("④ 点确认「%s」(%s, %s)" % (cp["txt"], cp["x"], cp["y"]))
    click_at(cdp, cp["x"], cp["y"])
    time.sleep(4)

    after = json.loads(cdp.eval(JS_COUNT % json.dumps(a.title), refresh_context=True) or "{}")
    print("⑤ 回读：共发表了 %s 次（删前 65），标题仍在页面=%s"
          % (after.get("count"), after.get("hasTitle")))
    left = json.loads(cdp.eval(JS_DIALOG, refresh_context=True) or "[]")
    for d in left:
        print("  · 残留弹层：%s | %s" % (d["text"][:140], d["btns"]))
    shot(cdp, "wechat_del_after_%s.png" % time.strftime("%H%M%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
