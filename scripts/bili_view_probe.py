"""探针：看 draft/view 返回的 paragraphs 结构，判断能不能原样回传改标题。

⚠ 背景：`build_arg()` 从 HTML 重建段落会**丢行内样式**（粗体/颜色/居中）。
  但 `draft/view` 返回的 `opus.content.paragraphs` 是**编辑器自己的结构**，
  如果它带 word.style，那改标题时就该原样回传，而不是从 HTML 重建。

用法：python -u -X utf8 scripts/bili_view_probe.py <article_id>
"""
from __future__ import annotations

import json
import os
import sys

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili_title_push as tp        # noqa: E402
import bili_draft_api as api         # noqa: E402


def main():
    aid = sys.argv[1] if len(sys.argv) > 1 else "342556"
    cdp = tp.connect()
    v = api.view_draft(cdp, aid)
    print("顶层字段:", sorted(v.keys()))
    opus = v.get("opus") or {}
    print("opus 字段:", sorted(opus.keys()))
    paras = ((opus.get("content") or {}).get("paragraphs")) or []
    print("段落数:", len(paras))

    types = {}
    styled = 0
    for p in paras:
        t = p.get("para_type")
        types[t] = types.get(t, 0) + 1
        for n in (p.get("text") or {}).get("nodes") or []:
            w = n.get("word") or {}
            if (w.get("style") or {}) or (w.get("color") or ""):
                styled += 1
    print("para_type 分布:", types)
    print("带样式/颜色的节点数:", styled)
    print()
    print("== 第一段原文 ==")
    print(json.dumps(paras[0] if paras else None, ensure_ascii=False, indent=1))
    if len(paras) > 1:
        print("== 第二段原文 ==")
        print(json.dumps(paras[1], ensure_ascii=False, indent=1))
    print()
    print("image_urls:", json.dumps(v.get("image_urls"), ensure_ascii=False))
    print("title:", v.get("title"), "| opus.title:", opus.get("title"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
