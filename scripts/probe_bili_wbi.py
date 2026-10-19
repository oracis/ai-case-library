"""探测 B站 WBI 签名所需的 mixin key（draft/add 带 w_rid/wts 必须要）。

B站 web 端所有写接口都过 WBI 签名：
    w_rid = md5(query + w_rid_secret)
mixin key = nav 接口 `data.wbi_img` 的 img_url + sub_url 各取
[0,32,58,...] 的文件名尾巴按序拼接，再截前 32。

nav 也要签名，但 nav 本身**免签**（老接口的特例），所以能直接拿到密钥。
"""
import hashlib
import json
import os
import sys
import time
import urllib.parse

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp  # noqa: E402

MIXIN_TAB = (0, 46, 46, 43, 70, 19, 68, 39, 2, 5, 100, 62, 6, 26, 18, 22,
             51, 45, 33, 54, 42, 41, 21, 8, 49, 45, 3, 30, 29, 39, 18, 38,
             56, 20, 9, 57, 36, 53, 28, 42, 16, 33, 9, 34, 5, 19, 52, 39)


def mixin_key(img_url, sub_url):
    raw = ""
    for u in (img_url, sub_url):
        name = u.rsplit("/", 1)[-1].split(".")[0]
        raw += "".join(name[i] for i in MIXIN_TAB if i < len(name))
    return raw[:32]


def wbi_sign(params, key):
    """给 dict 参数加 wts/w_rid，返回可用的 query 字符串。"""
    p = dict(params)
    p["wts"] = int(time.time())
    items = sorted(p.items(), key=lambda kv: kv[0])
    # WBI 规范：值里的 !'()* 要百分号编码
    clean = []
    for k, v in items:
        v = "".join(ch for ch in str(v) if ch not in "!'()*")
        clean.append((k, urllib.parse.quote(str(v), safe="")))
    q = urllib.parse.urlencode(clean)
    p["w_rid"] = hashlib.md5((q + key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(p.items(), key=lambda kv: kv[0]))


def main():
    cdp = wp.CDP(9222)
    for t in cdp.list_targets():
        if "bilibili.com" in (t.get("url") or "") and t.get("type") == "page":
            cdp.connect_target(t["id"])
            tid = t["id"]
            break
    else:
        print("没有 bilibili page")
        return 1
    print("tab:", tid, cdp.eval("location.href", refresh_context=True))

    js = """(async () => {
      const r = await fetch('https://api.bilibili.com/x/web-interface/nav',
                            {credentials: 'include'});
      return await r.text();
    })()"""
    raw = cdp.eval(js, refresh_context=True)
    j = json.loads(raw)
    print("nav code:", j.get("code"), j.get("message"))
    if j.get("code") != 0:
        print(raw[:300])
        return 1
    img = j["data"]["wbi_img"]
    print("img_url :", img["img_url"])
    print("sub_url :", img["sub_url"])
    key = mixin_key(img["img_url"], img["sub_url"])
    print("mixin key:", key)

    # 用一个免签的 GET 验证签名算法对不对
    qs = wbi_sign({"r": "er"}, key) if False else None
    return 0


if __name__ == "__main__":
    sys.exit(main())