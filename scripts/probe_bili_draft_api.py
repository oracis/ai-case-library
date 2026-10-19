"""探测 B站草稿箱有哪些**可用**接口（读 / 写 / 删）。

为什么要探测而不是照抄路径：B站前端 bundle 里的接口路径没有文档，
写/改/删的路径全靠猜。已验证可用的只有读（draft/list）和删（draft/delete）。
本脚本对候选路径各发一次 **空 POST**，看返回体形状：

- 返回 `{"code":-404,...}` / `-400` / 空 `{}` → 路径不存在或方法不对
- 返回 `{"code":0}` 或带「参数错误」类业务码 → **路径存在**，是可用写入面
- 返回 `{"code":-101}` → 路径存在但要求登录（说明确实是接口而非 404）

空 body 不会真的建草稿，只看路由是否命中。

用法：
    python scripts/probe_bili_draft_api.py
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

DRAFT_PAGE = "https://member.bilibili.com/opus/management/drafts"
API_ORIGIN = "https://api.bilibili.com"

# (方法, 路径, 说明)
CANDIDATES = [
    ("GET", "/x/dynamic/feed/article/draft/list?pn=1&ps=5&keyword=", "读草稿列表（已验证可用）"),
    ("POST", "/x/dynamic/feed/article/draft/add", "新建草稿"),
    ("POST", "/x/dynamic/feed/article/draft/update", "更新草稿"),
    ("POST", "/x/dynamic/feed/article/draft/modify", "更新草稿（旧称）"),
    ("POST", "/x/dynamic/feed/article/draft/publish", "发布草稿"),
    ("POST", "/x/dynamic/feed/article/draft/editor", "编辑器取草稿"),
    ("GET", "/x/dynamic/feed/article/draft/editor?article_id=1", "编辑器取草稿"),
    ("POST", "/x/dynamic/feed/article/draft/delete", "删草稿（已验证可用）"),
]

# `draft/add` 空 body 只回 -400，要确认它真能建草稿就得带上最小业务参数。
# 字段名不能猜 —— 从草稿箱列表接口返回的 draft 对象里借：title / content /
# category_id / image_urls。MIN_ADD_BODY 里的键就是照着列表返回拼的。
MIN_ADD_BODY = [
    ("title", "【接口探测】可安全删除"),
    ("content", "接口探测用临时草稿，确认后立即删除。"),
    ("category_id", "3"),
    ("image_urls", "[]"),
    ("origin_image_urls", "[]"),
    ("summary", "接口探测用临时草稿，确认后立即删除。"),
]

PROBE_JS = """(async () => {
  const cands = %s;
  // 写接口必须有 bili_jct，否则一律 -111 CSRF 校验失败 —— 那个码本身
  // 就证明「路由存在」，但补上 token 才能进一步看业务层参数是否合理。
  const m0 = document.cookie.match(/(?:^|;\\s*)bili_jct=([^;]+)/);
  const csrf = m0 ? m0[1] : '';
  const out = [];
  for (const c of cands) {
    try {
      // GET/HEAD 带 body 会直接抛 TypeError，压根发不出去 —— 读接口必须
      // 单独构造 init，否则读接口的探测结果全是 TypeError，结论不可信。
      const init = {method: c.m, credentials: 'include'};
      if (c.m !== 'GET' && c.m !== 'HEAD') {
        // 必须用表单编码而不是 JSON：B站已验证可用的删除接口就是
        // x-www-form-urlencoded + body 里带 csrf。实测发 JSON 时
        // draft/add 恒 -111，怀疑是 CSRF 校验走 form 字段解析。
        init.headers = {'Content-Type': 'application/x-www-form-urlencoded'};
        init.body = 'csrf=' + csrf;
      }
      const r = await fetch(c.p, init);
      const t = await r.text();
      out.push({m: c.m, p: c.p, note: c.note, status: r.status,
                csrf: !!csrf, body: t.slice(0, 200)});
    } catch (e) {
      out.push({m: c.m, p: c.p, note: c.note, err: String(e)});
    }
  }
  return JSON.stringify(out);
})()"""


def main():
    cdp = wp.CDP(9222)
    tid = None
    for t in cdp.list_targets():
        if "bilibili.com" in (t.get("url") or "") and t.get("type") == "page":
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(DRAFT_PAGE)["id"]
        time.sleep(6)
    cdp.connect_target(tid)
    cands = [{"m": m, "p": p, "note": n} for m, p, n in CANDIDATES]
    # 关键：用**绝对 URL**打到 api.bilibili.com。从 member.bilibili.com
    # 页面上下文里 fetch 相对路径会打到 member 主机上，member 上没有这套
    # 路由 → 全 404，看着像「接口不存在」，其实是打错主机。
    # cookie 靠 credentials:'include' + 域匹配带过去（项目里读/删接口已验证可行）。
    for c in cands:
        c["p"] = API_ORIGIN + c["p"]
    raw = cdp.eval(PROBE_JS % json.dumps(cands), refresh_context=True)
    try:
        rows = json.loads(raw)
    except Exception:                                    # noqa: BLE001
        print("拿不到 JSON：%r" % (raw,))
        return 1
    print("%-6s %-52s %-5s %s" % ("方法", "路径", "HTTP", "返回"))
    for r in rows:
        if r.get("err"):
            print("%-6s %-52s %-5s %s" % (r["m"], r["p"].replace(API_ORIGIN, ""),
                                           "-", r["err"]))
            continue
        body = (r.get("body") or "").replace(API_ORIGIN, "")
        print("%-6s %-52s %-5s %s" % (r["m"], r["p"].replace(API_ORIGIN, ""),
                                       r.get("status"), body[:150]))

    add = next((r for r in rows if r["p"].endswith("draft/add")), None)
    if not add or add.get("err") or "404" in (add.get("body") or ""):
        print("\ndraft/add 未命中，跳过建草稿验证。")
        return 0
    if '"code":0' in (add.get("body") or ""):
        print("\n⚠ draft/add 空 body 就建成了草稿！立刻去草稿箱人工删掉。")
        return 0

    # 空 body 只到 -400；带最小业务参数再试一次，确认真能建。
    js = """(async () => {
      const m = document.cookie.match(/(?:^|;\\s*)bili_jct=([^;]+)/);
      const csrf = m ? m[1] : '';
      const p = new URLSearchParams();
      p.set('csrf', csrf);
      const body = %s;
      for (const k of Object.keys(body)) p.set(k, body[k]);
      const r = await fetch(%s, {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: p.toString()});
      return await r.text();
    })()""" % (json.dumps(dict(MIN_ADD_BODY)),
                 json.dumps(API_ORIGIN + "/x/dynamic/feed/article/draft/add"))
    r2 = cdp.eval(js, refresh_context=True)
    print("\n=== draft/add 带最小业务参数 ===")
    print((r2 or "")[:400])
    if '"code":0' in (r2 or ""):
        print("→ 建草稿成功。去草稿箱删掉《【接口探测】可安全删除》。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
