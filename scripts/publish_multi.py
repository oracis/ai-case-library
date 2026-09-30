#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次发布多平台 —— 统一入口。

    python scripts/publish_multi.py queue                 # 看清单（不连浏览器）
    python scripts/publish_multi.py run --case prosp      # 存草稿（B站+头条+…）
    python scripts/publish_multi.py run --all             # 剩下的全发
    python scripts/publish_multi.py run --all --dry       # 只打印子命令
    python scripts/publish_multi.py status                # 各平台状态统计
    python scripts/publish_multi.py sync                  # 把既有记录灌进清单
    python scripts/publish_multi.py retry --case kibu     # 重试失败项

与旧的 `publish_both.py` 的关系：后者仍在用（三平台 + 特殊参数），但**新活
一律走这里** —— 它多了 B站适配器、清单状态机、单条重试和断点续传。
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from multiplatform import state as S                    # noqa: E402
from multiplatform.adapters import REGISTRY, get as get_ad   # noqa: E402
from multiplatform.runner import Runner, bootstrap_manifest   # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ALL = sorted(REGISTRY)
LABEL = {k: REGISTRY[k].label for k in REGISTRY}


def parse_plats(s):
    if not s:
        return list(ALL)
    out = []
    for p in s.replace("，", ",").split(","):
        p = p.strip().lower()
        if p in REGISTRY and p not in out:
            out.append(p)
        elif p:
            raise SystemExit("不认识的平台：%s（可选 %s）"
                             % (p, "/".join(ALL)))
    return out or list(ALL)


def cmd_sync(args):
    m, added = bootstrap_manifest(parse_plats(args.platforms), force=args.force)
    print("已把 %d 条既有记录写进清单：%s" % (added, S.MANIFEST))
    for p in parse_plats(args.platforms):
        n = len(get_ad(p).done_ids())
        print("  %-10s 本地状态文件 %d 条" % (LABEL[p], n))
    return 0


def cmd_status(args):
    plats = parse_plats(args.platforms)
    m = S.Manifest()
    print("清单：%s（%d 条 case 有记录）" % (S.MANIFEST, len(m.cids())))
    for p in plats:
        ad = get_ad(p)
        s = m.summary([p])[p]
        print("\n%s（%s）" % (ad.label, p))
        for st in S.STATES:
            if s.get(st):
                print("  %-12s %d" % (st, s[st]))
        if ad.has_state_file:
            print("  平台状态文件 %d 条" % len(ad.done_ids()))
        else:
            print("  （无本地状态文件，幂等只认清单）")
        fails = [cid for cid in m.cids() if m.state(cid, p) == "failed"]
        if fails:
            print("  失败：%s" % ", ".join(fails[:12]))
    return 0


def cmd_queue(args):
    plats = parse_plats(args.platforms)
    r = Runner(plats, dry=True, verbose=False)
    jobs = r.queue(only=_resolve(args.case), limit=args.near or 0)
    if not jobs:
        print("没有待发的（所选平台都发过了）。")
        return 0
    print("待发 %d 条（最新在前）：" % len(jobs))
    for i, (art, todo) in enumerate(jobs, 1):
        print("  %2d. %-24s %-26s → %s"
              % (i, art.cid, art.name,
                 " + ".join(LABEL[p] for p in todo)))
    return 0


def _resolve(case):
    if not case:
        return None
    from multiplatform import article as A
    for c in A.load_cases():
        if c.get("id") == case or c.get("name", "") == case:
            return c.get("id")
    return case


def cmd_run(args):
    plats = parse_plats(args.platforms)
    r = Runner(plats, dry=args.dry, yes=args.yes, retries=args.retries,
               replace=args.replace)
    r.run(only=_resolve(args.case), limit=args.near or 0,
          force=args.replace)
    return 0


def cmd_retry(args):
    plats = parse_plats(args.platforms)
    m = S.Manifest()
    cid = _resolve(args.case)
    targets = [cid] if cid else [
        c for c in m.cids()
        if any(m.state(c, p) == "failed" for p in plats)]
    if not targets:
        print("没有失败项。")
        return 0
    for c in targets:
        for p in plats:
            if m.state(c, p) == "failed":
                m.mark_pending(c, p, m.title_of(c, p))
    print("已把 %d 条重置为 pending" % len(targets))
    return cmd_run(args)


