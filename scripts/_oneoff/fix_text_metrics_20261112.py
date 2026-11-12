# -*- coding: utf-8 -*-
"""2026-11-12 第三批：正文金额/订阅数对齐「实测快照」（PRD #152）。

背景
----
前两批只动 `one_liner` 与 `caliber`。抽验线上页面时发现**详情页正文**仍是旧数字：
`lunarbuild` 数据卡写「MRR $3,864」、正文写「MRR 为 $6,031」，同屏自相矛盾。

根因不是「口径混用」，而是**正文取自队列缓存快照，`metrics` 取自之后支付网关实测**。
`metric_note` 自己就写着：「⚠ 队列缓存值是 2026-10-26 采集时的快照（当时 $2,214），
与上述实测不同；本条以实测为准。」——正文里的 2,214 正是这句已被作废的缓存值。

授权方向（用户 2026-11-12 选 A）：正文数字对齐门面/`metrics`，全页单一口径。

为什么是 6 条，不是 8 条
------------------------
`claw-mart` 与 `webclaw` 的 `corrections` 反证了 `metrics` 才是旧的一侧：
  claw-mart：「最新一次同步（2026-11-03T03:32:06Z）的 Last 30 days 为 $1,355，
              页面另存快照值 $1,395；$1,576 来自 2026-10-14 的旧同步，已过期」
  webclaw ：「$900 为旧数据（2026-09 月数据）」
⇒ 这两条的 `metrics`/`headline` 本身是 stale，改正文只会让页面更错。
  已在本轮**排除**，另案提请修数据台账（见 PRD #152 末尾）。

6 条的内证（三条独立证据同向）
------------------------------
  1. `metric_note` 首句 + 「以实测为准」声明
  2. `corrections` 的 truth 侧写的正是 `metrics` 值（pdf-vault $2,133/$8,060/$63,736、
     knightvision $8,018/$167,118、coldcaller-pro $2,810/$92,499、rankspot $1,627）
  3. 客单价回算：pdf-vault 2133÷237=$9.00 与素材给的 $9 完全吻合，
     knightvision 8018÷201=$39.89 与素材给的 $39.89 完全吻合
     ⇒ 素材的客单价本来就是按实测快照算的，正文的 MRR/订阅数才是旧数

改法
----
  金额对齐 `metrics.mrr` / `last_30d_revenue` / `all_time`
  订阅数对齐 `metrics.subscriptions`（字段为 None 的保留原文：rankspot/claw-mart/webclaw）
  客单价：分子分母换到同一快照后按 mrr ÷ subscriptions 重算（两位小数，措辞改「推算/约」）
          —— 与 #151「删分句不重算」的区别：那轮订阅数没有真值，重算等于造数；
             本轮分子分母都在同一条记录的同一快照里，重算是复位不是造数。

覆盖字段：what_it_does / how_it_makes_money / one_liner / why_it_works[i] / china_fit.note
（`why_it_works` 是 list、`china_fit` 是 dict —— 上一轮扫描用 isinstance(str) 把它们漏掉了，
  这也是上一轮只改了 what_it_does 那一处的原因）

不动：`corrections`（纠错记录本就要引用被纠正的旧说法）、正文里的同步日期串。

铁律
----
  * 每处按「旧串 → 新串」显式写死，旧串在该字符串里必须**恰好出现一次**，否则跳过报错
  * 同一字段的多处替换必须**串行应用**（上一版按原串各自替换会互相覆盖，已修）
  * 写前备份 tmp/，写后 `load_cases` + 直连 sqlite3 双路回读
  * 越界改动必须为 0（除下表字段外，546 条逐字段比对不变）
  * 缺省干跑，`--yes` 才写盘

用法：python scripts/_oneoff/fix_text_metrics_20261112.py [--yes]
"""
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import case_store as CS          # noqa: E402

BACKUP = "tmp/cases_before_text_metrics_20261112.json"

