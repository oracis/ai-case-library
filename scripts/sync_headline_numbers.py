#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 candidates.json 里写死的收入数字同步成实拉现值。

为什么需要这个脚本
------------------
`audit_delisted.py --write` 只回写**结构化数字**（metrics.mrr /
metrics.all_time / metrics.subscriptions / data_freshness），
但 `metrics.headline` 与 `metrics.metric_note` 是**写死的字符串**——
里面同样有 MRR / 近30天 / 累计 / 订阅数 / 同步日期。

结果就是：字段已经是今天实测值，文案还停在几天前的快照，
**同一行里自相矛盾**（"MRR $2,039" 配着 mrr=4251）。下游的
`daily_picks.py` / 后台列表 / 写正文的模型都直接读 headline ⇒
会把过期数字当现值抄走。这是红线「收入字段是快照不是现值，
一律回源后再入库」的最后一环没闭上。

所以本脚本做**数值同步**，不重新判定口径：
  · 只替换字符串里**已知的旧数字** → 新数字（按字段一一对应）
  · 叙述、结论、⚠ 警告、日期措辞一律不动
  · 替换前先核对：字符串里的旧值必须**恰好等于**该字段的旧值，
    对不上就跳过并在报告里点名（宁可漏改，不可错改）

用法
----
    python scripts/sync_headline_numbers.py --dry-run   # 只看会改什么（默认）
    python scripts/sync_headline_numbers.py --write      # 实际写入
    python scripts/sync_headline_numbers.py --write --ids garagelab,lunarbuild

⚠ 读写统一走 `candidate_store`（2026-11-06）：格式按磁盘字节探测后
原子落盘。旧版这里是原地截断写（无 fsync / os.replace），被中断会留半截 JSON。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import candidate_store as CSt  # noqa: E402  候选池唯一读写入口

# 字段 → 字符串里对应的标签。顺序有意义：先长后短，避免 "$1,400" 被
# "1,400" 之类的短模式先吃掉。
FIELD_TAGS = [
    ("mrr", "MRR"),
    ("last_30d_revenue", "近30天收入"),
    ("all_time", "累计"),
    ("subscriptions", "付费订阅"),
]


def fmt_money(v):
    """金额的字符串形态：$1,351（千分位，无小数）。"""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if float(v).is_integer():
        return "${:,}".format(int(v))
    return "${:,.2f}".format(float(v))


def fmt_int(v):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return "{:,}".format(int(v))


def fmt_pct(v):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    s = "{:+.1f}".format(float(v))
    return s.rstrip("0").rstrip(".") + "%" if s.endswith(".0") else s


def variants(v):
    """一个数值在文案里可能出现的几种写法。"""
    out = set()
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return out
    iv = int(v) if float(v).is_integer() else None
    if iv is not None:
        out.add("{:,}".format(iv))          # 1,351
        out.add(str(iv))                     # 1351
    else:
        out.add("{:,.2f}".format(v))         # 1,351.25
        out.add(str(v))
    return out


# 文案里「数字前面那个词」→ 该数字应该来自哪个字段。
# 🚨 为什么必须有这张表（2026-11-01 实测踩到）：
# 只靠「旧值相等」来定位数字是不够的 —— 当两个字段的旧值**恰好相同**
# （bobby: last30=1,436 且 all_time=1,436），按 all_time 去替换 headline
# 里那个数，就把「近 30 天收入」写成了累计口径（$1,666）。
# 所以替换必须**先看文案里那个数的口径标签**，再决定用哪个字段的新值。
#
# 🚨 标签本身也要精确（11-01 第二轮踩到）：
#   · 「MRR」不能匹配「近 30 天 MRR 增速 +12%」—— 增速不是 MRR 金额
#   · 「MRR」不能匹配「历史峰值 $4,251」—— 峰值不是当前 MRR
#   · 「付费订阅 52 户」的数字后面跟着「户」，不能被当成别的数的前缀
# 所以每条规则都带**否定后缀**（这些词后面出现的同名词不是金额），
# 且数字用**边界断言**卡住（前后不能再接数字/逗号/小数点）。
LABEL_FIELD = [
    (r"MRR(?!\s*(?:增速|增长|环比|同比|历史|峰值|台阶))", "mrr"),
    (r"近\s*30\s*天(?:\s*已验证)?\s*(?:收入|流水|收款)", "last_30d_revenue"),
    (r"累计(?:收入)?", "all_time"),
    (r"付费订阅", "subscriptions"),
]

