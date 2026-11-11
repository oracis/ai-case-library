"""头条：删除用户截图指名的 5 条今日测试稿（草稿箱 44 → 39）。

截图指认（mp.toutiao.com/profile_v4/manage/draft，2026-10-14 21:45）：
  1. 上传人声和伴奏一键退出混音成品           ×1（有封面）
  2. Nutria (9e3eaa): AI 卡路里追踪应用, 但近  ×2（1h 有封面 / 2h 无封面）
  3. 唯一在增长的一个：把 PNG 转成可编辑的 SVG  ×2（2h 有封面 / 2h 无封面）

⚠ 片段必须精确，避免误伤同箱干净稿：
  - easymix 干净稿 =「上传人声和伴奏一键出混音成品」（无「退」）→ 片段用「退出混音」
  - nutria  干净稿 =「Nutria：AI 卡路里追踪应用」（无 case-id 后缀）→ 片段用「(9e3eaa)」
  - vectosolve 片段「唯一在增长」箱内只匹配测试稿（先列清单核实再删）

用法：python3 -u scripts/_tt_delete_test.py
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toutiao_publish as tp
from toutiao_publish import (
    TT_DRAFT, _open, _check_login, _load_all_drafts,
    _count_dup_and_delbtn, _confirm_delete, _DRAFT_ALL_TEXT_JS,
)

# 片段 → 预期条数（删完应全部归零）
FRAGS = {
    "退出混音": 1,      # easymix 测试稿（干净稿无「退」字）
    "(9e3eaa)": 2,      # nutria 测试稿 ×2
    "唯一在增长": 2,     # vectosolve 测试稿 ×2
}
TOTAL_CAP = 12


def card_texts(cdp):
    try:
        return json.loads(cdp.eval(_DRAFT_ALL_TEXT_JS) or "[]")
    except Exception:                                 # noqa: BLE001
        return []


def count_all(cdp):
    """返回 {frag: n} + 卡片总数。"""
    cards = card_texts(cdp)
    return {f: sum(1 for s in cards if f in s) for f in FRAGS}, len(cards)


def audit(cdp, tag):
    n_map, total = count_all(cdp)
    print("[%s] 卡片总数 %d；命中：%s" % (
        tag, total,
        ", ".join("%s=%d" % (f, n) for f, n in n_map.items())), flush=True)
    return n_map, total


def main():
    print("打开头条草稿箱（9223）...", flush=True)
    cdp, _t = _open(TT_DRAFT, wait=10)
    try:
        _check_login(cdp)
        time.sleep(4)
        _load_all_drafts(cdp)
    except SystemExit as e:
        print("[err] %s" % e, flush=True)
        return 2

    # ---- 删前审计：先列清单确认 5 条都在 ----
    n_map, total = audit(cdp, "删前")
    for s in card_texts(cdp):
        if any(f in s for f in FRAGS):
            print("   · %s" % s, flush=True)
    expect = sum(FRAGS.values())
    got = sum(n_map.values())
    if got != expect:
        print("[warn] 预期 %d 条命中，实际 %d 条 —— 片段与箱内状态不符，"
              "仍按实际命中处理" % (expect, got), flush=True)
    if got == 0:
        print("没有可删的测试稿。", flush=True)
        return 0

    # ---- 删除循环 ----
    removed = 0
    attempts = 0
    while attempts < TOTAL_CAP:
        attempts += 1
        progressed = False
        for frag in FRAGS:
            n, pos = _count_dup_and_delbtn(cdp, frag, keep_newest=True,
                                           expand=False)
            if n == 0:
                continue
            if pos is None:
                print("  [%s] 命中 %d 条但按钮不可点（offscreen/nodel），重试"
                      % (frag, n), flush=True)
                continue
            ok = _confirm_delete(cdp, pos)
            removed += 1
            progressed = True
            print("  [%s] 删 1 条（确认框 %s，累计 %d）"
                  % (frag, "ok" if ok else "MISS", removed), flush=True)
            time.sleep(1.5)
            break                      # 删一张后 DOM 变，重扫
        if not progressed:
            break
        n_map, total = count_all(cdp)
        if sum(n_map.values()) == 0:
            break

    # ---- 硬复核 ----
    time.sleep(2)
    n_map, total = audit(cdp, "删后")
    left = sum(n_map.values())
    print("\n==== 结果 ====", flush=True)
    print("本次共删 %d 条；剩余测试稿：%d 条；草稿箱总数：%d"
          % (removed, left, total), flush=True)
    for s in card_texts(cdp):
        if any(f in s for f in FRAGS):
            print("  ✗ 仍残留：%s" % s, flush=True)
    try:
        cdp.close_target(cdp.tid)
    except Exception:                                 # noqa: BLE001
        pass
    return 0 if left == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
