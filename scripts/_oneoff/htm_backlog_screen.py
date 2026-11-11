#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 howtomakemi backlog（47 条）整理成「小团队/SaaS 可复制」筛选用的摘要。

只做**只读**整理，不写 cases.json。
输出 tmp/backlog_screen.tsv + tmp/backlog_digest.txt（人工判读用）。

判据（用于摘要里的 flags 列，最终结论仍靠人读）：
    SELF   自助转化信号（免费版/直接注册/self-serve/PLG/无 demo）
    DEMO   企业直销信号（Book a demo / 联系销售 / enterprise）
    IMPL   需要实施或集成（实施/迁移/部署/集成/onboarding）
    INFRA  基础设施/API/算力/开源（护城河在工程与资本，不在产品形态）
    CONSUMER 消费级 App（App Store 内购）
    REG    牌照/合规门槛（医疗/金融/法律/保险）
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCAN = os.path.join(ROOT, "tmp", "htm_official_scan.json")
SLUGS = os.path.join(ROOT, "tmp", "backlog_slugs.txt")
OUT_TSV = os.path.join(ROOT, "tmp", "backlog_screen.tsv")
OUT_TXT = os.path.join(ROOT, "tmp", "backlog_digest.txt")

RATE = 6.72

# 人工核过的口径表（2026-10-26 逐条读 money 段确认）。
# 为什么要这张表：页面的「💰 月收入」栏什么都能装——GMV、融资额、单价都塞进去过。
#   annual   = ARR / 年化 / 年营收（要除以 12）
#   monthly  = MRR / 月收入
#   invalid  = 那个数根本不是收入（GMV / 融资额 / 定价），不能拿来排序
#   unknown  = 页面没说清是哪个口径，标出来等人核
CALIBER = {
    "higgsfield": "annual", "medvi": "annual", "huntress": "annual", "lovable": "annual",
    "bland-ai": "unknown", "glean": "annual", "hightouch": "annual", "micro1": "annual",
    "primeintellect": "annual", "retell": "annual", "cognition": "annual", "skello": "annual",
    "appliedcompute": "annual", "hello-clever": "annual", "openrouter": "annual",
    "paraform": "invalid", "cruva": "invalid", "decagon": "annual", "n8n": "annual",
    "fable-security": "invalid", "omnidocs": "annual", "tern-group": "annual",
    "opencode": "unknown", "lexroom": "annual", "sanas": "annual", "viverdeia": "annual",
    "develop-health": "invalid", "morph": "invalid", "picturethis": "monthly",
    "serviceform": "annual", "doomers": "annual", "rox": "invalid", "stimuler": "annual",
    "jobright": "annual", "geviti": "annual", "outward": "unknown", "rosie": "annual",
    "tjm-labs": "annual", "boredhumans": "monthly", "crustdata": "annual",
    "workhero": "annual", "sandra-ai": "annual", "autoshorts": "monthly",
    "research-match": "monthly", "salesrobot": "monthly", "gistly": "monthly",
    "crowdreply": "invalid",
}

PAT = {
    "SELF": r"免费版|免费试用|直接注册|自助|self-serve|self serve|无需预约|Free plan|freemium|PLG|无需 demo",
    "DEMO": r"Book a demo|book a demo|预约演示|联系销售|企业版|Enterprise|enterprise|直销|销售团队",
    "IMPL": r"实施|迁移|部署|集成|onboarding|对接|私有化|定制",
    "INFRA": r"\bAPI\b|开源|open source|算力|GPU|基础设施|infrastructure|开发者|SDK",
    "CONSUMER": r"App Store|内购|订阅解锁|iOS|Android",
    "REG": r"牌照|资质|合规认证|HIPAA|医疗|保险|证券|法律意见|FDA",
}


def cny(s):
    """把「¥1.2亿」这类页面文案转成人民币数值，解析不了返回 0。"""
    m = re.match(r"¥([\d,\.]+)([万亿]?)", s or "")
    if not m:
        return 0
    v = float(m.group(1).replace(",", ""))
    return v * (100_000_000 if m.group(2) == "亿" else (10_000 if m.group(2) == "万" else 1))


def monthly_usd(rec):
    """按人工核过的口径折算成 $/月；invalid / unknown 返回 (None, 口径)。"""
    cal = CALIBER.get(rec["slug"], "unknown")
    v = cny(rec.get("income") or "")
    if cal == "annual" and v:
        return round(v / RATE / 12), "arr"
    if cal == "monthly" and v:
        return round(v / RATE), "mrr"
    return None, cal


def flags(rec):
    blob = " ".join(str(rec.get(k) or "") for k in ("what", "money", "one", "traffic", "founder"))
    return ",".join(k for k, p in PAT.items() if re.search(p, blob, re.I)) or "-"


def main():
    recs = {r["slug"]: r for r in json.load(open(SCAN, encoding="utf-8")) if "error" not in r}
    slugs = [s for s in open(SLUGS, encoding="utf-8").read().split() if s]
    rows, blocks = [], []
    for s in slugs:
        r = recs.get(s)
        if not r:
            continue
        mo, cal = monthly_usd(r)
        rows.append([s, str(mo if mo is not None else ""), cal, r.get("income") or "",
                     r.get("name") or "", r.get("region") or "", flags(r)])
        blocks.append("\n".join([
            "### %s | %s | %s | %s | $%s/mo(%s)" % (
                s, r.get("name"), r.get("income"), r.get("region"),
                format(mo, ",") if mo else "-", cal),
            "FLAGS: " + flags(r),
            "ONE: " + re.sub(r"\s+", " ", (r.get("one") or ""))[:170],
            "MNY: " + re.sub(r"\s+", " ", (r.get("money") or ""))[:420],
            "FND: " + re.sub(r"\s+", " ", (r.get("founder") or ""))[:260],
            "",
        ]))
    with open(OUT_TSV, "w", encoding="utf-8", newline="\n") as f:
        f.write("slug\tusd_mo\tcaliber\tpage_raw\tname\tregion\tflags\n")
        for row in rows:
            f.write("\t".join(row) + "\n")
    open(OUT_TXT, "w", encoding="utf-8", newline="\n").write("\n".join(blocks))
    print("条目 %d（解析到 $/月 的 %d 条）" % (len(rows), sum(1 for r in rows if r[1])))
    print("写出 %s / %s" % (os.path.relpath(OUT_TSV, ROOT), os.path.relpath(OUT_TXT, ROOT)))


if __name__ == "__main__":
    main()
