"""小红书草稿箱红线复核：复用 xhs_publish.fetch_drafts 拉全部图文草稿，
扫描标题里是否还有金额/收入指标违规（¥€£$ 数字 / MRR / 月收$数字 / 欧刀美元万美+数字 等）。
输出 data/_xhs_verify.json。"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp

MONEY = re.compile(
    r'[¥€£$]\s*\d|MRR|ARR|月收\s*[¥€£$]?\d|月入\s*\d|累计\s*\d|营收\s*\d|'
    r'收入\s*\d|\d+\s*欧|\d[\d,]*\s*美元|\d[\d,]*\s*刀|\d[\d,]*\s*万美|'
    r'\d[\d,]*\s*元\b|赚\s*\d')

def main():
    print("打开小红书草稿箱（9223）...", flush=True)
    try:
        drafts = xp.fetch_drafts()
    except SystemExit as e:
        print("✗ 打不开草稿箱（可能 9223 未登录小红书，被重定向到登录页）：%s" % e, flush=True)
        return
    except Exception as e:
        print("✗ fetch_drafts 异常：%s: %s" % (type(e).__name__, e), flush=True)
        return
    titles = [d.get("title", "") for d in drafts]
    hits = [t for t in titles if MONEY.search(t)]
    out = {"total": len(titles), "amount_violations": hits}
    path = os.path.join(xp.ROOT, "data", "_xhs_verify.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("草稿总数：%d" % len(titles), flush=True)
    print("金额红线违规：%d 条" % len(hits), flush=True)
    for h in hits:
        print("  ✗ %s" % h, flush=True)
    print("已写出 %s" % path, flush=True)

if __name__ == "__main__":
    main()
