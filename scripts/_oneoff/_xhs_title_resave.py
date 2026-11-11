"""一条龙：启动 9223 + 删 N 案例旧标题稿 + 用新标题重发 + 硬复核。

背景（2026-10-15）：用户截图指出草稿箱一堆「XX：海外小生意」太笼统。
根因是 `_raw_make_xhs_title` 的「产品名：描述」候选塞裸 desc 没压缩，
描述一超 20 字预算就退到 generic 兜底。已修 bug + 用智谱 AI 把 12 条
覆盖标题改写成具体中文短语（`xhs_ai_titles.py --only … --cn`）。
本脚本把草稿箱里这 12 条的旧稿删掉，用新标题重发。

要点（沿用 _xhs_cover_resave 的 v3 韧性设计）：
- 9223 Chrome 必须作为本后台任务的子进程常驻（命令结束会被回收）。
- 删除复用 `_purge_one`（坐标真实点击），循环到 nobtn；每 3 删重开抽屉
  稳住；异常/超时重连重试。
- ⚠ 匹配片段**只用品牌名**，不能带旧标题特征 —— 旧标题已被 AI 改写，
  带特征反而匹配不到；品牌名在新旧标题里都出现，最稳。
- publish 走子进程 CLI（note.json 已在盘）。
- 删完先 `_unmark` 本地台账，publish 成功后会自动重新 mark。
- ⚠ rc≠0 ≠ 没存稿（CDP 偶发慢 eval 会 TimeoutError 但草稿已存），
  所以最终以**草稿箱复核**为准，不信子进程返回码。

用法：python3 scripts/_xhs_title_resave.py <cid> [<cid>…]
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


def _frag(cid):
    """删除匹配片段 = 品牌名（新标题前缀）+ 完整产品名兜底 + cid 兜底。

    ⚠ 2026-10-15 修正：旧版只返回**完整产品名**（如 "Boothie: AI Photo
    Editor" / "WPConvert.ai - Convert AI sites to WordPress"），但草稿箱里的
    标题前缀是**品牌名**（"Boothie：" / "WPConvert："），整串英文在中文标题里
    不是子串 → 删不掉、反而发重复稿。

    正确做法：从**新 note.json 标题**取品牌前缀（第一个冒号之前的片段），
    它在新旧标题里都出现，最稳；再补完整产品名（部分旧稿用全名前缀）、
    最后 cid 兜底。
    """
    frags = []
    # 1) 新 note.json 标题的品牌前缀
    try:
        d = json.load(open(os.path.join(ROOT, "out", "xhs", cid, "note.json"),
                           encoding="utf-8"))
        t = (d.get("title") or "").strip()
        if t:
            brand = re.split(r"[:：]", t, 1)[0].strip()
            if brand and brand not in frags:
                frags.append(brand)
    except Exception:
        pass
    # 2) 完整产品名兜底
    for c in xp.load_cases():
        if c.get("id") == cid:
            nm = (c.get("name") or "").strip()
            if nm and nm not in frags:
                frags.append(nm)
            break
    # 3) cid 兜底
    if cid not in frags:
        frags.append(cid)
    return frags


# cid -> 删除匹配片段（从命令行传入的案例列表生成）
TARGETS = [(cid, _frag(cid)) for cid in sys.argv[1:] if not cid.startswith("-")]

GENERIC = re.compile(r"海外小生意|一人公司生意|海外小项目")
# ⚠ 2026-10-15：裸 `MRR` 会把品牌名「TrustMRR」误判成金额 ⇒ 整条被跳过
#   发布（line 192 的发前闸）。改成「MRR 前不能是字母」：品牌名不误伤，
#   「MRR 涨 88%」这种真金额表述照样拦。
MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d|(?<![A-Za-z])MRR')

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

    # ---- 3) 发布干净稿（新卡片）----
    print("\n==== 3) 发布干净稿（新卡片）====", flush=True)
    pub_stat = {}
    # ⚠ 幂等护栏：先把箱内标题抓一份，已存在新标题的跳过 ——
    #   防「上一轮跑到 step3 中途卡死、重启后把已发的又发一遍」产生重复草稿。
    try:
        sub, cards = reopen(sub)
    except (Exception, SystemExit):
        cards = []
    box_titles = set(title_of(c) for c in (cards or []))
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
        # ⚠ 发前最后一道：note.json 标题里还有通用词就别发 ——
        #   说明 build 没吃到新 overrides（改了覆盖表但没重建 note.json）。
        if GENERIC.search(t):
            print("--- publish %s：标题仍含通用词 %r，**多半是没重新 build**，"
                  "跳过" % (cid, t), flush=True)
            pub_stat[cid] = "fail"
            continue
        # ⚠ 幂等：新标题已在箱内 → 上次已发成功，跳过避免重复草稿
        if t in box_titles:
            print("--- publish %s：新标题《%s》已在箱内，跳过（防重复）"
                  % (cid, t), flush=True)
            pub_stat[cid] = "ok"
            continue
        print("--- publish %s（标题《%s》）---" % (cid, t), flush=True)
        rc, out, dt = run(XHS, ["publish", "--case", cid], 540)
        last = out.strip().splitlines()[-3:] if out.strip() else []
        # ⚠ rc≠0 ≠ 没存稿（CDP 偶发慢 eval → TimeoutError 但草稿已进箱），
        #   所以这里只记录，不判生死；最终以第 4 步草稿箱复核为准。
        if rc == 0:
            print("  ✓ ok (%.0fs)" % dt, flush=True)
            pub_stat[cid] = "ok"
        else:
            print("  ⚠ rc=%d (%.0fs)（可能已存稿，以复核为准）：%s"
                  % (rc, dt, "\n".join(last)), flush=True)
            pub_stat[cid] = "rc%d?" % rc

    # ---- 4) 硬复核 ----
    print("\n==== 4) 硬复核小红书草稿箱 ====", flush=True)
    try:
        sub, cards = reopen(sub)
        titles = [title_of(c) for c in cards]
        bad = [t for t in titles if MONEY.search(t)]
        gen = [t for t in titles if GENERIC.search(t)]
        print("删前 %d 张 → 删后+发后 %d 张" % (box_before, len(cards)),
              flush=True)
        print("金额违规：%d" % len(bad), flush=True)
        for t in bad:
            print("  ✗ 违规：%s" % t, flush=True)
        # ⚠ 本次任务的核心指标：通用词兜底标题必须降到 0
        print("通用词兜底标题：%d" % len(gen), flush=True)
        for t in gen:
            print("  ✗ 仍是兜底：%s" % t, flush=True)
        for cid, frags in TARGETS:
            hit = [t for t in titles if any(f in t for f in frags)]
            flag = "" if len(hit) == 1 else ("  <<< 数量异常" if hit else
                                             "  <<< 箱内没有")
            print("  %-42s 箱内 %d 张：%s%s"
                  % (cid, len(hit), hit, flag), flush=True)
    except (Exception, SystemExit) as e:
        print("复核抛 %s：%s" % (type(e).__name__, e), flush=True)

    print("\n==== 汇总 ====", flush=True)
    for cid, _ in TARGETS:
        print("  %-42s 删 %d 张 / 发 %s" % (cid, del_stat.get(cid, 0),
                                            pub_stat.get(cid, "?")),
              flush=True)


if __name__ == "__main__":
    main()
