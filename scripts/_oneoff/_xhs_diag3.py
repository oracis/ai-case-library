"""同进程内：启动 9223 + 看页面落点 + 抓 fetch_drafts 完整 traceback。"""
import sys, os, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import wechat_publish as wp
import xhs_publish as xp

ok = wp._autostart_chrome_if_needed(xp.PUB_PORT, profile=__import__('chrome_cdp_launch').PLAT_PROFILE)
print("[autostart] ok=%s" % ok, flush=True)

cdp = wp.CDP(xp.PUB_PORT)
tid = None
for t in cdp.list_targets():
    if t.get("type") == "page" and "creator.xiaohongshu.com" in (t.get("url") or ""):
        tid = t["id"]; break
if not tid:
    tid = cdp.new_target(xp.PUB_URL)["id"]
    time.sleep(4)
sub = wp.CDP(xp.PUB_PORT)
sub.connect_target(tid)
sub.send("Page.navigate", {"url": xp.DRAFT_URL})
time.sleep(10)
url = sub.eval("location.href")
body = (sub.eval("document.body ? document.body.innerText.slice(0,400) : ''") or "")
print("URL   :", url, flush=True)
print("含登录/手机号/验证码:", any(k in (body or "") for k in ("登录","手机号","验证码","短信")), flush=True)
print("含草稿箱:", "草稿箱" in (body or ""), flush=True)
print("BODY[:200]:", repr((body or "")[:200]), flush=True)

print("\n--- fetch_drafts traceback ---", flush=True)
try:
    cards = xp.fetch_drafts(sub)
    print("cards:", len(cards), flush=True)
    for s in cards[:5]:
        print("  ·", (s or "")[:60], flush=True)
except Exception:
    traceback.print_exc()
