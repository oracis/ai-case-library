#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站专栏删除（已发布 / 草稿统一入口）。默认干跑，`--yes` 才真删。

端点（2026-11-20 实测，删 53996900 成功）：
    POST https://member.bilibili.com/x/web/article/delete
    body: aid=<aid>&csrf=<bili_jct>
    → {"code":0,"message":"OK"}
    无 csrf 返回 -111（CSRF 校验失败）⇒ 端点存在的判据。

⚠ 删除**不返还**当日投稿额度（2026-11-20 实测），删了就是删了。
⚠ 判据只认「独立回读」：删后重新拉已投稿列表，目标 aid 不在 + 条数 -1。

用法：
    python scripts/bili_article_delete.py --aid 53683188          # 干跑
    python scripts/bili_article_delete.py --aid 53683188 --yes    # 真删
    python scripts/bili_article_delete.py --show 53683188         # 只看详情
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"


def _cdp():
    import bilibili_publish as bp
    import bili_draft_api as bia
    import wechat_publish as wp
    cdp = wp.CDP(bp.PUB_PORT)
    bia._bili_tab(cdp)
    return cdp, bp, bia


def detail(cdp, aid):
    """文章详情（含发布时间的可能来源）。返回原始 data dict。"""
    js = """(async () => {
      const out = {};
      const urls = {
        viewinfo: 'https://api.bilibili.com/x/article/viewinfo?id=%s',
        cards: 'https://api.bilibili.com/x/article/cards?ids=%s'
      };
      for (const k in urls) {
        try {
          const r = await fetch(urls[k], {credentials: 'include'});
          const j = await r.json();
          out[k] = {code: j.code, data: j.data};
        } catch (e) { out[k] = {err: String(e).slice(0, 100)}; }
      }
      return out;
    })()""" % (str(aid), str(aid))
    return cdp.eval(js, refresh_context=True)


def find_in_list(cdp, bia, aid):
    pubs = bia.list_published_articles(cdp, max_page=8) or []
    for i, a in enumerate(pubs):
        if str(a.get("id")) == str(aid):
            return a, i + 1, len(pubs)
    return None, None, len(pubs)


def do_delete(cdp, aid):
    js = """(async () => {
      var m = document.cookie.match(/(?:^|;\\s*)bili_jct=([^;]+)/);
      var csrf = m ? m[1] : '';
      if (!csrf) return {err: 'no-bili_jct'};
      var body = new URLSearchParams();
      body.append('aid', '%s');
      body.append('article_id', '%s');
      body.append('csrf', csrf);
      const r = await fetch(
        'https://member.bilibili.com/x/web/article/delete?aid=%s&csrf=' +
        encodeURIComponent(csrf),
        {method: 'POST', credentials: 'include',
         headers: {'Content-Type':
                     'application/x-www-form-urlencoded; charset=UTF-8',
                   'X-Requested-With': 'XMLHttpRequest'},
         body: body.toString()});
      const txt = await r.text();
      try { return JSON.parse(txt); } catch (e) { return {raw: txt.slice(0, 200)}; }
    })()""" % (str(aid), str(aid), str(aid))
    return cdp.eval(js, refresh_context=True)


def _ts(v):
    if not v:
        return "-"
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")
    except Exception:                                   # noqa: BLE001
        return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", type=str, required=True)
    ap.add_argument("--show", action="store_true", help="只打详情，不删")
    ap.add_argument("--yes", action="store_true", help="真删（否则干跑）")
    a = ap.parse_args()

    cdp, bp, bia = _cdp()
    aid = str(a.aid)

    item, pos, total = find_in_list(cdp, bia, aid)
    print("已投稿列表：共 %d 条" % total)
    if item:
        print("  命中 aid=%s（第 %d 条）%s" % (aid, pos, item.get("title")))
    else:
        print("  ⚠ 列表里没有 aid=%s（可能已删 / 审核中 / 不是本账号）" % aid)

    d = detail(cdp, aid)
    vi = (d or {}).get("viewinfo") or {}
    dd = vi.get("data") or {}
    if isinstance(dd, dict) and dd:
        keys = ("title", "publish_time", "ctime", "mtime", "state",
                "words", "view", "like")
        print("详情：" + "  ".join("%s=%s" % (k, dd.get(k)) for k in keys if k in dd))
        pt = dd.get("publish_time") or dd.get("ctime")
        if pt:
            print("发布时间：%s（epoch %s）" % (_ts(pt), pt))
        else:
            print("发布时间：接口没给（键：%s）" % ",".join(list(dd)[:12]))
    else:
        print("详情：接口无数据 code=%s" % (vi.get("code"),))

    if a.show:
        print(json.dumps(d, ensure_ascii=False)[:800])
        return 0
    if not a.yes:
        print("\n[干跑] 加 --yes 才真删")
        return 0
    if not item:
        print("\n⛔ 列表里没有这条，拒绝删除（先确认 aid）")
        return 2

    print("\n→ 执行删除 …")
    r = do_delete(cdp, aid)
    print("  返回：%s" % json.dumps(r, ensure_ascii=False)[:200])
    if not (isinstance(r, dict) and r.get("code") == 0):
        print("⛔ 删除失败")
        return 3

    after_item, _, after_total = find_in_list(cdp, bia, aid)
    print("回读：%d → %d 条；aid=%s %s"
          % (total, after_total, aid, "仍在（失败）" if after_item else "已消失 ✓"))
    if after_item:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