# 这些词后面出现的金额是**历史值**，不是当前值 —— 一律不动。
# 🚨 「当时」尤其要拦：metric_note 末尾常有
#   「⚠ 队列缓存值是 2026-10-22 采集时的快照（当时 $1,400），与上述实测不同」
# 那个 $1,400 指的是**10-22 采集那一刻**的值，跟今天的实测无关 ——
# 把它同步成今天的新值，句子就变成「当时 $1,351」，语义直接坏掉。
HISTORY_CUES = ("历史峰值", "峰值", "台阶", "当时", "缓存值", "上次", "快照",
                "采集时")


def labelled_numbers(text):
    """找出文案里「带口径标签的数字」→ [(字段, 数字字符串, 起, 止)]。

    只认「数字紧跟在标签后面」的情况（`MRR $1,400` / `近 30 天收入 $500` /
    `累计 $9,508` / `付费订阅 52 户`）。数字用前后边界断言卡住，
    避免抓到别的前缀（`107` vs `1077`）。
    """
    out = []
    for pat, field in LABEL_FIELD:
        for m in re.finditer(
                pat + r"[^\d$]{0,12}\$?(\d[\d,]*(?:\.\d+)?)(?!\d)", text):
            s, e = m.start(1), m.end(1)
            # 前面紧邻数字/小数点 → 是别的前缀，不认
            if s > 0 and (text[s - 1].isdigit() or text[s - 1] in ",."):
                continue
            ctx = text[max(0, s - 24):s]
            if any(cue in ctx for cue in HISTORY_CUES):
                continue
            out.append((field, m.group(1), s, e))
    return out


def _num_eq(num_str, value):
    """文案里的数字串是否等于某个数值（容忍千分位与小数）。"""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return abs(float(num_str.replace(",", "")) - float(value)) < 0.005
    except ValueError:
        return False


def sync_text(text, old_map, new_map):
    """把 text 里的旧数字按**口径标签**换成新数字。

    两条规则：
      1. 优先按口径标签定位（`MRR $2,039` 用 mrr 的新值）——
         否则两个字段旧值相同时会写错口径（bobby 踩过）。
      2. 标签定位不到的裸数字（无标签上下文）才退回「旧值相等」匹配，
         且必须**恰好等于**某字段旧值才替换，对不上就留着（宁可漏改）。
    """
    if not text:
        return text, []
    out = text
    hits = []
    # 标签定位：按位置从后往前替换，避免偏移错乱
    labelled = sorted(labelled_numbers(text), key=lambda t: t[2], reverse=True)
    covered = []
    for field, num, s, e in labelled:
        new = new_map.get(field)
        if new is None:
            continue
        # ⚠ 判据是「**文案里的数字** != 该字段的新值」，而不是
        # 「字段旧值 == 新值」。后者会漏改这种情形：某字段这次没变，
        # 但文案里的数是上一轮错改出来的（bobby: last30 新旧都是 1,436，
        # 文案里却是 1,666 —— 那是上轮按 all_time 错改的残留）。
        if _num_eq(num, new):
            covered.append((s, e))
            continue
        nv = sorted(variants(new), key=len, reverse=True)
        if not nv:
            continue
        out = out[:s] + nv[0] + out[e:]
        hits.append((field, num, nv[0]))
        covered.append((s, e))

    # 裸数字：只替换仍等于某字段旧值的
    for field in ("mrr", "last_30d_revenue", "all_time", "subscriptions"):
        old = old_map.get(field)
        new = new_map.get(field)
        if old is None or new is None or old == new:
            continue
        for ov in sorted(variants(old), key=len, reverse=True):
            pat = re.compile(r"(?<![\d,.])" + re.escape(ov) + r"(?![\d,.])")
            m = pat.search(out)
            if not m:
                continue
            if any(m.start() >= a and m.end() <= b for a, b in covered):
                break                       # 已被标签定位处理过
            # 🚨 历史语境里的裸数字不动：「（当时 $1,400）」「历史峰值 $2,057」
            # 指的是过去某个时点的值，不是当前值。
            ctx = out[max(0, m.start() - 24):m.start()]
            if any(cue in ctx for cue in HISTORY_CUES):
                break
            nv = sorted(variants(new), key=len, reverse=True)[0]
            out = out[:m.start()] + nv + out[m.end():]
            hits.append((field, ov, nv))
            break
    return out, hits


