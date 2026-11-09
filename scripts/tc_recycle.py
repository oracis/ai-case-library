#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""腾讯云创作中心「回收站」列出 / 彻底删除（永久，不可恢复）。

为什么单独一支：`tc_manage.py` 管的是**正常稿件**（删除=进回收站），
回收站里的条目要「彻底删除」（`forever:1`）走的是同一个接口但语义不同，
混在一起容易误删。这里把「列出」与「清空」分开，清空必须显式 `--yes`。

实测（2026-11-09，CDP 9228）：
- 回收站 = `POST /developer/api/creator/articleList {"hostStatus":6,...}`，
  条目 `hostStatus=6`，与创作中心 tab 上「回收站 N」计数一致。
- 彻底删除 = `POST /developer/api/common/delete-content
  {"objId": <articleId>, "module": 1, "forever": 1}`（`forever:0` 才是进回收站）。
- ⛔ 匿名页对回收站条目一律 404，**不能**用来判断是否删掉；判据只能是
  `hostStatus=6` 的条目数与 id 集合。

用法：
    python scripts/tc_recycle.py list                     # 只读
    python scripts/tc_recycle.py list --json
    python scripts/tc_recycle.py purge --ids 2776839 --dry
    python scripts/tc_recycle.py purge --yes              # 清空全部（不可恢复！）
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import yun_finalize as Y          # noqa: E402
import tc_manage as T            # noqa: E402

LIST_JS = r"""
(async function(hs){
  const out = [];
  for (let p = 1; p <= 8; p++) {
    const r = await fetch('/developer/api/creator/articleList',
      {method:'POST', headers:{'Content-Type':'application/json'},
       credentials:'include',
       body: JSON.stringify({hostStatus: hs, sortType:'create', page: p, pageSize: 20})});
    let j = null; try { j = await r.json(); } catch(e) {}
    const list = (j && (j.data && (j.data.list || j.data.records) || j.list || j.records)) || [];
    if (!list.length) break;
    for (const it of list) out.push(it);
    if (list.length < 20) break;
  }
  return JSON.stringify(out);
})
"""

PURGE_JS = r"""
(async function(){
  const r = await fetch('/developer/api/common/delete-content',
    {method:'POST', headers:{'Content-Type':'application/json'},
     credentials:'include',
     body: JSON.stringify({objId: %s, module: 1, forever: 1})});
  let j = null; try { j = await r.json(); } catch(e) {}
  return JSON.stringify({status: r.status, body: j});
})()
"""


def fetch(cdp, hs):
    return json.loads(cdp.eval("(%s)(%d)" % (LIST_JS, hs)) or "[]")


def cmd_list(cdp, args):
    rows = fetch(cdp, 6)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("回收站 %d 条：" % len(rows))
    for r in rows:
        print("  %-8s %s" % (r.get("articleId"), (r.get("title") or "")[:60]))
    return 0


def cmd_purge(cdp, args):
    rows = fetch(cdp, 6)
    ids = [str(r.get("articleId")) for r in rows]
    print("回收站现有 %d 条：%s" % (len(ids), ", ".join(ids)))
    if not ids:
        print("已经是空的，无需操作")
        return 0

    targets = args.ids or ids
    extra = [t for t in targets if t not in ids]
    if extra:
        print("✗ 这些 id 不在回收站里，拒绝执行：%s" % ", ".join(extra))
        return 1
    print("本次将彻底删除 %d 条：%s" % (len(targets), ", ".join(targets)))
    if args.dry:
        print("（预演，未发请求）")
        return 0

    # 落一份删除前清单（pid/标题/时间），便于事后追溯
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "platform": "tencentcloud",
           "action": "purge_recycle_bin", "items": [
               {"articleId": r.get("articleId"), "title": r.get("title"),
                "createTime": r.get("createTime")} for r in rows
               if str(r.get("articleId")) in targets]}
    path = os.path.join(ROOT, "tmp", "tc_recycle_purged_%s.json"
                        % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print("删除前清单已落盘：%s" % path)

    ok, fail = [], []
    for aid in targets:
        r = json.loads(cdp.eval(PURGE_JS % aid) or "{}")
        time.sleep(1.5)
        left = {str(x.get("articleId")) for x in fetch(cdp, 6)}
        gone = aid not in left
        print("  %-8s delete-content=%s ⇒ 回收站剩 %d 条、本条%s"
              % (aid, json.dumps(r, ensure_ascii=False)[:60], len(left),
                 "已消失" if gone else "仍在"))
        (ok if gone else fail).append(aid)
        if not gone:
            print("  ✗ 回读未消失，立即停止（不继续删后面的）")
            break
    print("\n成功 %d：%s" % (len(ok), ok))
    if fail:
        print("失败/中止 %d：%s" % (len(fail), fail))
    return 0 if not fail else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=T.CDP_PORT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list", help="列出回收站（只读）")
    l.add_argument("--json", action="store_true")
    l.set_defaults(fn=cmd_list)

    p = sub.add_parser("purge", help="彻底删除（永久，不可恢复）")
    p.add_argument("--ids", nargs="+", help="只删指定 articleId（缺省=全部）")
    p.add_argument("--yes", action="store_true", help="真正执行；不带则只预演")
    p.add_argument("--dry", action="store_true")
    p.set_defaults(fn=cmd_purge)

    args = ap.parse_args()
    if args.cmd == "purge" and not args.yes:
        args.dry = True
    proc, cdp = Y._connect(headed=False)
    tid = cdp.open(T.LIST_URL)
    try:
        cdp.send("Page.enable")
        time.sleep(8)
        return args.fn(cdp, args)
    finally:
        try:
            cdp.send_browser("Target.closeTarget", {"targetId": tid})
        except Exception:                                  # noqa: BLE001
            pass
        cdp.close()


if __name__ == "__main__":
    sys.exit(main())
