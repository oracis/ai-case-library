#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""四平台台账巡检 + 自愈：把「对账」从人工事件变成后台常态。

## 它和 `*_reconcile.py` 的区别

对账脚本回答「**线上和本地到底差哪几条**」（要开浏览器、要语义匹配、要人看）。
巡检回答「**账本自己有没有病**」（大部分检查不联网、毫秒级、退出码可当门禁）。
先有病治病，才轮得到对账 —— 2026-10-19 那次头条对账，55 条草稿台账里
16 条早该迁走、17 条已发布里 13 条没存 gid，都是**病**，不是「差异」。

## 两档检查

| 档 | 命令 | 查什么 | 代价 |
|---|---|---|---|
| 本地 | `--offline`（默认） | 一条记录两个状态、无远端 ID、状态非法、产物缺失 | 毫秒，零风险 |
| 远端 | `--online` | 本地 remote_id vs 线上列表 ID 的集合差 | 要开浏览器（每平台 20-40s） |

远端档目前接好了**头条**（`toutiao_reconcile` 的拉取函数现成、已实测）；
其余三平台见 `REMOTE_PROBES` 里的接入点说明。

## 自愈边界（--apply）

只修**证据唯一、可逆**的：
  · `DUP`      同一条挂在两本账 → 规整成一条（已发布优先）
  · `PROMOTE`  本地在草稿、线上已在已发布列表 → 迁成 published（带 ID）
  · `BACKFILL` 已发布缺 ID，且线上标题**唯一命中** → 回填 ID
其余（远端删了、孤儿、未接入）一律只报告 —— 误删一次比漏修十次贵。

用法：
    python ledger_health.py                     # 四平台本地巡检
    python ledger_health.py --online --platform toutiao
    python ledger_health.py --offline --apply
退出码：0=干净；1=有需要人工处理的项。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402

PLAT_CN = {"wechat": "公众号", "toutiao": "头条",
           "bilibili": "B站", "xiaohongshu": "小红书"}

# 平台 → 内容包目录（判「台账记了但产物没了」）
PACK_DIR = {
    "toutiao": os.path.join(ROOT, "out", "toutiao"),
    "bilibili": os.path.join(ROOT, "out", "bili"),
    "xiaohongshu": os.path.join(ROOT, "out", "xhs"),
    "wechat": os.path.join(ROOT, "out", "articles"),
}

AUTO_FIXABLE = ("DUP", "PROMOTE", "BACKFILL")


def issue(level, platform, cid, code, detail, fix=None):
    return {"level": level, "platform": platform, "id": cid,
            "code": code, "detail": detail, "fix": fix}


# ---------------------------------------------------------------- 本地检查

def check_offline(platform, recs):
    out = []
    for cid in L.duplicates(platform):
        out.append(issue("WARN", platform, cid, "DUP",
                         "同一条同时挂在草稿台账和已发台账",
                         fix="规整成一条（已发布优先）"))
    for cid, r in recs.items():
        if not cid:
            out.append(issue("ERROR", platform, "", "EMPTY_ID", "记录没有 id"))
            continue
        if r["state"] not in (L.DRAFT, L.PUBLISHED, L.UNVERIFIED):
            out.append(issue("ERROR", platform, cid, "BAD_STATE",
                             "状态非法：%r" % r["state"]))
        if r["state"] == L.UNVERIFIED:
            out.append(issue("WARN", platform, cid, "UNVERIFIED",
                             "写了但没回读确认（接口返 OK 不等于落库）",
                             fix="重读线上，命中则补 ID 转 published"))
        if r["state"] == L.PUBLISHED and not r.get("remote_id"):
            out.append(issue("WARN", platform, cid, "NO_ID",
                             "已发布却没存远端 ID ⇒ 对账只能靠标题猜",
                             fix="回读线上标题唯一命中后回填"))
        d = PACK_DIR.get(platform)
        if d and os.path.isdir(d):
            # 公众号产物是单文件 out/articles/<id>.html，其余平台是 <id>/ 目录
            if platform == "wechat":
                ok = os.path.isfile(os.path.join(d, cid + ".html"))
            else:
                ok = os.path.isdir(os.path.join(d, cid))
            if not ok:
                out.append(issue("INFO", platform, cid, "NO_PACK",
                                 "台账有、本地无内容包（%s）" % (
                                     os.path.relpath(d, ROOT))))
    return out


