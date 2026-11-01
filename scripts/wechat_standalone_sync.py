"""独立稿台账 ⇄ 公众号远端 对账（`data/wechat_standalone.json`）。

为什么需要（2026-10-31）：
    `wechat_publish.py send` 会对每条草稿做「发表前基线 / 发表后回读」，判据很严
    （已发表列表出现同名条目 **且** 草稿箱里那条消失）。这套判据在**被沙箱中途
    杀掉**或**草稿箱列表滞后**时拿不到结论，于是明明发成功了却记成 draft ——
    今天 ep-003 就是这样：文章已上线，台账还写着 draft。

    所以补一个**只读远端、可反复跑**的对账器：远端是唯一真相源，本地永远向它看齐。

用法：
    python scripts/wechat_standalone_sync.py            # 只报告差异（默认，不写盘）
    python scripts/wechat_standalone_sync.py --apply    # 确认后回写台账

判定：
    published  已发表列表里有**标题完全相同**的条目 ⇒ 记 published + 新 id + 永久链接
    draft      草稿还在草稿箱里（appmsgid 命中）      ⇒ 保持 draft
    missing    两边都没有                             ⇒ 标 missing（**不删记录**）
    unknown    任一侧接口没读成功                     ⇒ 不下结论，留给下次

⚠ 标题必须**精确匹配**（归一化后），因为系列文的标题前缀相同
   （「游戏厅：xxx」），做包含匹配会把 2048 认成 Mario —— 这条踩过。
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)

import wechat_publish as wp                      # noqa: E402
import wechat_sync as ws                         # noqa: E402
import atomicio                                  # noqa: E402

SA_PATH = wp.STANDALONE_PATH


def _remote(cdp, tok):
    """读远端：返回 (published {规范标题: rec}, drafts {appmsgid}, ok)。"""
    published = {}
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list&search_field=null"
           "&begin=0&count=20&token=%s&lang=zh_CN&f=json&ajax=1" % tok)
    d = ws._xhr_json(cdp, url)
    pok = isinstance(d, dict) and bool(d.get("publish_page"))
    if pok:
        pp = d["publish_page"]
        if isinstance(pp, str):
            pp = json.loads(pp)
        for it in (pp.get("publish_list") or []):
            try:
                info = json.loads(it.get("publish_info") or "{}")
            except ValueError:
                continue
            for ai in (info.get("appmsg_info") or []):
                t = ws.norm_title(ai.get("title") or "")
                if t:
                    published[t] = {
                        "appmsgid": str(ai.get("appmsgid") or info.get("msgid") or ""),
                        "url": ai.get("content_url") or "",
                        "title": ai.get("title") or "",
                    }

    drafts = set()
    u2 = ("https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_ex&type=77&sub=all"
          "&begin=0&count=20&token=%s&lang=zh_CN&f=json&ajax=1&random=0.7" % tok)
    d2 = ws._xhr_json(cdp, u2)
    dok = isinstance(d2, dict)
    if dok:
        for it in (d2.get("app_msg_list") or []):
            aid = it.get("appmsgid")
            if aid is not None:
                drafts.add(str(aid))
    return published, drafts, (pok and dok)


def main():
    ap = argparse.ArgumentParser(description="独立稿台账 ⇄ 公众号远端 对账")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--apply", action="store_true", help="回写台账（默认只报告）")
    ap.add_argument("--only", help="只看指定 slug（逗号分隔）")
    args = ap.parse_args()

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)

    if not os.path.exists(SA_PATH):
        raise SystemExit("没有 %s" % SA_PATH)
    sa = json.load(open(SA_PATH, encoding="utf-8")) or {}
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}

    cdp = wp.CDP(args.port)
    tid, tok = wp._connect_mp(cdp)
    if not tok:
        raise SystemExit("拿不到 token：确认 %d 端口里公众号后台已登录" % args.port)
    published, drafts, ok = _remote(cdp, tok)
    print("远端：已发表 %d 条可匹配、草稿箱 %d 条%s"
          % (len(published), len(drafts), "" if ok else "（⚠ 有一侧没读全）"))

    changed = []
    for slug, rec in sa.items():
        if only and slug not in only:
            continue
        title = rec.get("title") or ""
        nt = ws.norm_title(title)
        hit = published.get(nt)
        if hit:
            want = {"status": "published",
                    "published_appmsgid": hit["appmsgid"],
                    "url": hit["url"]}
            diff = {k: v for k, v in want.items() if rec.get(k) != v}
            if diff:
                rec.update(diff)
                rec.setdefault("sent_via", "reconcile")
                rec.setdefault("published_at", time.strftime("%Y-%m-%d %H:%M"))
                changed.append((slug, "published", diff))
            print("  %-34s published  %s" % (slug, hit["appmsgid"]))
        elif str(rec.get("appmsgid") or "") in drafts:
            if rec.get("status") != "draft" and ok:
                rec["status"] = "draft"
                changed.append((slug, "draft→draft", {"status": "draft"}))
            print("  %-34s draft      %s" % (slug, rec.get("appmsgid")))
        else:
            if ok:
                rec["status"] = "missing"
                rec["note"] = "远端已发表列表与草稿箱都找不到（可能被手动删除）"
                changed.append((slug, "missing", {}))
            print("  %-34s %s" % (slug, "missing" if ok else "unknown"))

    if not changed:
        print("\n台账与远端一致，无需改动。")
        return 0
    print("\n需改动 %d 条" % len(changed))
    if not args.apply:
        print("（只报告模式；确认后加 --apply 回写）")
        return 0
    # 改前备份：这是唯一的发布台账，覆盖掉就再也对不上远端
    bak = SA_PATH + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    with open(bak, "wb") as f:
        f.write(open(SA_PATH, "rb").read())
    atomicio.write_json(SA_PATH, sa)
    print("已回写 %s（备份 %s）" % (os.path.basename(SA_PATH),
                                   os.path.basename(bak)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