#: (case_id, 字段路径, 旧串, 新串)
#: 路径形如 "how_it_makes_money" / "why_it_works[0]" / "china_fit.note"
EDITS = [
    # ============ lunarbuild：MRR 6,031→3,864，订阅 8→7，客单 753.88→552 ============
    ("lunarbuild", "what_it_does",
     "当前活跃订阅数为 8，MRR 为 $6,031。",
     "当前活跃订阅数为 7，MRR 为 $3,864。"),
    ("lunarbuild", "how_it_makes_money",
     "当前 MRR 为 $6,031，活跃订阅数为 8，客单价为 $753.88。",
     "当前 MRR 为 $3,864，活跃订阅数为 7，按 MRR 除以活跃订阅数推算，客单价约为 $552。"),
    ("lunarbuild", "one_liner",
     "MRR $3,864，8 个活跃订阅。",
     "MRR $3,864，7 个活跃订阅。"),
    ("lunarbuild", "china_fit.note",
     "付费 4：客单 $753.88，8 个订阅 $6,031——高价少客户",
     "付费 4：客单 $552，7 个订阅 $3,864——高价少客户"),
    ("lunarbuild", "china_fit.note",
     "它提醒一件事：$753 的月费能撑起一个人",
     "它提醒一件事：$552 的月费能撑起一个人"),

    # ============ creativeclaw：11,590→12,240，订阅 220→232，客单 52.68→52.76 ============
    ("creativeclaw", "what_it_does",
     "当前活跃订阅数为 220，MRR 为 $11,590。",
     "当前活跃订阅数为 232，MRR 为 $12,240。"),
    ("creativeclaw", "how_it_makes_money",
     "当前 MRR 为 $11,590，活跃订阅数为 220。按 MRR 除以活跃订阅数推算，客单价为 $52.68。",
     "当前 MRR 为 $12,240，活跃订阅数为 232。按 MRR 除以活跃订阅数推算，客单价约为 $52.76。"),
    ("creativeclaw", "one_liner",
     "MRR $12,240，活跃订阅 220 个。",
     "MRR $12,240，活跃订阅 232 个。"),
    ("creativeclaw", "why_it_works[0]",
     "MRR $11,590 与活跃订阅 220 的支付网关直读数据表明",
     "MRR $12,240 与活跃订阅 232 的支付网关直读数据表明"),
    ("creativeclaw", "why_it_works[2]",
     "但 MRR 达 $11,590。",
     "但 MRR 达 $12,240。"),
    ("creativeclaw", "china_fit.note",
     "220 个订阅 $11,590，付费逻辑顺。",
     "232 个订阅 $12,240，付费逻辑顺。"),

    # ============ rankspot：1,548→1,627，客单 81.47→85.63（订阅 19 无真值，保留） ============
    ("rankspot", "what_it_does",
     "活跃订阅数为 19，MRR 为 $1,548。",
     "活跃订阅数为 19，MRR 为 $1,627。"),
    ("rankspot", "how_it_makes_money",
     "活跃订阅数 19，MRR $1,548，客单价为 $81.47。",
     "活跃订阅数 19，MRR $1,627，按 MRR 除以活跃订阅数推算，客单价约为 $85.63。"),
    ("rankspot", "why_it_works[2]",
     "MRR $1,548 对应 19 个活跃订阅，客单价 $81.47",
     "MRR $1,627 对应 19 个活跃订阅，客单价 $85.63"),
    ("rankspot", "china_fit.note",
     "客单 $81.47 说明客户会批量下单",
     "客单 $85.63 说明客户会批量下单"),

    # ============ pdf-vault：2,214→2,133，订阅 246→237，近30天/累计同步 ============
    ("pdf-vault", "what_it_does",
     "活跃订阅数为 246，累计已验证收入 $65,218。",
     "活跃订阅数为 237，累计已验证收入 $63,736。"),
    ("pdf-vault", "how_it_makes_money",
     "246 个活跃订阅对应 MRR $2,214，素材已算好客单价 $9，与 $9/月托管档位一致",
     "237 个活跃订阅对应 MRR $2,133，按 MRR 除以活跃订阅数推算客单价约 $9，与 $9/月托管档位一致"),
    ("pdf-vault", "how_it_makes_money",
     "近 30 天已验证收入 $9,515",
     "近 30 天已验证收入 $8,060"),
    ("pdf-vault", "one_liner",
     "活跃订阅 246 个。",
     "活跃订阅 237 个。"),
    ("pdf-vault", "why_it_works[0]",
     "MRR $2,214 除以活跃订阅 246 得到的客单价正好是 $9",
     "MRR $2,133 除以活跃订阅 237 得到的客单价正好是 $9"),
    ("pdf-vault", "why_it_works[2]",
     "累计已验证收入做到 $65,218",
     "累计已验证收入做到 $63,736"),

    # ============ coldcaller-pro：2,817→2,810，客单 32.01→31.93，近30天/累计同步 ============
    ("coldcaller-pro", "how_it_makes_money",
     "当前 MRR $2,817、活跃订阅 88 个（支付网关直读），客单价 $32.01——该数字为素材按 MRR ÷ 活跃订阅算好，直接引用，不再自行除法。近 30 天已验证收入 $14,457、累计 $92,731（支付网关直读）。",
     "当前 MRR $2,810、活跃订阅 88 个（支付网关直读），按 MRR 除以活跃订阅数推算，客单价约为 $31.93。近 30 天已验证收入 $14,439、累计 $92,499（支付网关直读）。"),
    ("coldcaller-pro", "why_it_works[1]",
     "88 个订阅对应 MRR $2,817、客单价 $32.01",
     "88 个订阅对应 MRR $2,810、客单价 $31.93"),

    # ============ knightvision：7,978→8,018，订阅 200→201，近30天/累计同步 ============
    ("knightvision", "what_it_does",
     "活跃订阅 200 个，档案自述用户数 326",
     "活跃订阅 201 个，档案自述用户数 326"),
    ("knightvision", "how_it_makes_money",
     "当前 MRR $7,978，活跃订阅 200 个，素材已算好的客单价为 $39.89。近 30 天已验证收入 $34,641，累计 $167,837，均来自",
     "当前 MRR $8,018，活跃订阅 201 个，按 MRR 除以活跃订阅数推算，客单价约为 $39.89。近 30 天已验证收入 $34,197，累计 $167,118，均来自"),
    ("knightvision", "one_liner",
     "活跃订阅 200 个，客单价 $39.89。",
     "活跃订阅 201 个，客单价 $39.89。"),
    ("knightvision", "why_it_works[0]",
     "所以 200 个订阅是在没有销售环节的情况下攒出来的",
     "所以 201 个订阅是在没有销售环节的情况下攒出来的"),
    ("knightvision", "why_it_works[2]",
     "近 30 天收入 $34,641 与 MRR $7,978 两个口径并存",
     "近 30 天收入 $34,197 与 MRR $8,018 两个口径并存"),

    # ============ playbook（页面「能搬走的部分」）里的引用数字同步 ============
    ("pdf-vault", "playbook[0]",
     "结果是 246 个活跃订阅撑起 $2,214 MRR",
     "结果是 237 个活跃订阅撑起 $2,133 MRR"),
    ("coldcaller-pro", "playbook[2]",
     "88 个订阅、$32.01 客单价、MRR $2,817、环比 -14.9%",
     "88 个订阅、$31.93 客单价、MRR $2,810、环比 -14.9%"),
    ("knightvision", "playbook[2]",
     "本案 $167,837 的累计收入就是这种口径下的数字",
     "本案 $167,118 的累计收入就是这种口径下的数字"),
]

