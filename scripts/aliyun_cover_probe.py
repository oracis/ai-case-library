#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云「文章卡片有没有封面」的**唯一可信回读口**。

判定位置
--------
个人主页动态列表里，文章的卡片结构是：

    <div class="dynamic-content">
      <div class="dynamic-left"> <h3><a href="/article/<aid>">标题</a></h3>
                                 <div class="dynamic-summary">摘要</div> </div>
      <div class="dynamic-image"> <img src="https://ucc.alicdn.com/pic/
                                     developer-ecology/<uid>_<hash>.jpg"> </div>
    </div>

**有封面**才有 `div.dynamic-image` 这个节点；没封面时 DOM 里根本没有它
（2026-11-16 实测：1783930 有 → `3bd6080be4a44a758a89d943bbd85c4f.jpg`；
 同日对照 1783731 没有该节点）。所以判据是「节点存在 + src 是 ucc 图片」，
不是「公开页有没有 `<img>`」。

为什么不用别的口（全部实测证伪，别再试）
----------------------------------------
· 文章公开页：没有 coverUrl/firstImg 字段；`og:image` 恒为平台默认 200×200 图标；
  连确认有封面的 1783930 公开页也搜不到 ucc 图。
· `draftDetail.coverUrl`：对**已发布**文章整体读不到（草稿被消费掉了）。
· 「我的文章」列表：纯文字卡片，无缩略图。
· 编辑页「文章图片」列表：页面态，重载即空，不代表落库。
· 传图动作只 PUT 到 `ucc-image.oss-cn-beijing.aliyuncs.com`（预签名直传），
  **不调用任何落库接口** —— 所以传图成功 ≠ 封面已保存。

这个页面是**登录态 SSR**（没有可 curl 的 XHR），因此必须走 CDP。

用法
----
    python -X utf8 scripts/aliyun_cover_probe.py --aid 1783930
    python -X utf8 scripts/aliyun_cover_probe.py --aid 1783930 --json

退出码：0=有封面 ／ 2=文章在列表里但没有封面 ／ 3=列表里没找到该文章
（3 常见于刚发布还在审核、列表暂不展示）。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DEFAULT_UID = "32o2bc7dqh5k6"   # 粥粥海王星（台账主账号）

CARD_JS = r"""
(function(aid){
  var links = [].slice.call(document.querySelectorAll('a[href*="/article/"]'));
  var hit = links.filter(function(e){
    return (e.getAttribute('href') || '') === '/article/' + aid;
  })[0];
  if (!hit) return JSON.stringify({found: false, n: links.length});
  var box = hit;
  for (var k = 0; k < 8 && box.parentElement; k++) {
    box = box.parentElement;
    if (box.classList && box.classList.contains('dynamic-content')) break;
  }
  var imgNode = box.querySelector('.dynamic-image img');
  var src = imgNode ? (imgNode.getAttribute('src') || imgNode.src || '') : '';
  if (!src) {
    // 兜底：容器里任何 ucc 图片都算
    var any = [].slice.call(box.querySelectorAll('img')).filter(function(i){
      return /ucc\.alicdn\.com\/pic/.test(i.src || '');
    })[0];
    src = any ? any.src : '';
  }
  var titleNode = box.querySelector('.dynamic-title a') || hit;
  return JSON.stringify({
    found: true,
    has_image_node: !!imgNode,
    cover_url: src,
    title: (titleNode.innerText || '').replace(/\s+/g, ' ').slice(0, 80)
  });
})('%s')
"""


def probe(aid, uid=DEFAULT_UID, wait=18):
    """返回 dict：{found, has_image_node, cover_url, title, state}。"""
    import yun_draft_cdp as Y   # 延迟导入：只在真的要开浏览器时付代价
    proc, cdp = Y._connect()
    try:
        cdp.open("https://developer.aliyun.com/profile/%s" % uid)
        deadline = time.time() + wait
        last = None
        while time.time() < deadline:
            time.sleep(2)
            raw = cdp.eval(CARD_JS % aid)
            try:
                last = json.loads(str(raw))
            except (ValueError, TypeError):
                last = {"found": False, "parse": str(raw)[:120]}
            if last.get("found"):
                break
        if not last or not last.get("found"):
            return {"aid": aid, "found": False,
                    "state": "not_listed", "raw": last}
        cover = (last.get("cover_url") or "").strip()
        return {"aid": aid, "found": True,
                "has_image_node": bool(last.get("has_image_node")),
                "cover_url": cover,
                "title": last.get("title") or "",
                "state": "ok" if cover.startswith("http") else "empty"}
    finally:
        try:
            if proc is not None:
                proc.terminate()
        except Exception:      # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", required=True)
    ap.add_argument("--uid", default=DEFAULT_UID)
    ap.add_argument("--wait", type=int, default=18)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = probe(a.aid, a.uid, a.wait)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print("文章: %s" % a.aid)
        print("标题: %s" % (r.get("title") or "-"))
        if r["state"] == "not_listed":
            print("状态: 列表里没找到（多半还在审核，稍后再查）")
        elif r["state"] == "ok":
            print("状态: 有封面")
            print("封面: %s" % r["cover_url"])
        else:
            print("状态: 卡片无封面（没有 div.dynamic-image）")
    sys.exit({"ok": 0, "empty": 2}.get(r["state"], 3))


if __name__ == "__main__":
    main()
