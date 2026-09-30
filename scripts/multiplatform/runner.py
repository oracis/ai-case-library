#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""顺序调度器：一条 case × N 个平台，带幂等、重试、断点续传。

刻意**串行**，不并发。批量发版抢同一个 Chrome/CDP 会互相踩：实测并发时
子进程批量 `ConnectionAbortedError: [WinError 10053]`、`TimeoutError`，
甚至把头条草稿箱 tab 挤掉。并发的正确解法是「一个平台一个浏览器实例」，
那是以后的事；现在串行 + 单条重试就已经够快（一条约 1 分钟）。

失败**不打断**整轮：一条挂了记 failed，继续下一条，末尾汇总。
这是 CSDN 那套示例最大的问题（一个平台异常会让整轮 result 不可信）。
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from multiplatform import article as A            # noqa: E402
from multiplatform import state as S              # noqa: E402
from multiplatform.adapters import get as get_ad  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class Runner:
    def __init__(self, plats, dry=False, yes=False, retries=1,
                 manifest=None, verbose=True, replace=False):
        self.plats = list(plats)
        self.dry = dry
        self.yes = yes
        self.retries = max(0, retries)
        self.m = manifest or S.Manifest()
        self.verbose = verbose
        # replace：改文案后覆盖重存。平台侧是 --replace（B站按 article_id
        # 打开已有草稿重写），调度侧要把已 draft_saved 的也重新纳入队列。
        self.replace = replace
        self.ads = [get_ad(p) for p in self.plats]

    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    # ---- 队列 -------------------------------------------------------------
    def queue(self, only=None, limit=0, force=False):
        """至少一个平台还没发过的 case（库里最新在前）。

        force=True（`--replace`）时忽略"已发过"，把该平台重新纳入队列 ——
        改了文案要覆盖重存时用它。
        """
        out = []
        for c in A.load_cases():
            cid = c.get("id")
            if only and cid != only:
                continue
            art = A.load_article(cid, c.get("name", ""))
            force = force or self.replace
            todo = [p for p in self.plats
                    if force or not self._is_done(cid, p, art)]
            if not todo:
                continue
            out.append((art, todo))
            if limit and len(out) >= limit:
                break
        return out

    def _is_done(self, cid, plat, art=None):
        """幂等判据：清单优先，平台状态文件兜底。"""
        if self.m.is_done(cid, plat):
            return True
        ad = get_ad(plat)
        if ad.has_state_file and cid in ad.done_ids():
            self.m.mark_draft(cid, plat, self._title(art, plat) if art else "")
            return True
        return False

    def _title(self, art, plat):
        ad = get_ad(plat)
        prio = (ad.title_from,) if ad.title_from else ()
        return art.title(prio) or art.name

    # ---- 单条 × 单平台 ----------------------------------------------------
    def run_one(self, art, plat):
        ad = get_ad(plat)
        cid = art.cid
        title = self._title(art, plat)
        if not art.ok:
            self.m.mark_failed(cid, plat, "缺母版 out/articles/%s.html" % cid,
                               title)
            return "failed"
        if self._is_done(cid, plat, art) and not self.replace:
            self.log("    [%s] 已发过，跳过" % ad.label)
            return "skip"

        if not self.dry:
            self.m.mark_building(cid, plat, title)
        for attempt in range(self.retries + 1):
            miss = ad.missing_artifacts(art)
            if miss and not self.dry:
                self.log("    [%s] 补 build：缺 %s" % (ad.label, ",".join(miss)))
                ad.build(art, dry=False)
            elif attempt == 0 and not self.dry and ad.can_build and not miss:
                pass                            # 产物齐了，不必 build
            r = ad.publish(art, dry=self.dry, yes=self.yes,
                           replace=self.replace)
            if r.ok:
                st = "published" if (self.yes and getattr(
                    ad, "supports_publish", False)) else "draft_saved"
                if not self.dry:
                    self.m.set(cid, plat, st, title)
                self.log("    [%s] %s %s" % (ad.label, "✓", r.note[:90]))
                return "ok"
            if attempt < self.retries:
                self.log("    [%s] 第 %d 次失败，重试：%s"
                         % (ad.label, attempt + 1, r.note[:80]))
                time.sleep(2)
        if not self.dry:
            self.m.mark_failed(cid, plat, r.note, title)
        self.log("    [%s] ✗ %s" % (ad.label, r.note[:110]))
        return "failed"

    # ---- 整轮 -------------------------------------------------------------
    def run(self, only=None, limit=0, force=False):
        """按**平台**聚合调度，不是按 case。

        为什么要换维度：B站这类平台的批量必须在一个进程内串行跑完 ——
        逐条 spawn 每条都要重起 CDP、重开草稿箱 tab，37 条下来既慢又会把
        页面 WebSocket 拖超时（2026-09-30 实测：跑到第 2 条就
        TimeoutError）。所以先把「每个平台各自待办哪些 case」算出来，
        支持 batch() 的平台一次提交，不支持的才逐条。

        副作用：同一条 case 的不同平台不再紧挨着跑，日志按平台分段。
        """
        jobs = self.queue(only=only, limit=limit,
                          force=force or self.replace)
        if not jobs:
            self.log("没有待发的（所选平台都发过了）。")
            return []
        self.log("=" * 62)
        self.log("待发 %d 条：%s  %s"
                 % (len(jobs), "/".join(a.cid for a, _ in jobs),
                    "[dry-run]" if self.dry else
                    ("[真发]" if self.yes else "[存草稿]")))

        # 平台 → 待办 case 列表（保持 queue 顺序）
        per = {p: [] for p in self.plats}
        arts = {}
        for art, todo in jobs:
            arts[art.cid] = art
            for p in todo:
                per[p].append(art.cid)

        tally = {}                      # cid → 该 case 是否全平台都过
        for p in self.plats:
            cids = per[p]
            if not cids:
                continue
            ad = get_ad(p)
            self.log("\n—— %s：%d 条 ——" % (ad.label, len(cids)))
            if getattr(ad, "supports_only", False):
                self._run_batch(ad, p, cids, arts, tally)
            else:
                for cid in cids:
                    art = arts[cid]
                    self.log("  %s" % cid)
                    r = self.run_one(art, p)
                    tally[cid] = tally.get(cid, True) and (r != "failed")

        done = [c for c, v in tally.items() if v]
        bad = [c for c, v in tally.items() if not v]
        self.log("\n" + "=" * 62)
        self.log("共 %d 条，全平台都过了 %d 条" % (len(jobs), len(done)))
        if bad:
            self.log("有问题 %d 条：%s" % (len(bad), ", ".join(bad)))
            self.log("（重试：publish_multi.py retry --case <id>）")
        return done + bad

    def _run_batch(self, ad, plat, cids, arts, tally):
        """一次提交整个平台的多条，再按脚本输出逐条回写状态。

        ⚠ 不能只看退出码定成败：批量「15 条挂 1 条」整体 rc≠0，但那 14 条
        已经存好了。按 rc 一刀切会把成功的误标 failed，下次重发 —— 对
        B站这种「一条草稿要 1 分钟」的平台代价很高。
        """
        r = ad.batch(cids, dry=self.dry, retries=self.retries,
                     replace=self.replace)
        if r is None:                       # 平台没实现 batch，退回逐条
            for cid in cids:
                res = self.run_one(arts[cid], plat)
                tally[cid] = tally.get(cid, True) and (res != "failed")
            return
        if self.dry:
            self.log("    [dry] %s" % r.note[:100])
            return
        done, failed = ad.parse_batch(r.out)
        for cid in cids:
            art = arts[cid]
            title = self._title(art, plat)
            if cid in done:
                self.m.set(cid, plat, "draft_saved", title)
                self.log("    ✓ %s" % cid)
                tally[cid] = tally.get(cid, True) and True
            else:
                why = ("脚本报告失败" if cid in failed
                       else "批量输出里没确认到（可能中途断连）")
                self.m.mark_failed(cid, plat, why, title)
                self.log("    ✗ %s %s" % (cid, why))
                tally[cid] = False


def bootstrap_manifest(plats, force=False):
    """首次启用时把各平台既有记录灌进清单，否则 37 条会被当成全新重发一遍。

    force=True 会把清单里该平台的记录全部按「已存草稿」重写 —— 用于
    「我手工核对过草稿箱，确认都在」之后重建。
    """
    m = S.Manifest()
    added = 0
    for p in plats:
        ad = get_ad(p)
        if not ad.has_state_file:
            continue
        ids = ad.done_ids()
        if force:
            for cid in ids:
                m.set(cid, p, "draft_saved")
            added += len(ids)
        else:
            added += S.import_legacy(p, ids)
    return m, added