#: 客单价复核：(id, 期望文本, 分母)。脚本自己除一遍，结果不符就中止。
ARPU = {
    "lunarbuild": ("552", 7),
    "creativeclaw": ("52.76", 232),
    "rankspot": ("85.63", 19),
    "pdf-vault": ("9", 237),
    "coldcaller-pro": ("31.93", 88),
    "knightvision": ("39.89", 201),
}

#: 残留扫描只覆盖「现值陈述」字段；`corrections` 是引用旧说法，天然含旧值，必须排除。
SCAN_TEXT = ["what_it_does", "how_it_makes_money", "one_liner",
             "solo_fit", "what_to_copy", "moat"]


def get_path(case, path):
    """取字段值。支持 "field" / "field[i]" / "field.key"。"""
    m = re.match(r"^([a-z_]+)(?:\[(\d+)\]|\.([a-z_]+))?$", path)
    if not m:
        raise ValueError("路径写法不认识: %s" % path)
    base, idx, key = m.group(1), m.group(2), m.group(3)
    val = case.get(base)
    if idx is not None:
        if not isinstance(val, list) or int(idx) >= len(val):
            return None
        return val[int(idx)]
    if key is not None:
        if not isinstance(val, dict):
            return None
        return val.get(key)
    return val


def set_path(case, path, value):
    m = re.match(r"^([a-z_]+)(?:\[(\d+)\]|\.([a-z_]+))?$", path)
    base, idx, key = m.group(1), m.group(2), m.group(3)
    if idx is not None:
        case[base][int(idx)] = value
    elif key is not None:
        case[base][key] = value
    else:
        case[base] = value


