"""一条龙：启动 9223 + 发布 16 小红书干净稿 + 头条重发 nutria/vectosolve + 复核。

要点：
- 9223 Chrome 作为本后台任务的子进程，任务期间常驻（命令结束才会被回收，
  所以全部发布必须在这一个任务内完成）。
- 16 个小红书 note.json 标题已被 _xhs_patch_titles.py 改成干净版，publish 直接
  复用现成 note + 已生成的卡片图，不再走 9333 无头渲染。
- 头条 nutria/vectosolve 的干净标题来自 xhs_title_overrides.json（已提交）。
- publish 不重试（避免重复干净稿），最后用 fetch_drafts 硬复核落箱。
"""
import sys, os, time, subprocess, re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp
import chrome_cdp_launch as L
import xhs_publish as xp

XHS = os.path.join(ROOT, "scripts", "xhs_publish.py")
TT = os.path.join(ROOT, "scripts", "toutiao_publish.py")
PY = sys.executable
ENV = dict(os.environ)
ENV.update({"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"})

XHS_CASES = ['augora-ai', 'boothie-ai-photo-editor', 'downconvert-sketchup-files',
             'fastwrite-io', 'growingupvideo', 'homy', 'macwall', 'nutria-9e3eaa',
             'pet-plus-us', 'publbee', 'quotedeck', 'stealth-rdp', 'vectosolve',
             'wagiff-app', 'white-glove-content',
             'wpconvert-ai-convert-ai-sites-to-wordpress']
TT_CASES = ['nutria-9e3eaa', 'vectosolve']

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d|MRR')


def run(script, args, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([PY, script] + args, cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True, env=ENV)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired as e:
        return -9, "TIMEOUT after %ds" % timeout, timeout


def title_of(card):
    # card 可能是 dict(title=...) 或 str
    if isinstance(card, dict):
        return card.get("title") or ""
    return str(card)


def main():
    # ---- 0) 启动 9223 ----
    print("==== 0) 启动 9223 ====", flush=True)
    ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=L.PLAT_PROFILE)
    print("autostart ok=%s" % ok, flush=True)
    if not ok:
        print("✗ 9223 起不来，停下")
        return

    # ---- 1) 复核标题已干净（保险）----
    print("\n==== 1) 复核 16 note.json 标题无金额 ====", flush=True)
    for cid in XHS_CASES:
        p = os.path.join(ROOT, "out", "xhs", cid, "note.json")
        d = json.load(open(p, encoding="utf-8"))
        t = d.get("title", "")
        print("  %s%s %s" % ("✓" if not MONEY.search(t) else "✗", cid, t),
              flush=True)

    # ---- 2) 小红书发布 16 ----
    print("\n==== 2) 小红书发布 16 个干净稿 ====", flush=True)
    res = {}
    for cid in XHS_CASES:
        print("--- xhs publish %s ---" % cid, flush=True)
        rc, out, dt = run(XHS, ["publish", "--case", cid], 540)
        last = out.strip().splitlines()[-3:] if out.strip() else []
        if rc == 0:
            print("  ✓ ok (%.0fs)" % dt, flush=True)
            res[cid] = "ok"
        else:
            print("  ✗ rc=%d (%.0fs): %s" % (rc, dt, "\n".join(last)), flush=True)
            res[cid] = "fail"

    # ---- 3) 头条重发 2 ----
    print("\n==== 3) 头条重发 nutria / vectosolve ====", flush=True)
    for cid in TT_CASES:
        print("--- tt publish %s ---" % cid, flush=True)
        rc, out, dt = run(TT, ["publish", "--case", cid], 540)
        last = out.strip().splitlines()[-3:] if out.strip() else []
        if rc == 0:
            print("  ✓ ok (%.0fs)" % dt, flush=True)
            res["tt:" + cid] = "ok"
        else:
            print("  ✗ rc=%d (%.0fs): %s" % (rc, dt, "\n".join(last)),
                  flush=True)
            res["tt:" + cid] = "fail"

    # ---- 4) 硬复核小红书箱 ----
    print("\n==== 4) 硬复核小红书草稿箱 ====", flush=True)
    try:
        cdp, cards = xp.fetch_drafts()
        titles = [title_of(c) for c in cards]
        bad = [t for t in titles if MONEY.search(t)]
        print("草稿箱总数：%d" % len(cards), flush=True)
        print("金额违规：%d" % len(bad), flush=True)
        for t in bad:
            print("  ✗ 违规：%s" % t, flush=True)
        cdp.close_target(cdp.tid)
    except Exception as e:
        print("复核抛 %s：%s" % (type(e).__name__, e), flush=True)

    print("\n==== 汇总 ====", flush=True)
    for k, v in res.items():
        print("  %-30s %s" % (k, v))


if __name__ == "__main__":
    import json
    main()