def build_old_new(rec):
    """从 metrics 推出（字段 → (旧值, 新值)）。旧值来自备份，新值来自当前字段。"""
    m = rec.get("metrics") or {}
    # 新值 = 当前文件里的值；旧值由调用方从备份注入（这里返回占位）
    return {f: (None, m.get(f)) for f, _ in FIELD_TAGS}


def main():
    ap = argparse.ArgumentParser(description="同步 candidates.json 里的写死数字")
    ap.add_argument("--ids", default="", help="逗号分隔的 id；留空 = 全部")
    ap.add_argument("--backup", default="", help="旧值来源（备份 candidates.json）；留空=用当前值当旧值")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    rows = CSt.load_candidates()

    ids = {x.strip() for x in args.ids.split(",") if x.strip()}

    old_rows = None
    if args.backup:
        with open(args.backup, encoding="utf-8") as f:
            old_rows = {c.get("id"): c for c in json.load(f)}
    old_by_id = old_rows or {}

    changed, skipped = [], []
    for rec in rows:
        rid = rec.get("id")
        if ids and rid not in ids:
            continue
        m = rec.get("metrics") or {}
        oldrec = old_by_id.get(rid)
        om = (oldrec or {}).get("metrics") or {}
        if not om:
            skipped.append((rid, "没有旧值来源（未给 --backup），跳过"))
            continue

        # old_map/new_map 要包含**所有有值的字段**，不只是「变了的」。
        # 理由：标签定位靠 new_map 回答「这个口径的数该是多少」——
        # 某字段这轮没变（bobby 的 last30 新旧都是 1,436）也必须能查到，
        # 否则上一轮错改的残留值永远修不回来。
        # 「变没变」只影响裸数字兜底替换，不影响标签定位。
        old_map, new_map = {}, {}
        for f, _ in FIELD_TAGS:
            o, n = om.get(f), m.get(f)
            if o is None or n is None:
                continue
            old_map[f], new_map[f] = o, n

        if not new_map:
            continue

        # 同步日期
        df_old = (oldrec or {}).get("data_freshness")
        df_new = rec.get("data_freshness")

        for key in ("headline", "metric_note"):
            t = m.get(key)
            if not t:
                continue
            nt, hits = sync_text(t, old_map, new_map)
            if df_old and df_new and df_old != df_new:
                # 同步日期片段：2026-10-28 → 2026-11-01
                # ⚠ 与数字变化**独立**：某些条目这轮数字没变（mrr 相同），
                # 但「2026-10-28 同步」这个时点仍必须更新 ——
                # 它声明的是「这个数是什么时候核的」，不是「这个数变了没」。
                d_old = df_old[:10]
                d_new = df_new[:10]
                if d_old in nt:
                    nt = nt.replace(d_old, d_new)
                    hits.append(("data_freshness", d_old, d_new))
            if nt != t:
                m[key] = nt
                changed.append((rid, key, hits))

    if args.write:
        CSt.save_candidates(rows)
        print("[write] 改了 %d 处（%d 条条目）" % (len(changed), len({c[0] for c in changed})))
    else:
        print("[dry-run] 会改 %d 处（%d 条条目）：" % (len(changed), len({c[0] for c in changed})))
        for rid, key, hits in changed:
            print("  %-32s %-12s %s" % (rid[:31], key,
                                        "; ".join("%s %s→%s" % h for h in hits)))
    for rid, why in skipped:
        print("  [!] 跳过 %s：%s" % (rid, why))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
