"""一条龙：启动 9223 + 删 6 案例英文封面旧稿 + 发 6 个中文封面干净稿 + 硬复核。

背景（2026-10-14 深夜）：用户要求「批量删+发活稿」——
6 个案例（wpconvert/augora/vectosolve/publbee/podawaa/vid-ai）的封面描述句
此前回退成英文 one_liner 且被裁切；已在 cases.json 补中文 cover_desc 并
用 build 重生卡片（9333）。本脚本把草稿箱里这些案例的旧稿删掉，
再用新卡片重发干净稿。

要点：
- 9223 Chrome 必须作为本后台任务的子进程常驻（命令结束会被回收），
  所以启动+删除+发布+复核全部在这一个任务内完成。
- 删除复用 _purge_one（坐标真实点击），循环到 nobtn；每 3 删重开一次
  抽屉稳住；异常/超时重连重试（v3 同款韧性）。
- 匹配片段：品牌名优先，备选英文描述特征/旧脏标题特征（月收$XXK），
  确保旧稿无论干净还是脏都能被命中。
- publish 走子进程 CLI（note.json 已在盘，直接复用，不碰 9333）。
- 删完先 _unmark 本地台账，publish 成功后会自动重新 mark。
"""
import sys, os, time, subprocess, re, json

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp
import chrome_cdp_launch as L
import xhs_publish as xp

XHS = os.path.join(ROOT, "scripts", "xhs_publish.py")
PY = sys.executable
ENV = dict(os.environ)
ENV.update({"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"})

# cid -> 删除匹配片段（按优先级；品牌名优先，备选旧标题特征）
TARGETS = [
    ("wpconvert-ai-convert-ai-sites-to-wordpress", ["WPConvert"]),
    ("augora-ai", ["Augora"]),
    ("vectosolve", ["VectoSolve", "VECTOSOLVE"]),
    ("publbee", ["Publbee"]),
    ("podawaa", ["Podawaa", "own your audience", "月收$66K"]),
    ("vid-ai", ["Vid.AI", "Vid AI", "vid-ai", "Turn any", "月收$48K"]),
]

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d|MRR')


