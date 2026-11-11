"""小红书：删掉草稿箱里所有标题含「月收」的脏稿（金额红线违规）。v3。

v2 的坑：_purge_one 的 .draft-item/.draft-title-text 选择器只有在草稿抽屉
**打开并点进「图文笔记」tab** 之后才有元素；v2 没先 fetch_drafts 开抽屉，导致
_FIND 直接 nobtn（而末尾的 fetch_drafts 复核又因为开了抽屉看到 17 张，自相矛盾）。

v3 修法：
- 进循环前先 fetch_drafts 一次把抽屉打开+点进图文笔记+稳住。
- 每轮先扫 leftover：空了就完工。
- _purge_one 抛异常（含 90s recv 超时）→ 重连 + 重开抽屉后重试。
- _purge_one 返回 nobtn 但 leftover 非空 → 重开抽屉再试一次；仍 nobtn 视为
  选择器失效，停下报错（避免空转）。
- 末尾 fetch_drafts + MONEY 硬复核。

用法：python3 -u scripts/_xhs_delete_dirty3.py
"""
import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _purge_one

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d')


def leftover(cards):
    return [c.get("title", "") for c in cards if MONEY.search(c.get("title", ""))]


def reopen():
    sub = _draft_cdp()
    cards = fetch_drafts(sub)        # 开抽屉 + 点图文笔记 + 稳 DOM
    return sub, cards


def main():
    print("打开小红书草稿箱（9223）...", flush=True)
    sub, cards = reopen()
    removed = 0
    CAP = 40
    nobtn_streak = 0
    since_reopen = 0
    for i in range(CAP):
        bad = leftover(cards)
        if not bad:
            print("  [%d] 已无金额违规稿（箱共 %d 张）" % (i, len(cards)), flush=True)
            break
        print("  [%d] 仍有 %d 张含金额 → 删 1 张" % (i, len(bad)), flush=True)
        try:
            r = _purge_one(sub, "月收")
        except (Exception, SystemExit) as e:
            print("  [err] _purge_one 抛 %s：重连+重开抽屉" % type(e).__name__,
                  flush=True)
            try:
                sub.close_target(sub.tid)
            except Exception:
                pass
            time.sleep(3)
            sub, cards = reopen()
            nobtn_streak = 0
            since_reopen = 0
            continue
        if r == "ok":
            removed += 1
            nobtn_streak = 0
            since_reopen += 1
            print("    删 1 张 (ok，累计 %d)" % removed, flush=True)
            # 删完一张 DOM 原地更新，一般不用重开；每 4 张稳一次抽屉防 stale
            if since_reopen >= 4:
                try:
                    sub, cards = reopen()
                    since_reopen = 0
                except (Exception, SystemExit) as e:
                    print("  [err] 稳抽屉抛 %s：下次循环再试" % type(e).__name__,
                          flush=True)
        elif r == "nobtn":
            nobtn_streak += 1
            print("    [warn] _purge_one nobtn，但箱里还有脏稿 → 重开抽屉复核",
                  flush=True)
            try:
                sub, cards = reopen()
            except (Exception, SystemExit) as e:
                print("  [err] 重开抽屉抛 %s" % type(e).__name__, flush=True)
                time.sleep(3)
                continue
            if nobtn_streak >= 2:
                print("  [err] 连续 2 次 nobtn 但仍有脏稿，疑似选择器失效，停下。",
                      flush=True)
                break
        else:  # stuck 等
            print("    [warn] _purge_one 返回 %r：重开抽屉" % r, flush=True)
            try:
                sub, cards = reopen()
            except (Exception, SystemExit):
                time.sleep(3)
            nobtn_streak = 0

    # ---- 硬复核 ----
    try:
        sub, cards = reopen()
    except (Exception, SystemExit):
        cards = []
    bad = leftover(cards)
    print("\n==== 结果 ====", flush=True)
    print("本次共删 %d 张；剩余金额违规：%d 张" % (removed, len(bad)), flush=True)
    for t in bad:
        print("  ✗ 仍残留：%s" % t, flush=True)
    print("草稿箱总数：%d；金额红线违规：%d"
          % (len(cards), len([c for c in cards
                              if MONEY.search(c.get("title", ""))])),
          flush=True)
    try:
        sub.close_target(sub.tid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
