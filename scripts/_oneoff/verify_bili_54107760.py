import sys
import time
import json

sys.path.insert(0, "scripts")
from cdp_core import CDP

URL = "https://www.bilibili.com/read/cv54107760"
cdp = CDP(9223)
tid = cdp.new_target(URL)["id"]
time.sleep(9)
cdp.connect_target(tid)
time.sleep(2)
raw = cdp.eval("""JSON.stringify({
  href: location.href,
  title: document.title,
  text: (document.body.innerText || '').slice(0, 5000),
  h1: (document.querySelector('h1') || {}).innerText || ''
})""")
data = json.loads(raw)
print("HREF:", data["href"])
print("TITLE:", data["title"])
print("H1:", data["h1"][:200])
text = data["text"]
for kw in ["Wagiff", "中东北非", "戒除", "TrustMRR", "4843", "社区"]:
    print("KW", kw, kw in text)
print("TEXT_HEAD:", text[:900].replace("\n", " | "))
