#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知乎草稿标题同步：A 档去「游戏厅：」前缀 → 平台侧 + 三本台账（2026-11-07，PRD #83）。

🚨 为什么不能直接改 `data/zhihu_drafts.json`：那本账的 `title` 是**建稿时提交
给知乎的标题镜像**，不是「本地待发标题」。2026-11-07 00:2x 实测回读
`GET /api/v4/articles/my_drafts`：草稿箱里 2102032057182446679 的线上标题
正是「游戏厅：Freedoom，免费合法的毁灭战士」，与台账一字不差。单改本地只会
造出一条与平台侧对不上的假记录（`zhihu_ledger_sync.py` 按 article_id +
标题兜底认亲），和 PRD #70「改平台侧已有内容的字段，只认回读」冲突。

所以「同步」= 先改线上草稿标题 → **回读双命中** → 才同步本地：

1. 平台侧 `PATCH /api/articles/{aid}/draft  {"title": 新标题}`
2. 回读判据（缺一不可）：
   - `GET /api/articles/{aid}/draft`：title == 新标题 **且 content 与改前逐字相同**
     （PATCH 是部分更新，但必须实测，不能假设「提交成功」= 正文还在）
   - `GET /api/v4/articles/my_drafts`：按 aid 命中且 title == 新标题
3. 三处本地台账（只改 title 一个字段，别的不碰）：
   - `data/zhihu_drafts.json`
   - `data/publish_manifest.json` 的 `d[cid]["zhihu"]`
   - SQLite `platform_posts` 的 `title`
     ⚠ 必须改 db：`Manifest.load()` 是「JSON 底座 + db 覆盖」（PRD #82），
     只改 JSON 会在下一次 load 时被 db 里的旧标题刷回去。

目标集是**自动发现**的，不写死 case：`zhihu_drafts.json` 里 status=draft、
title 带前缀、且 article_id 在线上草稿箱命中的条目。新标题一律取
`data/toutiao_titles.json[cid]["title"]`（A 档已去前缀的真源）。
台账里另外两条 ep-001 系列草稿箱已无（已发表/已删），属历史留痕，自动跳过。

明确不做：草稿**正文**里那句「「游戏厅」栏目，每期只讲一款游戏：」仍在
（线上旧产物，A 档只清了本地 platform md）。删它属改平台侧正文，另议。

用法::

    python scripts/zhihu_retitle_drafts.py          # 干跑：只报告将改什么
    python scripts/zhihu_retitle_drafts.py --go     # 真改 + 回读 + 同步台账
    python scripts/zhihu_retitle_drafts.py --check  # 只回读核验，0 残留 exit 0
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import atomicio                                                    # noqa: E402
import db                                                          # noqa: E402

PREFIX = "游戏厅："
DRAFTS_PATH = os.path.join(ROOT, "data", "zhihu_drafts.json")
MANIFEST_PATH = os.path.join(ROOT, "data", "publish_manifest.json")
TITLES_PATH = os.path.join(ROOT, "data", "toutiao_titles.json")


# --------------------------------------------------------------------------
# 平台侧（唯一注入点：单测换掉 API，不碰网络）
# --------------------------------------------------------------------------
class ZhihuApi(object):
    def drafts(self):
        """远端草稿箱 -> {article_id: title}。"""
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


# --------------------------------------------------------------------------
# 数据读写
# --------------------------------------------------------------------------
def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _dump_exact(path, obj, indent, orig_bytes):
    """按原格式 dump，且断言「除目标字段外结构完全一致」由调用方负责。

    格式按磁盘字节探测（不猜）：zhihu_drafts.json 是 indent=2、
    publish_manifest.json 是 indent=1，两者都是 LF + 无尾换行。
    """
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    atomicio.write_json(path, obj, indent=indent)
    with open(path, "rb") as f:
        got = f.read()
    if got != text.encode("utf-8"):
        raise RuntimeError("写盘格式不符：%s" % os.path.basename(path))
    return len(orig_bytes), len(got)


def _backup(path):
    os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
    dst = os.path.join(ROOT, "tmp", os.path.basename(path) + ".bak-" +
                       time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, dst)
    return os.path.relpath(dst, ROOT)


def discover(drafts_path=DRAFTS_PATH, titles_path=TITLES_PATH, api=None):
    """返回 [(cid, aid, old_title, new_title)]，只包含线上草稿箱命中的。

    三重闸门：status=draft、title 带前缀、aid 在远端草稿箱、新标题在标题表里。
    """
    api = api or API
    ledger = _load(drafts_path)
    titles = _load(titles_path)
    remote = api.drafts()
    out = []
    for cid, rec in sorted(ledger.items()):
        if not isinstance(rec, dict) or (rec.get("status") or "") != "draft":
            continue
        old = rec.get("title") or ""
        if PREFIX not in old:
            continue
        aid = str(rec.get("article_id") or "")
        if aid not in remote:
            continue
        if remote[aid] != old:
            # 线上标题与台账不一致 ⇒ 先人工对账，绝不猜着改
            continue
        new = ((titles.get(cid) or {}).get("title") or "").strip()
        if not new or PREFIX in new:
            continue
        out.append((cid, aid, old, new))
    return out


