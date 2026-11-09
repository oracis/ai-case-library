# -*- coding: utf-8 -*-
"""腾讯云：按 draftId / 标题关键词删除**草稿箱**里的草稿（缺省只预演，`--yes` 才真删）。

为什么不能走接口（2026-11-09 实测）：
  草稿箱条目（`POST /developer/api/article/getUserArticleDrafts`）的 `articleId`
  **恒为 0** —— 草稿未提交，平台还没给它分配文章号。而删除接口
  `POST /developer/api/common/delete-content {objId, module:1, forever}`
  只认文章号：传 `draftId` 返回 `{"msg":"内容不存在","code":11002}`。
  ⇒ 草稿只能在「文章管理 → 草稿箱」tab 里用 UI 删。

UI 的坑（都实测过）：
  1. `el.click()` 触发不了框架事件 ⇒ 必须 `Input.dispatchMouseEvent` 点坐标；
  2. 卡片与分页器在 y>900 的位置，默认 1440x900 视口外点不到 ⇒ 先用
     `Emulation.setDeviceMetricsOverride` 把视口拉到 1440x1600;
  3. 确认弹窗文案是「**温馨提示 草稿删除后无法找回，请确认**」，按钮「取消 / 确定」；
     ⚠ 草稿**没有回收站**，删了就是真没了（与文章不同，文章 `forever:0` 可进回收站）；
  4. 🚨 **每页只渲染 5 张卡片**（7 条草稿也只会出 5 个 DOM 节点），
     第 6 条起必须翻到第 2 页才存在 ⇒ **命中项在第 1 页就一定要 break**。
     2026-11-09 事故原版就是没 break：扫完 page1 又翻到 page2，却仍拿 page1 的
     y 坐标去点，结果点在 page2 首条上，把 `338082`（refactoringui）误删了。
     现在的做法见 `locate()`：命中即记住页码，点击前**回到该页重取坐标**，
     并用 `elementFromPoint` 复核「点下去的那个像素真的属于目标卡片的删除按钮」，
     删完再用 draftId 做**集合差**（目标消失 且 其他一个不少）而不是按标题猜。

卡片 → draftId 的可靠来源：卡片里「编辑」链接的
`href=".../article/write-new?draftId=338232"`，比标题稳（标题会改）。

与 `tc_recycle.py`（清空回收站）、`tc_manage.py`（删文章=进回收站）职责分开。

用法：
    python -X utf8 scripts/tc_draft_purge.py                       # 只列草稿箱
    python -X utf8 scripts/tc_draft_purge.py 338232                 # 预演：按 draftId 定位
    python -X utf8 scripts/tc_draft_purge.py "马力欧兄弟"            # 预演：按标题关键词
    python -X utf8 scripts/tc_draft_purge.py 338232 --yes
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

# 一张卡片 = 从 .cdc-2-course-panel-info 向上找到的第一个「只含 1 个 info 容器，
# 且同时带『删除』控件与 draftId 链接」的祖先。draftId 从编辑链接的 href 里取。
CARDS_JS = """
(function(){
  var out = [];
  document.querySelectorAll('.cdc-2-course-panel-info').forEach(function(p){
    var n = p;
    for (var i=0;i<8 && n.parentElement;i++){
      n = n.parentElement;
      var infos = n.querySelectorAll('.cdc-2-course-panel-info').length;
      if (infos !== 1) continue;
      var del = null;
      n.querySelectorAll('span.cdc-svg-icon-text').forEach(function(e){
        if ((e.innerText||'').trim() === '删除') del = e;
      });
      var href = null;
      n.querySelectorAll('a[href*="draftId="]').forEach(function(a){
        if (!href && /draftId=\\d+/.test(a.getAttribute('href')||'')) href = a.getAttribute('href');
      });
      if (!del || !href) continue;
      var r = del.getBoundingClientRect();
      out.push({id:(/draftId=(\\d+)/.exec(href)||[])[1],
                title:(p.innerText||'').replace(/\\s+/g,' ').trim().slice(0,70),
                x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
                w:Math.round(r.width), h:Math.round(r.height)});
      break;
    }
  });
  return JSON.stringify(out);
})()"""

# 点击前复核：这一点上的元素（或它的祖先）是不是那张卡片的『删除』控件
AT_JS = """
(function(){
  var e = document.elementFromPoint(%d, %d);
  if (!e) return 'null';
  var n = e;
  for (var i=0;i<6 && n;i++){
    var s = (n.innerText||'').replace(/\\s+/g,' ').trim();
    if (s === '删除' || /\\b删除\\b/.test(s) && s.length < 12) break;
    n = n.parentElement;
  }
  var card = e;
  for (var j=0;j<12 && card;j++){
    if (card.querySelector && card.querySelectorAll('.cdc-2-course-panel-info').length === 1) break;
    card = card.parentElement;
  }
  var href = '';
  if (card && card.querySelector) {
    var a = card.querySelector('a[href*="draftId="]');
    if (a) href = a.getAttribute('href') || '';
  }
  var m = /draftId=(\\d+)/.exec(href);
  return JSON.stringify({id: m ? m[1] : '', tag: e.tagName, txt: (e.innerText||'').slice(0,20)});
})()"""

PAGE_JS = """
(function(){
  var items = document.querySelectorAll('.tp1-pagination__item');
  for (var i=0;i<items.length;i++){
    if ((items[i].innerText||'').trim() === '%d') {
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


def draft_ids(cdp):
    return set(str(r.get("draftId")) for r in list_drafts(cdp))


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
    time.sleep(6)
    return tab["text"]


def goto_page(cdp, page):
    if page == 1:
        return True
    raw = cdp.eval(PAGE_JS % page)
    p = json.loads(raw or "null")
    if not p:
        return False
    click(cdp, p["x"], p["y"])
    time.sleep(4)
    return True


def read_cards(cdp):
    """卡片是懒渲染的：刚进 tab 时可能一张都没挂上，轮询几轮再判空。"""
    for _ in range(5):
        cards = json.loads(cdp.eval(CARDS_JS) or "[]")
        if cards:
            return cards
        time.sleep(2)
    return []


def locate(cdp, key):
    """找到目标卡片所在页；返回 (page, id, title) 或 None。

    🚨 一圈扫完只到命中为止，绝不继续翻页（翻页后旧坐标会指到别人身上）。
    """
    for page in (1, 2):
        if not goto_page(cdp, page):
            break
        for c in read_cards(cdp):
            if key.isdigit():
                if c["id"] == key:
                    return page, c["id"], c["title"]
            elif key in c["title"]:
                return page, c["id"], c["title"]
    return None


def purge_one(cdp, key):
    """删一条；成功返回 True。每条都重新加载页面，避免列表重排导致坐标失效。"""
    label = open_draftbox(cdp)
    print("  进草稿箱:", label)
    hit = locate(cdp, key)
    if not hit:
        print("   ✗ 草稿箱里没有「%s」" % key)
        return False
    page, did, txt = hit
    print("   命中: draftId=%s 第%d页 %s" % (did, page, txt[:52]))

    # 回到命中页重新读一次坐标（翻页前后 DOM 会重建，坐标必须现取）
    if not goto_page(cdp, page):
        print("   ✗ 回不到第 %d 页" % page)
        return False
    card = next((c for c in read_cards(cdp) if c["id"] == did), None)
    if not card:
        print("   ✗ 第 %d 页又找不到 draftId=%s" % (page, did))
        return False
    if not card["w"]:
        print("   ✗ 删除按钮无尺寸（不在视口内？），跳过")
        return False

    at = json.loads(cdp.eval(AT_JS % (card["x"], card["y"])) or "null")
    if not at or at.get("id") != did:
        print("   ✗ 像素复核不通过：(%d,%d) 上是 %s id=%s，期望 %s ⇒ 不点"
              % (card["x"], card["y"], (at or {}).get("tag"), (at or {}).get("id"), did))
        return False
    print("   像素复核: (%d,%d) → %s 卡片 draftId=%s ✓" % (card["x"], card["y"], at["tag"], at["id"]))

    before = draft_ids(cdp)
    click(cdp, card["x"], card["y"])
    time.sleep(2)
    dlg = cdp.eval(DIALOG_JS)
    print("   弹窗:", (dlg or "")[:80])
    if "草稿删除" not in (dlg or ""):
        print("   ✗ 弹窗文案不含『草稿删除』，不像删除确认框，跳过（不盲点）")
        return False
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

    after = draft_ids(cdp)
    gone = before - after
    added = after - before
    if_gone = did not in after
    ok = if_gone and not added
    print("   草稿箱 %d -> %d ｜本次消失: %s ｜新增: %s ｜目标已消失: %s"
          % (len(before), len(after), sorted(gone) or "无", sorted(added) or "无", if_gone))
    if not ok:
        print("   🚨 删除结果与预期不符：目标 %s 仍在，或误删了 %s —— 立即停下人工核对"
              % (did, sorted(gone - {did}) or "无"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="draftId（纯数字）或标题关键词，可多个")
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
            for k in a.keys:
                hit = locate(cdp, k)
                if hit:
                    print("  [命中 %s] 第%d页 draftId=%s %s" % (k, hit[0], hit[1], hit[2][:50]))
                else:
                    print("  [未命中] %s" % k)
            print("\n草稿箱总数:", len(list_drafts(cdp)))
            return 0

        ok, bad = 0, []
        for k in a.keys:
            print("\n== 删 %s" % k)
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