# ---------------------------------------------------------------- 远端探针

def _remote_toutiao():
    import toutiao_reconcile as tr
    drafts = tr.pull_drafts()
    pubs = tr.pull_published()
    return {"draft": {x["gid"]: x["title"] for x in drafts if x["gid"]},
            "pub": {x["gid"]: x["title"] for x in pubs if x["gid"]},
            "draft_ok": True, "pub_ok": True, "why": ""}


def _remote_xhs():
    """小红书：已发布侧能直读（带 note_id），草稿侧**没有服务端 API**
    （离开编辑页自动暂存到浏览器 IndexedDB，实测 0 个 XHR）⇒ 诚实报未知。"""
    import xhs_webapi as xw
    notes, _ = xw.posted_notes(pages=2, page_size=50)
    return {"draft": {}, "pub": {n["note_id"]: n["title"] for n in notes
                                 if n.get("note_id")},
            "draft_ok": False, "pub_ok": True,
            "why": "小红书草稿只存在于浏览器本地 IndexedDB，无法读"}


def _remote_bili():
    """B站：草稿 article_id / 已发布 id 都能直读。

    ⚠ 两个接口都要求 cdp 的**当前页**在 bilibili.com —— 头条页上直接调会
      读到空（静默失败）。`_bili_tab` 负责切/开正确页面。
    """
    import bilibili_publish as bp
    import bili_draft_api as ba
    import wechat_publish as wp
    cdp = wp.CDP(bp.PUB_PORT)
    ba._bili_tab(cdp)
    drafts = ba.list_drafts(cdp) or []
    arts = ba.list_published_articles(cdp) or []
    return {"draft": {str(d.get("article_id")): (d.get("title") or "")
                      for d in drafts if d.get("article_id")},
            "pub": {str(a.get("id")): (a.get("title") or "")
                    for a in arts if a.get("id")},
            "draft_ok": True, "pub_ok": True, "why": ""}


# 其余平台的接入点：
#   wechat  已由 `wechat_reconcile.py` 覆盖（它有 wechat_sync 的 diffs 口径，
#           比这里的 ID 集合差更细），别重复实现
REMOTE_PROBES = {"toutiao": _remote_toutiao,
                 "bilibili": _remote_bili,
                 "xiaohongshu": _remote_xhs}


def check_online(platform, recs, remote):
    """本地 remote_id ↔ 线上 ID 的集合差。不匹配标题，只看 ID。"""
    out = []
    pub_remote = remote.get("pub") or {}
    draft_remote = remote.get("draft") or {}
    for cid, r in recs.items():
        # 草稿侧：本地登记了草稿 ID，线上草稿箱里却没了 ⇒ 不是"线上没操作"
        # 就是"已经发了/删了"。只报告，不自动清 —— 删账比留着脏一行贵。
        drid = r.get("draft_remote_id")
        if drid and remote.get("draft_ok") and drid not in draft_remote:
            out.append(issue("WARN", platform, cid, "STALE_DRAFT",
                             "登记的草稿 id=%s 不在线上草稿箱（已删？已发布？）"
                             % drid,
                             fix="人工确认后再动；发过文的草稿删掉是正常的"))
        rid = r.get("remote_id")
        if r["state"] == L.PUBLISHED:
            # 🚨 「读不到」≠「线上没有」：pub_ok=False 时远端列表是空的，
            #   拿它当证据会把一次网络抖动判成「文章被删」。
            if rid and remote.get("pub_ok") and rid not in pub_remote:
                out.append(issue("ERROR", platform, cid, "STALE",
                                 "本地记已发布（id=%s），线上已发布列表里没有"
                                 % rid, fix="确认是否被删；别自动重发"))
            elif not rid and remote.get("pub_ok"):
                out.append(issue("WARN", platform, cid, "BACKFILL",
                                 "缺远端 ID，线上无法按 ID 认领",
                                 fix="按台账 note/标题与线上精确比对，唯一命中才回填"))
        elif r["state"] == L.DRAFT:
            if rid and rid in pub_remote:
                out.append(issue("WARN", platform, cid, "PROMOTE",
                                 "本地还是草稿，线上已发布（id=%s）" % rid,
                                 fix="迁成 published"))
            elif not rid and remote.get("draft_ok"):
                pass          # 草稿没 ID 是常态（头条草稿也有 gid，但老记录没有）

    # 反查漏登：线上有、但没有任何本地记录的 remote_id 指向它。
    # 台账 note 里写过的别名视为「已知的第二篇」——bustem/cometly 都是
    # 新旧口径各发一篇，不是漏登，别拿它当 ERROR。
    if remote.get("pub_ok"):
        # 🚨 只拿**已发布状态**的 remote_id 去认领。草稿和正文在 B站是两个
        #    不同的 article_id（草稿 342372 / 正文 53460777），把草稿侧的
        #    ID 混进 claimed，等于让一篇真文章被别人的草稿"认领"掉，
        #    ORPHAN 就漏报了。
        claimed = {r.get("remote_id") for r in recs.values()
                   if r.get("remote_id") and r["state"] in (L.PUBLISHED,
                                                            L.UNVERIFIED)}
        known = {a for r in recs.values() for a in _alias_titles(r)}
        for gid, title in pub_remote.items():
            if gid in claimed or (title and title in known):
                continue
            out.append(issue("WARN", platform, "", "ORPHAN",
                             "线上有「%s」(id=%s)，本地没登记"
                             % (title[:26], gid),
                             fix="确认归属后补登（别用标题猜，先看正文）"))
    return out