def retitle_remote(cid, aid, old, new, api=None):
    """改平台侧并双回读。返回 (ok, 说明)；失败时尝试把正文补回去。"""
    api = api or API
    before = api.detail(aid)
    if not isinstance(before, dict) or not before.get("id"):
        return False, "读不到草稿详情（id 缺失）"
    if (before.get("title") or "") == new:
        return True, "线上已是新标题，跳过 PATCH（幂等）"
    if (before.get("title") or "") != old:
        return False, "详情标题与台账旧值不符：%r" % before.get("title")

    content_before = before.get("content") or ""
    api.patch(aid, {"title": new})
    after = api.detail(aid)
    title_ok = (after.get("title") or "") == new
    content_ok = (after.get("content") or "") == content_before
    if not content_ok:
        # 别把「改标题」做成「洗正文」：原样补回，判失败
        try:
            api.patch(aid, {"content": content_before})
        except Exception:                                          # noqa: BLE001
            pass
        return False, "PATCH 后正文被改动（已尝试原样补回），拒绝同步本地台账"
    if not title_ok:
        return False, "详情回读标题未生效：%r" % after.get("title")
    listed = api.drafts().get(aid)
    if listed != new:
        return False, "草稿箱列表回读未生效：%r" % listed
    return True, "详情 + 草稿箱列表双回读命中"


def sync_local(cid, aid, old, new, drafts_path=DRAFTS_PATH,
               manifest_path=MANIFEST_PATH, conn=None):
    """只改三处 title 字段。返回改动说明列表；任一步失败即抛，不落半截。"""
    notes = []

    # 1) zhihu_drafts.json（indent=2, LF, 无尾换行）
    raw = _read_bytes(drafts_path)
    d = json.loads(raw.decode("utf-8"))
    rec = d.get(cid)
    if not isinstance(rec, dict) or str(rec.get("article_id")) != aid:
        raise RuntimeError("台账里找不到 %s / aid=%s" % (cid, aid))
    if rec.get("title") != new:
        before = rec.get("title")
        if before != old:
            raise RuntimeError("%s 台账标题既不是旧值也不是新值：%r" % (cid, before))
        rec["title"] = new
        _dump_exact(drafts_path, d, 2, raw)
        notes.append("zhihu_drafts: %r → %r" % (before, new))

    # 2) publish_manifest.json 的 d[cid]["zhihu"]（indent=1）
    #    ⚠ 只改 zhihu 记录：同一 cid 的 cnblogs 记录有**完全相同的旧标题**，
    #    那是博客园已发布文章的留痕，绝不能全局字符串替换。
    raw2 = _read_bytes(manifest_path)
    m = json.loads(raw2.decode("utf-8"))
    zr = ((m.get(cid) or {}).get("zhihu") or {})
    if zr and (zr.get("draft_id") or zr.get("remote_id")) == aid:
        if zr.get("title") != new:
            before = zr.get("title")
            if before != old:
                raise RuntimeError("%s manifest.zhihu 标题异常：%r" % (cid, before))
            zr["title"] = new
            _dump_exact(manifest_path, m, 1, raw2)
            notes.append("manifest.zhihu: %r → %r" % (before, new))
    else:
        notes.append("manifest.zhihu: 无对应 aid=%s 的记录，跳过" % aid)

    # 3) SQLite platform_posts —— 只 UPDATE title，别用 upsert_post（会整条重建）
    own = conn is None
    c = conn or db.get_conn()
    try:
        cur = c.execute(
            "UPDATE platform_posts SET title=? WHERE case_id=? "
            "AND platform='zhihu' AND (remote_id=? OR draft_id=?)",
            (new, cid, aid, aid))
        if cur.rowcount:
            notes.append("db platform_posts: %d 行 title 更新" % cur.rowcount)
        else:
            notes.append("db platform_posts: 0 行（已是新值或无此行）")
        if own:
            c.commit()
    finally:
        if own:
            db.close_conn()
    return notes


def run(go=False, check=False, out=sys.stdout, api=None,
        drafts_path=DRAFTS_PATH, manifest_path=MANIFEST_PATH,
        titles_path=TITLES_PATH, conn=None):
    api = api or API
    targets = discover(drafts_path, titles_path, api)
    print("线上草稿箱命中、标题待同步：%d 篇" % len(targets), file=out)
    for cid, aid, old, new in targets:
        print("  %-26s aid=%s\n    %r → %r" % (cid, aid, old, new), file=out)
    if check:
        return 0 if not targets else 1
    if not go:
        print("\n[干跑] 未改任何东西。--go 才会 PATCH + 回读 + 同步台账。", file=out)
        return 0

    ok = fail = 0
    for cid, aid, old, new in targets:
        good, why = retitle_remote(cid, aid, old, new, api)
        print("\n[%s] %s：%s" % ("✓" if good else "✗", cid, why), file=out)
        if not good:
            fail += 1
            continue
        # 平台侧已双回读命中，才允许写本地
        for p in (drafts_path, manifest_path):
            print("  备份 → %s" % _backup(p), file=out)
        try:
            notes = sync_local(cid, aid, old, new, drafts_path,
                               manifest_path, conn)
            for n in notes:
                print("  " + n, file=out)
            ok += 1
        except Exception as e:                                     # noqa: BLE001
            print("  ✗ 本地台账同步失败：%r" % e, file=out)
            fail += 1
    print("\n完成：%d 成功 / %d 失败" % (ok, fail), file=out)
    return 0 if fail == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="知乎草稿标题去栏目前缀（平台侧 + 台账真同步）")
    ap.add_argument("--go", action="store_true", help="真改；默认干跑")
    ap.add_argument("--check", action="store_true", help="只回读核验，0 残留 exit 0")
    a = ap.parse_args(argv)
    return run(go=a.go, check=a.check)


if __name__ == "__main__":
    raise SystemExit(main())
