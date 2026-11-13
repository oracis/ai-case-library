# -*- coding: utf-8 -*-
"""只读：列全阿里云草稿箱，按时间升序找最久一条。

2026-11-13 由 tmp/ 提升为常驻工具（scripts/ 与 tmp/ 同为仓库根下一层，
ROOT 写法不用改）。⛔ 只读，不发任何写请求；别再把这类工具写回 tmp/。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import yun_draft_cdp as Y  # noqa: E402

proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open("https://developer.aliyun.com/article/new#/")
time.sleep(5)
# 登录态掉了会 302 到 account.aliyun.com/login，此时相对路径的接口打到
# 登录域名上，只会得到一句含糊的 `TypeError: Failed to fetch`（11-13 实测）。
# 先显式判一次，别把「登录失效」误报成「接口坏了 / 脚本没迁好」。
href = str(cdp.eval("location.href") or "")
if "account.aliyun.com" in href or "/login" in href:
    print("LOGIN_REQUIRED 页面跳到登录页：%s" % href[:160])
    print("先跑 scripts/yun_login_keep.py --check 只读核验；服务端 ticket 有 TTL，"
          "过期需人工重新登录后重跑。⛔ 本脚本不自动改登录态")
    sys.exit(3)
ep = "/developer/api/articleDraft/listDraft?pageSize=30&pageNum=1"
r = cdp.eval("""
fetch(%s, {credentials:"include"})
  .then(function(x){return x.text().then(function(t){return x.status+"|"+t;});})
  .catch(function(e){return "ERR|"+e;})""" % json.dumps(ep))
if not isinstance(r, str) or r.startswith("ERR|"):
    print("FETCH_FAIL", str(r)[:300])
    sys.exit(2)
status, _, payload = r.partition("|")
print("HTTP", status)
try:
    data = json.loads(payload).get("data") or []
except Exception:
    data = []
print("RAW_HEAD", payload[:300])
if isinstance(data, dict):
    data = data.get("list") or data.get("records") or []
print("TOTAL", len(data))
for it in data:
    if isinstance(it, dict):
        print("DRAFT", it.get("aid"), "title=", repr((it.get("title") or "")[:70]),
              "gmt=", it.get("gmtCreate") or it.get("createTime"))
