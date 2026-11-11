#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 howtomakemi.com 的「官方披露」类案例页，抽出可判定字段（只读，不写 data/）。

输出 tmp/htm_official_scan.json：
  slug / name / tag(分类) / income(页面「月收入」原文) / one(一句话) /
  region / website / sources(list) / has_ai / founder

用途：在 344 条官方披露里挑出「有明确收入数字 + AI 相关 + 库内未见」的部分入库。
"""
import concurrent.futures as cf
import html as H
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
BASE = "https://howtomakemi.com/cases/%s/"

TAG_RE = re.compile(r'class="[^"]*badge[^"]*"[^>]*>([^<]{1,20})<')

AI_HINT = re.compile(r"AI|人工智能|大模型|LLM|Agent|智能|机器学习|生成式|GPT|Copilot", re.I)


def strip_text(raw):
    t = re.sub(r"<script.*?</script>", "", raw, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = H.unescape(t)
    return [l.strip() for l in t.split("\n") if l.strip()]


def between(lines, start_kw, stop_kws, limit=40):
    """取 start_kw 之后到任一 stop_kw 之前的行（页面顺序结构固定）。"""
    try:
        i = next(i for i, l in enumerate(lines) if start_kw in l)
    except StopIteration:
        return []
    out = []
    for l in lines[i + 1:i + 1 + limit]:
        if any(k in l for k in stop_kws):
            break
        out.append(l)
    return out


def fetch(slug):
    try:
        req = urllib.request.Request(BASE % slug, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception as e:                                    # noqa: BLE001
        return {"slug": slug, "error": "%s: %s" % (type(e).__name__, str(e)[:60])}

    lines = strip_text(raw)
    rec = {"slug": slug, "len": len(raw)}

    # 页头结构固定：…「← 返回案例库」→ 口径徽章 → 产品名 → 一句话
    try:
        i = next(i for i, l in enumerate(lines) if "返回案例库" in l)
    except StopIteration:
        i = 0
    rec["tag"] = lines[i + 1] if i + 1 < len(lines) else ""
    rec["name"] = lines[i + 2] if i + 2 < len(lines) else ""
    rec["one"] = lines[i + 3] if i + 3 < len(lines) else ""

    # 月收入：💰 之后那一行 + 其后的口径说明（几行 ARR/融资备注）
    try:
        i = next(i for i, l in enumerate(lines) if l == "月收入")
        rec["income"] = lines[i + 1] if i + 1 < len(lines) else ""
        rec["income_note"] = " ".join(lines[i + 2:i + 5])[:200]
    except StopIteration:
        rec["income"] = ""
        rec["income_note"] = ""

    try:
        i = next(i for i, l in enumerate(lines) if "国家 / 地区" in l)
        rec["region"] = lines[i + 1] if i + 1 < len(lines) else ""
    except StopIteration:
        rec["region"] = ""

    # 官网
    m = re.search(r'href="(https?://[^"]+)"', raw)
    rec["website"] = m.group(1) if m else ""

    # 正文三段
    rec["what"] = " ".join(between(lines, "产品是什么", ["怎么赚的钱", "流量从哪来"], 30))[:900]
    rec["money"] = " ".join(between(lines, "怎么赚的钱", ["流量从哪来", "站长是谁"], 30))[:900]
    rec["traffic"] = " ".join(between(lines, "流量从哪来", ["站长是谁", "这个案例能学到什么"], 30))[:600]
    rec["founder"] = " ".join(between(lines, "站长是谁", ["这个案例能学到什么", "来源与数据"], 12))[:300]

    # 来源链接
    rec["sources"] = sorted(set(re.findall(r"https?://[^\s<\")]+", " ".join(
        between(lines, "来源与数据", ["一句话总结", "给别人的启发"], 20)))))[:8]
    rec["has_ai"] = bool(AI_HINT.search(" ".join([
        rec.get("tag", ""), rec.get("name", ""), rec.get("one", ""), rec.get("what", "")])))
    return rec


def main():
    data = json.load(open(os.path.join(ROOT, "tmp_htm_classified.json"), encoding="utf-8"))
    slugs = [x[0] for x in data if x[1].startswith("官方")]
    if len(sys.argv) > 1:
        slugs = slugs[:int(sys.argv[1])]
    print("待扫 %d 条" % len(slugs))

    out = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for i, rec in enumerate(ex.map(fetch, slugs), 1):
            out.append(rec)
            if i % 40 == 0:
                print("  %d/%d" % (i, len(slugs)))
    ok = [r for r in out if "error" not in r]
    print("成功 %d / 失败 %d" % (len(ok), len(out) - len(ok)))
    p = os.path.join(ROOT, "tmp", "htm_official_scan.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→", p)


if __name__ == "__main__":
    main()
