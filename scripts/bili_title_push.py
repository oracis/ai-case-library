#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站草稿：改标题（去金额）+ 补发缺失的草稿。

## 为什么标题要单独一趟

封面在2026-10-02 已经统一成「项目名 + 一句话介绍」，但草稿**标题**还是
「月收$1.6K：给跨境团队搭云通信」这种旧口径。金额是抓取当天的快照，
站点自己会变 —— 封面印快照金额已经不对，标题再印一遍等于把同一个
不稳定数字在两个位置各撒一次。

## 为什么不用 draft/update

B站**没有** update/modify 接口（`bili_draft_api.py` 头部实测记录）。
`draft/add` 带着 `article_id` 就是更新 —— 所以这里的做法是：

    draft/view 读回真实草稿 → 只改 title 字段 → 原 article_id 带回去 add

正文、封面、分类、分区等字段全部**原样带回**，不重新生成
（重新生成会有「正文被重新洗过」的风险，而这次的需求只是改标题）。

## 验证判据

`cover-status` / `draft/list` 里的布尔都不可信，必须比**完整标题字符串**。
所以流程是「读基线 → 改 → 再读 → 逐条比对」，且标题必须精确等于
`bili_title_overrides.json` 里的值。

## 用法

    python -X utf8 scripts/bili_title_push.py --plan
    python -X utf8 scripts/bili_title_push.py --grab
    python -X utf8 scripts/bili_title_push.py --apply
    python -X utf8 scripts/bili_title_push.py --check
    python -X utf8 scripts/bili_title_push.py --republish     # 补发远端缺失的

台账：`out/bili_title_push.json`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bilibili_publish as bp          # noqa: E402
import bili_draft_api as api            # noqa: E402
import wechat_publish as wp# noqa: E402

LEDGER = os.path.join(bp.ROOT, "out", "bili_title_push.json")
SEP = "─" * 78


def load_ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_ledger(d):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, LEDGER)


def connect():
    """起 Chrome + 驱动必须在**同一个进程**里 ——
    分成两次调用时 Chrome 会被环境回收（症状：10061 连不上）。

    ⚠ profile 必须是 `chrome-debug-profile`（B站/头条/小红书那份），
    不是公众号的 ChromeCDP —— 用错 profile症状是「B站页面不存在」。
    """
    import chrome_cdp_launch as cl
    wp._autostart_chrome_if_needed(bp.PUB_PORT, profile=cl.PLAT_PROFILE)
    cdp = wp.CDP(bp.PUB_PORT)
    api._bili_tab(cdp, new_if_none=True)
    return cdp


def all_remote_drafts(cdp):
    """远端草稿列表：{cid: draft}。标题反查靠 out/xhs/*/note.json，
    所以 B站这边用**旧标题**做键 —— 改完标题就再也反查不回来了。"""
    drafts = api.list_drafts(cdp, pn=1, ps=200)
    out = {}
    for d in drafts:
        t = (d.get("title") or "").strip()
        if not t:
            continue
        cid = _cid_by_title_any(t)
        out[cid or("unknown:" + str(d.get("article_id")))] = d
    return out


def _cid_by_title_any(title):
    """任意来源标题 -> cid。按「新表 → 旧表 → 小红书 note.json → 头条 meta」找。

    ⚠ 必须多路反查：远端标题的来源不止一处 ——
    `Quran Unlock：移动 app`（B站实际）既不在 xhs_title_overrides 里，
    也不在 out/xhs/note.json 里（那边是「月收$659：信仰类习惯打卡 App」），
    只在头条 meta.json 里。反查漏一路 ⇒ 那条会被记成 `unknown:<aid>`，
    之后所有按 cid 的操作都会漏掉它。
    """
    t = (title or "").strip()
    if not t:
        return None
    # 1) 新标题表（改完之后的标题）
    for cid, nt in bp._bili_title_overrides().items():
        if nt == t:
            return cid
    # 2) 旧标题表（改之前的标题）
    try:
        with open(os.path.join(bp.ROOT, "data", "xhs_title_overrides.json"),
                  encoding="utf-8") as f:
            old = json.load(f)
        for cid, ot in old.items():
            if ot == t:
                return cid
    except (ValueError, OSError):
        pass
    # 3) 小红书笔记标题（B站标题同源于那套文案）
    try:
        import xhs_publish as xp
        got = xp._card_to_cid(t)
        if got:
            return got
    except Exception:                                      # noqa: BLE001
        pass
    # 4) 头条 meta.json（必须是最后兜底：39 份文件逐个读，最慢）
    tt = bp.OUT_TT
    if os.path.isdir(tt):
        for cid in os.listdir(tt):
            mp = os.path.join(tt, cid, "meta.json")
            if not os.path.isfile(mp):
                continue
            try:
                with open(mp, encoding="utf-8") as f:
                    if (json.load(f).get("title") or "").strip() == t:
                        return cid
            except (ValueError, OSError):
                continue
    return None


