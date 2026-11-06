# -*- coding: utf-8 -*-
"""刷新**B站二号草稿箱**里那批 10-31 批量建的旧稿：换标题 + 正文对齐当前母版。

背景（2026-11-06）
------------------
二号草稿箱剩 8 条（376917 Quake ~ 376927 Minetest），全是 2026-10-31 一次
批量建的：
  · 标题取的是 `meta.title_alt` 的旧栏目写法「游戏厅：××」——用户实拍反馈
    「标题没信息量」，一号同款问题已在 11-05 用技术向标题修掉，二号这批漏了；
  · 正文取自 11-02 23:02 的产物，而母版 11-02 23:25 做过「去站内推广 +
    补『先说清楚：这里只有源码』」重生成 ⇒ 箱里这批是**旧口径**。

FIFO 发到它们时会原样重演「标题只有栏目名」的事故，所以在排队前先逐条改。

为什么不能直接跑 `bilibili_publish.py draft`
-------------------------------------------
`cmd_draft` 只会**新建**草稿（`build_arg` 不带 article_id），跑一遍箱里就多
8 条重复稿。B站草稿箱没有独立 update 接口，`draft/add` **带 article_id
即更新**（见 bili_draft_api 模块 docstring）⇒ 必须带 aid 回写。

正文为什么从 md 重建、不抄远端
----------------------------
`draft/view` 返回的 content 是编辑器结构，但只有几百字的预览残文（B站
截断），抄回去会把正文毁掉。这里走和 `cmd_draft` **完全同一条**管线
（`strip_source_links` → `md_to_blocks` → `blocks_to_html(footer=False)`），
保证「现在改的」和「以后重建的」逐字一致。

幂等判据（重跑安全）
------------------
远端标题 == meta.title_alt 且正文含 `MARK` 段落 ⇒ SKIP。MARK 是 11-02
母版新增的那句「这里只有源码，没有游戏文件」，旧稿没有。

用法
----
    python -X utf8 scripts/bili2_draft_refresh.py            # 干跑（默认）
    python -X utf8 scripts/bili2_draft_refresh.py --go       # 真改
    python -X utf8 scripts/bili2_draft_refresh.py --go --only ep-004-quake
"""

import argparse
import json
import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili_draft_api as api          # noqa: E402
import bilibili_publish as bp         # noqa: E402
import toutiao_publish as tt          # noqa: E402
import wechat_publish as wp           # noqa: E402

GAME_REPO = os.path.abspath(os.environ.get("GAME_REPO") or "D:/open-source-games")

# 远端 aid -> 游戏仓 slug。2026-11-06 按「远端草稿列表标题」逐条核对得出，
# 不依赖任何本地台账（台账与远端不一致时以远端为准）。
AID_MAP = {
    "376917": "ep-004-quake",
    "376919": "ep-005-supertux",
    "376920": "ep-006-nethack",
    "376921": "ep-007-openttd",
    "376923": "ep-008-mindustry",
    "376924": "ep-009-lichess",
    "376925": "ep-010-cataclysm-dark-days-ahead",
    "376927": "ep-011-minetest",
}

TITLE_MAX = 40          # B站专栏标题硬上限（cmd_draft 也是这么截的）
BODY_MIN = 200
MARK = "这里只有源码，没有游戏文件"      # 11-02 母版新增段的锚点
SOCK_TIMEOUT = 120


def fresh_cdp():
    c = wp.CDP(bp.PUB_PORT)
    api._bili_tab(c)
    try:
        c.ws.sock.settimeout(SOCK_TIMEOUT)
    except Exception:
        pass
    return c


