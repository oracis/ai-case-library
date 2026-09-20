#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「材料已齐 + 已补内容包」的候选发布成案例。零 AI 成本。

用法
----
    python scripts/publish_ready.py --dry-run          # 只看要发哪些、会落哪一档
    python scripts/publish_ready.py                    # 真发
    python scripts/publish_ready.py --id 1lookup       # 只发一条

前置：server.py 必须在跑（后台端口 5053）。发布只走
`POST /api/candidates/:id/promote`，规则引擎是唯一闸门 —— 这里不绕过它。

为什么先检查内容包
------------------
promote 只搬运候选上**已有**的字段。候选池只负责证据（数字/来源/口径），
内容（why_it_works / playbook / verdict）得由 contentpack_ready.py 先写进去。
不检查就发，会造出 9-17 那批空壳案例 —— 当时 6 条发完又被退回候选池。

所以在动手之前先挡一道：内容包不齐的条目直接跳过，并告诉人跑哪个脚本补。

自检（--selfcheck）不需要 server 在跑：它把「空内容包会被拦」这条规则本身
钉成断言，免得以后有人图省事绕过这道闸门。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth                                    # noqa: E402
import ai_verify as AV                         # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CAND_PATH = os.path.join(ROOT, "data", "candidates.json")

# 案例正文真正会读的内容字段。缺任何一个，发布出来的都是一张有数字没内容的卡片。
CONTENT_FIELDS = ("what_it_does", "why_it_works", "playbook", "verdict",
                  "how_it_makes_money")
MIN_WHY = 3
MIN_PLAY = 3


def content_gaps(cand):
    """这条候选的内容包还缺什么。返回人话清单（空 = 齐了）。"""
    gaps = []
    if not str(cand.get("what_it_does") or "").strip():
        gaps.append("what_it_does 空")
    if not str(cand.get("verdict") or "").strip():
        gaps.append("verdict 空")
    if not str(cand.get("how_it_makes_money") or "").strip():
        gaps.append("how_it_makes_money 空")
    if len(cand.get("why_it_works") or []) < MIN_WHY:
        gaps.append("why_it_works 不足 %d 条" % MIN_WHY)
    if len(cand.get("playbook") or []) < MIN_PLAY:
        gaps.append("playbook 不足 %d 条（solo_playbook 那 15 分也认它）" % MIN_PLAY)
    return gaps


def select(cands, ids):
    """挑出该发的：指定了 id 就按 id，否则全部；再按内容包过滤。"""
    chosen, skipped = [], []
    for c in cands:
        cid = c.get("id")
        if ids and cid not in ids:
            continue
        gaps = content_gaps(c)
        if gaps:
            skipped.append((cid, gaps))
        else:
            chosen.append(c)
    return chosen, skipped


