#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探测：小红书「删除笔记」接口存在性 + 风控表现。

## 为什么单独探

`probe_xhs_publish_api.py` 已定论：**写**（创建笔记）在 `web_api/sns/v2/note`
被 HTTP 461 + verifytype:124 拦住，与 payload 无关。但**删除**是另一条链路，
可能走 creator 同源网关（`/api/galaxy/...`），风控策略未必相同 —— 不能想当然
把「发布被拦」外推成「删除也被拦」。

## 安全边界（重要）

本脚本**绝不删真实笔记**：
  1) 端点用**不存在的 note_id** 试（`0000...`），最坏结果是被服务端回
     「笔记不存在」，不会误伤线上数据；
  2) 只有遇到 `461/471`（风控）或 `404/参数错` 才有信息量，两者都不删东西。

## 用法

    python -X utf8 scripts/_oneoff/probe_xhs_delete.py            # 扫 bundle + 假 ID 试探
    python -X utf8 scripts/_oneoff/probe_xhs_delete.py --no-scan  # 跳过 bundle 扫描
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import xhs_webapi as X  # noqa: E402

MANAGER = "https://creator.xiaohongshu.com/new/note-manager?roleType=creator"

# bundle 里出现过就说明端点大概率存在（键名/路径两种写法都搜）
NEEDLES = ["note/delete", "delete_note", "note_delete", "note/remove",
           "deleteNote", "noteIds", "note_id_list"]

# 候选端点：creator 同源优先（签名好过、CORS 不拦），edith 作对照
CANDIDATES = [
    ("creator", "POST", "/api/galaxy/v2/creator/note/delete"),
    ("creator", "POST", "/api/galaxy/v2/creator/note/delete_note"),
    ("creator", "POST", "/api/galaxy/v2/creator/note/remove"),
    ("edith", "POST", "/web_api/sns/v1/note/delete"),
    ("edith", "POST", "/web_api/sns/capa/postgw/note/delete"),
]
HOSTS = {"creator": X.HOST, "edith": "https://edith.xiaohongshu.com"}

# 不存在的 note_id：24 位 hex，服务端必判「不存在」
GHOST_ID = "000000000000000000000000"


def scan_bundles(cdp):
    """抓 note-manager 页加载的 JS，grep 删除相关字符串，返回命中片段。"""
    js = r"""(function(){
      var out = [];
      performance.getEntriesByType('resource').forEach(function(e){
        if (!/\.js(\?|$)/.test(e.name)) return;
        out.push(e.name);
      });
      return JSON.stringify(out.slice(0, 40));
    })()"""
    urls = json.loads(cdp.eval(js) or "[]")
    print("  页面 JS 资源 %d 个" % len(urls))
    hits = []
    for u in urls:
        st, body = _fetch_text(cdp, u)
        if st != 200 or not body:
            continue
        for n in NEEDLES:
            i = body.find(n)
            if i < 0:
                continue
            seg = body[max(0, i - 90):i + 90].replace("\n", " ")
            hit = {"needle": n, "url": u.rsplit("/", 1)[-1], "ctx": seg}
            # 去重：同一 needle + 同一上下文只留一条
            if not any(h["needle"] == n and h["ctx"] == seg for h in hits):
                hits.append(hit)
    return hits


def _fetch_text(cdp, url):
    """在页面里 GET 一段文本（同源 bundle 用，不签名）。"""
    js = r"""(function(u){
      return fetch(u, {credentials:'include'})
        .then(function(r){ return r.text().then(function(t){
          return JSON.stringify({s:r.status, t:t.slice(0, 400000)}); }); })
        .catch(function(e){ return JSON.stringify({s:0, t:String(e)}); });
    })(%s)""" % json.dumps(url)
    out = cdp.eval(js)
    try:
        r = json.loads(out)
        return r.get("s"), r.get("t")
    except Exception:
        return 0, ""


def try_candidates(cdp, note_id):
    """用假 ID 试候选端点，记录 (status, code, verifytype)。"""
    rows = []
    for host, method, path in CANDIDATES:
        url = HOSTS[host] + path
        for body in ({"note_id": note_id}, {"noteIds": [note_id]}):
            st, js, raw, hs = X.fetch_full(cdp, url, method, body)
            code = (js or {}).get("code")
            vt = hs.get("verifytype") or hs.get("Verifytype") or ""
            rows.append({"endpoint": host + path, "body": list(body)[0], "status": st,
                         "code": code, "verifytype": vt,
                         "msg": str((js or {}).get("msg") or raw)[:70]})
            # 端点明显不存在（404/405）就不用换 body 再试了
            if st in (404, 405):
                break
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--note-id", default=GHOST_ID)
    args = ap.parse_args()

    cdp, page = X.connect()
    if cdp is None:
        print("✗ 连不上 9223（先 python scripts/_launch_9223.py --hold 28800）")
        return 1
    print("页面：%s" % (page.get("url") or "")[:90])

    # 先导到笔记管理页，让删除相关 chunk 加载出来
    print("\n[1] 打开笔记管理页")
    cdp.eval("location.href=%s" % json.dumps(MANAGER))
    import time
    time.sleep(6)
    print("  现在：%s" % str(cdp.eval("location.href") or "")[:90])

    if not args.no_scan:
        print("\n[2] 扫 bundle 找删除端点")
        hits = scan_bundles(cdp)
        if not hits:
            print("  ✗ 未命中（chunk 可能懒加载，或删除走 UI 之外的通道）")
        for h in hits[:12]:
            print("  · [%s] %s\n      …%s…" % (h["needle"], h["url"], h["ctx"][:150]))

    print("\n[3] 候选端点试探（假 note_id=%s，不会删任何真实笔记）" % args.note_id)
    rows = try_candidates(cdp, args.note_id)
    print("  %-46s %-7s %-6s %-5s %-8s %s" % ("endpoint", "body", "HTTP", "code", "verify", "msg"))
    for r in rows:
        print("  %-46s %-7s %-6s %-5s %-8s %s"
              % (r["endpoint"], r["body"], r["status"], r["code"],
                 r["verifytype"] or "-", r["msg"]))

    verdict = []
    for r in rows:
        if r["status"] in (460, 461, 471):
            verdict.append("461 类风控（%s → %s）" % (r["endpoint"], r["status"]))
        elif r["status"] == 200 and r["code"] not in (0, None):
            verdict.append("接口通、业务拒绝（%s → code=%s: %s）"
                           % (r["endpoint"], r["code"], r["msg"]))
    print("\n判定：%s" % ("；".join(verdict) if verdict else "全部 404/405，候选路径不存在"))
    try:
        cdp.close_target(cdp.tid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
