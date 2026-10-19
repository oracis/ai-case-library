# -*- coding: utf-8 -*-
"""B站草稿**就地改标题**（不删、不重建、不产生重复稿）。

为什么要有这个脚本
------------------
`bilibili_publish.save_via_api(replace=True)` 是按**标题**认亲的：
它拿「新标题」去草稿箱里找同名草稿。改标题场景下新旧标题不同 ⇒
必然找不到 ⇒ 直接 return False（不会误建重复稿，但也改不了）。

B站草稿箱**没有独立的 update 接口**，`draft/add` 带 `article_id` 即更新。
所以正确做法是：**先用远端真实标题认出 article_id，再带 aid 回写新标题**。

⚠ 正文**必须从本地母版重建**，不能抄草稿箱里的 `content`
（`draft/view` 返回的 content 只有几百字的预览残文，抄回去会把正文毁掉）。

鲁棒性设计（针对 headless/headful CDP 偶发 30s 不回包超时）
-------------------------------------------------------
1. 每条案例用**独立**的 CDP 连接，并把 socket 超时从默认 30s 提到 120s。
2. 单条失败（超时/断连）自动重连重试，不中断整批。
3. 改后的「回读校验」降级为**尽力而为**：`save_draft` 返回 `code=0`
   已是权威成功信号，校验若因 CDP 抽风超时也不判失败（下次幂等重跑会
   按列表标题对账）。
4. 已对齐的条目用列表标题直接 SKIP，省掉一次 `view_draft`（最易超时的一步）。
5. 运行前清理 9223 上的 B站编辑器残留 tab（tab 堆积会拖垮 WebSocket）。

用法
----
    python -X utf8 scripts/bili_retitle.py --dry      # 只看会改什么
    python -X utf8 scripts/bili_retitle.py            # 实际改
    python -X utf8 scripts/bili_retitle.py --only macwall
"""

import argparse
import json
import os
import re
import sys
import time

# 连 127.0.0.1 必须绕开系统代理，否则 CDP 的 WebSocket 会被掐断
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import bili_draft_api as api          # noqa: E402
import bilibili_publish as bp         # noqa: E402
import wechat_publish as wp           # noqa: E402

OVERRIDES = os.path.join(ROOT, "data", "bili_title_overrides.json")

# 远端草稿 aid -> case id。由 2026-10-14 按「远端真实标题」逐条核对得出，
# 不依赖 publish_manifest.json（台账与远端不一致时以远端为准）。
AID_MAP = {
    "343837": "white-glove-content",
    "343836": "wagiff-app",
    "343835": "stealth-rdp",
    "343834": "quotedeck",
    "343833": "pet-plus-us",
    "343832": "nutria-9e3eaa",
    "343831": "macwall",
    "343830": "homy",
    "343829": "growingupvideo",
    "343828": "fastwrite-io",
    "343827": "downconvert-sketchup-files",
    "343826": "boothie-ai-photo-editor",
    "343502": "vid-ai",
    "343501": "vectosolve",
    "343500": "publbee",
    "343499": "podawaa",
    "343498": "cometly",
    "343497": "augora-ai",
}

SOCK_TIMEOUT = 120  # 默认 30s，CDP 抽风时不够，提到 120s


def fresh_cdp():
    """新建一个干净 CDP 连接，并把 socket 超时调长（针对 CDP 抽风）。"""
    c = wp.CDP(bp.PUB_PORT)
    api._bili_tab(c)
    try:
        c.ws.sock.settimeout(SOCK_TIMEOUT)
    except Exception:
        pass
    return c


def clean_bili_tabs():
    """关掉 9223 上多余的 B站编辑器 tab，只留一个（tab 堆积会拖垮 CDP）。"""
    try:
        c = wp.CDP(bp.PUB_PORT)
        try:
            c.ws.sock.settimeout(20)
        except Exception:
            pass
        targets = c.list_targets()
        pages = [t for t in targets if t.get("type") == "page"]
        bili = [t for t in pages if "member.bilibili.com" in (t.get("url") or "")
                or "space.bilibili.com" in (t.get("url") or "")]
        # 保留第一个，其余关掉
        kill = bili[1:]
        n = 0
        for t in kill:
            try:
                if c.close_target(t.get("targetId")):
                    n += 1
            except Exception:
                pass
        return n, len(bili)
    except Exception as e:
        return -1, str(e)


