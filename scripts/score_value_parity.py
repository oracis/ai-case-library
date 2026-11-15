#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分「数值」层面的常驻一致性判据（PRD #185）。

分工（与 score_note_policy 互补）
--------------------------------
    score_note_policy  → 文案（china_fit.note / solo_fit.delivery_note）：库是唯一真值
    score_value_parity → 数值（dims / raw / score / blocker / weights）：表是人工判断源头

权威方向（⛔ 与文案相反，别照搬 #184 去删表）
------------------------------------------------
    SCORES / DELIVERY 是人工逐条填的判断表，是数值的唯一源头；
    case_library.china_fit / solo_fit 是跑分脚本算出来的**派生结果**。
    改分数 ⇒ 改表，然后重跑两个评分脚本。直接在库里改数值不会有任何脚本
    发现，直到某次跑分把库刷回表值（或反过来，表删了条目后库被 preserve 住），
    这就是 #184 收尾时留下的「数值层面仍可能有不同步」。

11-06 已裁定口径（本判据钉住它，不许改）
------------------------------------------
    表里有   → 人工填表优先，库里的数值必须等于表重算结果
    表无     → 按全库中位数兜底 + provisional（统计量，不是人工结论）
    表无但库里已有非兜底判断 → preserve（合法，只重排 rank，计 WARNING 不算错）

判据只管**数值**，不碰文案；纯函数、只读、不写盘。
文案归 score_note_policy，别在这里做第二套裁定。

用法
----
    python scripts/score_value_parity.py --check    # 只读体检，rc=0 全绿 / 1 有错
    python scripts/score_value_parity.py --json     # 机器可读结果
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import score_china_fit as CF       # noqa: E402
import score_solo_fit as SF        # noqa: E402


# ---------------------------------------------------------------- 期望值计算
def expected_china(s):
    """SCORES 里一条人工判断 → 库里 china_fit 该有的数值字段。"""
    dims = {k: s[k] for k, _label, _desc in CF.DIMS}
    raw = CF.weighted_total(dims)
    return {
        "dims": dims,
        "raw": raw,
        "score": CF.to_score(raw),
        "blocker": s.get("blocker"),
        "weights": CF.WEIGHTS,
        "max_raw": CF.MAX_RAW,
    }


def expected_solo(case, delivery_score):
    """DELIVERY 一条 + replicability 四维 → 库里 solo_fit 该有的数值字段。

    返回 None 表示 replicability 四维不全，solo_fit 本来就写不出来。
    """
    rep = case.get("replicability") or {}
    dims = {}
    for key, src in SF.INVERT.items():
        v = rep.get(src)
        if not isinstance(v, int):
            return None
        dims[key] = 6 - v
    dims["delivery"] = delivery_score
    raw = round(sum(dims[k] * SF.SOLO_WEIGHTS[k] for k in SF.SOLO_WEIGHTS), 1)
    return {
        "dims": dims,
        "raw": raw,
        "score": SF.to_score(raw, SF.SOLO_MAX),
        "weights": SF.SOLO_WEIGHTS,
        "max_raw": SF.SOLO_MAX,
    }


def expected_fallback_china(fb=None):
    fb = fb if fb is not None else CF.fallback_dims()
    raw = CF.weighted_total(fb)
    return {"dims": fb, "raw": raw, "score": CF.to_score(raw)}


def expected_fallback_solo(case, fd=None):
    fd = fd if fd is not None else SF.fallback_delivery()
    return expected_solo(case, fd[0])


