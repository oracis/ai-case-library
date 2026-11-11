# -*- coding: utf-8 -*-
"""统一扫描各平台产物的「我的判断」段真伪。

兼容三种格式：
  md            ## 我的判断
  html/master   <h2>我的判断</h2>
  html/bili     <p><strong>▶ 我的判断</strong></p><hr>
判定：定位「我的判断」→ 取到固定收尾语之间的文本 → 剥标签/分隔线 → 量长度。
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES = [c["id"] for c in json.load(
    open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))]
FOOT = "这条案例的完整核实记录"
THRESH = 10  # <10 字符视为空（短但真实的「结论：别碰（综合 65.6 分）」有 19 字）


def judge_text(txt):
    i = txt.find("我的判断")
    if i < 0:
        return None
    seg = txt[i + 4:]
    j = seg.find(FOOT)
    if j >= 0:
        seg = seg[:j]
    seg = re.sub(r"<!--.*?-->", "", seg, flags=re.S)
    seg = re.sub(r"<[^>]+>", "", seg)
    seg = re.sub(r"^\s*[-*_]{3,}\s*$", "", seg, flags=re.M)
    return seg.strip()


def report(name, path_fmt):
    empty, ok = [], 0
    for cid in CASES:
        p = path_fmt % cid
        if not os.path.exists(p):
            continue
        s = judge_text(open(p, encoding="utf-8", errors="replace").read())
        if s is None or len(s) < THRESH:
            empty.append(cid)
        else:
            ok += 1
    print("=== %s ===" % name)
    print("   有实质判断: %d" % ok)
    print("   空/仅收尾语: %d" % len(empty))
    if empty:
        print("     %s" % "、".join(empty))
    print()
    return empty


r = {}
r["master"] = report("母版 out/articles/<id>.html（12:56 重建）",
                     os.path.join(ROOT, "out", "articles", "%s.html"))
r["toutiao"] = report("头条 out/toutiao/<id>/article.html",
                      os.path.join(ROOT, "out", "toutiao", "%s", "article.html"))
r["bilibili"] = report("B站 out/bili/<id>/article.html",
                       os.path.join(ROOT, "out", "bili", "%s", "article.html"))

json.dump(r, open(os.path.join(ROOT, "tmp_sec_audit.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("(清单: tmp_sec_audit.json)")