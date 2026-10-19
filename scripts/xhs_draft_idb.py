"""小红书草稿的 IndexedDB 读写层（替代 DOM 抓取）。

## 为什么不用 API

小红书创作者平台**没有草稿服务端 API**。2026-09-30 实测：
- 打开草稿箱时 performance 里**没有任何 list/draft XHR**；
- 28 个 bundle（合计 ~9MB）里 379 条 `/api/` 路径**无一含 draft**；
- 草稿真身在**浏览器本地 IndexedDB**：
  `draft-database-v1` → store `image-draft`（另有 `article-draft` /
  `video-draft` / `audio-draft`），key 是 `draftId`。

bundle 里的函数名把机制说得很清楚：
`initDraftDatabase` / `putDraftToTableWithUid` / `recoverDraftFromTableWithDraftId`
/ `deleteDraftFromTable` / `getAllDraftsCount`，还有一句
`"草稿总数已达上限"` 的中文报错 —— 纯客户端存储，没有服务端副本。

**推论（重要）**：草稿是「编辑器本地自动保存」，不是云端同步。
换浏览器 / 清缓存 / 换机器，草稿就没了。所以小红书草稿只能当临时草稿用。

## 记录结构

```
{ draftId, uid, timeStamp, content: {...} }
```
`content` 就是编辑器快照，含 `desc`（正文 HTML）、`title`、`imgList`（图片
fileid）、`cover` 等。`uid` 是作者 id（实测本账号单一 uid）。
实测 2026-09-30：DOM 读 35 条 == IDB `image-draft` 计数 35。

## 用法

```python
import xhs_draft_idb as xi

xi.list_drafts(cdp)                # -> [{draftId, uid, timeStamp, title, saved}, ...]
xi.put_draft(cdp, draft_id, uid, content)   # 新建/覆盖（upsert）
xi.delete_draft(cdp, draft_id)     # 真删，不可逆
```

⚠ 写入后草稿箱 UI 未必立刻刷新，让页面重新导航一次即可。
⚠ `put_draft` 只写本地存储，**不会上传图片**；图片必须先走
`/api/media/v1/upload/creator/permit` + COS 拿到 `fileid` 再写进
`content.imgList`，否则草稿里是坏图。
"""
import json
import re

DRAFT_DB = "draft-database-v1"
IMAGE_STORE = "image-draft"

_HELPER_JS = r"""
var __xhsOpen = function (name) {
  return new Promise(function (res, rej) {
    var r = indexedDB.open(name);
    r.onsuccess = function () { res(r.result); };
    r.onerror = function () { rej(r.error); };
  });
};
var __xhsAll = function (db, store) {
  return new Promise(function (res) {
    var r = db.transaction(store, 'readonly').objectStore(store).getAll();
    r.onsuccess = function () { res(r.result || []); };
    r.onerror = function () { res([]); };
  });
};
var __xhsTitle = function (c) {
  // content 结构随版本变过多版，尽量多路兜底取标题
  if (!c || typeof c !== 'object') return '';
  var cand = [c.title, c.descTitle, c.draftTitle];
  if (c.draftStore) cand.push(c.draftStore.title);
  if (c.publishStore) cand.push(c.publishStore.title);
  for (var i = 0; i < cand.length; i++) {
    if (typeof cand[i] === 'string' && cand[i].trim()) return cand[i].trim();
  }
  var html = (c.desc || (c.draftStore && c.draftStore.desc) || '');
  var m = String(html).match(/<h1[^>]*>([\s\S]{1,80}?)<\/h1>/);
  if (m) return m[1].replace(/<[^>]+>/g, '').trim();
  return '';
};
"""


def _js(body):
    """拼一段可 await 的 JS（wp.CDP.eval 支持返回 Promise）。"""
    return "(async () => {\n%s\n%s\n})()" % (_HELPER_JS, body)


