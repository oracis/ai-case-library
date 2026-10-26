#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_t_tt_del_testdraft.py -- 一次性清理（2026-10-26）。

删掉头条草稿箱里 autosave 探针留下的测试稿「AgentsGuild 拆解测试稿」。
红线：删除接口对假 gid 也返 code:0 ⇒ 唯一判据是删完拉列表核对。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as tt  # noqa: E402

TITLE = "AgentsGuild 拆解测试稿"


def main():
    cdp, t = tt._open(tt.TT_DRAFT, wait=6)
    try:
        raw = tt.fetch_draft_api_raw(cdp)
        dl = raw if isinstance(raw, list) else []
        hit = next((x for x in dl
                    if isinstance(x, dict) and TITLE in (x.get("title") or "")), None)
        if not hit:
            print("[ok] 草稿箱里没有测试稿，无需删除")
            return
        gid = hit.get("gid")
        print("[i] 命中测试稿 gid=%r" % gid)
        r = tt.delete_draft_api(cdp, gid)
        print("[i] delete 返回：%s" % str(r)[:120])
        time.sleep(3)
        raw2 = tt.fetch_draft_api_raw(cdp)
        dl2 = raw2 if isinstance(raw2, list) else []
        still = [x for x in dl2 if TITLE in (x.get("title") or "")]
        print("[verify] 删后列表 %d 条，测试稿仍在：%s" % (len(dl2), bool(still)))
        print("[ok] 已删干净" if not still else "[FAIL] 还在，人工处理")
    finally:
        cdp.close_target(t["id"])


if __name__ == "__main__":
    main()
