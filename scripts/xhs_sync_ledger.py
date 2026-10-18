#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书台账对账：拿【线上真实已发布列表】回填 `data/xhs_published.json`。

## 为什么需要

台账是 `publish` 的幂等判据（`_published_ids()` 命中就不再发）。记漏了会
重复发布 —— 2026-10-18 实测就是：线上 **9 条**，台账只有 **2 条**，其中
「月收$128K：销售挖客户线索的工具」在 09-29 发了**两次**。

两份数据天然会漂，因为发布路径不止一条：
  · 脚本发（会 `mark_published`）
  · 人工从草稿箱点发布（不写台账）
  · 手机上直接发（不写台账）

所以对账不能靠「谁改的」，只能靠**线上事实反查**。本工具就是干这个的。

## 标题 → case id 怎么认（三层，越靠前越可信）

1. `out/xhs/<cid>/note.json` 的 `title` —— 脚本自己生成的卡片标题，最准
2. `data/xhs_title_overrides.json` 的值 —— 人工改过的标题
3. 兜底打分：产品名命中 > 金额口径命中 > 正文二元组重叠

⚠ **老标题（含「月收$X」的）全部匹配不到第 1 层** —— 10-14 定了「标题禁金额」
红线，`out/xhs/*/note.json` 里现在的标题是新口径，而线上老笔记还是旧标题。
这类只能靠第 3 层或 `data/xhs_aliases.json` 人工固化。

## 用法

    python -X utf8 scripts/xhs_sync_ledger.py            # 只对账，不改文件
    python -X utf8 scripts/xhs_sync_ledger.py --verbose  # 带每条匹配依据
    python -X utf8 scripts/xhs_sync_ledger.py --apply    # 写回（自动备份）

`--apply` 前会写 `data/xhs_published.json.bak-<时间戳>`，写错可秒回滚。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LEDGER = os.path.join(ROOT, "data", "xhs_published.json")
ALIASES = os.path.join(ROOT, "data", "xhs_aliases.json")
OVERRIDES = os.path.join(ROOT, "data", "xhs_title_overrides.json")
CARDS = os.path.join(ROOT, "out", "xhs")
CASES = os.path.join(ROOT, "data", "cases.json")

MONEY_RE = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)([KkMm]?)(?![A-Za-z])")


def _read_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def norm(s):
    """去空白 + 全角冒号统一，标题比对只用这个。"""
    return re.sub(r"\s+", "", (s or "").replace("：", ":"))


def money_tokens(s):
    """抽出金额口径，统一成 (数值, 量级) 便于跨写法比对。

    「$1.6K」「$1,600」「月收$1.6k」→ 同一组 (1600, 1)
    """
    out = set()
    for m in MONEY_RE.finditer(s or ""):
        try:
            v = float(m.group(1).replace(",", ""))
        except Exception:
            continue
        unit = (m.group(2) or "").lower()
        if unit == "k":
            v *= 1000
        elif unit == "m":
            v *= 1000000
        out.add(round(v, 2))
    return out


def bigrams(s):
    """中英混排的粗粒度相似度特征：只取汉字/字母数字的相邻二元组。"""
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "").lower()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def load_card_titles():
    """{title_norm: cid}，来自 out/xhs/<cid>/note.json。"""
    m = {}
    if not os.path.isdir(CARDS):
        return m
    for d in sorted(os.listdir(CARDS)):
        f = os.path.join(CARDS, d, "note.json")
        if not os.path.isfile(f):
            continue
        t = (_read_json(f, {}) or {}).get("title")
        if t:
            m.setdefault(norm(t), d)
    return m


def load_override_titles():
    ov = _read_json(OVERRIDES, {}) or {}
    return {norm(v): k for k, v in ov.items() if isinstance(v, str)}


def load_cases():
    d = _read_json(CASES, [])
    return d if isinstance(d, list) else []


def load_aliases():
    """{note_id 或 标题: case id}。人工固化的映射，优先级最高。"""
    return _read_json(ALIASES, {}) or {}


def resolve(title, note_id, ctx):
    """标题 → (cid, method, score)。匹配不到返回 (None, None, 0)。"""
    n = norm(title)
    hit = ctx["aliases"].get(note_id) or ctx["aliases"].get(title) or ctx["aliases"].get(n)
    if hit:
        return hit, "alias", 100
    if n in ctx["cards"]:
        return ctx["cards"][n], "card_title", 95
    if n in ctx["overrides"]:
        return ctx["overrides"][n], "override", 90

    t_money = money_tokens(title)
    t_bi = bigrams(title)
    best, best_score, best_cid = None, 0.0, None
    for c in ctx["cases"]:
        cid = c.get("id")
        if not cid:
            continue
        name = (c.get("name") or "").strip()
        blob = " ".join(str(c.get(k) or "") for k in ("name", "one_liner"))
        blob += " " + json.dumps(c.get("metrics") or {}, ensure_ascii=False)
        score = 0.0
        # 产品名直接在标题里（Kibu / Mort / Outrank…），命中最可靠
        if name and len(name) >= 3 and name.lower() in (title or "").lower():
            score += 40
        m_money = money_tokens(blob)
        if t_money and (t_money & m_money):
            score += 25
        bi = t_bi & bigrams(blob)
        score += min(20.0, float(len(bi)))
        if score > best_score:
            best, best_score, best_cid = cid, score, c
    # 阈值：低于 25 基本等于瞎猜，宁可报「认不出」让人来看
    if best_score < 25:
        return None, None, best_score
    return best, "score", best_score