def load_meta(slug):
    p = os.path.join(GAME_REPO, "content", slug, "meta.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_body(slug):
    """与 `bilibili_publish.cmd_draft` 完全同一条 md→HTML 管线。"""
    p = os.path.join(GAME_REPO, "content", slug, "article-free.md")
    if not os.path.isfile(p):
        raise SystemExit("✗ 缺母版 %s" % p)
    with open(p, encoding="utf-8") as f:
        md = f.read()
    md, dropped = tt.strip_source_links(md)
    body = tt.blocks_to_html(tt.md_to_blocks(md), footer=False)
    return body, dropped


def cover_path(slug):
    for name in ("cover-bili.jpg", "cover.png"):
        p = os.path.join(GAME_REPO, "content", slug, name)
        if os.path.isfile(p):
            return p
    return None


def refresh_one(cdp, aid, slug, title, body, go=False):
    """改一条。返回 (ok, msg)。"""
    plain = tt.strip_md(body)
    if len(title) > TITLE_MAX:
        return False, "标题 %d 字 > %d" % (len(title), TITLE_MAX)
    if len(plain) < BODY_MIN:
        return False, "正文仅 %d 字" % len(plain)

    old = api.view_draft(cdp, aid)
    if not old:
        return False, "读不到草稿 aid=%s" % aid
    old_title = (old.get("title") or "").strip()
    old_body = old.get("content") or ""
    if old_title == title and MARK in old_body:
        return True, "SKIP：标题与正文都已是新口径"

    if not go:
        return True, "DRY 《%s》→《%s》 新正文 %d 字（远端现有 %d 字）" % (
            old_title[:24], title, len(plain), len(old_body))

    # 封面：旧稿建稿时已传过图床，沿用；没有才补传（不重复占图床）
    image_urls = old.get("origin_image_urls") or old.get("image_urls")
    if not image_urls:
        cov = cover_path(slug)
        if cov and os.path.isfile(cov):
            curl = api.upload_cover(cdp, cov)
            if curl:
                image_urls = [curl]
                print("    ✓ 补传封面：%s" % curl[:64], flush=True)
            else:
                print("    [warn] 封面上传失败，草稿将无封面", flush=True)

    arg = api.build_arg(
        title, body, article_id=aid, image_urls=image_urls,
        private_pub=old.get("private_pub", 2),
        original=old.get("original", 0),
        reprint=old.get("reprint", 1))
    out = api.save_draft(cdp, arg)
    if not isinstance(out, dict) or out.get("code") != 0:
        return False, "draft/add 失败：%s" % json.dumps(
            out, ensure_ascii=False)[:160]

    time.sleep(1.2)
    after = api.view_draft(cdp, aid) or {}
    got_t = (after.get("title") or "").strip()
    got_b = after.get("content") or ""
    if got_t != title:
        return False, "code=0 但回读标题仍是《%s》" % got_t[:30]
    if MARK not in got_b:
        return False, "code=0 但回读正文缺新段落锚点（%d 字）" % len(got_b)
    return True, "OK 《%s》→《%s》 正文 %d 字" % (
        old_title[:24], title, len(got_b))


def main(argv=None):
    ap = argparse.ArgumentParser(description="刷新 B站二号旧草稿（标题+正文）")
    ap.add_argument("--go", action="store_true", help="真改（默认只干跑）")
    ap.add_argument("--only", default="", help="只处理这个 slug")
    a = ap.parse_args(argv)

    if bp.PUB_PORT != 9224:
        print("⚠ 当前端口 %d 不是二号的 9224，确认 CDP_BILI_PORT" % bp.PUB_PORT)

    plan = []
    for aid, slug in sorted(AID_MAP.items()):
        if a.only and slug != a.only:
            continue
        meta = load_meta(slug)
        title = str(meta.get("title_alt") or "").strip()
        if not title:
            raise SystemExit("%s 缺 meta.title_alt" % slug)
        body, dropped = build_body(slug)
        plan.append((aid, slug, title, body, dropped))

    print("端口 %d ｜ 待处理 %d 条 ｜ %s\n"
          % (bp.PUB_PORT, len(plan), "真改" if a.go else "DRY"))
    cdp = fresh_cdp()
    ok, fail = 0, []
    for aid, slug, title, body, dropped in plan:
        msg = ""
        for attempt in range(3):
            try:
                good, msg = refresh_one(cdp, aid, slug, title, body, go=a.go)
                break
            except Exception as e:                 # 单条抽风不中断整批
                print("    [warn] %s 第%d次异常：%s" % (slug, attempt + 1, e))
                try:
                    cdp = fresh_cdp()
                except Exception:
                    pass
                good, msg = False, "异常:%s" % e
                time.sleep(2)
        if dropped:
            msg += "（剥离外链 %d 处）" % dropped
        print("  %-8s %-36s %s" % (aid, slug, msg), flush=True)
        if good:
            ok += 1
        else:
            fail.append((slug, msg))
    print("\n完成：成功 %d / 失败 %d" % (ok, len(fail)))
    for slug, msg in fail:
        print("  ✗ %s —— %s" % (slug, msg))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
