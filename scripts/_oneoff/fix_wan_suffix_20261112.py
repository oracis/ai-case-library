# -*- coding: utf-8 -*-
"""收尾第四批：修「MRR $10356.00 万」这类替换残留 + 6 条正文尾数。

问题来源（本轮刷新引入，必须修）：
  正文原写「MRR $1.0 万」这类近似值，刷新脚本按「MRR」前缀定位后把数字换成
  精确台账值，却没吃掉后面的「万」，产出「MRR $10356.00 万」——量级差 1 万倍。
  判据：数字不带千分位且整数部分 ≥4 位，且去掉后应等于 metrics 的
  mrr / last_30d_revenue / all_time 之一（带千分位的「$7,662 万」是合法写法，不动）。

第二部分是 6 条「改了台账但正文仍旧」的定点改写（正则抓不到的表述变体）：
  「30 天收入」「近 30 天流水」等、以及「MRR $18,480」写在 china_fit.note 里。

默认干跑；--yes 写入并回读断言。
"""
import os
import sys
import re
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]
RE_WAN = re.compile(r"\$\s?(\d{4,})(\.\d*)?\s?万")

# (case_id, field, index|None, before, after)
MANUAL = [
    ("flowmance-templates", "why_it_works", 0,
     "21 个活跃订阅就撑起 $395 MRR，近 30 天收入只有 $1,156 时",
     "21 个活跃订阅就撑起 $346 MRR，近 30 天收入只有 $1,727 时"),
    ("brevilabs", "china_fit", "note",
     "MRR $18,480 与 30 天收入 $25,032 的差额就是项目款",
     "MRR $17,756 与 30 天收入 $19,995 的差额就是项目款"),
    ("brevilabs", "how_it_makes_money", None,
     "MRR $17,756 与 30 天收入 $25,032 的差额",
     "MRR $17,756 与 30 天收入 $19,995 的差额"),
    ("hypit-ai", "china_fit", "note",
     "只有可验证收入（MRR $18,825、954 个订阅、上线不足两个月）",
     "只有可验证收入（MRR $18,597、728 个订阅、上线不足两个月）"),
    ("hypit-ai", "how_it_makes_money", None,
     "近 30 天流水 $46,111 高于 MRR $18,597",
     "近 30 天流水 $53,105 高于 MRR $18,597"),
    ("hypit-ai", "how_it_makes_money", None,
     "MRR 增速 2.1% 与收入增速 +155157% 的巨大背离",
     "MRR 增速 2.1% 与收入增速 +20.7% 的口径差异"),
    ("hypit-ai", "why_it_works", 3,
     "近 30 天流水与累计几乎相等（$46,111 vs $46,141）：这是一条干净的「上线即收钱」曲线，没有历史包袱。",
     "近 30 天流水 $53,105、累计 $96,821：两个月里收入几乎逐月爬升，没有历史包袱。"),
    ("chatseo", "how_it_makes_money", None,
     "近 30 天流水（$52,603）高于 MRR（$31,986）",
     "近 30 天流水（$46,905）高于 MRR（$35,591）"),
    ("brawler", "how_it_makes_money", None,
     "近 30 天流水（$59,942）",
     "近 30 天流水（$47,002）"),
    ("white-glove-content", "solo_fit", "delivery_note",
     "MRR $70K 对应的是一支内容团队",
     "MRR $60K 对应的是一支内容团队"),
    ("white-glove-content", "how_it_makes_money", None,
     "约117.3美元/月/客户，MRR约60,271美元，客户付费价格约117.3美元/月",
     "约 123.51 美元/月/客户，MRR 约 60,271 美元，客户付费价格约 123.51 美元/月"),
]


def walk(case):
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, None, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield f, i, x
        elif isinstance(v, dict):
            for k, y in v.items():
                if isinstance(y, str):
                    yield f, k, y


def get_field(case, field, idx):
    v = case.get(field)
    if idx is None:
        return v if isinstance(v, str) else None
    if isinstance(v, list) and isinstance(idx, int) and 0 <= idx < len(v):
        return v[idx]
    if isinstance(v, dict) and isinstance(idx, str):
        return v.get(idx)
    return None


def set_field(case, field, idx, text):
    if idx is None:
        case[field] = text
    else:
        case[field][idx] = text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    # ---------- A. 万 修正计划 ----------
    wan_edits = []           # (cid, field, idx, before, after)
    wan_skip = []
    for c in cases:
        m = c.get("metrics") or {}
        vals = [m.get(k) for k in ("mrr", "last_30d_revenue", "all_time")
                if isinstance(m.get(k), (int, float)) and m.get(k) > 0]
        for f, idx, t in walk(c):
            for mt in RE_WAN.finditer(t):
                n = float(mt.group(1))
                if not any(abs(n - v) < 0.5 for v in vals):
                    wan_skip.append((c["id"], "%s[%s]" % (f, idx), mt.group(0)))
                    continue
                after = "$%s" % format(int(n), ",")
                wan_edits.append((c["id"], f, idx, mt.group(0), after))
    print("万 修正：%d 处；未与台账对上而跳过：%d 处" % (len(wan_edits), len(wan_skip)))
    for s in wan_skip:
        print("   跳过", s)

    # ---------- B. 人工改写校验 ----------
    bad = []
    for cid, field, idx, before, _after in MANUAL:
        cur = get_field(by[cid], field, idx)
        if cur is None:
            bad.append((cid, field, idx, "字段缺失"))
        elif cur.count(before) != 1:
            bad.append((cid, field, idx, "命中 %d 次" % cur.count(before)))
    print("人工改写：%d / %d 唯一命中" % (len(MANUAL) - len(bad), len(MANUAL)))
    for b in bad:
        print("   ", b)
    if bad:
        return 1

    if not args.yes:
        print("干跑结束，未写盘。")
        return 0

    with open(os.path.join(ROOT, "tmp", "cases_before_wan_20261112.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    # 同一字段多处替换：串行基于当前值
    for cid, field, idx, before, after in wan_edits:
        cur = get_field(by[cid], field, idx)
        set_field(by[cid], field, idx, cur.replace(before, after, 1))
    for cid, field, idx, before, after in MANUAL:
        cur = get_field(by[cid], field, idx)
        set_field(by[cid], field, idx, cur.replace(before, after, 1))
    CS.save_cases(cases)
    print("已写盘：万 %d 处 + 人工 %d 处" % (len(wan_edits), len(MANUAL)))

    # ---------- 回读断言 ----------
    back = {c["id"]: c for c in CS.load_cases()}
    fail = []
    for cid, field, idx, before, after in wan_edits:
        if after not in (get_field(back[cid], field, idx) or ""):
            fail.append(("万", cid, field, idx))
    for cid, field, idx, before, after in MANUAL:
        cur = get_field(back[cid], field, idx) or ""
        if before in cur or after not in cur:
            fail.append(("人工", cid, field, idx))
    print("回读 %d 条，断言不符 %d 处" % (len(back), len(fail)))
    for f in fail:
        print("   ", f)

    # 残留扫描
    left = sum(len(list(RE_WAN.finditer(t))) for c in back.values() for _f, _i, t in walk(c)
               if any(abs(float(mt.group(1)) - v) < 0.5
                      for mt in RE_WAN.finditer(t)
                      for v in [(c.get("metrics") or {}).get(k) for k in ("mrr", "last_30d_revenue", "all_time")
                                if isinstance((c.get("metrics") or {}).get(k), (int, float))]))
    print("残留「精确数字 + 万」：%d 处" % left)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
