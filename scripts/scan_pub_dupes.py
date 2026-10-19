"""四平台扫描：草稿箱里「已发布过」的草稿（群发后残留）。只读。

背景（2026-10-17 用户需求）：四个平台都是「从草稿箱发布后草稿不消失」
⇒ 草稿箱里躺着一批已发表文章，肉眼要逐篇对。本脚本把两侧列表都拉
回来按标题算交集，输出待删清单（**只读，不删**；删用各平台既有命令：
头条 delete_draft_api / B站 delete_draft / 公众号 delete --appmsgid /
小红书 purge --match "标题" --all）。

匹配判据：
  - 主判据 = 标题去空白后**精确相等**（不硬猜，避免误删）。
  - 公众号附产品名级（首个冒号前）候选提示——公众号已发布是长口径
    标题、草稿是短口径，精确匹配会漏（2026-10-17 实测 47 草稿 ∩ 20
    已发，精确 0 命中、产品名 1 命中）。产品名级只是提示，删不删人工定。

端口：公众号 9222（ChromeCDP）；头条/B站/小红书 9223（chrome-debug-profile）。
小红书已发布抓取要点（2026-10-17 实证）：
  - 入口 https://creator.xiaohongshu.com/creator/notemanage?roleType=creator
    （会重定向到 /new/note-manager）
  - 列表在自己的滚动容器里（div.content，scrollHeight>clientHeight），
    window.scrollTo 无效，必须滚容器
  - 标题选择器 span.note-card__title；「全部 N」含审核中/未通过

用法：
    python scripts/scan_pub_dupes.py                 # 扫四平台，打印+写 JSON
    python scripts/scan_pub_dupes.py --only xhs      # 只扫某个平台
"""
import argparse
import json
import os
import re
import sys
import time

os.environ["no_proxy"] = "*"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp      # noqa: E402
import wechat_sync as ws         # noqa: E402
import toutiao_publish as tt     # noqa: E402
import bili_draft_api as ba      # noqa: E402
import xhs_publish as xp         # noqa: E402

OUT = os.path.join(ROOT, "tmp", "pub_dupes_scan.json")
XHS_NOTEMANAGE = ("https://creator.xiaohongshu.com/creator/notemanage"
                  "?roleType=creator")


def norm(s):
    return re.sub(r"\s+", "", str(s or "")).strip()


def prod_name(t):
    """产品名 = 首个中英文冒号前的部分（公众号长短口径对齐用）。"""
    name = re.split("[：:]", str(t or "").strip())[0]
    return re.sub(r"\s+", "", name)


def scan_wechat():
    cdp = wp.CDP(9222)
    _tid0, tok = wp._connect_mp(cdp)
    if not tok:
        return {"ok": False, "why": "拿不到 token"}
    drafts = wp._draft_list(cdp, tok, count=200)
    pub, pub_ok = ws.fetch_published_remote(cdp, tok)
    return {
        "ok": bool(pub_ok),
        "why": "" if pub_ok else "已发表列表没读到",
        "drafts": [{"id": str(d.get("appmsgid")),
                    "title": d.get("title") or ""} for d in drafts],
        "published": [{"id": str(a.get("appmsg_id")),
                       "title": a.get("title") or ""} for a in pub],
    }


def scan_toutiao():
    cdp, t = tt._open(tt.TT_DRAFT, wait=8)
    try:
        tt._check_login(cdp)
        raw = tt.fetch_draft_api_raw(cdp) or []
        pub = tt.fetch_published_articles(cdp, max_page=5)
        return {
            "ok": True,
            "drafts": [{"id": str(d.get("gid")),
                        "title": d.get("title") or ""} for d in raw],
            "published": [{"id": str(a.get("id")),
                           "title": a.get("title") or ""} for a in pub],
        }
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


def scan_bilibili():
    cdp = wp.CDP(9223)
    ba._bili_tab(cdp)
    drafts = ba.list_drafts(cdp)
    arts = ba.list_published_articles(cdp, max_page=3)
    return {
        "ok": True,
        "drafts": [{"id": str(d.get("article_id")),
                    "title": d.get("title") or ""} for d in drafts],
        "published": [{"id": str(a.get("id")),
                       "title": a.get("title") or ""} for a in arts],
    }


