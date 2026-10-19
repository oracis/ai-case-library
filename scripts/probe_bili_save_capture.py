"""**抓 B站「保存为草稿」的真实请求体**（2026-10-01 定案脚本）。

为什么需要这个：`draft/add` 的 `arg` 用 `URLSearchParams` 拼回去一律
`-400 请求错误`，但bundle 里的 arg 结构明明对。唯一能确定答案的办法
不是读压缩代码，而是**让真编辑器自己发一次，抓它发出去的字节**。

关键坑：axios 在浏览器里走**XMLHttpRequest**，不走 fetch。
只 hook `window.fetch` 什么都抓不到（这个坑踩过）。

用法：
    python scripts/probe_bili_save_capture.py            # 在当前空白编辑器跑
    python scripts/probe_bili_save_capture.py --keep     # 不删新建的草稿

它会在当前read-editor 页填一段占位正文 → hook XHR+fetch → 点「保存为草稿」
→ 把真实 body 落到 `.cache/bili_save_capture.json`，然后删掉刚建的草稿
（用list 前后差集算出新 article_id，不误删别人的）。
"""
import json
import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp  # noqa: E402
import bili_draft_api as api  # noqa: E402

OUT = os.path.join(ROOT, ".cache", "bili_save_capture.json")

HOOK = r"""
(() => {
  window.__cap = [];
  const push = (kind, url, method, body, ct) => {
    try {
      window.__cap.push({kind, url: String(url), method: String(method),
                         body: body == null ? null : String(body),
                         content_type: ct || ''});
    } catch (e) { window.__cap.push({kind, err: String(e)}); }
  };
  // --- XHR（axios 在浏览器里走这条） ---
  const XO = XMLHttpRequest.prototype.open;
  const XS = XMLHttpRequest.prototype.send;
  const XSETH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(m, u) {
    this.__m = m; this.__u = u; this.__h = {};
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(k, v) {
    try { this.__h[k] = v; } catch (e) {}
    return XSETH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(b) {
    if (String(this.__u || '').includes('/draft/')) {
      push('xhr', this.__u, this.__m, b, (this.__h || {})['Content-Type']);
    }
    return XS.apply(this, arguments);
  };
  // --- fetch（万一以后 axios 换实现） ---
  const OF = window.fetch;
  window.fetch = function(u, o) {
    try {
      const url = (typeof u === 'string') ? u : (u && u.url);
      if (String(url).includes('/draft/')) {
        push('fetch', url, (o && o.method) || 'GET',
             (o && o.body) || null, (o && o.headers && (o.headers['Content-Type'] || o.headers.get?.('Content-Type'))) || '');
      }
    } catch (e) {}
    return OF.apply(this, arguments);
  };
  return 'hooked';
})()
"""

FILL = r"""
(() => {
  const setNative = (el, val) => {
    const d = Object.getOwnPropertyDescriptor(
        el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
                                 : HTMLInputElement.prototype, 'value');
    d.set.call(el, val);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const t = document.querySelector('.title-input__inner');
  if (!t) return 'no-title-input';
  setNative(t, 'API-CAPTURE-PROBE-' + Date.now());
  const b = document.querySelector('.eva3-editor');
  if (!b) return 'no-body';
  b.focus();
  // 用 execCommand 走编辑器自己的输入通道（比直接改 innerHTML 安全）
  document.execCommand('selectAll', false, null);
  document.execCommand('insertText', false,
    '第一段：API 抓包探针。\n第二段：用来确认段落序列化结构。');
  return 'filled:' + b.innerText.length;
})()
"""


def _editor_tab(cdp):
    for t in cdp.list_targets():
        u = t.get("url") or ""
        if "read-editor" in u and t.get("type") == "page":
            cdp.connect_target(t["id"])
            return t["id"]
    return None


def main():
    keep = "--keep" in sys.argv
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cdp = wp.CDP(9222)
    tid = _editor_tab(cdp)
    if not tid:
        print("找不到 read-editor 页，先用 bilibili_publish.py 打开编辑器")
        return 1

    before = {d["article_id"] for d in api.list_drafts(cdp)}
    print("当前草稿 %d 条" % len(before))

    print("hook:", cdp.eval(HOOK, refresh_context=True))
    print("fill:", cdp.eval(FILL, refresh_context=True))
    time.sleep(1.5)

    clicked = cdp.eval(
        "(()=>{var b=[].slice.call(document.querySelectorAll('button,[role=button],a'))"
        ".find(function(x){return (x.innerText||'').trim()==='保存为草稿';});"
        "if(!b) return 'none'; b.click(); return 'clicked';})()",
        refresh_context=True)
    print("click:", clicked)

    cap = []
    for _ in range(20):
        time.sleep(1.0)
        cap = cdp.eval("JSON.stringify(window.__cap || [])", refresh_context=True)
        try:
            cap = json.loads(cap) if isinstance(cap, str) else (cap or [])
        except Exception:                                    # noqa: BLE001
            cap = []
        if cap:
            break
    print("抓到 %d 个草稿请求" % len(cap))

    if not cap:
        print("没抓到 —— 可能 axios 走了别的通道或按钮没触发提交")
        return 1

    record = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "requests": cap}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print("已落盘:", OUT)

    for c in cap:
        print("\n--- %s %s" % (c.get("method"), c.get("url")))
        print("    content-type:", c.get("content_type"))
        b = c.get("body") or ""
        print("    body 前 300 字:", b[:300])
        if "arg=" in b:
            arg = b.split("arg=", 1)[1].split("&", 1)[0]
            print("    arg 解码后长度:", len(arg))
            try:
                import urllib.parse as _u
                obj = json.loads(_u.unquote_plus(arg))
                print("    arg 顶层键:", sorted(obj.keys()))
                print("    arg 全文:", OUT)
            except Exception as e:                           # noqa: BLE001
                print("    arg 解析失败:", e, "原始:", arg[:200])

    if keep:
        print("\n(--keep：保留新建草稿)")
        return 0

    time.sleep(2)
    after = api.list_drafts(cdp)
    new = [d for d in after if d["article_id"] not in before]
    print("\n新建草稿 %d 条，清理:" % len(new))
    for d in new:
        r = api.delete_draft(cdp, d["article_id"])
        print("  delete %s -> %s" % (d["article_id"],
                                     json.dumps(r, ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main())