def retitle_one(cdp, aid, cid, new_title, dry=False, listed_title=None):
    """把 aid 这条草稿的标题改成 new_title，正文用本地母版重建。

    `listed_title`：草稿列表里已有的标题。若它已等于新标题，直接 SKIP，
    省掉一次最易超时的 `draft/view`（18 条逐条 view 会把前台跑超时）。
    """
    html = bp.load_article_html(cid)
    if not html:
        return False, "缺母版 out/articles/%s.html" % cid
    body = bp.clean_body(html)
    plain = re.sub(r"<[^>]+>", "", body)

    # 已对齐：用列表标题判断，跳过（不碰 CDP）
    if listed_title is not None and listed_title == new_title:
        return True, "SKIP:列表里已是新标题"

    risk = bp.risk_check(new_title + plain)
    if risk:
        return False, "风险词未清：%s" % risk
    if len(plain) < 200:
        return False, "正文仅 %d 字" % len(plain)

    if dry:
        return True, "DRY 旧《%s》→ 新《%s》（正文 %d 字，未提交）" % (
            (listed_title or "?")[:26], new_title, len(plain))

    old = api.view_draft(cdp, aid)
    if not old:
        return False, "读不到草稿 aid=%s" % aid
    old_title = (old.get("title") or "").strip()
    # 注意：B站草稿正文图片存在 content 段落节点里，顶层 image_urls/
    # origin_image_urls 是「合集封面」用的，本 pipeline 建稿时未设封面，
    # 所以通常为 None。build_arg 只在传入 truthy 时才设封面；传 None 不丢任何东西。
    image_urls = old.get("origin_image_urls") or old.get("image_urls")
    if old_title == new_title:
        return True, "SKIP:已是新标题"

    arg = api.build_arg(
        new_title, body, article_id=aid,
        image_urls=image_urls,
        private_pub=old.get("private_pub", 2),
        original=old.get("original", 0),
        reprint=old.get("reprint", 1))
    out = api.save_draft(cdp, arg)
    if not isinstance(out, dict) or out.get("code") != 0:
        return False, "draft/add 失败：%s" % json.dumps(
            out, ensure_ascii=False)[:160]

    # 回读校验：尽力而为（CDP 抽风时即便超时，save 已 code=0 视为成功，
    # 下次幂等重跑会按列表标题对账）。
    try:
        time.sleep(1.0)
        after = api.view_draft(cdp, aid)
        got = (after.get("title") or "").strip()
        if got != new_title:
            return True, "OK(改) code=0 但回读仍为《%s》，以重跑对账" % got[:30]
        return True, "OK 旧《%s》→ 新《%s》" % (old_title[:26], new_title)
    except Exception as e:
        return True, "OK(改) code=0，回读超时(%s)，以重跑对账" % e


def main(argv=None):
    ap = argparse.ArgumentParser(description="B站草稿就地改标题")
    ap.add_argument("--dry", action="store_true", help="只看不改")
    ap.add_argument("--only", default="", help="只改这一条 case id")
    args = ap.parse_args(argv)

    ov = json.load(open(OVERRIDES, encoding="utf-8"))

    if not args.dry:
        n, info = clean_bili_tabs()
        print("清理 9223 残留 B站 tab：关掉 %s（共 %s 个）" % (n, info))

    cdp = fresh_cdp()
    # 以远端草稿为准，只对**实际存在**的草稿动手
    drafts = api.list_drafts(cdp)
    by_aid = {str(d.get("article_id")): d for d in drafts}
    print("远端草稿 %d 条（端口 %d）\n" % (len(drafts), bp.PUB_PORT))

    plan = []
    for aid, cid in sorted(AID_MAP.items()):
        if args.only and cid != args.only:
            continue
        new = ov.get(cid)
        if not new:
            print("  ! %s 没有 override，跳过" % cid)
            continue
        new = new[:30]
        if aid not in by_aid:
            print("  ! aid=%s（%s）远端已不存在，跳过" % (aid, cid))
            continue
        plan.append((aid, cid, new))

    print("待改 %d 条%s\n" % (len(plan), "（DRY）" if args.dry else ""))
    ok, fail = 0, []

    cdp = fresh_cdp()  # 一个连接复用；SKIP 项不碰它，改标题项共用，异常才重连
    for aid, cid, new in plan:
        lt = (by_aid[aid].get("title") or "").strip()
        good, msg = False, ""
        for attempt in range(3):
            try:
                good, msg = retitle_one(cdp, aid, cid, new, dry=args.dry,
                                        listed_title=lt)
                break
            except Exception as e:  # 单条超时/断连不再中断整批
                print("  [warn] %s 第%d次尝试异常：%s" % (cid, attempt + 1, e))
                try:
                    cdp = fresh_cdp()
                except Exception:
                    pass
                good, msg = False, "异常:%s" % e
                time.sleep(2)
        print("  %-26s %s" % (cid[:26], msg))
        if good:
            ok += 1
        else:
            fail.append((cid, msg))
    print("\n完成：成功 %d / 失败 %d" % (ok, len(fail)))
    for cid, msg in fail:
        print("  ✗ %s —— %s" % (cid, msg))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