def _xhs_published_titles(cdp):
    """笔记管理页抓已发布标题（滚容器 + note-card__title）。"""
    cdp.send("Page.navigate", {"url": XHS_NOTEMANAGE})
    time.sleep(8)
    titles, stale = [], 0
    for _ in range(30):
        r = cdp.eval("""(function(){
          var ts=[].slice.call(document.querySelectorAll('.note-card__title'))
            .map(function(e){return (e.innerText||'').trim();})
            .filter(function(s){return s.length>0;});
          var card = document.querySelector('.note-card');
          var box = null, n = card && card.parentElement;
          for(var k=0;k<10 && n && n !== document.body;k++){
            if(n.scrollHeight > n.clientHeight + 20){ box = n; break; }
            n = n.parentElement;
          }
          if(box) box.scrollTop = box.scrollHeight;
          else window.scrollTo(0, document.body.scrollHeight);
          return JSON.stringify(ts);
        })()""", refresh_context=True)
        try:
            ts = json.loads(r) if isinstance(r, str) else (r or [])
        except Exception:
            ts = []
        before = len(titles)
        for s in ts:
            if s not in titles:
                titles.append(s)
        stale = stale + 1 if len(titles) == before else 0
        if stale >= 2:                # 连滚两轮无新增 ⇒ 加载完
            break
        time.sleep(1.5)
    return titles


def scan_xiaohongshu():
    cdp = wp.CDP(9223)
    t = cdp.new_target("about:blank")
    tid = t["targetId"] if isinstance(t, dict) else t
    if not cdp.connect_target(tid):
        return {"ok": False, "why": "连不上 target"}
    try:
        pub_titles = _xhs_published_titles(cdp)
        sub = xp._draft_cdp()
        try:
            cards = xp.fetch_drafts(sub)
        finally:
            try:
                sub.close_target(sub.tid)
            except Exception:
                pass
        return {
            "ok": True,
            "drafts": [{"id": d.get("id") or "",
                        "title": d.get("title") or "",
                        "saved": d.get("saved") or ""} for d in cards],
            "published": [{"id": "", "title": s} for s in pub_titles],
        }
    finally:
        try:
            cdp.close_target(tid)
        except Exception:
            pass


SCANNERS = {"wechat": scan_wechat,
            "toutiao": scan_toutiao,
            "bilibili": scan_bilibili,
            "xiaohongshu": scan_xiaohongshu}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="",
                    help="只扫某个平台（wechat/toutiao/bilibili/xiaohongshu）")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    names = ([args.only] if args.only in SCANNERS
             else list(SCANNERS))

    scan, hits, prod_hits = {}, {}, {}
    for name in names:
        print("== %s ==" % name, flush=True)
        try:
            scan[name] = SCANNERS[name]()
        except Exception as e:                      # noqa: BLE001
            scan[name] = {"ok": False,
                          "why": "%s: %s" % (type(e).__name__, str(e)[:160])}
        r = scan[name]
        print("   ok=%s drafts=%d published=%d %s"
              % (r.get("ok"), len(r.get("drafts") or []),
                 len(r.get("published") or []), r.get("why") or ""),
              flush=True)
        if not r.get("ok"):
            continue
        pubn = {}
        for p in r.get("published") or []:
            k = norm(p.get("title"))
            if k:
                pubn.setdefault(k, p.get("title"))
        m = [{"id": d.get("id"), "title": d.get("title"),
              "pub_title": pubn[norm(d.get("title"))],
              "saved": d.get("saved") or ""}
             for d in r.get("drafts") or []
             if norm(d.get("title")) in pubn]
        hits[name] = m
        print("   【精确标题命中 %d 条】" % len(m))
        for h in m:
            print("     · [%s] %s" % (h["id"], h["title"]))
        if name == "wechat":
            # 产品名级提示（长短口径）：只列，不删
            pubp = {}
            for p in r.get("published") or []:
                k = prod_name(p.get("title"))
                if k:
                    pubp.setdefault(k, p.get("title"))
            pm = [{"id": d.get("id"), "title": d.get("title"),
                   "pub_title": pubp[prod_name(d.get("title"))]}
                  for d in r.get("drafts") or []
                  if prod_name(d.get("title")) in pubp]
            prod_hits[name] = pm
            print("   【产品名级提示 %d 条（口径不同，人工定）】" % len(pm))
            for h in pm:
                print("     ? [%s] %s" % (h["id"], h["title"]))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"scan": scan, "hits": hits,
                   "prod_hits": prod_hits}, f, ensure_ascii=False, indent=1)
    print("\n结果已写：%s" % args.out)
    print("四平台精确命中合计 %d 条" % sum(len(v) for v in hits.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