def no_proxy_opener():
    """后台走 127.0.0.1 —— 必须绕开系统代理，否则被拦成 502（踩过）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def connect():
    secret = auth.get_secret(auth.load_admin())
    cookie = auth.cookie_header(auth.make_token(secret))
    opener = no_proxy_opener()

    def _req(self, method, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Cookie": self.cookie})
        try:
            with opener.open(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8")), e.code
            except Exception:                                   # noqa: BLE001
                return {"error": "HTTP %s" % e.code}, e.code
        except urllib.error.URLError as e:
            raise SystemExit("[!] 连不上后台 %s（%s）—— 先启动 server.py"
                             % (self.base, e.reason))

    AV.Admin._req = _req
    return AV.Admin(AV.ADMIN_BASE, cookie=cookie)


def selfcheck():
    """钉住「内容包不齐不许发」这条规则本身，不依赖 server。"""
    bad = 0

    def check(label, cond, detail=""):
        nonlocal bad
        if cond:
            print("  [OK] %s" % label)
        else:
            bad += 1
            print("  [!!] %s %s" % (label, detail))

    full = {"what_it_does": "x", "verdict": "x", "how_it_makes_money": "x",
            "why_it_works": ["a", "b", "c"], "playbook": ["a", "b", "c"]}
    check("内容齐全 → 无缺口", content_gaps(full) == [])

    for field in CONTENT_FIELDS:
        broken = dict(full)
        if isinstance(broken[field], list):
            broken[field] = []
        else:
            broken[field] = ""
        check("缺 %s → 被拦下" % field, content_gaps(broken) != [])

    for n in (0, 1, 2):
        broken = dict(full, why_it_works=["a"] * n)
        check("why_it_works 只有 %d 条 → 被拦下" % n, content_gaps(broken) != [])
    check("why_it_works 刚好 3 条 → 放行",
          content_gaps(dict(full, why_it_works=["a", "b", "c"])) == [])

    for n in (0, 1, 2):
        broken = dict(full, playbook=["a"] * n)
        check("playbook 只有 %d 条 → 被拦下" % n, content_gaps(broken) != [])

    # select() 必须把不合格的挑出去，而不是静默放过
    good = dict(full, id="good")
    bogus = {"id": "bogus"}
    chosen, skipped = select([good, bogus], None)
    check("select 只放行合格条目",
          [c["id"] for c in chosen] == ["good"] and [s[0] for s in skipped] == ["bogus"])

    # --id 指定到不合格条目时也不能破例
    chosen2, skipped2 = select([bogus], ["bogus"])
    check("--id 指定不合格条目也不放行",
          chosen2 == [] and len(skipped2) == 1)

    print("\n自检：%d 项失败" % bad)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="发布「材料已齐」的候选（零 AI 成本）")
    ap.add_argument("--id", action="append", default=None, help="只发指定候选 id，可多次")
    ap.add_argument("--dry-run", action="store_true", help="只列清单，不真发")
    ap.add_argument("--selfcheck", action="store_true", help="只跑内容包闸门的自检")
    args = ap.parse_args()

    if args.selfcheck:
        raise SystemExit(selfcheck())

    with open(CAND_PATH, encoding="utf-8") as f:
        cands = json.load(f)

    chosen, skipped = select(cands, args.id)
    print("候选池 %d 条：内容包齐全 %d 条，缺内容包 %d 条\n"
          % (len(cands), len(chosen), len(skipped)))

    for cid, gaps in skipped:
        print("  [跳过] %-22s %s" % (cid, "；".join(gaps)))
        print("         补法：python scripts/contentpack_ready.py --apply --id %s" % cid)

    if not chosen:
        print("\n没有可发布的条目。")
        raise SystemExit(0 if skipped else 1)

    for c in chosen:
        print("  [待发] %-22s why=%d play=%d | %s"
              % (c["id"], len(c["why_it_works"]), len(c["playbook"]),
                 (c.get("metrics") or {}).get("headline", "")[:46]))

    if args.dry_run:
        print("\n（--dry-run，没有真的发布）")
        return

    admin = connect()
    print("\n已连接后台 %s\n" % AV.ADMIN_BASE)

    done, failed = [], []
    for c in chosen:
        cid = c["id"]
        d, st = admin.promote(cid)
        if st in (200, 201):
            case = (d or {}).get("case") or {}
            result = (d or {}).get("result") or {}
            done.append((cid, case.get("id"), case.get("tier"),
                         result.get("bonus_score")))
            print("  [发布] %-22s → 案例 %-24s 档位 %-9s 质量分 %s"
                  % (cid, case.get("id"), case.get("tier"),
                     result.get("bonus_score")))
        else:
            failed.append((cid, st, (d or {}).get("error")))
            print("  [被拒] %-22s HTTP %s：%s" % (cid, st, (d or {}).get("error")))

    print("\n发布 %d 条，失败 %d 条" % (len(done), len(failed)))
    if done:
        print("\n下一步：重建静态站 ——")
        print("  python scripts/build_static.py --no-inbox --out public   # 对外")
        print("  python scripts/build_static.py --out dist                # 本地预览")


if __name__ == "__main__":
    main()