def _build_update_arg(draft, want):
    """把 draft/view 的真实草稿转成 draft/add 接受的 arg，**只改标题**。

    ⚠⚠ 三次踩坑换来的结构认知（2026-10-03，别改回去）：

    1. `draft/view` 返回**扁平结构**，`draft/add` 要的是**嵌套 arg**。
       直接把 view 结果当 arg 发出去 ⇒ `code=0` 但什么都没改
       （改的`arg.title` 根本不存在，请求体里没有这个字段）。
       反过来把整个 view 结构原样回传 ⇒ `-400 请求错误`
       （多了 opus_id/list/edit_url 这些**只读**字段）。
    2. **标题有两处**：`arg.title` 和 `arg.opus.title`。
       只改一处会出现「列表显示新标题、正文页还是旧标题」。
    3. 分区字段在 view 里是 `category.id`，在 arg 里叫 `category_id`。

    正文原样用 view 里的 `opus.content.paragraphs`（含行内 style）带回，
    **不重新生成** —— 走 build_arg 会把粗体/颜色/居中全洗掉。
    """
    opus = draft.get("opus") or {}
    arg = {
        "type": draft.get("type") or 4,
        "template_id": opus.get("template_id") or draft.get("template_id") or 1,
        "category_id": (draft.get("category") or {}).get("id")
                       or draft.get("category_id") or 0,
        "title": want,
        "private_pub": draft.get("private_pub", 2),
        "reprint": draft.get("reprint", 1),
        "original": draft.get("original", 0),
        "list_id": 0,
        "comment_selected": draft.get("comment_selected", 0),
        "up_closed_reply": draft.get("up_closed_reply", 0),
        "timer_pub_time": draft.get("timer_pub_time", 0),
        "only_fans_level": draft.get("only_fans_level", 0),
        "only_fans_dnd": draft.get("only_fans_dnd", 0),
        "summary": draft.get("summary") or "",
        "opus": {
            "opus_source": opus.get("opus_source", 2),
            "title": want,                       # ← 第二处标题
            "content": opus.get("content") or {"paragraphs": []},
            "pub_info": opus.get("pub_info") or {},
            "attachments": opus.get("attachments") or {"is_aigc": 0},
        },
    }
    if draft.get("summary") is None:
        arg["summary"] = ""
    iu = draft.get("image_urls") or []
    if iu:
        arg["image_urls"] = list(iu)[:1]
    arg["article_id"] = draft.get("article_id")
    return arg


def _strip_money(t):
    return bool(bp.TITLE_MONEY_RE.search(t or ""))


def _needs_change(cid, cur):
    """这条要不要改。

    ⚠ 判据**不能只看「带没带金额」** —— `Quran Unlock：移动 app`
    不带金额，但它是旧文案（还是最早那版一行的 h1），口径与其它38 条
    不一致。所以真正的判据是「当前标题 != 目标标题」。
    """
    want = bp._bili_title_overrides().get(cid)
    if not want:
        return False, "没有目标标题"
    if (cur or "").strip() == want:
        return False, "已一致"
    return True, ("含金额" if _strip_money(cur) else "旧文案")


def cmd_plan(_args):
    """只打印要做什么，不碰远端。"""
    ov = bp._bili_title_overrides()
    print("标题覆盖表 %d 条｜目标：B站草稿标题一律不带金额" % len(ov))
    print(SEP)
    n = 0
    for cid in sorted(ov):
        new = ov[cid]
        old = _old_title(cid)
        if old and old == new:
            continue
        n += 1
        print("  %-22s %s" % (cid, old or "（远端无草稿）"))
        print("  %-22s   -> %s" % ("", new))
    print(SEP)
    print("需要改 %d 条｜远端缺失需补发 %d 条"
          % (n, len(ov) - len(bp._bili_title_overrides())))
    print("提示：真正缺失的清单要看--grab 的远端回读")


def _old_title(cid):
    """本地记的旧标题（来自 xhs_title_overrides.json / toutiao meta.json）。"""
    try:
        with open(os.path.join(bp.ROOT, "data", "xhs_title_overrides.json"),
                  encoding="utf-8") as f:
            old = json.load(f)
        if cid in old:
            return old[cid]
    except (ValueError, OSError):
        pass
    mp = os.path.join(bp.OUT_TT, cid, "meta.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8") as f:
                return (json.load(f).get("title") or "").strip()
        except (ValueError, OSError):
            pass
    return None