def fmt_money(v):
    if abs(v - round(v)) < 1e-9:
        return "%d" % round(v)
    return ("%.2f" % v).rstrip("0").rstrip(".")


def arpu_text(mrr, subs):
    return fmt_money(round(mrr / subs, 2))


def collect_blob(case):
    """把现值陈述字段拼成一整段（含 why_it_works 各条与 china_fit.note）。"""
    parts = []
    for f in SCAN_TEXT:
        v = case.get(f)
        if isinstance(v, str):
            parts.append(v)
    for v in (case.get("why_it_works") or []):
        if isinstance(v, str):
            parts.append(v)
    for v in (case.get("playbook") or []):
        if isinstance(v, str):
            parts.append(v)
    cf = case.get("china_fit")
    if isinstance(cf, dict) and isinstance(cf.get("note"), str):
        parts.append(cf["note"])
    return "\n".join(parts)


def main():
    write = "--yes" in sys.argv
    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    print("=" * 80)
    print("干跑校验（旧串在该字符串里必须恰好出现一次）")
    print("=" * 80)
    drift = []
    for cid, path, old, new in EDITS:
        s = get_path(by[cid], path)
        if not isinstance(s, str):
            drift.append((cid, path, "取不到字符串"))
            continue
        n = s.count(old)
        if n != 1:
            drift.append((cid, path, "旧串出现 %d 次，期望 1 次" % n))
            continue
        print("[OK] %-18s %-22s" % (cid, path))
    if drift:
        print()
        for d in drift:
            print("[SKIP] %-18s %-22s %s" % d)
        print("\n有 %d 处失配，未写盘。" % len(drift))
        return 1

    print()
    print("客单价复算（mrr ÷ subscriptions）：")
    bad = []
    for cid, (want, subs) in ARPU.items():
        mrr = (by[cid].get("metrics") or {}).get("mrr")
        got = arpu_text(float(mrr), subs)
        print("  [%s] %-16s %s ÷ %d = %s（文中 %s）" % (
            "OK" if got == want else "MISMATCH", cid, fmt_money(float(mrr)), subs, got, want))
        if got != want:
            bad.append(cid)
    if bad:
        print("\n客单价复算不符，未写盘：", bad)
        return 1

    print("\n拟改 %d 处 / %d 条案例" % (len(EDITS), len({e[0] for e in EDITS})))
    if not write:
        print("\n[DRY-RUN] 未写盘。加 --yes 执行。")
        return 0

    os.makedirs("tmp", exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    print("\n备份 → %s" % BACKUP)

    # 串行应用：每处都基于「当前值」替换，避免同字段多改互相覆盖
    touched = {}
    expect = {}
    for cid, path, old, new in EDITS:
        cur = get_path(by[cid], path)
        assert cur.count(old) == 1, (cid, path)
        set_path(by[cid], path, cur.replace(old, new, 1))
        touched.setdefault(cid, set()).add(path)
        expect[(cid, path)] = get_path(by[cid], path)
    CS.save_cases(cases)
    print("已写盘，案例数 %d。" % len(cases))

    print()
    print("=" * 80)
    print("回读校验")
    print("=" * 80)
    again = {c["id"]: c for c in CS.load_cases()}
    assert_n = 0
    for (cid, path), want in expect.items():
        got = get_path(again[cid], path)
        if got != want:
            assert_n += 1
            print("[FAIL] %s / %s 回读与预期不符" % (cid, path))
    print("断言不符：%d" % assert_n)

    conn = sqlite3.connect("data/ledger.db")
    conn.row_factory = sqlite3.Row
    sqlite_bad = 0
    for cid in sorted(touched):
        row = conn.execute(
            "SELECT * FROM case_library WHERE id=? AND deleted=0", (cid,)).fetchone()
        if row is None:
            print("[FAIL] %s 不在 case_library" % cid)
            sqlite_bad += 1
            continue
        keys = row.keys()
        for path in touched[cid]:
            base = path.split("[")[0].split(".")[0]
            if base not in keys:
                continue          # 列表/字典字段不在列里，跳过（已由载荷层断言）
            if path != base:
                continue
            if str(row[base] if row[base] is not None else "") != str(again[cid].get(base) or ""):
                print("[FAIL] %s / %s SQLite 与载荷层不一致" % (cid, path))
                sqlite_bad += 1
    conn.close()
    print("SQLite 直查不符：%d" % sqlite_bad)

    with open(BACKUP, encoding="utf-8") as f:
        before = {c["id"]: c for c in json.load(f)}
    out_of_scope = 0
    changed = 0
    for cid, c in before.items():
        after = again[cid]
        for k, v in c.items():
            if json.dumps(after.get(k), ensure_ascii=False, sort_keys=True) != \
               json.dumps(v, ensure_ascii=False, sort_keys=True):
                if any(p.split("[")[0].split(".")[0] == k for p in touched.get(cid, set())):
                    changed += 1
                else:
                    out_of_scope += 1
                    print("[越界] %s / %s" % (cid, k))
    print("实际改动 %d 个字段；越界改动 %d" % (changed, out_of_scope))

    print()
    print("残留扫描（现值陈述字段，corrections 已排除）")
    residual = 0
    for cid in sorted(touched):
        c = again[cid]
        m = c.get("metrics") or {}
        blob = collect_blob(c)
        mrr, l30, allt, subs = (m.get("mrr"), m.get("last_30d_revenue"),
                                m.get("all_time"), m.get("subscriptions"))
        checks = [
            (r"MRR[^0-9$]{0,8}\$([\d][\d,]*)", mrr, "MRR"),
            (r"近 30 天已验证收入 \$([\d][\d,]*)", l30, "近30天"),
            (r"累计(?:已验证收入)?(?:做到)? ?\$([\d][\d,]*)", allt, "累计"),
        ]
        for pat, truth, label in checks:
            if truth is None:
                continue
            for mt in re.finditer(pat, blob):
                v = float(mt.group(1).replace(",", ""))
                if abs(v - float(truth)) > 0.5:
                    residual += 1
                    print("  [残留-%s] %-16s %s（真值 %s）" % (label, cid, mt.group(1), fmt_money(float(truth))))
        if subs is not None:
            for mt in re.finditer(r"(?:活跃订阅数?为? ?|对应 )(\d+) ?个", blob):
                if int(mt.group(1)) != int(subs):
                    residual += 1
                    print("  [残留-订阅数] %-16s %s（真值 %s）" % (cid, mt.group(1), subs))
    print("残留冲突：%d" % residual)

    ok = (assert_n == 0 and sqlite_bad == 0 and out_of_scope == 0 and residual == 0)
    print()
    print("结论：%s" % ("全通过" if ok else "有问题，需排查"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
