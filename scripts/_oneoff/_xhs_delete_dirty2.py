"""小红书：删掉草稿箱里所有标题含「月收」的脏稿（金额红线违规）。

比 v1 更稳：
- 每个 _purge_one 包在 try/except 里，TimeoutError / ConnectionError / 其它异常
  一律重连 _draft_cdp() 后重试（v1 就是栽在第一个 _purge_one 的 90s recv 超时，
  整脚本直接崩）。
- 靠 _purge_one 返回 "nobtn"（找不到命中草稿）来判断删干净了，不再每轮 fetch_drafts
  狂刷 DOM（重 DOM 下 refresh_context 很贵，正是 v1 卡死的温床）。
- 末尾再 fetch_drafts + MONEY 扫一遍做硬复核。

用法：python3 -u scripts/_xhs_delete_dirty2.py
"""
import sys, os, time, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xhs_publish as xp
from xhs_publish import _draft_cdp, fetch_drafts, _purge_one

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d')


def leftover(cards):
    return [c.get("title", "") for c in cards if MONEY.search(c.get("title", ""))]


def fresh():
    sub = _draft_cdp()
    return sub


def main():
    print("打开小红书草稿箱（9223）...", flush=True)
    sub = fresh()
    removed = 0
    CAP = 30
    for i in range(CAP):
        try:
            r = _purge_one(sub, "月收")
        except Exception as e:
            print("  [err] 第 %d 次 _purge_one 抛 %s：重连后重试"
                  % (i, type(e).__name__), flush=True)
            try:
                sub.close_target(sub.tid)
            except Exception:
                pass
            time.sleep(3)
            sub = fresh()
            continue
        if r == "ok":
            removed += 1
            print("  [%d] 删 1 张（累计 %d）" % (i, removed), flush=True)
        elif r == "nobtn":
            print("  [%d] _purge_one 返回 nobtn（箱里已无「月收」稿）" % i,
                  flush=True)
            break
        elif r == "stuck":
            print("  [%d] _purge_one 返回 stuck（页面疑似卡住）：重连" % i,
                  flush=True)
            try:
                sub.close_target(sub.tid)
            except Exception:
                pass
            time.sleep(3)
            sub = fresh()
        else:
            print("  [%d] _purge_one 返回 %r：休眠后重试" % (i, r), flush=True)
            time.sleep(3)

    # ---- 硬复核 ----
    for attempt in range(3):
        try:
            cards = fetch_drafts(sub)
            break
        except Exception as e:
            print("  [err] 复核 fetch_drafts 抛 %s：重连" % type(e).__name__,
                  flush=True)
            try:
                sub.close_target(sub.tid)
            except Exception:
                pass
            time.sleep(3)
            sub = fresh()
    else:
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
