#!/usr/bin/env python3
"""批量晋升「已就绪」的候选进 cases.json（案例库）。

这是晋升链条的最后一环。server 的 promote 端点自己会再把三道闸门各查
一遍（核实规则引擎 / 正文质量 / 证据等级对账）—— 本脚本先做一遍**判据
完全相同**的本地预检（同一个 verify_rules），只是为了别让批量跑到一半
被 409 打断，而不是要绕过谁。预检不过的条目一律不碰。

就绪判据（与 server promote 一致）：
  1. 候选还在 candidates.json 里，且没进过 cases.json；
  2. verifications.json 里有它的核实草稿，且 VR.evaluate() 判 publishable；
  3. VR.content_gate() 过（正文四硬字段写齐）。

用法：
    python scripts/promote_ready.py --ready          # 晋升全部预检通过的
    python scripts/promote_ready.py --ready --dry    # 只预检，不晋升
    python scripts/promote_ready.py --ids a,b,c      # 只晋升指定的（照样先预检）

注意：promote 成功后 server 会把该候选的核实草稿删掉（用完即清），
并把候选从 candidates.json 移进 cases.json —— 这是幂等设计，重跑不会重复入。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# 连 127.0.0.1 必须绕开系统代理，否则请求会被拦成 502（本机老坑）。
_np = os.environ.get("no_proxy", "")
if "127.0.0.1" not in _np:
    os.environ["no_proxy"] = (_np + ",127.0.0.1,localhost").lstrip(",")

import auth                                                  # noqa: E402
import ai_verify as AV                                       # noqa: E402
import verify_rules as VR                                    # noqa: E402

ADMIN_BASE = os.environ.get("CASE_LIB_ADMIN_BASE", "http://127.0.0.1:5053")

# 正文四硬字段（与 verify_rules.CONTENT_FIELDS 的 blocking 项对应）
CONTENT_FIELDS = ["what_it_does", "how_it_makes_money", "verdict", "why_it_works"]


def load(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def precheck(cid, cands, vers, case_ids):
    """返回 (candidate, result, None) 或 (None, None, 原因)。"""
    cand = next((x for x in cands if x.get("id") == cid), None)
    if cand is None:
        return None, None, "候选不存在（可能已晋升）"
    if cid in case_ids:
        return None, None, "已在 cases.json"
    draft = vers.get(cid)
    if draft is None:
        return None, None, "没有核实草稿（先跑 verify_cookie.py）"
    result = VR.evaluate(draft)
    if not result["publishable"]:
        if result["gates_failed"]:
            why = "；".join(g["label"] for g in result["gates_failed"])
        else:
            why = "；".join(m["label"] for m in result["missing"])
        return None, None, "规则引擎不过：" + why
    cg = VR.content_gate(cand)
    if not cg["ok"]:
        why = "；".join(p["label"] for p in cg["blocked"])
        return None, None, "正文闸门不过：" + why
    return cand, result, None


def main():
    ap = argparse.ArgumentParser(description="批量晋升就绪候选进案例库")
    ap.add_argument("--ids", default="",
                    help="逗号分隔的候选 id；不给则配合 --ready 用")
    ap.add_argument("--ready", action="store_true",
                    help="晋升所有本地预检通过的候选")
    ap.add_argument("--dry", action="store_true", help="只预检，不真正晋升")
    args = ap.parse_args()

    cands = load("candidates.json")
    vers = load("verifications.json")
    cases = load("cases.json")
    case_ids = {c.get("id") for c in cases}

    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    elif args.ready:
        ids = [c.get("id") for c in cands if c.get("id")]
    else:
        ap.error("要么给 --ids，要么给 --ready")

    print("候选池 %d 条｜案例库 %d 条｜待检查 %d 条\n" % (len(cands), len(cases), len(ids)))

    ready, blocked = [], []
    for cid in ids:
        cand, result, why = precheck(cid, cands, vers, case_ids)
        if why:
            blocked.append((cid, why))
        else:
            m = cand.get("metrics", {}) or {}
            rev = max(m.get("mrr") or 0, m.get("last_30d_revenue") or 0)
            ready.append((cid, result["tier"], rev))

    print("== 预检通过 %d 条 ==" % len(ready))
    for cid, tier, rev in sorted(ready, key=lambda x: -x[2]):
        print("  %-30s tier=%-8s 门面收入 $%s" % (cid, tier, format(rev, ",.0f")))
    if blocked:
        print("\n== 预检不过 %d 条（不碰）==" % len(blocked))
        for cid, why in blocked:
            print("  %-30s %s" % (cid, why))

    if args.dry or not ready:
        print("\n（dry 模式或无就绪条目，未晋升）")
        return 0

    # 自签 cookie：secret 就在 data/admin.json，make_token 是纯函数，
    # 不碰明文口令、不掉登录态（与 verify_cookie.py 同一手法）。
    cfg = auth.load_admin()
    cookie = auth.cookie_header(auth.make_token(cfg["secret"]))
    admin = AV.Admin(ADMIN_BASE, cookie=cookie)

    print("\n== 开始晋升（%s）==" % ADMIN_BASE)
    ok, fail = 0, 0
    for cid, _tier, _rev in sorted(ready, key=lambda x: -x[2]):
        data, st = admin.promote(cid)
        if st == 201 and data.get("ok"):
            ok += 1
            print("  [✓] %-30s -> %s（tier=%s）" % (
                cid, (data.get("case") or {}).get("id"),
                (data.get("case") or {}).get("tier")))
        else:
            fail += 1
            print("  [✗] %-30s HTTP %s：%s" % (cid, st, data.get("error")))

    print("\n晋升完成：成功 %d，失败 %d" % (ok, fail))
    cases2 = load("cases.json")
    print("案例库现有 %d 条" % len(cases2))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
