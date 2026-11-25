"""公众号风控只读核查：打开后台，读标题/正文/告警条，截图存证。

2026-11-25 13:31 用户授权：08:15 群发 `betting-tips` 后收到「风控提示、限制使用」，
先确认后台到底写了什么处罚，再决定申诉还是改选题。

⚠ 本脚本**只读**：不点任何按钮、不保存、不发布。唯一副作用是开页面 + 截图。
⚠ 后台页面大量用 iframe / 动态渲染，`body.innerText` 只能拿到外壳，所以逐个
   扫 iframe 的 innerText，并把「处罚/违规/限制/异常」附近的行单独摘出来。

用法：
    python scripts/_oneoff/wechat_risk_check_20261125.py            # 看当前页
    python scripts/_oneoff/wechat_risk_check_20261125.py --goto home
    python scripts/_oneoff/wechat_risk_check_20261125.py --goto notice
"""
import argparse
import base64
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import cdp_core  # noqa: E402

PORT = 9222
SHOT_DIR = os.path.join(ROOT, "tmp")

KEYWORDS = ("违规", "处罚", "限制", "异常", "风控", "封禁", "冻结", "申诉",
            "删除", "下架", "警告", "通知")

JS_PROBE = r"""
(function () {
  function txt(el) { try { return (el && el.innerText) || ""; } catch (e) { return ""; } }
  var out = {
    title: document.title,
    url: location.href,
    body: txt(document.body).slice(0, 4000),
    frames: []
  };
  var fr = document.querySelectorAll("iframe");
  for (var i = 0; i < fr.length; i++) {
    var t = "";
    try { t = txt(fr[i].contentDocument.body); } catch (e) { t = "<cross-origin>"; }
    if (t) out.frames.push({i: i, text: t.slice(0, 4000)});
  }
  var banners = [];
  ["[class*=warn]", "[class*=error]", "[class*=notice]", "[class*=tips]",
   "[class*=banned]", "[class*=limited]", "[class*=risk]"].forEach(function (sel) {
    var ns = document.querySelectorAll(sel);
    for (var j = 0; j < ns.length && j < 20; j++) {
      var s = (txt(ns[j]) || "").replace(/\s+/g, " ").trim();
      if (s && s.length < 300) banners.push(sel + " :: " + s);
    }
  });
  out.banners = banners.slice(0, 40);
  return out;
})()
"""


def pick_target(cdp):
    """优先拿 mp.weixin.qq.com 那个标签页。"""
    for t in cdp.list_targets():
        if t.get("type") == "page" and "mp.weixin.qq.com" in (t.get("url") or ""):
            return t
    for t in cdp.list_targets():
        if t.get("type") == "page":
            return t
    return None


def shot(cdp, path):
    r = cdp.send("Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["result"]["data"]))
    print("  📷 %s" % os.path.relpath(path, ROOT))


def highlight(info):
    """把含关键词的行单独列出来（正文太长，人工只看这几行）。"""
    lines = []
    for chunk in [info.get("body") or ""] + [f.get("text") or "" for f in info.get("frames") or []]:
        for ln in chunk.splitlines():
            s = ln.strip()
            if s and any(k in s for k in KEYWORDS) and len(s) < 200:
                if s not in lines:
                    lines.append(s)
    return lines[:40]


def main():
    ap = argparse.ArgumentParser(description="公众号风控只读核查")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--goto", choices=("home", "notice", "none"), default="none")
    ap.add_argument("--shot", default="wechat_risk_%s.png" % time.strftime("%H%M%S"))
    a = ap.parse_args()

    cdp = cdp_core.CDP(a.port)
    t = pick_target(cdp)
    if not t:
        raise SystemExit("✗ %d 上没有可用标签页" % a.port)
    cdp.connect_target(t["id"])
    print("target: %s\n        %s" % (t.get("title") or "", (t.get("url") or "")[:110]))

    if a.goto != "none":
        url = ("https://mp.weixin.qq.com/" if a.goto == "home"
               else "https://mp.weixin.qq.com/cgi-bin/announce?action=getannouncement0")
        print("→ %s" % url)
        cdp.send("Page.navigate", {"url": url})
        time.sleep(6)

    info = cdp.eval(JS_PROBE, refresh_context=True) or {}
    print("\n=== title ===\n%s\n=== url ===\n%s" % (info.get("title"), info.get("url")))
    hit = highlight(info)
    print("\n=== 命中关键词的行（%d）===" % len(hit))
    for ln in hit:
        print("  · %s" % ln)
    print("\n=== DOM 里的告警块（%d）===" % len(info.get("banners") or []))
    for b in (info.get("banners") or [])[:20]:
        print("  · %s" % b)
    body = (info.get("body") or "").replace("\n", " ")[:600]
    print("\n=== 正文前 600 字 ===\n%s" % body)
    os.makedirs(SHOT_DIR, exist_ok=True)
    shot(cdp, os.path.join(SHOT_DIR, a.shot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
