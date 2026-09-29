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
                 manifest=None, verbose=True):
        self.plats = list(plats)
        self.dry = dry
        self.yes = yes
        self.retries = max(0, retries)
        self.m = manifest or S.Manifest()
        self.verbose = verbose
        self.ads = [get_ad(p) for p in self.plats]

    def log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    # ---- 队列 -------------------------------------------------------------
    def queue(self, only=None, limit=0):
        """至少一个平台还没发过的 case（库里最新在前）。"""
        out = []
        for c in A.load_cases():
            cid = c.get("id")
            if only and cid != only:
                continue
            art = A.load_article(cid, c.get("name", ""))
            todo = [p for p in self.plats
                    if not self._is_done(cid, p, art)]
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
        if self._is_done(cid, plat, art):
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
            r = ad.publish(art, dry=self.dry, yes=self.yes)
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
    def run(self, only=None, limit=0):
        jobs = self.queue(only=only, limit=limit)
        if not jobs:
            self.log("没有待发的（所选平台都发过了）。")
            return []
        self.log("=" * 62)
        self.log("待发 %d 条（%s）  %s"
                 % (len(jobs), "/".join(a.cid for a, _ in jobs),
                    "[dry-run]" if self.dry else
                    ("[真发]" if self.yes else "[存草稿]")))
        done, bad = [], []
        for i, (art, todo) in enumerate(jobs, 1):
            self.log("\n[%d/%d] %s —— %s"
                     % (i, len(jobs), art.cid, art.name))
            ok = True
            for plat in todo:
                r = self.run_one(art, plat)
                if r == "failed":
                    ok = False
            (done if ok else bad).append(art.cid)
        self.log("\n" + "=" * 62)
        self.log("完成 %d 条，全平台都过了 %d 条" % (len(jobs), len(done)))
        if bad:
            self.log("有问题 %d 条：%s" % (len(bad), ", ".join(bad)))
            self.log("（重试：publish_multi.py --retry-failed）")
        return done + bad


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
