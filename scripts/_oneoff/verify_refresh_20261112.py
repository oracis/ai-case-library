# -*- coding: utf-8 -*-
"""独立复核：全库「正文 MRR / 近 30 天收入 / 累计 / 订阅数」是否已对齐 metrics。

不信任 apply 脚本自报，直接读 SQLite 三方比对（sqlite3 / load_cases / cases.json），
并用**只允许连接虚词**的正则定位金额，避免把「MRR ÷ 订阅数 = 客单价」误判成 MRR 金额。
退出码非 0 表示仍有残留。
"""
import os
import sys
import re
import json
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import case_store as CS  # noqa: E402

FIELDS = ["one_liner", "what_it_does", "how_it_makes_money", "why_it_works",
          "china_fit", "solo_fit", "what_to_copy", "moat", "playbook"]
_LINK = r"(?:\s|为|达到|达|约|是|：|:|，|、|有){0,12}"
_NOT_PCT = r"(?!\s*(?:增速|增长|环比|同比|下降|上升|下滑|上涨))"
PAT = {
    "mrr": re.compile(r"MRR" + _NOT_PCT + "(" + _LINK + r")\$\s?([\d][\d,]*(?:\.\d+)?)"),
    "l30": re.compile(r"(?:近\s?30\s?天(?:已验证)?收入)" + _NOT_PCT + "(" + _LINK + r")\$\s?([\d][\d,]*(?:\.\d+)?)"),
    "all": re.compile(r"(?:累计(?:收入)?)" + _NOT_PCT + "(" + _LINK + r")\$\s?([\d][\d,]*(?:\.\d+)?)"),
}


def walk(case):
    for f in FIELDS:
        v = case.get(f)
        if isinstance(v, str):
            yield f, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    yield "%s[%d]" % (f, i), x
        elif isinstance(v, dict):
            for k, y in v.items():
                if isinstance(y, str):
                    yield "%s.%s" % (f, k), y


def main():
    cases = CS.load_cases()
    by = {c["id"]: c for c in cases}

    # 三方一致
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    n_db = conn.execute("SELECT COUNT(*) FROM case_library WHERE deleted=0").fetchone()[0]
    mirror = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
    ids_a, ids_b, ids_c = {c["id"] for c in cases}, {r[0] for r in conn.execute("SELECT id FROM case_library WHERE deleted=0")}, {c["id"] for c in mirror}
    print("三方条数：load_cases=%d sqlite=%d cases.json=%d；id 集合一致=%s"
          % (len(cases), n_db, len(mirror), ids_a == ids_b == ids_c))

    # 正文 vs metrics
    bad = []
    for c in cases:
        m = c.get("metrics") or {}
        for key, mkey in (("mrr", "mrr"), ("l30", "last_30d_revenue"), ("all", "all_time")):
            want = m.get(mkey)
            if not isinstance(want, (int, float)) or want <= 0:
                continue
            for path, t in walk(c):
                for mt in PAT[key].finditer(t):
                    got = float(mt.group(2).replace(",", ""))
                    if abs(got - want) / max(abs(want), 1e-9) > 0.01:
                        bad.append((c["id"], path, key, got, want))
    print("正文与台账仍不符：%d 处 / %d 条" % (len(bad), len({b[0] for b in bad})))
    for b in bad[:15]:
        print("   %-28s %-20s %s 文中=%s 台账=%s" % b)

    # 订阅数
    subs_bad = []
    for c in cases:
        n = (c.get("metrics") or {}).get("subscriptions")
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        for path, t in walk(c):
            for mt in re.finditer(r"(\d[\d,]*)\s?个(?:活跃|付费)?订阅", t):
                got = float(mt.group(1).replace(",", ""))
                if abs(got - n) / max(n, 1e-9) > 0.01:
                    subs_bad.append((c["id"], path, got, n))
            for mt in re.finditer(r"(?:活跃|付费)订阅(?:数)?\s?(?:为|是|:|：)?\s?(\d[\d,]*)", t):
                got = float(mt.group(1).replace(",", ""))
                if abs(got - n) / max(n, 1e-9) > 0.01:
                    subs_bad.append((c["id"], path, got, n))
    print("订阅数仍不符：%d 处 / %d 条" % (len(subs_bad), len({b[0] for b in subs_bad})))
    for b in subs_bad[:10]:
        print("   %-28s %-20s 文中=%s 台账=%s" % b)

    conn.close()
    return 1 if (bad or subs_bad or ids_a != ids_b or ids_a != ids_c) else 0


if __name__ == "__main__":
    sys.exit(main())
