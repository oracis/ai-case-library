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

URL = "https://mp.weixin.qq.com/s/2sNJBuoxyLEPSBIlhbVrnQ"
wp._autostart_chrome_if_needed(port=9222)
cdp = wp.CDP(9222)
tid = cdp.new_target(URL)["id"]
time.sleep(7)
cdp.connect_target(tid)
time.sleep(2)
raw = cdp.eval("""JSON.stringify({
 href: location.href,
 title: document.title,
 text: (document.body.innerText || '').slice(0, 3500)
})""")
d = json.loads(raw)
print("HREF", d["href"], "TITLE", d["title"])
print(d["text"][:1600].replace("\n", " | "))
