"""小红书：删掉草稿箱里所有标题含「月收」的脏稿（金额红线违规）。
不 reload、不重数，纯坐标点击删除（复用 xhs_publish._purge_one）。
`月收` 是金额红线违规的统一前缀，17 张脏稿全以它开头，删光即 0 违规。
用法：python3 -u scripts/_xhs_delete_dirty.py [--verify]
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _purge_one, OUT

MONEY = __import__("re").compile(r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d')

def leftover(cards):
    return [c.get("title", "") for c in cards if MONEY.search(c.get("title", ""))]

def main():
    print("打开小红书草稿箱（9223）...", flush=True)
    sub = _draft_cdp()
    try:
        removed = 0
        for i in range(30):
            cards = fetch_drafts(sub)
            bad = leftover(cards)
            if not bad:
                print("  已无金额违规稿（第 %d 轮）" % i, flush=True)
                break
            print("  第 %d 轮：仍有 %d 张含金额，开始删..." % (i, len(bad)), flush=True)
            for t in bad[:5]:
                print("    · %s" % t[:40], flush=True)
            r = _purge_one(sub, "月收")
            if r == "ok":
                removed += 1
                print("    删 1 张 (ok)", flush=True)
            else:
                print("    [warn] purge 返回 %s，停本轮" % r, flush=True)
                time.sleep(2)
        cards = fetch_drafts(sub)
        bad = leftover(cards)
        print("\n==== 结果 ====", flush=True)
        print("本次删了 %d 张；剩余金额违规：%d 张" % (removed, len(bad)), flush=True)
        for t in bad:
            print("  ✗ 仍残留：%s" % t, flush=True)
        # 全量红线复核
        allbad = [c.get("title","") for c in cards if MONEY.search(c.get("title",""))]
        print("草稿箱总数：%d；金额红线违规：%d" % (len(cards), len(allbad)), flush=True)
    finally:
        try:
            sub.close_target(sub.tid)
        except Exception:
            pass

if __name__ == "__main__":
    main()
