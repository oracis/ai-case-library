"""重启 9223（chrome-debug-profile）并验证小红书 + 头条登录态仍在。

只读，不发布、不删除。9223 死掉后，xhs/toutiao 的发布都依赖它。
profile 的 cookie 持久化，重启通常能直接恢复登录；若过期则需人工扫码。
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import chrome_cdp_launch as L
import wechat_publish as wp

PROFILE = L.PLAT_PROFILE
PORT = L.PLAT_PORT
print("profile=%s port=%d" % (PROFILE, PORT), flush=True)

ok = wp._autostart_chrome_if_needed(PORT, profile=PROFILE)
print("[autostart] ok=%s" % ok, flush=True)
if not ok:
    print("✗ 无法启动 9223 Chrome，停下")
    sys.exit(1)

# ---- 小红书：能拉到草稿箱即登录有效 ----
try:
    import xhs_publish as xp
    cdp, cards = xp.fetch_drafts()
    print("[xhs] 草稿箱卡片数=%d（能拉到即登录有效）" % len(cards), flush=True)
    # 打印前几条标题看看
    for s in cards[:3]:
        print("    ·", s[:50])
    cdp.close_target(cdp.tid)
except Exception as e:
    print("[xhs] ✗ fetch_drafts 抛 %s：可能掉登录" % type(e).__name__, flush=True)

# ---- 头条：能开草稿箱即登录有效 ----
try:
    import toutiao_publish as tp
    cdp, _t = tp._open(tp.TT_DRAFT, wait=10)
    tp._check_login(cdp)
    time.sleep(3)
    tp._load_all_drafts(cdp)
    cards = json.loads(cdp.eval(tp._DRAFT_ALL_TEXT_JS) or '[]')
    print("[toutiao] 草稿卡片数=%d（能拉到即登录有效）" % len(cards), flush=True)
    cdp.close_target(cdp.tid)
except Exception as e:
    print("[toutiao] ✗ 抛 %s：可能掉登录" % type(e).__name__, flush=True)

print("探针结束（Chrome 仍在后台跑，供后续发布复用）", flush=True)
