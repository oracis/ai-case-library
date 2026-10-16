# -*- coding: utf-8 -*-
"""公众号草稿**封面**的独立回读对账（2026-10-16 新增）。

为什么单独一个脚本
------------------
`wechat_publish.py refresh --cover` 的「换封面(API): SUC」只是编辑器内的
即时回调，`保存: OK` 也只是「保存后 URL 出现 appmsgid=」—— 都不保证服务端
`cover` 字段真落值。而 `wechat_verify_refresh.py` 的判据是「篇号命中 +
标题一致 + update_time」，**不查封面**。封面是这次批量踩坑最多的字段
（UI 路线 headless 下 CONFIRM_ERR 11/11），必须结果导向地单独回读。

判据
----
`_cover_state()` 读 `.js_cover_preview_new` 的 background-image：
display != none 且含 mmbiz ⇒ 服务端 cover 字段已设（草稿箱列表项有图）。
打出的 URL 尾部 36 字符供人工比对「是不是想要的那张」。

编辑器打不开时的两种成因（别混为一谈）
--------------------------------------
  · `ERR_320003` —— 页面返「系统错误(320003)」。可能是限流（等几分钟
    重试），也可能是**死篇号**（草稿被删/群发后离开草稿箱）。判别法：
    再拉一次草稿列表，篇号不在里面 ⇒ 死篇号，重试无用，走重建
    （publish 新建 + 台账回写新 appmsgid）。
  · 其它异常（连接被收等）—— 环境问题，重跑本脚本即可。

用法
----
    # 核对指定几条
    python -X utf8 scripts/wechat_verify_covers.py checkvibe voklit

    # 核对台账里所有未发表、带 appmsgid 的草稿（默认行为）
    python -X utf8 scripts/wechat_verify_covers.py --all

    # 结果另存 json
    python -X utf8 scripts/wechat_verify_covers.py --all --out tmp/covers.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wechat_publish as W                                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def probe_one(cdp, tok, cid, appmsgid):
    """返回 (case_id, status, cover_url_tail)。status ∈ SET/EMPTY/ERR_*。"""
    try:
        tid = W._open_draft_editor(cdp, appmsgid, tok, timeout=30, strict=False)
        ok, bg = W._cover_state(cdp)
        tail = ""
        if "mmbiz" in bg:
            tail = bg.split("mmbiz")[-1].strip('")')[-36:]
        try:
            cdp.close_target(tid)
        except Exception:
            pass
        return (cid, "SET" if ok else "EMPTY", tail)
    except Exception as e:
        # 320003 = 服务端错误页（限流或死篇号）；其余多是连接被收
        st = "ERR_320003" if "320003" in str(e) else "ERR:%s" % type(e).__name__
        return (cid, st, str(e)[:60])


def main():
    ap = argparse.ArgumentParser(description="公众号草稿封面回读对账")
    ap.add_argument("cases", nargs="*", help="case id 列表（不给则看 --all）")
    ap.add_argument("--all", action="store_true",
                    help="核对台账里所有未发表、带 appmsgid 的草稿")
    ap.add_argument("--out", default="", help="把结果写成 json")
    args = ap.parse_args()

    ledger = json.load(open(os.path.join(ROOT, "data", "wechat_published.json"),
                            encoding="utf-8"))
    if args.cases:
        pairs = [(cid, (ledger.get(cid) or {}).get("appmsgid"))
                 for cid in args.cases]
    elif args.all:
        pairs = [(cid, rec.get("appmsgid")) for cid, rec in ledger.items()
                 if rec.get("appmsgid") and rec.get("status") != "published"]
        pairs.sort()
    else:
        raise SystemExit("给 case id 列表，或加 --all")

    todo = [(c, a) for c, a in pairs if a]
    skipped = [c for c, a in pairs if not a]
    for c in skipped:
        print("%-22s NO_APPMSGID（台账里没记篇号）" % c)
    if not todo:
        raise SystemExit("没有可核对的条目")
    print("待核对 %d 篇" % len(todo))

    if not W._autostart_chrome_if_needed():
        raise SystemExit("Chrome 起不来")
    cdp = W.CDP(W.CDP_PORT)
    _tid, tok = W._connect_mp(cdp)
    if not tok:
        raise SystemExit("拿不到 token")

    results = []
    for cid, aid in todo:
        r = probe_one(cdp, tok, cid, aid)
        results.append(r)
        print("%-22s %-12s ...%s" % (r[0], r[1], r[2]))

    n_set = sum(1 for _c, st, _t in results if st == "SET")
    print("\n=== 汇总：%d/%d 封面已设 ===" % (n_set, len(results)))
    for c, st, t in results:
        if st != "SET":
            print("  ✗ %-22s %-12s %s" % (c, st, t))
    if args.out:
        json.dump([{"case": c, "status": st, "cover_tail": t}
                   for c, st, t in results],
                  open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
