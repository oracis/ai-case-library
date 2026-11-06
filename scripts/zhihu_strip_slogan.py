#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删掉知乎线上草稿正文里那句一字不差的栏目标语（2026-11-07，PRD #84）。

背景：`open-source-games/scripts/strip_column_line.py` 早已把本地 97 份
`content/*/platform/*.md` 的栏目标语行清干净（`--check` 0 残留），但**已建好的
线上草稿是旧产物**，正文里还挂着：

    <p>「游戏厅」栏目，每期只讲一款游戏：它是什么、怎么玩、它用的是什么技术。</p>

#83 把这两篇草稿的标题去了「游戏厅：」前缀后，这句与标题更不自洽了。

🚨 边界（别顺手扩）：
- 只处理**线上草稿箱里 content 恰好含一段完整 SLOGAN** 的草稿，自动发现。
- 只删那一个 `<p>SLOGAN</p>` 段（连同其换行），其余字节逐字不动；
  变体、出现多次、还夹着别的「游戏厅」字样的一律跳过，只报告。
- 不动 `title` / `state` / 本地任何 `data/*.json`；**本地产物也不动**（已清）。
- 其余 43 处历史留痕（manifest、cnblogs_drafts、wechat_standalone、
  游戏仓 publish_state 等）按 A 档 #79 口径不碰。

判据（只认回读，不认「PATCH 200」）：
1. `GET /api/articles/{aid}/draft`：content 逐字等于删后的期望值，
   且 title / state 未被这次写入洗掉；
2. `summary` 是服务端派生字段，也含同一句 ⇒ 回读检查是否随 content 重算；
   没重算就显式 PATCH 一次，仍含则判失败（如实报告，不假装清干净）；
3. `GET /api/v4/articles/my_drafts` 按 aid 命中（草稿还在，没被写没）。

失败时把原 content/summary 原样补回去（回滚），绝不留下半截稿。
改前把完整详情备份到 `tmp/`，可人工回滚。

用法::

    python scripts/zhihu_strip_slogan.py           # 干跑
    python scripts/zhihu_strip_slogan.py --go      # 真改 + 回读
    python scripts/zhihu_strip_slogan.py --check   # 0 残留 exit 0
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# 与 open-source-games/scripts/strip_column_line.py 的 SLOGAN 常量一字不差
SLOGAN = "「游戏厅」栏目，每期只讲一款游戏：它是什么、怎么玩、它用的是什么技术。"
PARA = "<p>" + SLOGAN + "</p>"
KEYWORD = "游戏厅"


class ZhihuApi(object):
    def drafts(self):
        import zhihu_creator as ZC
        st, d = ZC.call_json("GET", "/api/v4/articles/my_drafts?offset=0&limit=100")
        if st != 200:
            raise RuntimeError("读草稿箱失败 HTTP %s" % st)
        return {str(it.get("id")): (it.get("title") or "")
                for it in (d.get("data") or [])}

    def detail(self, aid):
        import zhihu_publish as ZP
        return ZP._call_zhuanlan("GET", "/api/articles/%s/draft" % aid, {})

    def patch(self, aid, payload):
        import zhihu_publish as ZP
        return ZP._call_zhuanlan("PATCH", "/api/articles/%s/draft" % aid, payload)


API = ZhihuApi()


def strip_content(content):
    """定点删一段，返回 (新 content, 说明)；不满足安全条件返回 (None, 原因)。"""
    if not isinstance(content, str):
        return None, "content 不是字符串"
    if PARA not in content:
        return None, "没有完整标语段（可能是已清或变体）"
    if content.count(PARA) != 1:
        return None, "标语段出现 %d 次，>1 不猜" % content.count(PARA)
    if content.count(KEYWORD) != 1:
        return None, "还有别的「游戏厅」字样（%d 处），只报告不动" % content.count(KEYWORD)
    # 独立成行：前后各吃一个换行，只保留一个，段落间距不变。
    # 用 split(…, 1) 而不是 replace：head/tail 原样拼回 ⇒ 其余字节天然逐字不变。
    seg = "\n" + PARA + "\n"
    if content.count(seg) == 1:
        head, tail = content.split(seg, 1)
        new = head + "\n" + tail
    else:
        head, tail = content.split(PARA, 1)
        new = head + tail
    if KEYWORD in new or PARA in new:
        return None, "删后仍有栏目字样，拒绝"
    if new.count("<h2>") != content.count("<h2>"):
        return None, "结构标签数变了，拒绝"
    return new, "删 1 段（%d 字符）" % (len(content) - len(new))


