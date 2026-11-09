# -*- coding: utf-8 -*-
"""腾讯云：按标题关键词删除**草稿箱**里的草稿（缺省只预演，`--yes` 才真删）。

为什么不能走接口（2026-11-09 实测）：
  草稿箱条目（`POST /developer/api/article/getUserArticleDrafts`）的 `articleId`
  **恒为 0** —— 草稿未提交，平台还没给它分配文章号。而删除接口
  `POST /developer/api/common/delete-content {objId, module:1, forever}`
  只认文章号：传 `draftId` 返回 `{"msg":"内容不存在","code":11002}`。
  ⇒ 草稿只能在「文章管理 → 草稿箱」tab 里用 UI 删。

UI 的三个坑（都实测过）：
  1. `el.click()` 触发不了框架事件 ⇒ 必须 `Input.dispatchMouseEvent` 点坐标；
  2. 卡片与分页器在 y>900 的位置，默认 1440x900 视口外点不到 ⇒ 先用
     `Emulation.setDeviceMetricsOverride` 把视口拉到 1440x1600;
  3. 确认弹窗文案是「**温馨提示 草稿删除后无法找回，请确认**」，按钮「取消 / 确定」；
     ⚠ 草稿**没有回收站**，删了就是真没了（与文章不同，文章 `forever:0` 可进回收站）。

与 `tc_recycle.py`（清空回收站）、`tc_manage.py`（删文章=进回收站）职责分开。

用法：
    python -X utf8 scripts/tc_draft_purge.py                       # 只列草稿箱
    python -X utf8 scripts/tc_draft_purge.py "bootstrapped 六年"    # 预演：定位命中项
    python -X utf8 scripts/tc_draft_purge.py "bootstrapped 六年" --yes
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

URL = "https://cloud.tencent.com/developer/creator/article"

LIST_JS = """
fetch("https://cloud.tencent.com/developer/api/article/getUserArticleDrafts",
      {method:"POST", credentials:"include",
       headers:{"Content-Type":"application/json"},
       body: JSON.stringify({page:1, pageSize:50, contentType:"rich"})})
  .then(function(x){return x.text();}).catch(function(e){return "ERR|"+e;})"""

TAB_JS = """
(function(){
  var as = document.querySelectorAll('a.com-card-hd-link');
  for (var i=0;i<as.length;i++){
    if (/草稿箱/.test(as[i].innerText||'')) {
      var r = as[i].getBoundingClientRect();
      return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
                             text:as[i].innerText.trim()});
    }
  }
  return 'null';
})()"""

# 每个「删除」控件 → 向上找第一层 innerText 够长的祖先当作卡片文本（标题在首段）
PICK_JS = """
(function(){
  var out = [];
  document.querySelectorAll('span.cdc-svg-icon-text').forEach(function(e){
    if ((e.innerText||'').trim() !== '删除') return;
    var r = e.getBoundingClientRect();
    if (!r.width) return;
    var p = e, txt = '';
    for (var i=0;i<6 && p.parentElement;i++){
      p = p.parentElement;
      var s = (p.innerText||'').replace(/\\s+/g,' ').trim();
      if (s.length > 24) { txt = s; break; }
    }
    out.push({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2), text:txt.slice(0,110)});
  });
  return JSON.stringify(out);
})()"""

PAGE2_JS = """
(function(){
  var items = document.querySelectorAll('.tp1-pagination__item');
  for (var i=0;i<items.length;i++){
    if ((items[i].innerText||'').trim() === '2') {
      var r=items[i].getBoundingClientRect();
      return JSON.stringify({x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});
    }
  }
  return 'null';
})()"""

CONFIRM_JS = """
(function(){
  var out=[];
  document.querySelectorAll('.cdc-modal button, .cdc-modal [class*="btn"]').forEach(function(e){
    if (e.children.length > 1) return;
    var t=(e.innerText||'').trim();
    if (t !== '确定' && t !== '取消' && t !== '确定删除') return;
    var r=e.getBoundingClientRect();
    if (!r.width || !r.height) return;
    out.push({t:t, x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)});
  });
  return JSON.stringify(out);
})()"""

DIALOG_JS = """
(function(){
  var m = document.querySelector('.cdc-modal__wrap, .cdc-modal');
  return m ? (m.innerText||'').replace(/\\s+/g,' ').trim().slice(0,120) : '';
})()"""


def click(cdp, x, y):
    for ev in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent", {"type": ev, "x": x, "y": y,
                                             "button": "left", "clickCount": 1})
        time.sleep(0.15)


def list_drafts(cdp):
    for _ in range(3):
        raw = cdp.eval(LIST_JS)
        if isinstance(raw, str) and not raw.startswith("ERR|"):
            break
        time.sleep(4)
    if not isinstance(raw, str) or raw.startswith("ERR|"):
        raise SystemExit("读草稿箱失败: %s" % str(raw)[:200])
    d = json.loads(raw)
    items = d.get("list") or d.get("data") or []
    if isinstance(items, dict):
        items = items.get("list") or []
    return [x for x in items if isinstance(x, dict)]


def open_draftbox(cdp):
    """重新加载文章管理页 → 放大视口 → 点草稿箱 tab。返回 tab 文案。"""
    cdp.open(URL)
    time.sleep(3)
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1600, "deviceScaleFactor": 1, "mobile": False})
    tab = None
    for _ in range(10):
        raw = cdp.eval(TAB_JS)
        if isinstance(raw, str) and raw.startswith("{"):
            tab = json.loads(raw)
            break
        time.sleep(2)
    if not tab:
        raise SystemExit("没找到草稿箱 tab（页面没加载完）")
    click(cdp, tab["x"], tab["y"])
    time.sleep(5)
    return tab["text"]


def find_hits(cdp, keys):
    """在当前状态下扫第 1/2 页，返回 [(keyword, x, y, card_text)]。"""
    hits = []
    for page in (1, 2):
        if page == 2:
            p2 = json.loads(cdp.eval(PAGE2_JS) or "null")
            if not p2:
                break
            click(cdp, p2["x"], p2["y"])
            time.sleep(4)
        for c in json.loads(cdp.eval(PICK_JS)):
            for k in keys:
                if k in c["text"]:
                    hits.append((k, c["x"], c["y"], c["text"]))
    return hits


def purge_one(cdp, key):
    """删一条；成功返回 True。每条都重新加载页面，避免列表重排导致坐标失效。"""
    label = open_draftbox(cdp)
    print("  进草稿箱:", label)
    hits = [h for h in find_hits(cdp, [key])]
    if not hits:
        print("   ✗ 没找到含「%s」的草稿" % key)
        return False
    if len(hits) > 1:
        print("   ⚠ 命中 %d 条，只处理第一条（防误删）" % len(hits))
    _, x, y, txt = hits[0]
    print("   命中:", txt[:60])
    before = len(list_drafts(cdp))
    click(cdp, x, y)
    time.sleep(2)
    dlg = cdp.eval(DIALOG_JS)
    print("   弹窗:", dlg[:80])
    btn = None
    for b in json.loads(cdp.eval(CONFIRM_JS)):
        if b["t"] in ("确定删除", "确定"):
            btn = b
            break
    if not btn:
        print("   ✗ 没找到确认按钮，跳过（不盲点）")
        return False
    click(cdp, btn["x"], btn["y"])
    time.sleep(4)
    after = list_drafts(cdp)
    gone = not [d for d in after if key in (d.get("title") or "")]
    print("   草稿箱 %d -> %d ｜该条已消失: %s" % (before, len(after), gone))
    return gone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="标题关键词（可多个）")
    ap.add_argument("--yes", action="store_true", help="真删；不给只预演")
    a = ap.parse_args()

    proc, cdp = Y._connect()
    try:
        if not a.keys:
            label = open_draftbox(cdp)
            rows = list_drafts(cdp)
            print("草稿箱 %s，共 %d 条：" % (label, len(rows)))
            for r in rows:
                print("  %-8s %s" % (r.get("draftId"), (r.get("title") or "")[:56]))
            return 0

        print("模式:", "真删" if a.yes else "预演（加 --yes 才删）")
        print("⚠ 草稿删除不可恢复（平台无草稿回收站），删前请自行备份正文")
        if not a.yes:
            label = open_draftbox(cdp)
            print("  进草稿箱:", label)
            hits = find_hits(cdp, a.keys)
            if not hits:
                print("  没有命中项")
            for k, _, _, txt in hits:
                print("  [命中 %s] %s" % (k, txt[:70]))
            print("\n草稿箱总数:", len(list_drafts(cdp)))
            return 0

        ok, bad = 0, []
        for k in a.keys:
            print("\n== 删「%s」" % k)
            if purge_one(cdp, k):
                ok += 1
            else:
                bad.append(k)
        print("\n结果：成功 %d / 失败 %d" % (ok, len(bad)))
        if bad:
            print("失败项:", "；".join(bad))
        print("草稿箱剩余:", len(list_drafts(cdp)))
        return 0 if not bad else 1
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:                            # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