def run(script, args, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([PY, script] + args, cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True, env=ENV)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, "TIMEOUT after %ds" % timeout, timeout


def title_of(card):
    if isinstance(card, dict):
        return card.get("title") or ""
    return str(card)


def matches(cards, frags):
    return [c for c in cards if any(f in title_of(c) for f in frags)]


def reopen(old=None):
    if old is not None:
        try:
            old.close_target(old.tid)
        except Exception:
            pass
        time.sleep(2)
    sub = xp._draft_cdp()
    cards = xp.fetch_drafts(sub)
    return sub, cards


def main():
    # ---- 0) 启动 9223 ----
    print("==== 0) 启动 9223 ====", flush=True)
    ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=L.PLAT_PROFILE)
    print("autostart ok=%s" % ok, flush=True)
    if not ok:
        print("✗ 9223 起不来，停下")
        return

    # ---- 1) 开抽屉 + 全量清单（审计）----
    print("\n==== 1) 小红书草稿箱全量清单 ====", flush=True)
    try:
        sub = xp._draft_cdp()
        cards = xp.fetch_drafts(sub)
    except (Exception, SystemExit) as e:
        print("✗ 打不开草稿箱：%s：%s" % (type(e).__name__, e), flush=True)
        return
    print("草稿箱共 %d 张：", len(cards), flush=True)
    for i, c in enumerate(cards, 1):
        t = title_of(c)
        mark = "  <<< MONEY" if MONEY.search(t) else ""
        print("  [%2d] %s%s" % (i, t, mark), flush=True)
    box_before = len(cards)

    # ---- 2) 逐案例删旧稿 ----
    print("\n==== 2) 删除 6 案例旧稿（英文封面/脏标题）====", flush=True)
    del_stat = {}
    for cid, frags in TARGETS:
        cur = matches(cards, frags)
        print("--- %s：命中 %d 张 %s" % (cid, len(cur),
              [title_of(c) for c in cur] or ""), flush=True)
        if not cur:
            del_stat[cid] = 0
            continue
        n = 0
        frag = next((f for f in frags if f in title_of(cur[0])), frags[0])
        for attempt in range(10):
            try:
                r = xp._purge_one(sub, frag)
            except (Exception, SystemExit) as e:
                print("    [err] _purge_one 抛 %s：重连重开" % type(e).__name__,
                      flush=True)
                try:
                    sub, cards = reopen(sub)
                except (Exception, SystemExit) as e2:
                    print("    [err] 重开也失败：%s" % type(e2).__name__, flush=True)
                    time.sleep(3)
                    continue
                continue
            if r == "ok":
                n += 1
                print("    删 1 张 ok（%s 累计 %d）" % (cid, n), flush=True)
                if n % 3 == 0:
                    try:
                        sub, cards = reopen(sub)
                    except (Exception, SystemExit):
                        pass
            elif r == "nobtn":
                # 可能真删完 → 重开复核
                try:
                    sub, cards = reopen(sub)
                except (Exception, SystemExit) as e:
                    print("    [err] 重开失败：%s" % type(e).__name__, flush=True)
                    break
                left = matches(cards, frags)
                if not left:
                    break
                print("    [warn] nobtn 但还剩 %d 张命中，换片段继续"
                      % len(left), flush=True)
                frag = next((f for f in frags if f in title_of(left[0])), frag)
            else:
                print("    [warn] _purge_one=%r，重开抽屉" % r, flush=True)
                try:
                    sub, cards = reopen(sub)
                except (Exception, SystemExit):
                    time.sleep(3)
        # 删后硬复核
        try:
            sub, cards = reopen(sub)
        except (Exception, SystemExit):
            cards = []
        left = matches(cards, frags)
        del_stat[cid] = n
        print("  %s 删除完毕：本循环删 %d 张；箱内仍命中 %d 张 %s"
              % (cid, n, len(left), [title_of(c) for c in left] or ""),
              flush=True)
        # 本地台账回退（publish 成功后会重新 mark）
        try:
            xp._unmark(xp._draft_file(), cid)
        except Exception as e:
            print("  [warn] _unmark 失败：%s" % e, flush=True)

    # ---- 3) 发布 6 个中文封面干净稿 ----
    print("\n==== 3) 发布 6 个干净稿（新卡片）====", flush=True)
    pub_stat = {}
    for cid, _ in TARGETS:
        # 发前再确认 note.json 标题干净
        try:
            d = json.load(open(os.path.join(ROOT, "out", "xhs", cid,
                                            "note.json"), encoding="utf-8"))
            t = d.get("title", "")
        except Exception as e:
            print("--- publish %s：note.json 读不到（%s），跳过" % (cid, e),
                  flush=True)
            pub_stat[cid] = "fail"
            continue
        if MONEY.search(t):
            print("--- publish %s：标题仍脏 %r，跳过" % (cid, t), flush=True)
            pub_stat[cid] = "fail"
            continue
        print("--- publish %s（标题《%s》）---" % (cid, t), flush=True)
        rc, out, dt = run(XHS, ["publish", "--case", cid], 540)
        last = out.strip().splitlines()[-3:] if out.strip() else []
        if rc == 0:
            print("  ✓ ok (%.0fs)" % dt, flush=True)
            pub_stat[cid] = "ok"
        else:
            print("  ✗ rc=%d (%.0fs): %s" % (rc, dt, "\n".join(last)),
                  flush=True)
            pub_stat[cid] = "fail"

    # ---- 4) 硬复核 ----
    print("\n==== 4) 硬复核小红书草稿箱 ====", flush=True)
    try:
        sub, cards = reopen(sub)
        titles = [title_of(c) for c in cards]
        bad = [t for t in titles if MONEY.search(t)]
        print("删前 %d 张 → 删后+发后 %d 张" % (box_before, len(cards)),
              flush=True)
        print("金额违规：%d" % len(bad), flush=True)
        for t in bad:
            print("  ✗ 违规：%s" % t, flush=True)
        for cid, frags in TARGETS:
            hit = [t for t in titles if any(f in t for f in frags)]
            print("  %-42s 箱内 %d 张：%s" % (cid, len(hit), hit), flush=True)
    except (Exception, SystemExit) as e:
        print("复核抛 %s：%s" % (type(e).__name__, e), flush=True)

    print("\n==== 汇总 ====", flush=True)
    for cid, _ in TARGETS:
        print("  %-42s 删 %d 张 / 发 %s" % (cid, del_stat.get(cid, 0),
                                            pub_stat.get(cid, "?")),
              flush=True)


if __name__ == "__main__":
    main()
