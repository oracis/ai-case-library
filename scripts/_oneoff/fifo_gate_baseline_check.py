#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读证据：证明 11-10 全量回归里 2 个红是**既有红**，与 PRD #124 无关。

跑法：cd /d/ai-case-library && python -X utf8 tmp/fifo_gate_baseline_check.py

结论口径（2026-11-10 13:xx 实测）：
  ① xhs_publish.pick_draft 与 HEAD 逐字相同 ⇒ test_xhs_send_draft.test_默认取最久
     的失败来自真台账里既有的 autoreels-ai 已发记录，不是本次改动。
  ② retone_drafts.plan() 依赖的 WATERMARK / CANNED_SOURCE / TITLE_MAX /
     title_of / scan_title 本次一字未改；换成 HEAD 正则后 plan 产出完全一致
     ⇒ test_platform_tone 的 2 个失败来自 11-09 改写的存量稿。
"""
import io
import json
import os
import subprocess
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def head(path):
    return subprocess.check_output(["git", "show", "HEAD:" + path],
                                   text=True, encoding="utf-8")


def cur(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def func(src, name):
    i = src.index("def " + name + "(")
    j = src.find("\ndef ", i + 5)
    return src[i:j if j > 0 else len(src)]


def main():
    out = {}
    out["pick_draft_same_as_HEAD"] = (
        func(head("scripts/xhs_publish.py"), "pick_draft")
        == func(cur("scripts/xhs_publish.py"), "pick_draft"))

    base = types.ModuleType("pg_baseline")
    base.__file__ = os.path.join(ROOT, "scripts", "publish_guard.py")
    exec(compile(head("scripts/publish_guard.py"), "HEAD:publish_guard.py",
                 "exec"), base.__dict__)
    import publish_guard as G
    out["guard_symbols_same_as_HEAD"] = {
        "WATERMARK": base.WATERMARK.pattern == G.WATERMARK.pattern,
        "CANNED_SOURCE": base.CANNED_SOURCE.pattern == G.CANNED_SOURCE.pattern,
        "TITLE_MAX": base.TITLE_MAX == G.TITLE_MAX,
        "title_of": base.title_of("out/articles/promptmonitor-io.html")
                    == G.title_of("out/articles/promptmonitor-io.html"),
        "scan_title": base.scan_title("xhs", "标题")
                      == G.scan_title("xhs", "标题"),
    }

    import retone_drafts as RT
    now = [(r, t) for r, t, _ in RT.plan() if t != RT._read(r)]
    old = RT.WM_LINE, RT.CANNED_LINE
    RT.WM_LINE, RT.CANNED_LINE = base.WATERMARK, base.CANNED_SOURCE
    baseline = [(r, t) for r, t, _ in RT.plan() if t != RT._read(r)]
    RT.WM_LINE, RT.CANNED_LINE = old
    out["retone_plan_same_new_vs_baseline"] = now == baseline
    out["retone_pending_files"] = [r for r, _ in now]

    out["platform_tone_has_local_changes"] = (
        "scripts/platform_tone.py"
        in subprocess.check_output(["git", "status", "--short"], text=True))
    d = json.load(io.open("data/xhs_published.json", encoding="utf-8"))
    rows = d if isinstance(d, list) else list(d.items())
    out["xhs_mirror_has_autoreels_ai"] = any(
        (str(x.get("id")) == "autoreels-ai") if isinstance(x, dict)
        else (x[0] == "autoreels-ai") for x in rows)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = (out["pick_draft_same_as_HEAD"]
          and all(out["guard_symbols_same_as_HEAD"].values())
          and out["retone_plan_same_new_vs_baseline"]
          and not out["platform_tone_has_local_changes"])
    print("\n结论：%s" % ("2 个全量红均为既有红（本次改动零引入）" if ok
                       else "⚠ 证据不成立，需继续排查"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
