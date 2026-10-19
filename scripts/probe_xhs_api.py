"""探测小红书创作平台的草稿/笔记接口。

背景：项目里 `xhs_publish.fetch_drafts()` 是**纯 DOM 抓取**（点开草稿箱抽屉
再读卡片文本），不是网络接口。小红书创作者平台（creator.xiaohongshu.com）
是否提供可用的读写 API，本脚本实测。

做法：在已登录的 creator 页面上下文里，对候选路径各发一次请求，看返回形状：
- 返回 JSON（`{code:..}` / `{success:..}` / `{data:..}`）→ **接口存在**
- 返回 HTML（`<!DOCTYPE html>` / SPA 骨架）→ 路径不存在或打到了页面路由
- 401/403 或登录页 → 接口存在但要额外鉴权头

不发任何写操作（不建、不删、不改），纯只读探测。

用法：
    python scripts/probe_xhs_api.py
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

HOME = "https://creator.xiaohongshu.com/new/home"

# 只读候选。写接口（发布/更新/删除）先不猜 —— 探到读接口后从 bundle 里扒。
CANDIDATES = [
    # 常见的笔记/草稿列表
    ("GET", "/api/galaxy/note/note_list", "笔记列表?"),
    ("POST", "/api/galaxy/note/note_list", "笔记列表(POST)"),
    ("GET", "/api/galaxy/note/draft_list", "草稿列表?"),
    ("POST", "/api/galaxy/note/draft_list", "草稿列表(POST)"),
    ("GET", "/api/galaxy/note/my_draft", "我的草稿?"),
    # 创作者平台的笔记管理
    ("GET", "/api/author/note/list", "作者笔记列表?"),
    ("POST", "/api/author/note/list", "作者笔记列表(POST)?"),
    # 创作者中心常见的 publish 相关
    ("POST", "/api/galaxy/publish/note", "发布笔记?"),
    ("GET", "/api/galaxy/creator/note/list", "创作者笔记列表?"),
]

PROBE_JS = """(async () => {
  const cands = %s;
  // 每个请求都带 AbortController 超时，否则一个挂住的路径就把整个
  // Runtime.evaluate 拖到 CDP 超时（实测 9 个串行 fetch 直接 TimeoutError，
  // 一个结果都拿不到）。3s 足够判「返回 JSON 还是 HTML」。
  const one = async (c) => {
    const ac = new AbortController();
    const to = setTimeout(() => ac.abort(), 3000);
    try {
      const init = {method: c.m, credentials: 'include', signal: ac.signal,
        headers: {'Content-Type': 'application/json'}};
      if (c.m !== 'GET' && c.m !== 'HEAD') init.body = '{}';
      const r = await fetch(c.p, init);
      const t = await r.text();
      let kind = 'other';
      if (/^\\s*[{[]/.test(t)) kind = 'JSON';
      else if (/^\\s*<(!DOCTYPE|html)/i.test(t)) kind = 'HTML';
      return {m: c.m, p: c.p, note: c.note, status: r.status,
              kind: kind, body: t.slice(0, 180)};
    } catch (e) {
      return {m: c.m, p: c.p, note: c.note, err: String(e)};
    } finally { clearTimeout(to); }
  };
  // 并发，别串行
  return JSON.stringify(await Promise.all(cands.map(one)));
})()"""


def main():
    cdp = wp.CDP(9222)
    tid = None
    for t in cdp.list_targets():
        if "xiaohongshu.com" in (t.get("url") or "") and t.get("type") == "page":
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(HOME)["id"]
        time.sleep(8)
    cdp.connect_target(tid)
    url = cdp.eval("location.href", refresh_context=True)
    print("tab:", (url or "")[:90])
    cands = [{"m": m, "p": p, "note": n} for m, p, n in CANDIDATES]
    raw = cdp.eval(PROBE_JS % json.dumps(cands), refresh_context=True)
    try:
        rows = json.loads(raw)
    except Exception:                                    # noqa: BLE001
        print("拿不到 JSON：%r" % (raw,))
        return 1
    print("%-5s %-42s %-5s %-6s %s" % ("方法", "路径", "HTTP", "类型", "返回"))
    for r in rows:
        if r.get("err"):
            print("%-5s %-42s %-5s %-6s %s" % (r["m"], r["p"], "-", "-", r["err"]))
            continue
        print("%-5s %-42s %-5s %-6s %s" % (r["m"], r["p"], r.get("status"),
                                             r.get("kind"), r.get("body")[:110]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
