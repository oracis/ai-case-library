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
import ledger as L                                       # noqa: E402

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


# publish_multi 的平台键和 ledger 的**不一致**（这里叫 xhs，ledger 叫
# xiaohongshu）。2026-10-19 体检第一次跑就是在这儿炸的 KeyError ——
# 别的模块习惯用自己的简称，跨模块调 ledger 必须先过这一层。
PLAT_KEY = {"xhs": "xiaohongshu"}


def _file_health(plat):
    """台账文件本身是不是合法 JSON。

    🚨 这是本体检**唯一真能拦车的结构性故障**：`ledger._read_file` 解析失败时
       返回 `entries=None`，`load()` 再把它当成空账 —— 一个写坏的文件不会报错，
       只会让台账凭空归零，然后按「这条没发过」重发一遍。
       （`check_offline` 里 BAD_STATE/EMPTY_ID 那两类 ERROR 其实到不了：
        `load()` 会用文件位置覆盖 state、跳过没 id 的记录。别指望它们。）
    """
    bad = []
    key = PLAT_KEY.get(plat, plat)
    for st in (L.DRAFT, L.PUBLISHED):
        try:
            p = L._path(key, st)
        except ValueError:
            continue                       # 该平台没有这一侧（如公众号）
        except KeyError:
            # 2026-10-26：juejin 接入后不在 ledger._SPEC 里（自带
            # data/juejin_drafts.json，slug→draft_id 字典，schema 不同）。
            # 它的幂等与回读由适配器 verify() 负责（list_drafts 比对
            # draft_id），本地账体检对它无意义 —— 跳过，不是 ERROR。
            return bad
        if not os.path.isfile(p):
            continue
        try:
            _, entries, _, _ = L._read_file(p)
        except Exception as e:                          # noqa: BLE001
            bad.append("%s：%s 读不了（%s）" % (plat, os.path.basename(p),
                                               str(e)[:40]))
            continue
        if entries is None:
            bad.append("%s：%s 不是合法 JSON（现在会被静默读成空账）"
                       % (plat, os.path.basename(p)))
    return bad


def _preflight(plats):
    """发车前的账本体检。**纯本地、毫秒级、不开浏览器。**

    为什么发车前要查一次：往一本已经坏了的账上继续写，只会让「哪条真发了」
    更说不清。而离线档只读本地 json —— 一次文件 IO，比 `status` 还便宜，
    没理由不跑。

    拦截口径分两级，别混：
      · ERROR（文件坏、读不了）⇒ **拦车**。再写只会把错误固化进去；
      · WARN（重复、没远端 ID）⇒ 只提示。这些是历史欠账（比如某条老记录
        缺 appmsgid），拿它当拦车条件会让新车永远发不出去。
    """
    import ledger_health as H
    errs, warns, infos, total = [], [], 0, 0
    for p in plats:
        if PLAT_KEY.get(p, p) not in L.PLATFORMS:
            continue                       # 不进 ledger 体系的平台（juejin），见 _file_health
        errs.extend(_file_health(p))
        try:
            recs = L.load(PLAT_KEY.get(p, p))
        except Exception as e:                          # noqa: BLE001
            errs.append("%s：台账读不了（%s）" % (p, str(e)[:60]))
            continue
        total += len(recs)
        for i in H.check_offline(PLAT_KEY.get(p, p), recs):
            line = "%s / %s %s：%s" % (p, i["id"] or "(空id)",
                                       i["code"], i["detail"])
            if i["level"] == "ERROR":
                errs.append(line)
            elif i["level"] == "WARN":
                warns.append(line)
            else:
                infos += 1        # 产物缺失之类，发车前用不着占版面
    print("[体检] %d 个平台本地台账，%d 条记录" % (len(plats), total))
    if not errs and not warns:
        print("  ✓ 账本结构正常%s" % ("（另 %d 条 INFO：产物缺失等）" % infos
                                      if infos else ""))
        return True
    for w in warns[:8]:
        print("  ⚠ %s" % w)
    if len(warns) > 8:
        print("    …另 %d 条 WARN（ledger_health --verbose 看全）"
              % (len(warns) - 8))
    for e in errs:
        print("  ✗ %s" % e)
    if errs:
        print("  ✗ %d 个 ERROR：账本结构坏了，先修再发"
              "（确认历史欠账可加 --no-preflight 强行发）" % len(errs))
        return False
    return True


def cmd_run(args):
    plats = parse_plats(args.platforms)
    if not getattr(args, "no_preflight", False):
        if not _preflight(plats):
            return 1
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
    if args.all_platforms:
        m.remove(cid)
    else:
        for p in parse_plats(args.platforms):
            m.remove(cid, p)
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
            print("（确认调试 Chrome %d 起着、且已登录该平台）"
                  % V._port_for(p))
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
    # 文档与历史命令都写成 `run --case prosp`，但 argparse 只认位置参数，
    # 于是照文档敲会报 unrecognized arguments（2026-10-19 实测）。
    # 两个都收，谁给用谁。
    p.add_argument("--case", dest="case_opt", default=None,
                   help="案例 id（与位置参数二选一）")
    p.add_argument("--near", type=int, default=0, help="最近 N 条，0=全部")
    p.add_argument("--dry", action="store_true", help="只打印子命令")
    p.add_argument("--yes", action="store_true",
                   help="真公开发布（默认只存草稿）")
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--replace", action="store_true",
                   help="改文案后覆盖重存（已 draft_saved 的也重发）")
    p.add_argument("--no-preflight", action="store_true",
                   help="跳过发车前的本地账本体检（默认跑，毫秒级）")
    p.set_defaults(fn=cmd_run)

    p = _add_plats(sub.add_parser("status", help="各平台状态统计"))
    p.set_defaults(fn=cmd_status)

    p = _add_plats(sub.add_parser("sync", help="把既有平台记录灌进清单（首次用）"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = _add_plats(sub.add_parser("retry", help="重试失败项"))
    p.add_argument("case", nargs="?")
    p.add_argument("--near", type=int, default=0, help="最近 N 条，0=全部")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--replace", action="store_true",
                   help="改文案后覆盖重存（已 draft_saved 的也重发）")
    p.add_argument("--no-preflight", action="store_true",
                   help="跳过发车前的本地账本体检（默认跑，毫秒级）")
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
    # 位置参数与 --case 归一化：后续 cmd_* 只认 args.case
    if getattr(args, "case_opt", None) and not getattr(args, "case", None):
        args.case = args.case_opt
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
