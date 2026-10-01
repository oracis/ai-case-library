"""从 B站草稿箱页面的前端 bundle 里正则抓出所有 `/draft/xxx` 接口路径。

为什么需要：写接口的参数名没有任何文档，`draft/add` 空 body 只回 -400，
猜参数名（title/content/image_urls…）全不对。与其一个个试（每次都要 30s+
CDP 往返），不如直接把 bundle 里的真实路径与调用点扒出来。

做法：复用已登录 tab 的 fetch 拿 HTML → 抽出 <script src> → 逐个下 bundle →
正则 `/x/dynamic/feed/article/draft/[a-z_]+`。**只抓路径，不执行代码。**

用法：
    python scripts/grep_bili_bundle.py            # 抓路径
    python scripts/grep_bili_bundle.py --dump add # 抓 add 的调用点上下文
"""
import json
import os
import re
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

# 页面里出现的所有 draft 路径（不猜，靠 bundle 实抓）
PATH_RE = re.compile(r"/x/dynamic/feed/article/draft/[a-z_]+")
# 兜底：完整路径被 webpack 拆片段时，只抓「draft/xxx」这半截
LOOSE_RE = re.compile(r"draft/[a-z_]{2,20}")
# 调用点上下文
CTX_RE = re.compile(r".{0,260}%s.{0,700}", re.S)

FETCH_PAGE_JS = """(async () => {
  const r = await fetch(%s, {credentials: 'include'});
  const t = await r.text();
  const srcs = [];
  const re = /<script[^>]+src="([^"]+)"/g;
  let m;
  while ((m = re.exec(t))) srcs.push(m[1]);
  // 页面 HTML 只有 ~3KB（SPA 骨架），真正的 bundle 是运行时动态 import 的，
  // 静态正则抓不到。performance.getEntriesByType('resource') 里有**已加载
  // 的全部 js**，按 .js 过滤即可，不用猜 webpack chunk 的命名规律。
  const res = performance.getEntriesByType('resource')
      .map(e => e.name)
      .filter(u => /\\.js(\\?|$)/.test(u));
  return JSON.stringify({n: t.length, srcs: srcs, res: res});
})()"""


def main(argv):
    want = None
    if "--dump" in argv:
        want = argv[argv.index("--dump") + 1]
    cdp = wp.CDP(9222)
    tid = None
    for t in cdp.list_targets():
        if "bilibili.com" in (t.get("url") or "") and t.get("type") == "page":
            tid = t["id"]
            break
    if not tid:
        tid = cdp.new_target(DRAFT_PAGE)["id"]
        # 必须真**导航**过去再等：草稿箱的 DraftList-xxx.js 是路由懒加载
        # 的 chunk，不实际进这个路由，浏览器根本不会去请求它，
        # performance 资源列表里也就没有 —— 抓了一轮 0 条就是这个原因。
        time.sleep(10)
    cdp.connect_target(tid)

    info = json.loads(cdp.eval(FETCH_PAGE_JS % json.dumps(DRAFT_PAGE),
                               refresh_context=True))
    # 已加载过的 js 优先（performance 资源），再补 HTML 里静态引用的
    srcs = list(dict.fromkeys((info.get("res") or []) + (info.get("srcs") or [])))
    print("页面 %d 字节，候选 js %d 个（静态 %d + 运行时 %d）"
          % (info.get("n", 0), len(srcs), len(info.get("srcs") or []),
             len(info.get("res") or [])))

    paths, dumps = set(), []
    for s in srcs:
        url = s if s.startswith("http") else "https:" + s if s.startswith("//") \
            else "https://member.bilibili.com" + s
        try:
            js = cdp.eval("(async () => {const r = await fetch(%s, "
                          "{credentials:'include'}); return await r.text();})()"
                          % json.dumps(url), refresh_context=True)
        except Exception as e:                           # noqa: BLE001
            print("  拉取失败 %s: %s" % (url[-50:], e))
            continue
        if not js or len(js) < 200:
            continue
        # 路径可能写在字符串里，也可能被 js 拆成片段拼接（"draft/" + "add"），
        # 所以同时找「完整路径」和「draft/xxx」这种半截。
        hits = PATH_RE.findall(js)
        if not hits:
            hits = ["(半截) " + h for h in set(LOOSE_RE.findall(js))]
        if not hits:
            continue
        paths.update(hits)
        for h in set(hits):
            if want and want in h:
                m = CTX_RE % re.escape(h)
                mm = m.search(js)
                if mm:
                    dumps.append((h, url[-60:], mm.group(0)))

    print("\n=== 抓到的草稿接口路径（%d 条）===" % len(paths))
    for p in sorted(paths):
        print(" ", p)
    if want:
        print("\n=== %s 的调用点 ===" % want)
        for h, u, ctx in dumps:
            print("--- %s (%s)" % (h, u))
            print(ctx.replace("\\n", " ")[:900])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