def _backup(aid, detail):
    os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
    p = os.path.join(ROOT, "tmp", "zhihu_slogan_%s_%s.json" %
                     (aid, time.strftime("%Y%m%d-%H%M%S")))
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"aid": aid, "detail": detail}, f, ensure_ascii=False, indent=2)
    return os.path.relpath(p, ROOT)


def do_one(aid, title, api=None):
    """改一篇并回读。返回 (ok, 说明)。"""
    api = api or API
    before = api.detail(aid)
    if not isinstance(before, dict) or not before.get("id"):
        return False, "读不到草稿详情"
    old_content = before.get("content") or ""
    new_content, why = strip_content(old_content)
    if new_content is None:
        return False, why

    old_title, old_state = before.get("title"), before.get("state")
    old_summary = before.get("summary") or ""
    bak = _backup(aid, before)

    def rollback(reason):
        payload = {"content": old_content}
        if old_summary:
            payload["summary"] = old_summary
        try:
            api.patch(aid, payload)
        except Exception:                                          # noqa: BLE001
            pass
        return False, "%s（已尝试回滚，备份 %s）" % (reason, bak)

    api.patch(aid, {"content": new_content})
    after = api.detail(aid)
    if (after.get("content") or "") != new_content:
        return rollback("详情 content 回读不一致")
    if after.get("title") != old_title or after.get("state") != old_state:
        return rollback("title/state 被这次写入改动")

    # summary 是服务端派生字段：先看有没有随 content 重算
    summary_note = ""
    if KEYWORD in (after.get("summary") or ""):
        if KEYWORD in old_summary:
            cleaned = old_summary.replace(SLOGAN + " ", "").replace(SLOGAN, "")
            if KEYWORD not in cleaned:
                api.patch(aid, {"summary": cleaned})
                after = api.detail(aid)
                if (after.get("content") or "") != new_content:
                    return rollback("补写 summary 时正文被改动")
                summary_note = "；summary 未自动重算，已显式补写"
            else:
                summary_note = "；summary 仍是旧值（服务端派生，未接受补写）"
        else:
            summary_note = "；summary 含栏目字样但无法确定来源"
        if KEYWORD in (after.get("summary") or ""):
            return rollback("summary 仍含栏目字样" + summary_note)
    else:
        summary_note = "；summary 已随 content 重算"

    listed = api.drafts()
    if aid not in listed:
        return rollback("草稿箱列表按 aid 回读不到该稿")
    return True, "%s%s；详情 + 草稿箱列表双回读命中（备份 %s）" % (why, summary_note, bak)


def targets(api=None):
    """自动发现：草稿箱里 content 需要清理的稿。返回 [(aid, title, new, why)]。"""
    api = api or API
    out = []
    for aid, title in sorted(api.drafts().items()):
        d = api.detail(aid)
        new, why = strip_content(d.get("content") or "")
        out.append((aid, title, new, why))
    return out


def run(go=False, check=False, out=sys.stdout, api=None):
    api = api or API
    all_rows = targets(api)                 # 只回读一轮，别发两次请求
    rows = [r for r in all_rows if r[2] is not None]
    print("线上草稿箱待清标语：%d 篇" % len(rows), file=out)
    for aid, title, _new, why in rows:
        print("  %s  %r  %s" % (aid, title, why), file=out)
    others = [(a, w) for a, _t, n, w in all_rows if n is None and
              w != "没有完整标语段（可能是已清或变体）"]
    for aid, why in others:
        print("  ⚠ 跳过 %s：%s" % (aid, why), file=out)
    if check:
        return 0 if not rows else 1
    if not go:
        print("\n[干跑] 未改任何东西。--go 才真删。", file=out)
        return 0
    ok = fail = 0
    for aid, title, _new, _why in rows:
        good, why = do_one(aid, title, api)
        print("\n[%s] %s %r：%s" % ("✓" if good else "✗", aid, title, why), file=out)
        ok += bool(good)
        fail += (not good)
    print("\n完成：%d 成功 / %d 失败" % (ok, fail), file=out)
    return 0 if fail == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="删知乎线上草稿正文里的栏目标语段")
    ap.add_argument("--go", action="store_true", help="真改；默认干跑")
    ap.add_argument("--check", action="store_true", help="只回读核验，0 残留 exit 0")
    a = ap.parse_args(argv)
    return run(go=a.go, check=a.check)


if __name__ == "__main__":
    raise SystemExit(main())