def _parse(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def list_drafts(cdp, store=IMAGE_STORE):
    """读全部草稿，按保存时间倒序（同 UI 顺序）。"""
    body = """
var db = await __xhsOpen(%s);
var rows = await __xhsAll(db, %s);
db.close();
rows.sort(function (a, b) { return (b.timeStamp || 0) - (a.timeStamp || 0); });
return JSON.stringify(rows.map(function (r) {
  return {draftId: r.draftId, uid: r.uid, timeStamp: r.timeStamp,
          title: __xhsTitle(r.content)};
}));
""" % (json.dumps(DRAFT_DB), json.dumps(store))
    out = _parse(cdp.eval(_js(body), refresh_context=True))
    if out is None:
        raise RuntimeError("读 IndexedDB 草稿失败（页面可能没登录 creator）")
    for r in out:
        r["saved"] = _fmt_ts(r.get("timeStamp"))
    return out


def get_draft(cdp, draft_id, store=IMAGE_STORE):
    """读单条完整记录（含 content 快照）。"""
    body = """
var db = await __xhsOpen(%s);
var r = await new Promise(function (res) {
  var q = db.transaction(%s, 'readonly').objectStore(%s).get(%s);
  q.onsuccess = function () { res(q.result || null); };
  q.onerror = function () { res(null); };
});
db.close();
return JSON.stringify(r);
""" % (json.dumps(DRAFT_DB), json.dumps(store), json.dumps(store),
       json.dumps(draft_id))
    return _parse(cdp.eval(_js(body), refresh_context=True))


def put_draft(cdp, draft_id, uid, content, time_stamp=None, store=IMAGE_STORE):
    """upsert 一条草稿。**只写本地存储，不上传图片。**"""
    ts = time_stamp if time_stamp is not None else 0
    body = """
var db = await __xhsOpen(%s);
var ts = %s || Date.now();
await new Promise(function (res, rej) {
  var tx = db.transaction(%s, 'readwrite');
  tx.objectStore(%s).put({draftId: %s, uid: %s, timeStamp: ts, content: %s});
  tx.oncomplete = function () { res(1); };
  tx.onerror = function () { rej(tx.error); };
});
var n = await new Promise(function (res) {
  var q = db.transaction(%s, 'readonly').objectStore(%s).count();
  q.onsuccess = function () { res(q.result); }; q.onerror = function () { res(-1); };
});
db.close();
return JSON.stringify({total: n});
""" % (json.dumps(DRAFT_DB), int(ts), json.dumps(store), json.dumps(store),
       json.dumps(draft_id), json.dumps(uid), json.dumps(content),
       json.dumps(store), json.dumps(store))
    return _parse(cdp.eval(_js(body), refresh_context=True))


def delete_draft(cdp, draft_id, store=IMAGE_STORE):
    """真删一条草稿。**不可逆，删前务必让用户确认。**"""
    body = """
var db = await __xhsOpen(%s);
await new Promise(function (res, rej) {
  var tx = db.transaction(%s, 'readwrite');
  tx.objectStore(%s).delete(%s);
  tx.oncomplete = function () { res(1); };
  tx.onerror = function () { rej(tx.error); };
});
var n = await new Promise(function (res) {
  var q = db.transaction(%s, 'readonly').objectStore(%s).count();
  q.onsuccess = function () { res(q.result); }; q.onerror = function () { res(-1); };
});
db.close();
return JSON.stringify({total: n});
""" % (json.dumps(DRAFT_DB), json.dumps(store), json.dumps(store),
       json.dumps(draft_id), json.dumps(store), json.dumps(store))
    return _parse(cdp.eval(_js(body), refresh_context=True))


def _fmt_ts(ms):
    if not ms:
        return ""
    import time as _t
    lt = _t.localtime(ms / 1000.0)
    return _t.strftime("%Y-%m-%d %H:%M:%S", lt)


def main(argv=None):
    import argparse
    import os
    import sys

    for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "ALL_PROXY"):
        os.environ.pop(_k, None)
    os.environ["no_proxy"] = "*"

    ap = argparse.ArgumentParser(description="小红书草稿（IndexedDB）只读查看")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import wechat_publish as wp

    cdp = wp.CDP(9222)
    tid = None
    for t in cdp.list_targets():
        if "creator.xiaohongshu.com" in (t.get("url") or "") and t.get("type") == "page":
            tid = t["id"]
            break
    if not tid:
        print("没找到 creator.xiaohongshu.com 的标签页，请先在 Chrome 里登录并打开")
        return 1
    cdp.connect_target(tid)
    rows = list_drafts(cdp)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("草稿 %d 条（本地 IndexedDB）" % len(rows))
    for r in rows:
        print("  %-22s %s" % (r["title"][:22] or "(无标题)", r["saved"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())