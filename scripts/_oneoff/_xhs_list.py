"""小红书：列出草稿箱全部图文草稿标题（只读），并标出用户指名的测试变体稿。

用法：python3 -u scripts/_xhs_list.py
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts

# 用户截图点名的 5 条测试变体稿的标题片段（_purge_one 用 indexOf 子串匹配）
TEST_FRAGS = ["上传人声和伴奏", "Nutria (9e3eaa)", "唯一在增长"]

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d')


def main():
    print("打开小红书草稿箱（9223）...", flush=True)
    sub = _draft_cdp()
    cards = fetch_drafts(sub)
    print("草稿箱共 %d 张：\n" % len(cards), flush=True)
    n_test = 0
    n_money = 0
    for i, c in enumerate(cards):
        t = c.get("title", "")
        tags = []
        if any(f in t for f in TEST_FRAGS):
            tags.append("TEST")
            n_test += 1
        if MONEY.search(t):
            tags.append("MONEY")
            n_money += 1
        mark = ("  <<< " + ",".join(tags)) if tags else ""
        print("  [%2d] %s%s" % (i + 1, t, mark), flush=True)
    print("\n合计 %d 张；其中测试变体 %d 张；金额违规 %d 张"
          % (len(cards), n_test, n_money), flush=True)
    try:
        sub.close_target(sub.tid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
