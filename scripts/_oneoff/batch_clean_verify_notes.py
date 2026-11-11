#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量清理 out/articles 下的内部核验注记。"""

import os
import sys
import re
from pathlib import Path
import json

ROOT = Path(__file__).parent.parent.parent
SCRIPTS = ROOT / "scripts"
ARTICLES = ROOT / "out" / "articles"

# 检测模式：以>开头的引用块，内容包含"我去核了一遍"
NOTICE_PATTERN = re.compile(
    r'^> 我去核了一遍：.*?(?=^\n|^# |$)',
    re.MULTILINE | re.DOTALL
)

def clean_content(content: str) -> str:
    """移除所有核验注记引用块。"""
    return NOTICE_PATTERN.sub('', content)

def process_file(filepath: Path) -> tuple[int, bool]:
    """处理单个文件，返回 (行原行数, 是否变化)"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            original = f.read()
        cleaned = clean_content(original)
        changed = cleaned != original
        return len(original.splitlines()), changed
    except Exception as e:
        return 0, False

def main():
    # 获取 fifo_content_gate.py 扫描的 72 个文件
    files_json = [
        "out/articles/1lookup.html",
        "out/articles/augora-ai.html",
        "out/articles/autoreels-ai.html",
        "out/articles/boothie-ai-photo-editor.html",
        "out/articles/bustem.html",
        "out/articles/chatbase.html",
        "out/articles/cometly.html",
        "out/articles/comp-ai.html",
        "out/articles/divine-widgets.html",
        "out/articles/draftly.html",
        "out/articles/easymix.html",
        "out/articles/gojiberryai.html",
        "out/articles/harperai.html",
        "out/articles/insect-bite-id.html",
        "out/articles/kibu.html",
        "out/articles/le19emetrou.html",
        "out/articles/magicslides-app.html",
        "out/articles/meerkats-ai.html",
        "out/articles/mort.html",
        "out/articles/nitra.html",
        "out/articles/outrank.html",
        "out/articles/postiz.html",
        "out/articles/promptmonitor-io.html",
        "out/articles/prosp.html",
        "out/articles/quran-unlock.html",
        "out/articles/search1api.html",
        "out/articles/shipfast.html",
        "out/articles/sierra.html",
        "out/articles/speel-co.html",
        "out/articles/stan.html",
        "out/articles/storyshort-ai.html",
        "out/articles/uplinked-b-v.html",
        "out/articles/vectosolve.html",
        "out/articles/vid-ai.html",
        "out/articles/viktor.html",
        "out/articles/voklit.html",
        "out/articles/1lookup.md",
        "out/articles/augora-ai.md",
        "out/articles/autoreels-ai.md",
        "out/articles/boothie-ai-photo-editor.md",
        "out/articles/bustem.md",
        "out/articles/chatbase.md",
        "out/articles/cometly.md",
        "out/articles/comp-ai.md",
        "out/articles/divine-widgets.md",
        "out/articles/draftly.md",
        "out/articles/easymix.md",
        "out/articles/gojiberryai.md",
        "out/articles/harperai.md",
        "out/articles/insect-bite-id.md",
        "out/articles/kibu.md",
        "out/articles/le19emetrou.md",
        "out/articles/magicslides-app.md",
        "out/articles/meerkats-ai.md",
        "out/articles/mort.md",
        "out/articles/nitra.md",
        "out/articles/outrank.md",
        "out/articles/postiz.md",
        "out/articles/promptmonitor-io.md",
        "out/articles/prosp.md",
        "out/articles/quran-unlock.md",
        "out/articles/search1api.md",
        "out/articles/shipfast.md",
        "out/articles/sierra.md",
        "out/articles/speel-co.md",
        "out/articles/stan.md",
        "out/articles/storyshort-ai.md",
        "out/articles/uplinked-b-v.md",
        "out/articles/vectosolve.md",
        "out/articles/vid-ai.md",
        "out/articles/viktor.md",
        "out/articles/voklit.md"
    ]

    total = len(files_json)
    changed = 0
    results = []

    print(f"\n开始清理 {total} 个文件")
    print("-" * 60)

    for rel_path in files_json:
        filepath = ROOT / rel_path
        if not filepath.exists():
            results.append((rel_path, "skip", f"文件不存在"))
            continue

        lines, did = process_file(filepath)
        if did:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(clean_content(open(filepath, 'r', encoding='utf-8').read()))
                changed += 1
                results.append((rel_path, "ok", f"清理 {lines} 行"))
            except Exception as e:
                results.append((rel_path, "error", str(e)))
        else:
            results.append((rel_path, "skip", "无需修改"))

    print(f"\n结果统计:")
    print(f"  已修改: {changed}/{total}")
    print("-" * 60)

    # 输出详情
    for path, status, msg in results:
        print(f"  {status}: {path} ({msg})")

    # 记录到日志
    log = {
        "timestamp": "2026-11-10 16:43:05",
        "total": total,
        "changed": changed,
        "results": results
    }
    with open(ROOT / "tmp" / "batch_clean_log.json", "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n日志已写 tmp/batch_clean_log.json")
    return changed

if __name__ == "__main__":
    main()