# ---------------------------------------------------------------- 自愈

def _alias_titles(rec):
    """台账 note 里『…』包住的远端标题（历次对账的结论，最可靠）。"""
    import re
    return re.findall(r"『([^』]+)』", rec.get("note") or "")


def _backfill(platform, cid, remote):
    """给缺 ID 的已发布记录回填远端 ID。

    只认**三种硬证据**，绝不猜：
      1. note 里写了 cv 号（B站文章编号就是这个 id）；
      2. note 里的『标题』与线上标题**完全相等**；
      3. 台账 title 与线上标题完全相等。
    多条候选一律放弃（宁可留着 WARN 让人看）。
    """
    pub = remote.get("pub") or {}
    if not pub:
        return None, "线上已发布列表没读到"
    rec = L.get(platform, cid) or {}
    note = rec.get("note") or ""
    import re as _re
    for m in _re.findall(r"cv(\d{6,})", note):
        if m in pub:
            return m, "note 里写了 cv 号"
    for alias in _alias_titles(rec):
        hits = [gid for gid, t in pub.items() if t == alias]
        if len(hits) == 1:
            return hits[0], "note 别名精确命中"
    title = rec.get("title") or ""
    if title:
        hits = [gid for gid, t in pub.items() if t == title]
        if len(hits) == 1:
            return hits[0], "台账标题精确命中"
    return None, "线上无唯一命中"


def _dup_sidecar(platform, cid):
    """DUP 合并前，把草稿侧那条原始记录捞出来（用于留痕）。

    ⚠ 「一条记录一个事实」的代价：同一个 case 在头条可能是「草稿箱里还有
      一条旧稿 + 已发布一篇新文章」两件真实存在的事，按 case 记账只能留一条。
      合并时必须把另一侧的时间写进 note —— 否则「草稿箱里那条旧稿」这个
      事实就凭空消失了（voklit 就是这样：10-09 的草稿至今还躺在草稿箱）。
    """
    spec = L._SPEC[platform]
    if not spec.get("draft"):
        return None
    _, entries, _, _ = L._read_file(L._path(platform, L.DRAFT))
    for it in (entries or []):
        if isinstance(it, dict) and it.get("id") == cid:
            return it
    return None


