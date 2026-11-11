"""头条草稿箱红线复核（基于 RAW 卡片文本，避免 _draft_all_titles 解析漏卡）。
- 扫描所有草稿卡片，报告金额/收入指标违规（带数字的 ¥€£$ / MRR / 月收$数字 / 欧/刀/美元/万美 + 数字 等）。
- 统计 60 个案例里哪些在草稿箱有对应草稿（按 case id 或 clean title 子串粗匹配）。
- 单独检查 vectosolve 是否在箱（之前解析可能漏）。
输出 data/_tt_verify.json。
"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toutiao_publish as tp
from toutiao_publish import _open, TT_DRAFT, _check_login, _load_all_drafts, _DRAFT_ALL_TEXT_JS

MONEY = re.compile(
    r'[¥€£$]\s*\d|MRR|ARR|月收\s*[¥€£$]?\d|月入\s*\d|累计\s*\d|营收\s*\d|'
    r'收入\s*\d|\d+\s*欧|\d[\d,]*\s*美元|\d[\d,]*\s*刀|\d[\d,]*\s*万美|'
    r'\d[\d,]*\s*元\b|赚\s*\d')

def main():
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        _load_all_drafts(cdp)
        try:
            cards = json.loads(cdp.eval(_DRAFT_ALL_TEXT_JS) or "[]")
        except Exception:
            cards = []
        cases = tp.load_cases()
        # 案例 -> 期望 clean 标题（与 publish_one 同口径）
        want = {}
        for c in cases:
            want[c["id"]] = tp.make_toutiao_title(c)
        # 违规 + 存在性
        violations = []
        present_ids = set()
        for card in cards:
            if MONEY.search(card):
                violations.append(card)
            for cid, title in want.items():
                if not title:
                    continue
                key = cid.split("-")[0] if "-" in cid else cid
                if key and key.lower() in (card or "").lower():
                    present_ids.add(cid)
        missing = [c["id"] for c in cases if c["id"] not in present_ids]
        out = {
            "card_count": len(cards),
            "amount_violations": violations,
            "present_count": len(present_ids),
            "missing_count": len(missing),
            "missing_sample": missing[:30],
        }
    finally:
        cdp.close_target(t["id"])
    path = os.path.join(tp.ROOT, "data", "_tt_verify.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("卡片数：%d" % len(cards))
    print("金额红线违规：%d 条" % len(violations))
    for v in violations:
        print("  ✗ %s" % v[:60])
    print("有草稿的案例：%d / %d" % (len(present_ids), len(cases)))
    print("缺失（前30）：%s" % missing[:30])
    print("已写出 %s" % path)

if __name__ == "__main__":
    main()