def cmd_grab(_args):
    """抓远端真实标题做基线。"""
    cdp = connect()
    rem = all_remote_drafts(cdp)
    led = load_ledger()
    # ⚠ 每次**重建**基线，不能往上一轮上合并 ——
    # 反查逻辑修好后，上一轮记的 `unknown:<aid>` 幽灵键会永远留在台账里，
    # 计数虚高、还会让「已一致」和「要改」两边都算错。
    base = {}
    for cid, d in rem.items():
        aid = d.get("article_id")
        t = (d.get("title") or "").strip()
        need, why = _needs_change(cid, t)
        base[cid] = {"article_id": aid, "title": t,
                     "has_money": _strip_money(t), "needs_change": need,
                     "why": why}
    led["baseline"] = base
    save_ledger(led)
    todo = [c for c, v in base.items() if v.get("needs_change")]
    print("远端草稿 %d 条｜需要改标题 %d 条（其中带金额 %d）"
          % (len(base), len(todo),
             sum(1 for c in todo if base[c].get("has_money"))))
    print(SEP)
    for cid in sorted(base):
        v = base[cid]
        mark = ("← 改（%s）" % v.get("why")) if v.get("needs_change") else "✓ 已一致"
        print("  %-22s aid=%-8s %s %s"
              % (cid, v.get("article_id"), v.get("title"), mark))
    print(SEP)
    print("基线已存", LEDGER)


def cmd_apply(args):
    """逐条：view -> 只改 title -> 原 article_id 带回 add。"""
    led = load_ledger()
    base = led.get("baseline") or {}
    if not base:
        print("没有基线，先跑 --grab")
        return
    targets = [c for c, v in base.items() if v.get("needs_change")]
    if args.case:
        targets = [c for c in targets if c == args.case]
    if args.batch:
        targets = targets[:args.batch]
    if not targets:
        print("没有要改的（基线里没有带金额的标题）")
        return

    cdp = connect()
    done = led.get("applied") or {}
    ok = fail = 0
    for i, cid in enumerate(targets, 1):
        want = bp._bili_title_overrides().get(cid)
        aid = base[cid].get("article_id")
        if not want:
            print("[%d/%d] %-22s SKIP 没有新标题" % (i, len(targets), cid))
            continue
        # 断点续跑：台账里已成功且标题目标没变的跳过。
        # ⚠ 不能只看「done 里有这个 cid」—— 目标标题可能又改了，
        # 所以要比对记录的 title 与本次 want 是否一致。
        rec = done.get(cid) or {}
        if rec.get("ok") and rec.get("title") == want:
            print("[%d/%d] %-22s SKIP 已完成" % (i, len(targets), cid))
            continue
        try:
            draft = api.view_draft(cdp, aid)
            # ⚠ 只改标题，正文/封面/分区原样带回（见 _build_update_arg 的结构说明）
            arg = _build_update_arg(draft, want)
            assert arg["title"] == want and arg["opus"]["title"] == want
            assert arg.get("article_id") == aid, "article_id 必须原样带回，否则变新建"
            r = api.save_draft(cdp, arg)
            if (r or {}).get("code") == 0:
                print("[%d/%d] %-22s OK  %s"
                      % (i, len(targets), cid, want))
                done[cid] = {"ok": True, "title": want, "aid": aid,
                             "at": time.strftime("%Y-%m-%d %H:%M:%S")}
                ok += 1
            else:
                print("[%d/%d] %-22s FAIL %s"
                      % (i, len(targets), cid, r))
                done[cid] = {"ok": False, "err": str(r)[:200]}
                fail += 1
        except Exception as exc:                          # noqa: BLE001
            print("[%d/%d] %-22s ERROR %s"
                  % (i, len(targets), cid, str(exc)[:120]))
            done[cid] = {"ok": False, "err": str(exc)[:200]}
            fail += 1
        led["applied"] = done
        save_ledger(led)
        time.sleep(1.5)
    print(SEP)
    print("本批成功 %d / 失败 %d" % (ok, fail))


