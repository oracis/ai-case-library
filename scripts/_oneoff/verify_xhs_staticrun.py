import os
import sys
import time
import json

ROOT = "D:/ai-case-library"
sys.path.insert(0, ROOT + "/scripts")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
import wechat_publish as wp  # noqa: E402
from cdp_core import CDP  # noqa: E402

cdp = CDP(9223)
tid = cdp.new_target("https://creator.xiaohongshu.com/new/note-manager")["id"]
time.sleep(10)
cdp.connect_target(tid)
time.sleep(3)
raw = cdp.eval("""JSON.stringify({
 href: location.href,
 title: document.title,
 text: (document.body.innerText || '').slice(0, 3000)
})""")
d = json.loads(raw)
print("HREF", d["href"], "TITLE", d["title"])
t = d["text"]
print("HIT_STATIC", "Static.run" in t)
print("HIT_TITLE", "静态网站" in t)
print(t[:1200].replace("\n", " | "))