# ---------------------------------------------------------------- 主判据
def audit(cases, scores=None, delivery=None, strict_fallback=True):
    """逐条核对表数值 ↔ 库数值，返回结构化结果（只读，不写盘）。

    :param cases: 案例 list（真值快照；单测可传构造数据）
    :param scores / delivery: 可注入的判断表（单测用），默认取模块里的常驻表
    :param strict_fallback: 缺表 provisional 案例是否校验兜底值与当前中位数一致
    :returns: dict(ok, errors, warnings, stats)
    """
    S = CF.SCORES if scores is None else scores
    D = SF.DELIVERY if delivery is None else delivery
    fd = SF.fallback_delivery()
    exp_fb_china = expected_fallback_china()
    errors, warnings = [], []
    stats = {
        "cases": len(cases),
        "scores_entries": len(S),
        "delivery_entries": len(D),
        "table_scored": 0,
        "fallback_provisional": 0,
        "preserved": 0,
        "provisional_china": 0,
        "provisional_solo": 0,
    }
    case_ids = {c.get("id") for c in cases}

    def err(kind, cid, detail):
        errors.append({"kind": kind, "case_id": cid, "detail": detail})

    def warn(kind, cid, detail):
        warnings.append({"kind": kind, "case_id": cid, "detail": detail})

    # ① 幽灵条目：表里有、库里没有。它参与中位数统计，会让兜底口径虚报样本数。
    for cid in sorted(set(S) - case_ids):
        err("ghost_in_scores", cid, "SCORES 有此 id，case_library 里没有")
    for cid in sorted(set(D) - case_ids):
        err("ghost_in_delivery", cid, "DELIVERY 有此 id，case_library 里没有")

    # ② 两张表覆盖必须一致，否则同一条案例会出现「一边人工、一边兜底」的错位。
    for cid in sorted((set(S) ^ set(D)) & case_ids):
        err("table_coverage_mismatch", cid,
            "只在 %s 里有" % ("SCORES" if cid in S else "DELIVERY"))

    prov_c, prov_s = set(), set()

    for c in cases:
        cid = c.get("id")
        cf = c.get("china_fit") or {}
        sf = c.get("solo_fit") or {}
        if not cf:
            err("missing_china_fit", cid, "库里没有 china_fit")
        if not sf:
            err("missing_solo_fit", cid, "库里没有 solo_fit")
        if cf.get("provisional"):
            prov_c.add(cid)
            stats["provisional_china"] += 1
        if sf.get("provisional"):
            prov_s.add(cid)
            stats["provisional_solo"] += 1

        in_s, in_d = cid in S, cid in D
        if in_s and in_d:
            stats["table_scored"] += 1
            # 表里有 ⇒ 人工判断优先，库里必须等于表重算；provisional 是矛盾状态。
            if cf.get("provisional"):
                err("provisional_with_table_china", cid,
                    "SCORES 里有人工判断，库里却标了 provisional")
            if sf.get("provisional"):
                err("provisional_with_table_solo", cid,
                    "DELIVERY 里有人工判断，库里却标了 provisional")
            for key, want in expected_china(S[cid]).items():
                if cf.get(key) != want:
                    err("china_value_drift", cid,
                        "%s: 库=%r 表重算=%r" % (key, cf.get(key), want))
            exp_s = expected_solo(c, D[cid][0])
            if exp_s is None:
                err("solo_missing_replicability", cid,
                    "DELIVERY 有判断但 replicability 四维不全，库里不该有完整 solo_fit")
            else:
                for key, want in exp_s.items():
                    if sf.get(key) != want:
                        err("solo_value_drift", cid,
                            "%s: 库=%r 表重算=%r" % (key, sf.get(key), want))

        elif not in_s and not in_d:
            # 缺表 ⇒ 11-06 中位数兜底。库里已有非兜底判断是合法的 preserve。
            if cf.get("provisional") or not cf.get("score"):
                stats["fallback_provisional"] += 1
                if strict_fallback:
                    for key, want in exp_fb_china.items():
                        if cf.get(key) != want:
                            err("fallback_drift_china", cid,
                                "%s: 库=%r 当前中位数兜底=%r" % (key, cf.get(key), want))
            else:
                stats["preserved"] += 1
                warn("preserve_china", cid,
                     "表里没有但库里有非兜底判断，跑分会 preserve（11-06 合法）")

            if sf.get("provisional") or not sf.get("score"):
                if strict_fallback:
                    exp_s = expected_fallback_solo(c, fd)
                    if exp_s is None:
                        err("fallback_missing_replicability", cid,
                            "兜底案例 replicability 四维不全")
                    else:
                        for key, want in exp_s.items():
                            if sf.get(key) != want:
                                err("fallback_drift_solo", cid,
                                    "%s: 库=%r 当前中位数兜底=%r"
                                    % (key, sf.get(key), want))
            else:
                warn("preserve_solo", cid,
                     "表里没有但库里有非兜底判断，跑分会 preserve（11-06 合法）")

    # ③ 两轴的 provisional 集合必须一致：一边人工一边兜底会让四象限口径撕裂。
    if prov_c != prov_s:
        for cid in sorted(prov_c ^ prov_s):
            err("provisional_set_mismatch", cid,
                "china=%s solo=%s" % (cid in prov_c, cid in prov_s))

    # ④ 兜底文案里的样本数必须诚实（只查生成器输出，不碰库里文案）。
    #    ⚠ 基准取**生成器自己依据的常驻表条目数**，不随本次注入的 cases 变化：
    #    fallback_note / fallback_delivery 读的是模块级 SCORES / DELIVERY，
    #    若拿注入的小样本 cases 去比，单测会全量误报。库外幽灵条目由 ① 单独抓。
    #    ⚠ 用正则抽「全库 N 条」再比，不用 `str(N) in text`：分数、维度里也可能
    #    出现同一个数字，子串命中会假绿。
    lib_s, lib_d = len(CF.SCORES), len(SF.DELIVERY)
    pat = re.compile(r"全库\s*(\d+)\s*条")
    for label, text, want in (
        ("china", CF.fallback_note(CF.fallback_dims()), lib_s),
        ("solo", fd[1], lib_d),
    ):
        m = pat.search(text or "")
        if not m:
            err("fallback_sample_count_%s" % label, None,
                "兜底文案没写明样本数，无法核对")
        elif int(m.group(1)) != want:
            err("fallback_sample_count_%s" % label, None,
                "兜底文案写 %s 条，库内真实人工判断只有 %d 条（虚报，通常是库外幽灵条目混入）"
                % (m.group(1), want))

    # ⑤ 库里 provisional 文案是历史快照（写死当时的样本数），归 #184 文案裁定口，
    #    跑分时会由 resolve_note 用当前初值刷新。这里只如实统计，不判错、不改写。
    stale = [c.get("id") for c in cases
             if ((c.get("solo_fit") or {}).get("delivery_note") or "").find("全库 ") >= 0
             and pat.search((c.get("solo_fit") or {}).get("delivery_note") or "")
             and int(pat.search((c.get("solo_fit") or {}).get("delivery_note")).group(1)) != lib_d]
    stats["stale_fallback_note_solo"] = len(stale)
    if stale:
        warn("stale_fallback_note_sample", None,
             "%d 条 provisional 文案仍写着旧样本数（%s…）；这是历史快照，"
             "下次跑分由 resolve_note 自动刷新，非数值不同步"
             % (len(stale), ", ".join(stale[:3])))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def load_cases():
    import case_store as CS
    return CS.load_cases()


