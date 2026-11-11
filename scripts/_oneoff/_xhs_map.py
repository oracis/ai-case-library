"""把 17 条「月收$X：…」脏稿标题映射到 case id，并验证当前生成器产出的
clean 标题是否合理（不应再含金额）。不连浏览器，纯本地。"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cases = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
overrides = json.load(open(os.path.join(ROOT, "data", "xhs_title_overrides.json"), encoding="utf-8"))
ver = json.load(open(os.path.join(ROOT, "data", "_xhs_verify.json"), encoding="utf-8"))

# 去掉 TrustMRR 这类品牌名误报（含 MRR 但不是收入数字）
AMT = re.compile(r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d')
dirties = [t for t in ver["amount_violations"] if AMT.search(t)]
print("真实金额违规：%d 条" % len(dirties))

# 预期 clean 标题：override 优先，否则用生成器
import xhs_publish as xp
def clean_title(c):
    ov = overrides.get(c["id"])
    if ov:
        return ov
    try:
        return xp.make_xhs_title(c)
    except Exception as e:
        return "ERR:%s" % e

# 用标题里的主题词去 cases 里反查（name / one_liner / override value）
def candidates(topic):
    topic_kw = re.sub(r"[^\w一-鿿]", "", topic)[:8]
    out = []
    for c in cases:
        blob = " ".join(str(c.get(k, "")) for k in ("name", "one_liner", "description", "id"))
        ov = overrides.get(c["id"], "")
        if topic_kw and topic_kw in re.sub(r"[^\w一-鿿]", "", ov + blob):
            out.append(c["id"])
    return out[:5]

for t in dirties:
    m = re.match(r"^月收[^\s：:]*[：:]\s*(.*)$", t)
    topic = m.group(1) if m else t
    cands = candidates(topic)
    # 找到 case 后看 clean 标题
    shown = []
    for cid in cands:
        c = next(x for x in cases if x["id"] == cid)
        shown.append((cid, clean_title(c)))
    print("\n脏稿: %s" % t)
    print("  主题: %s" % topic)
    print("  候选: %s" % (cands or "—"))
    for cid, ct in shown:
        flag = "✗金额" if AMT.search(ct) or re.search(r'MRR|ARR', ct) else "✓"
        print("    [%s] %s → %s" % (flag, cid, ct))