def apply_fixes(platform, issues, remote=None):
    """只动白名单里的三类，且每次都重新 load（避免拿着旧快照覆盖）。"""
    done = []
    for i in issues:
        if i["code"] not in AUTO_FIXABLE:
            continue
        cid = i["id"]
        try:
            if i["code"] == "DUP":
                d = _dup_sidecar(platform, cid)
                rec = L.get(platform, cid)
                if d and rec:
                    tip = "｜草稿箱另有一条同名旧稿（%s），台账不再重复记" % (
                        d.get("at") or "时间未知")
                    note = rec.get("note") or ""
                    if tip not in note:
                        L.mark(platform, cid, rec["state"], note=note + tip)
                L.save(platform, L.load(platform))     # 合并视图落盘 ⇒ 冗余消失
                done.append(("DUP", cid))
            elif i["code"] == "PROMOTE":
                r = L.get(platform, cid) or {}
                L.promote(platform, cid, remote_id=r["remote_id"],
                          title=r.get("title", ""),
                          evidence="health_promote")
                done.append(("PROMOTE", cid))
            elif i["code"] == "BACKFILL" and remote:
                gid, why = _backfill(platform, cid, remote)
                if gid:
                    L.mark(platform, cid, L.PUBLISHED, remote_id=gid,
                           evidence="health_backfill")
                    done.append(("BACKFILL", "%s → %s（%s）" % (cid, gid, why)))
        except Exception as e:                          # noqa: BLE001
            done.append(("FAIL:" + i["code"], "%s (%s)" % (cid, str(e)[:60])))
    return done


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", action="append", default=[],
                    help="只查这个平台，可重复；默认四平台全查")
    ap.add_argument("--online", action="store_true", help="连远端做 ID 对照")
    ap.add_argument("--apply", action="store_true", help="自动修可确定的项")
    ap.add_argument("--quiet", action="store_true", help="只打有问题的")
    ap.add_argument("--verbose", action="store_true", help="连 INFO 一起打")
    a = ap.parse_args()

    plats = [p for p in (a.platform or L.PLATFORMS)]
    bad = 0
    for plat in plats:
        if plat not in L.PLATFORMS:
            print("跳过未知平台 %r" % plat)
            continue
        recs = L.load(plat)
        cnt = {}
        for r in recs.values():
            cnt[r["state"]] = cnt.get(r["state"], 0) + 1
        print("\n=== %s  %s  ===" % (PLAT_CN.get(plat, plat),
                                     " / ".join("%s %d" % (k, v)
                                                for k, v in sorted(cnt.items()))))
        issues = check_offline(plat, recs)
        remote = None                      # 每轮重置，别串上一个平台的远端数据

        if a.online:
            fn = REMOTE_PROBES.get(plat)
            if fn is None:
                issues.append(issue("SKIP", plat, "", "NO_PROBE",
                                    "远端探针未接入（见 REMOTE_PROBES 注释）"))
            else:
                try:
                    remote = fn()
                    issues.extend(check_online(plat, recs, remote))
                    if not (remote.get("draft_ok") and remote.get("pub_ok")):
                        # 只剩一侧可读时，只对那一侧下结论；另一侧明说原因
                        # （小红书草稿本来就没有服务端接口，那是平台限制，
                        #   不是「读失败」，别混成一件事）
                        side = "已发布" if remote.get("pub_ok") else "草稿"
                        issues.append(issue("INFO", plat, "", "PARTIAL_READ",
                                            "只核了%s侧；另一侧不可读：%s"
                                            % (side, remote.get("why") or "接口未通")))
                except Exception as e:                  # noqa: BLE001
                    issues.append(issue("SKIP", plat, "", "PROBE_FAIL",
                                        "探针失败：%s" % str(e)[:80]))

        if not issues and not a.quiet:
            print("  ✓ 干净")
        info = [i for i in issues if i["level"] == "INFO"]
        for i in issues:
            if i["level"] == "INFO" and not a.verbose:
                continue
            print("  [%s] %-10s %-24s %s%s"
                  % (i["level"], i["code"], i["id"][:24], i["detail"],
                     ("\n                     → %s" % i["fix"]) if i["fix"] else ""))
        if info and not a.verbose:
            print("  · 另 %d 条 INFO（产物缺失等），加 --verbose 看" % len(info))
        bad += sum(1 for i in issues if i["level"] in ("WARN", "ERROR"))

        if a.apply:
            fixed = apply_fixes(plat, issues, remote)
            for code, cid in fixed:
                print("  ↻ %s %s" % (code, cid))

    print("\n%s" % ("✓ 没有需要人工处理的项" if bad == 0
                    else "✗ %d 项待处理" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