def main():
    ap = argparse.ArgumentParser(description="评分数值一致性判据（只读）")
    ap.add_argument("--check", action="store_true", help="只读体检（默认）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    result = audit(load_cases())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    s = result["stats"]
    print("案例 %d 条 | SCORES %d 条 | DELIVERY %d 条"
          % (s["cases"], s["scores_entries"], s["delivery_entries"]))
    print("  表里有人工判断 : %d 条" % s["table_scored"])
    print("  缺表走中位数兜底: %d 条（provisional china %d / solo %d）"
          % (s["fallback_provisional"], s["provisional_china"], s["provisional_solo"]))
    print("  库里真判断被 preserve: %d 条" % s["preserved"])
    print()
    if result["errors"]:
        print("[FAIL] 数值不一致 %d 处：" % len(result["errors"]))
        for e in result["errors"][:30]:
            print("  · %-30s %s | %s" % (e["kind"], e["case_id"], e["detail"]))
        if len(result["errors"]) > 30:
            print("  · … 另有 %d 处" % (len(result["errors"]) - 30))
    else:
        print("[OK] 表 ↔ 库数值逐键一致，缺表案例按当前中位数兜底，无幽灵条目。")
    for w in result["warnings"]:
        print("[i] %s：%s" % (w["kind"], w["detail"]))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
