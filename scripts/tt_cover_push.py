#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""头条草稿换封面：**一趟做完**「读旧 URL → 换图 → 读新 URL → 判真换」。

⚠⚠ 为什么必须一趟（2026-10-02 实测教训）：
   头条打开一篇草稿的编辑页要 ~120s，而 `cover_one()` 只返回 "ok"
   （铁律：脚本自报成功不算数）。原先设计的「先跑一遍全量建基线 →
   再换图 → 再跑一遍全量回读」等于把编辑器打开 **3N 次**，33 条要
   66 分钟以上，根本跑不完。合并成一趟后每条只开 **1 次**编辑器，
   前后 URL 在同一个页面上下文里取，比对还更可信（同源、无二次加载差异）。

   ⚠ 更隐蔽的坑：头条 CDN URL 里有 `lk3s=` / `x-signature=` 这类
   **会随请求重新签发的参数**，同一张图两次取到的 URL 可能不同
   ⇒ 比对必须先剥掉签名参数，只比**图片路径那段 hash**。

用法：
    python -X utf8 scripts/tt_cover_push.py --batch 10     # 前 10 条
    python -X utf8 scripts/tt_cover_push.py --batch 10 --off 10  # 接着跑
    python -X utf8 scripts/tt_cover_push.py --case quran-unlock
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import toutiao_publish as tp  # noqa: E402
import chrome_cdp_launch as cl  # noqa: E402
import wechat_publish as wp  # noqa: E402

LOG = os.path.join(ROOT, "out", "tt_cover_push.json")
GAP = 2.0
JS_IMG = ("(function(){var i=document.querySelector('.article-cover-images img');"
          "return i?i.src:'';})()")


def img_key(url):
    """从头条 CDN URL 里抽出可稳定比对的图片标识。

    URL 形如：
      https://p3-sign.toutiaoimg.com/tos-cn-i-6w9my0ksvp/<HASH>~tplv-tt-cover-v2.image
        ?lk3s=...&x-signature=...
    ⇒ 只取 `<HASH>~tplv-...` 这段；签名参数每次请求都会变，比它们必假阳性。
    """
    if not url:
        return ""
    m = re.search(r"/([0-9a-f]{16,})(~tplv-[^?#/]*)", url)
    if m:
        return m.group(1) + m.group(2)
    return re.sub(r"[?#].*$", "", url)


def draft_ids():
    """草稿箱里真实存在的案例 id（精确解析，不用模糊匹配）。"""
    src = os.path.join(ROOT, "out", "tt_draftbox.txt")
    d = open(src, encoding="utf-8").read()
    items = re.findall(r"([^\n]{4,60})\n(\d{2}-\d{2} \d{2}:\d{2})\n编辑删除", d)
    dt = {t for t, _ in items}
    return [c for c in tp.load_cases() if tp.find_title(c) in dt]


def load_done():
    if os.path.exists(LOG):
        try:
            return json.load(open(LOG, encoding="utf-8"))
        except Exception:                # noqa: BLE001
            return {}
    return {}


def save_done(d):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    tmp = LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LOG)


def one(c, cdp_holder):
    """换一条的封面，返回 (verdict, old_key, new_key)。"""
    title = tp.find_title(c)
    cdp, t, err = tp.open_draft_editor(title, wait=25)
    if not cdp:
        return("ERR_OPEN: %s" % err, "", "")
    try:
        time.sleep(1.5)
        old_url = cdp.eval(JS_IMG) or ""
        old_key = img_key(old_url)
        cp = tp.cover_path(c["id"])
        if not cp:
            return ("ERR_NOFILE: 没找到 cover.png", old_key, "")
        r = tp.upload_cover(cdp, [cp])
        if r != "ok":
            return ("ERR_UPLOAD: %s" % r, old_key, "")
        tp._wait_autosave(cdp)
        time.sleep(1.5)
        new_url = cdp.eval(JS_IMG) or ""
        new_key = img_key(new_url)
        if not new_key:
            return ("ERR_NOIMG: 上传后封面读不到", old_key, "")
        if old_key and new_key == old_key:
            return ("NOT_CHANGED: 图没变", old_key, new_key)
        return ("OK", old_key, new_key)
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:                # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0, help="本批最多处理几条")
    ap.add_argument("--off", type=int, default=0, help="跳过前N 条（续跑用）")
    ap.add_argument("--case", help="只处理这一条")
    args = ap.parse_args()

    cases = draft_ids()
    done = load_done()
    todo = [c for c in cases
            if not (done.get(c["id"], {}).get("v", "").startswith("OK"))]
    if args.case:
        todo = [c for c in todo if c["id"] == args.case]
    else:
        todo = todo[args.off:]
        if args.batch:
            todo = todo[:args.batch]

    print("草稿箱 %d 条｜待处理 %d｜本批 %d｜已完成 %d"
          % (len(cases), len([c for c in cases
                              if not done.get(c["id"], {}).get("v", "").startswith("OK")]),
             len(todo), len(done)), flush=True)
    if not todo:
        print("没有待处理项。")
        return 0

    wp._autostart_chrome_if_needed(tp.CDP_PORT, profile=cl.PLAT_PROFILE)
    t_all = time.time()
    ok = bad = 0
    for i, c in enumerate(todo, 1):
        cid = c["id"]
        t0 = time.time()
        v = "?"
        try:
            v, ok_, nk = one(c, None)
        except Exception as e:                # noqa: BLE001
            v = "EXC: %s: %s" % (type(e).__name__, e)
            ok_, nk = "", ""
        dt = time.time() - t0
        done[cid] = {"v": v, "old": ok_, "new": nk,
                     "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "sec": round(dt, 1)}
        save_done(done)
        print("[%d/%d] %-22s %-16s %.0fs  %s → %s"
              % (i, len(todo), cid, v, dt, ok_[-12:] or "-", nk[-12:] or "-"),
              flush=True)
        if v.startswith("OK"):
            ok += 1
        else:
            bad += 1
        time.sleep(GAP)

    print("\n==== 本批：成功 %d / 失败 %d｜总耗时 %.1f 分钟 ===="
          % (ok, bad, (time.time() - t_all) / 60.0), flush=True)
    print("累计 OK %d / %d"
          % (sum(1 for v in done.values() if v.get("v", "").startswith("OK")),
             len(cases)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())