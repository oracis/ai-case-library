"""本地（不连浏览器）把 17 条脏稿标题精准映射到 case id：
脏稿标题是由 build_note→make_xhs_title 生成的，本应 == out/xhs/<id>/note.json 的 title。
所以拿脏稿标题去精确匹配各 note.json 的 title 即可拿到 case id。"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ver = json.load(open(os.path.join(ROOT, "data", "_xhs_verify.json"), encoding="utf-8"))
dirties = [t for t in ver["amount_violations"]
           if not t.startswith("TrustMRR")]  # 品牌名误报剔除

# 收集 out/xhs/<id>/note.json 的 title
note_titles = {}   # title -> id
for nj in glob.glob(os.path.join(ROOT, "out", "xhs", "*", "note.json")):
    cid = os.path.basename(os.path.dirname(nj))
    try:
        t = json.load(open(nj, encoding="utf-8")).get("title", "")
    except Exception:
        continue
    note_titles.setdefault(t, cid)

# 正向精确匹配
matched = {}
unmatched = []
for d in dirties:
    if d in note_titles:
        matched.setdefault(note_titles[d], []).append(d)
    else:
        unmatched.append(d)

print("=== 精确命中 ===")
ids = set()
for cid, ds in sorted(matched.items()):
    ids.add(cid)
    print("  %-45s %d 张: %s" % (cid, len(ds), ds[0][:30]))
print("\n=== 未精确命中（需人工/topic 反查）===")
for d in unmatched:
    print("  %s" % d)

# topic 反查未命中的
import re
cases = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
overrides = json.load(open(os.path.join(ROOT, "data", "xhs_title_overrides.json"), encoding="utf-8"))
def clean_title(c):
    ov = overrides.get(c["id"])
    if ov:
        return ov
    import xhs_publish as xp
    try:
        return xp.make_xhs_title(c)
    except Exception as e:
        return "ERR:%s" % e
for d in unmatched:
    m = re.match(r"^月收[^\s：:]*[：:]\s*(.*)$", d)
    topic = (m.group(1) if m else d)
    kw = re.sub(r"[^\w一-鿿]", "", topic)[:10]
    cands = []
    for c in cases:
        ov = overrides.get(c["id"], "")
        blob = " ".join(str(c.get(k, "")) for k in ("name", "one_liner", "description", "id"))
        if kw and kw in re.sub(r"[^\w一-鿿]", "", ov + blob):
            cands.append(c["id"])
    print("\n脏稿: %s" % d)
    print("  topic=%s kw=%s" % (topic, kw))
    print("  候选: %s" % (cands or "—"))
    for cid in cands:
        c = next(x for x in cases if x["id"] == cid)
        ct = clean_title(c)
        flag = "✗" if re.search(r'MRR|ARR|月收|月入|\$[0-9]|€\d|£\d', ct) else "✓"
        print("    [%s] %s → %s" % (flag, cid, ct))

print("\n=== 汇总 ===")
print("已定位 case id 数：%d" % len(ids))
print("ids = %s" % sorted(ids))