def top_candidates(title, ctx, k=3):
    t_money = money_tokens(title)
    t_bi = bigrams(title)
    rows = []
    for c in ctx["cases"]:
        blob = " ".join(str(c.get(k) or "") for k in ("name", "one_liner"))
        blob += " " + json.dumps(c.get("metrics") or {}, ensure_ascii=False)
        s = min(20.0, float(len(t_bi & bigrams(blob))))
        if t_money & money_tokens(blob):
            s += 25
        if s > 0:
            rows.append((s, c.get("id"), (c.get("name") or "")[:20]))
    rows.sort(reverse=True)
    return rows[:k]


def local_entries():
    d = _read_json(LEDGER, [])
    if isinstance(d, dict):
        return [{"id": k, "at": v} for k, v in d.items()]
    return [e for e in d if isinstance(e, dict) and e.get("id")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回台账（先备份）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import xhs_webapi as X
    notes, _ = X.posted_notes(pages=args.pages)

    ctx = {"aliases": load_aliases(), "cards": load_card_titles(),
           "overrides": load_override_titles(), "cases": load_cases()}
    local = local_entries()
    local_ids = [e["id"] for e in local]

    rows, unresolved = [], []
    for n in notes:
        title = n.get("title") or ""
        cid, how, score = resolve(title, n.get("note_id"), ctx)
        row = {"note_id": n.get("note_id"), "title": title, "time": n.get("time"),
               "likes": n.get("likes"), "views": n.get("view_count"),
               "cid": cid, "how": how, "score": round(score, 1)}
        rows.append(row)
        if not cid:
            row["candidates"] = top_candidates(title, ctx)
            unresolved.append(row)

    # 同一 case 发过多次 = 重复发布（本条后面的都标记）
    seen, dup = {}, []
    for r in sorted(rows, key=lambda x: x["time"] or ""):
        cid = r["cid"]
        if not cid:
            continue
        if cid in seen:
            r["dup_of"] = seen[cid]
            dup.append(r)
        else:
            seen[cid] = r["note_id"]

    online_ids = {r["cid"] for r in rows if r["cid"]}
    missing_local = sorted(online_ids - set(local_ids))     # 线上有、台账没有
    ghost_local = sorted(set(local_ids) - online_ids)       # 台账有、线上没有

    if args.json:
        print(json.dumps({"online": len(notes), "local": len(local),
                          "rows": rows, "unresolved": unresolved,
                          "missing_local": missing_local,
                          "ghost_local": ghost_local,
                          "dup": [{"cid": d["cid"], "title": d["title"],
                                   "time": d["time"]} for d in dup]},
                         ensure_ascii=False, indent=1))
    else:
        print("线上 %d 条 / 台账 %d 条" % (len(notes), len(local)))
        print("-" * 66)
        for r in sorted(rows, key=lambda x: x["time"] or ""):
            tag = r["cid"] or "??"
            flag = " ⚠重复" if r.get("dup_of") else ""
            if args.verbose:
                print("  %s  %-24s %-22s %s(%s)%s"
                      % (r["time"], tag, (r["title"] or "")[:22], r["how"], r["score"], flag))
            else:
                print("  %s  %-24s %s%s" % (r["time"], tag, (r["title"] or "")[:34], flag))
        if unresolved:
            print("\n认不出的标题（不会写进台账，需人工确认）：")
            for r in unresolved:
                print("  %s  %r" % (r["time"], r["title"]))
                for s, cid, nm in r["candidates"]:
                    print("      ? 候选 %-24s %s  score=%s" % (cid, nm, round(s, 1)))
        print("\n差额：")
        print("  需补进台账：%s" % (", ".join(missing_local) or "无"))
        print("  台账里有但线上没有：%s" % (", ".join(ghost_local) or "无"))
        if dup:
            print("  重复发布：%s" % "; ".join("%s @%s" % (d["cid"], d["time"]) for d in dup))

    if not args.apply:
        if not args.json:
            print("\n（只对账，未改文件。确认无误后加 --apply 写回）")
        return 0

    if unresolved:
        print("\n✗ 有 %d 条标题认不出，拒绝写回（先补 data/xhs_aliases.json）"
              % len(unresolved))
        return 3

    now = time.strftime("%Y-%m-%d %H:%M")
    old_by_id = {e["id"]: e for e in local}
    out = []
    for r in sorted(rows, key=lambda x: x["time"] or ""):
        prev = old_by_id.get(r["cid"]) or {}
        e = {"id": r["cid"], "at": r["time"], "note_id": r["note_id"],
             "title": r["title"]}
        if r.get("dup_of"):
            e["note"] = "%s 接口对账回填；⚠ 与 %s 重复发布" % (now, r["dup_of"][:8])
        elif prev.get("note"):
            e["note"] = prev["note"]
        else:
            e["note"] = "%s 接口对账回填（线上已发布）" % now
        out.append(e)

    shutil.copy2(LEDGER, "%s.bak-%s" % (LEDGER, time.strftime("%Y%m%d-%H%M%S")))
    with open(LEDGER, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n✓ 已写回 %s：%d 条（备份在同目录 .bak-*）" % (LEDGER, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