def cmd_check(_args):
    """独立回读：逐条用 draft/view 比标题是否精确等于目标值。

    ⚠ **不能用 draft/list 的title 做判据**（2026-10-03 实测）：
    list 返回的可能是服务端摘要缓存，改完之后仍显示旧标题，
    38条会一起报 NOT_FOUND —— 看起来像「全失败」，实际是判据取错了字段。
    `draft/view` 才是真值（title 在**顶层**，不在 arg 里）。
    """
    led = load_ledger()
    base = led.get("baseline") or {}
    if not base:
        print("没有基线，先跑 --grab")
        return
    cdp = connect()
    ov = bp._bili_title_overrides()
    same = wrong = 0
    bad = []
    print(SEP)
    for cid in sorted(base):
        aid = base[cid].get("article_id")
        want = ov.get(cid)
        try:
            d = api.view_draft(cdp, aid)
            cur = (d.get("title") or "").strip()
        except Exception as exc:                          # noqa: BLE001
            print("  %-22s READ_ERR %s" % (cid, str(exc)[:80]))
            bad.append(cid)
            continue
        aid_now = d.get("article_id")
        if str(aid_now) != str(aid):
            # 说明 save 走成了新建 —— 草稿数会多出来，必须报
            print("  %-22s AID_CHANGED %s -> %s（save 变成新建了？）"
                  % (cid, aid, aid_now))
            bad.append(cid)
            continue
        if cur == want:
            same += 1
        else:
            wrong += 1
            bad.append(cid)
            print("  %-22s WRONG远端=%r 期望=%r" % (cid, cur, want))
    print(SEP)
    print("标题正确 %d / 不对 %d （共 %d）" % (same, wrong, len(base)))
    if bad:
        print("不对的：%s" % "、".join(bad))


def cmd_republish(args):
    """补发远端缺失的草稿（如 mort）。

    走与首次发布完全相同的路径：make_title + clean_body + build_arg +
    save_draft（不带 article_id = 新建）。
    """
    led = load_ledger()
    base = led.get("baseline") or {}
    ov = bp._bili_title_overrides()
    missing = [c for c in ov if c not in base]
    if args.case:
        missing = [c for c in missing if c in args.case]
    if args.batch:
        missing = missing[:args.batch]
    if not missing:
        print("远端没有缺失的草稿，无需补发")
        return
    print("远端缺失 %d 条：%s" % (len(missing), "、".join(missing)))
    cdp = connect()
    done = led.get("republished") or {}
    for i, cid in enumerate(missing, 1):
        art = os.path.join(bp.OUT_ART, cid, "article.html")
        if not os.path.isfile(art):
            print("[%d/%d] %-22s SKIP 没有 article.html"
                  % (i, len(missing), cid))
            continue
        with open(art, encoding="utf-8") as f:
            html = f.read()
        title = bp.make_title(cid, html)
        body = bp.clean_body(html)
        risk = bp.risk_check(title + re.sub(r"<[^>]+>", "", body))
        arg = api.build_arg(title, body,
                            image_urls=[_cover_url(cid)])
        r = api.save_draft(cdp, arg)
        if (r or {}).get("code") == 0:
            aid = ((r.get("data") or {}).get("article_id")
                   or (r.get("data") or {}).get("draft_id"))
            print("[%d/%d] %-22s OK  aid=%s  %s（风险:%s）"
                  % (i, len(missing), cid, aid, title, risk))
            done[cid] = {"ok": True, "aid": aid, "title": title,
                         "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        else:
            print("[%d/%d] %-22s FAIL %s" % (i, len(missing), cid, r))
            done[cid] = {"ok": False, "err": str(r)[:200]}
        led["republished"] = done
        save_ledger(led)
        time.sleep(2)
    print(SEP)
    print("补发完成 %d 条" % len(done))


def _cover_url(cid):
    p = os.path.join(bp.OUT, cid, "cover.png")
    if not os.path.isfile(p):
        return None
    return "file://" + os.path.abspath(p).replace("\\", "/")


def main():
    ap = argparse.ArgumentParser(description="B站草稿改标题 / 补发")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="只打印计划")
    g.add_argument("--grab", action="store_true", help="抓远端标题基线")
    g.add_argument("--apply", action="store_true", help="改标题并保存")
    g.add_argument("--check", action="store_true", help="独立回读校验")
    g.add_argument("--republish", action="store_true", help="补发缺失草稿")
    ap.add_argument("--case", default="")
    ap.add_argument("--batch", type=int, default=0)
    args = ap.parse_args()
    {"plan": cmd_plan, "grab": cmd_grab, "apply": cmd_apply,
     "check": cmd_check, "republish": cmd_republish}[
        "plan" if args.plan else "grab" if args.grab else
        "apply" if args.apply else "check" if args.check else "republish"
    ](args)


if __name__ == "__main__":
    main()