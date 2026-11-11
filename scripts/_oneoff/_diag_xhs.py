"""诊断 xhs fetch_drafts 为何抛 ValueError：打印完整 traceback + 页面落点。"""
import sys, os, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import wechat_publish as wp
import xhs_publish as xp

cdp = wp.CDP(xp.PUB_PORT)
# 找或开一个 creator 页
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

# 看落点
url = sub.eval("location.href")
title = sub.eval("document.title")
body = (sub.eval("document.body ? document.body.innerText.slice(0,300) : ''") or "")
print("URL   :", url)
print("TITLE :", title)
print("BODY  :", repr(body[:300]))
print("含'登录':", "登录" in (body or ""), " 含'草稿箱':", "草稿箱" in (body or ""),
      " 含'手机号':", "手机号" in (body or ""))

# 再试 fetch_drafts 抓 traceback
print("\n--- 直接调 fetch_drafts ---")
try:
    cards = xp.fetch_drafts(sub)
    print("cards 数:", len(cards))
except Exception:
    traceback.print_exc()