def cmd_reset(args):
    m = S.Manifest()
    cid = _resolve(args.case)
    if not cid:
        print("--reset 必须给 --case（不想误清整个库）")
        return 1
    m.remove(cid, None if args.all_platforms else parse_plats(args.platforms))
    print("已清除 %s 的清单记录" % cid)
    return 0


def _add_plats(p):
    p.add_argument("--platforms", default="",
                   help="逗号分隔，默认全开：%s" % ",".join(ALL))
    return p


def cmd_mark(args):
    """人工核对远端后，直接改清单状态（如把已发布的改成 published）。"""
    m = S.Manifest()
    cid = _resolve(args.case)
    if not cid:
        print("--case 必须给一个案例")
        return 1
    if args.state not in S.STATES:
        print("状态可选：%s" % "/".join(S.STATES))
        return 1
    m.set(cid, args.platform, args.state, title=m.title_of(cid, args.platform))
    print("%s / %s → %s" % (cid, args.platform, args.state))
    return 0


def cmd_verify(args):
    """远端回读校验（只读）：清单 vs 草稿箱。"""
    from multiplatform import verify as V
    plats = [args.platform] if args.platform else parse_plats(args.platforms)
    plats = [p for p in plats if p in V.READERS]
    if not plats:
        print("远端校验目前只支持：%s" % "/".join(sorted(V.READERS)))
        print("（公众号与小红书的草稿箱读取还没做，先用 status 看本地清单）")
        return 1
    for p in plats:
        try:
            titles = V.READERS[p]()
        except Exception as e:                          # noqa: BLE001
            print("读取 %s 草稿箱失败：%s：%s" % (p, type(e).__name__, e))
            print("（确认调试 Chrome 9222 起着、且已登录该平台）")
            continue
        V.reconcile(p, titles)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="一次发布多平台（公众号/小红书/头条/B站，草稿优先）")
    ap.add_argument("--platforms", default="",
                    help="逗号分隔，默认全开：%s" % ",".join(ALL))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = _add_plats(sub.add_parser("queue", help="看会发哪几条"))
    p.add_argument("case", nargs="?")
    p.add_argument("--near", type=int, default=0)
    p.set_defaults(fn=cmd_queue)

    p = _add_plats(sub.add_parser("run", help="执行"))
    p.add_argument("case", nargs="?")
    p.add_argument("--near", type=int, default=0, help="最近 N 条，0=全部")
    p.add_argument("--dry", action="store_true", help="只打印子命令")
    p.add_argument("--yes", action="store_true",
                   help="真公开发布（默认只存草稿）")
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--replace", action="store_true",
                   help="改文案后覆盖重存（已 draft_saved 的也重发）")
    p.set_defaults(fn=cmd_run)

    p = _add_plats(sub.add_parser("status", help="各平台状态统计"))
    p.set_defaults(fn=cmd_status)

    p = _add_plats(sub.add_parser("sync", help="把既有平台记录灌进清单（首次用）"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = _add_plats(sub.add_parser("retry", help="重试失败项"))
    p.add_argument("case", nargs="?")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--retries", type=int, default=2)
    p.set_defaults(fn=cmd_retry)

    p = _add_plats(sub.add_parser("reset", help="清除清单记录（强制重发）"))
    p.add_argument("case")
    p.add_argument("--all-platforms", action="store_true")
    p.set_defaults(fn=cmd_reset)

    p = _add_plats(sub.add_parser("verify",
                                  help="远端回读校验（只读）：清单 vs 草稿箱"))
    p.add_argument("--platform", default="", help="只校验一个平台")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("mark", help="人工核对后直接改清单状态")
    p.add_argument("platform", choices=ALL)
    p.add_argument("--case", required=True)
    p.add_argument("--state", default="published", choices=list(S.STATES))
    p.set_defaults(fn=cmd_mark)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
