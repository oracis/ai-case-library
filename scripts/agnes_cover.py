#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agnes_cover.py — Agnes AI 生图支路（给「万物解释者」出配图）
============================================================

背景：WorkBuddy 内置 ImageGen 走自家服务、**按张扣积分**。Agnes 的
`agnes-image-2.1-flash` 当前全免（标价 $10–24/1000 张，现价 $0），是给
公众号「万物解释者」配图省积分的落点。响应里没有 `usage.credit`，
所以这条链路**不消耗 WorkBuddy 积分**。

定位：**支路，不是主链路的一环**
--------------------------------
主链路 collect → review → store → make_article → publish 一概不动。
本脚本读同一份 `data/cases.json`，把图像产物单独落到 `out/agnes/<id>.png`，
**不覆盖**现有的两套封面：

  · out/toutiao/<id>/cover.png   3840x2160 横版，HTML 截图，带大字标题
  · out/xhs/<id>/card-1.png      2160x2880 竖版卡片，同上

两套并存才互补：截图封面有字（适合头条/B站封面），AI 图无字（适合正文插图、
头图备选）。**不要试图用 AI 图替换 cover.png** —— 头条/B站封面位要 3840x2160
且叠了标题字，AI 图直接换上会丢掉标题。

prompt 从 case 已有字段拼（全部现成，不需要额外采集）：
    name        产品名         → 主体
    one_liner   一句话定位      → 画面主题
    category    分类           → 视觉风格映射
    verdict     一句话判断      → 情绪基调
    metrics     关键数字       → 不入画（AI 画数字必糊，改由 HTML 排版）

子命令
------
  gen      逐条生成配图（默认 --limit 1，先看效果再放量）
  all      全库批量
  status   对账：哪些 case 已有图、哪些缺、图尺寸是否符合平台要求
  check    只做连通性与鉴权探测（1 次最小请求），不落盘

尺寸（2026-09-30 实测，服务端**不严格遵守**，必须像素回读）
--------------------------------------------------------
  `size` 合法值只有：1K / 2K / 3K / 4K / WIDTHxHEIGHT。**`16:9` 会 400**。

  | 请求        | 实得像素                   | 结论 |
  |-------------|----------------------------|------|
  | `1024x1024` | 1024x1024                  | 精确 |
  | `2160x2880` | 2160x2880                  | 精确 |
  | `3840x2160` | 3845x2157                  | 可用 |
  | `1920x1080` | 1312x736                   | 偏差大，不可用 |
  | `4K`        | 3845x2157 / **4096x4096**  | **随机，不可控** |

  → 两个硬结论：① 横版必须显式写 `WIDTHxHEIGHT`，**别用 `4K`**；
    ② 落盘后**必须回读像素**（`png_size()`），不能信请求值。
    平台报「图片太小」时先查这里，别去调 prompt。

限流（2026-09-30 官方公告 + 实测）
---------------------------------
  图像 1K 20 RPM、**4K 只有 1 RPM**（实测连发两次必 429）、视频 1 RPM；
  冷却窗口 >2 分钟。免费层**按 key 类型共享**，多建 key 不叠加额度。
  → 脚本自带串行 + 最小间隔（`spec_gap()`）+ 429 直接报退，
    **别并发**（本项目 CDP 那边也刻意串行，见 multiplatform/runner.py 注释）。

依赖：纯标准库。key 读取顺序：
  1. 环境变量 AGNES_API_KEY
  2. data/secrets.json 的 agnes_key
  3. ~/.workbuddy/keys/agnes-api-key.txt
"""
import argparse
import http.client
import json
import os
import re
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_PATH = os.path.join(ROOT, "data", "cases.json")
SECRETS_PATH = os.path.join(ROOT, "data", "secrets.json")
OUT_DIR = os.path.join(ROOT, "out", "agnes")
PROMPT_DIR = os.path.join(ROOT, "out", "agnes", "_prompt")
PROMPT_LOG = os.path.join(ROOT, "out", "agnes", "_prompts.jsonl")

API_HOST = "apihub.agnes-ai.com"
OUT_HOST = "platform-outputs.agnes-ai.space"
IMAGE_MODEL = "agnes-image-2.1-flash"
FALLBACK_MODELS = ("agnes-image-2.5-flash", "agnes-image-2.0-flash")

# 目标像素 -> (请求 size, 平台用途, 最低可接受宽度)
# 平台要求来自实测：头条/B站封面 3840x2160，小红书卡片 2160x2880。
#
# ⚠️ **`4K` 不能用来出横版**（2026-09-30 实测两次）：
#   一次给 3845x2157（≈对），一次给 **4096x4096 方图** —— 尺寸完全随机，
#   而且 4K 限流只有 **1 RPM**，连着两次就 429。
#   → 横版必须显式写 `WIDTHxHEIGHT`。但服务端对 WIDTHxHEIGHT 也**不严格遵守**：
#   3840x2160 → 3845x2157（可用），1920x1080 → 1312x736（偏差大，不可用），
#   2160x2880 → 2160x2880（精确命中）。
#   → 结论：**显式写尺寸 + 落盘后像素回读**，别信返回值。这是本脚本
#     `png_size()` 存在的原因，不是冗余校验。
TARGETS = {
    "wide": {
        "size": "3840x2160",
        "want": (3840, 2160),
        "label": "横版：头条 / B站 封面",
        "min_w": 1600,
    },
    "square": {
        "size": "1024x1024",
        "want": (1024, 1024),
        "label": "方图：正文插图",
        "min_w": 800,
    },
    "tall": {
        "size": "2160x2880",
        "want": (2160, 2880),
        "label": "竖版：小红书卡片",
        "min_w": 1080,
    },
}

# 分类 -> 视觉风格。写死而不是让模型自由发挥：风格一致才像一套系列。
# ⚠️ key 必须**精确等于** data/cases.json 里的 category 取值（2026-09-30 实测
# 全库只有 11 种，见下表）。曾经按"常见分类"猜了一堆（教育/医疗/金融…），
# 结果一条都命中不了，全落进 DEFAULT_STYLE —— 这就是「风格不统一」的来源。
CATEGORY_STYLE = {
    "AI 工具": "AI assistant interface, chat panels and automation flows, "
               "neural network motifs",
    "开发者工具": "developer tool interface, code editor and terminal panels",
    "营销工具": "marketing dashboard, campaign analytics and growth charts",
    "垂直行业 SaaS": "vertical SaaS dashboard, charts and subscription panels",
    "企业服务": "enterprise workflow interface, team collaboration and documents",
    "组合策略": "multiple revenue streams diagram, platform and services combined",
    "客户服务": "customer support workspace, ticket queue and chat panels",
    "电商": "e-commerce storefront and order management screens",
    "创作者经济": "creator economy studio, content and monetization panels",
    "交易市场": "marketplace listing and matching interface",
    "移动应用": "mobile phone app screens, app store listing and habit tracking UI",
    "Real Estate": "property listings, mortgage charts and map interface",
    "Entertainment": "audio waveform editor, mixing console and mastering presets",
    "未分类": "generic software product concept illustration",
}
DEFAULT_STYLE = "flat geometric editorial illustration, product and workflow concept"

# 允许的单字母/短缩写（`_ascii_only` 的白名单）。库里有 `1Lookup` 这种
# 数字开头的名字，也需要容忍。
ACRONYMS = {"ai", "os", "hr", "it", "ui", "ux", "io", "ml", "ar", "3d"}

# 分类 -> 画面主题（比 style 更具体，用于「画什么」而不是「画什么风格」）
CATEGORY_THEME = {
    "AI 工具": "AI assistant and automation",
    "开发者工具": "code editor and developer workflow",
    "营销工具": "marketing campaign analytics",
    "垂直行业 SaaS": "business dashboard and subscription",
    "企业服务": "team collaboration and documents",
    "组合策略": "platform plus services",
    "客户服务": "support tickets and live chat",
    "电商": "online store and orders",
    "创作者经济": "content creation and monetization",
    "交易市场": "marketplace and matching",
    "移动应用": "mobile app screens and habit streaks",
    "Real Estate": "property listings, mortgage records and map search",
    "Entertainment": "audio waveform editor, mixing console and mastering presets",
    "未分类": "software product",
}

BASE_STYLE = ("editorial illustration, flat vector style, clean geometric shapes, "
              "muted blue beige and warm grey palette, soft lighting, "
              "no text, no letters, no words, no watermark")

# 正文插图裁切留白：AI 生图常把主体放正中，公众号正文需要安全区。
CENTER_BIAS = "centered composition, subject in the middle third, generous clean margins"


def load_key():
    """key 读取顺序：环境变量 → data/secrets.json → ~/.workbuddy/keys/。"""
    k = os.environ.get("AGNES_API_KEY", "").strip()
    if k:
        return k, "env:AGNES_API_KEY"
    if os.path.isfile(SECRETS_PATH):
        try:
            with open(SECRETS_PATH, encoding="utf-8") as f:
                v = (json.load(f) or {}).get("agnes_key", "").strip()
            if v:
                return v, "data/secrets.json:agnes_key"
        except Exception:                                # noqa: BLE001
            pass
    p = os.path.expanduser("~/.workbuddy/keys/agnes-api-key.txt")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            v = f.read().strip()
        if v:
            return v, p
    return "", "(未找到 key)"


def _post(host, path, body, headers=None, timeout=180):
    conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
    try:
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request("POST", path, json.dumps(body), h)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _get(host, path, timeout=180):
    conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def png_size(path):
    """只读 PNG 头 24 字节拿真实像素 —— 平台报错「图片太小」时先查这里。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def download(url, dest):
    """下载 Agnes 产物。**url 必须是完整路径**，截断 .png 后缀会拿到
    S3 风格的 404 XML（2026-09-30 踩过）。"""
    # Agnes 返回的 url 形如 https://platform-outputs.agnes-ai.space/...png
    prefix = "https://%s" % OUT_HOST
    if not url.startswith(prefix):
        raise ValueError("unexpected image host: %s" % url[:80])
    path = url[len(prefix):]
    st, data = _get(OUT_HOST, path)
    if st != 200 or not data:
        raise IOError("download failed: HTTP %s (%d bytes)" % (st, len(data)))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _ascii_only(s):
    """只留像样的英文词/缩写，去掉中文与符号碎片。

    case 里的 one_liner / verdict 全是中文。直接进 prompt，图像模型会把它
    误解成"要把这些字画进画面"，实测就是出一张带乱码的图。
    → 中文只用来**选风格**（见 CATEGORY_STYLE），不进画面描述。

    但也不能只做 `ord(ch) < 127` 就完事：中文句子里的 ASCII 碎片
    （`SaaS 买卖 + 数据服务` → `+`）会变成 `subject: a + software product`
    这种无意义提示，比中文更糟。

    长度门槛用 **2** 而不是 3：缩写（AI / OS / HR / SEO）是有信息量的，
    单测实测 `Chatbase AI` 被 3 字母规则吃掉过。真正要挡的是**单个孤立字母**
    和纯符号，所以规则是「≥2 个字母」，再靠 `ACRONYM` 白名单兜底 1 字母情况。
    """
    kept = []
    for ch in s:
        if 32 <= ord(ch) < 127:
            kept.append(ch)
        elif ch in "，。、；：（）「」/／":
            kept.append(" ")
    words = re.findall(r"[A-Za-z][A-Za-z0-9._-]+", "".join(kept))
    out, seen = [], set()
    for w in words:
        # 纯符号尾巴（`app.` `io-`）剥掉，避免把标点带进 prompt
        w = w.strip("._-")
        if len(w) < 2 and w.lower() not in ACRONYMS:
            continue
        if not w:
            continue
        lw = w.lower()
        if lw in seen:
            continue
        seen.add(lw)
        out.append(w)
    return " ".join(out[:6])


def build_prompt(case, target="square"):
    """从 case 已有字段拼 prompt。不额外采集，不写 cases.json。

    注意：只有 category（→ 视觉风格映射）与英文产品名会进 prompt；
    one_liner / verdict 是中文，**只取其 ASCII 片段**（多半为空），
    因为图像模型画不出中文，硬塞会出乱码图。
    """
    cat = (case.get("category") or "").strip()
    style = CATEGORY_STYLE.get(cat, DEFAULT_STYLE)
    name = _ascii_only(case.get("name") or case.get("id") or "")
    one = _ascii_only(case.get("one_liner") or "")

    bits = [BASE_STYLE, style]
    # 产品名多为英文（Voklit / Bustem / Chatbase），进图有助于定调；
    # 但仍靠 BASE_STYLE 里的 no text 约束，避免模型把它当标语渲染。
    subject = one or name
    if subject:
        bits.append("subject: a %s software product" % subject[:70])
    bits.append("theme: %s" % CATEGORY_THEME.get(cat, "software product"))

    # 构图方向必须跟目标尺寸一致（曾把 square 也写成 horizontal，是真 bug）。
    if target == "tall":
        bits.append("vertical composition, %s" % CENTER_BIAS)
    elif target == "wide":
        bits.append("wide horizontal composition, %s" % CENTER_BIAS)
    else:
        bits.append("square composition, %s" % CENTER_BIAS)
    return ", ".join(bits)


def _log_prompt(cid, target, prompt, resp):
    """prompt 与响应摘要落 jsonl，方便复现 / 调参 / 事后审图。"""
    os.makedirs(os.path.dirname(PROMPT_LOG), exist_ok=True)
    rec = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case": cid,
        "target": target,
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "task_id": (resp or {}).get("task_id", ""),
        "revised_prompt": (((resp or {}).get("data") or [{}])[0].get("revised_prompt") or ""),
    }
    with open(PROMPT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.makedirs(PROMPT_DIR, exist_ok=True)
    with open(os.path.join(PROMPT_DIR, "%s.%s.txt" % (cid, target)),
              "w", encoding="utf-8") as f:
        f.write(prompt + "\n")


def gen_one(case, target="square", force=False, min_gap=3.0, save_prompt=True):
    """给一条 case 生成一张配图。返回 (ok:bool, info:str)。"""
    cid = case.get("id")
    if not cid:
        return False, "case 缺 id"
    spec = TARGETS[target]
    dest = os.path.join(OUT_DIR, "%s.%s.png" % (cid, target))
    if os.path.isfile(dest) and not force:
        return True, "已存在（跳过，加 --force 覆盖）: %s" % os.path.basename(dest)

    key, src = load_key()
    if not key:
        return False, "没有 API key（试 AGNES_API_KEY / data/secrets.json）"

    prompt = build_prompt(case, target)
    body = {"model": IMAGE_MODEL, "prompt": prompt, "size": spec["size"], "n": 1}

    # 限流：串行 + 最小间隔。图像 1K 20 RPM、4K 只有 1 RPM，间隔给足。
    gap = max(min_gap, spec_gap(target))
    wait = gap - (time.time() % gap)
    if wait > 0.05:
        time.sleep(wait)

    st, raw = _post(API_HOST, "/v1/images/generations", body,
                    {"Authorization": "Bearer " + key})
    if st != 200:
        # 429 退避；模型名失效时降级试备选模型。
        if st == 429:
            return False, "429 限流，等冷却后再跑（脚本已按最小间隔串行）"
        msg = raw[:160]
        for alt in FALLBACK_MODELS:
            body["model"] = alt
            st2, raw2 = _post(API_HOST, "/v1/images/generations", body,
                               {"Authorization": "Bearer " + key})
            if st2 == 200:
                st, raw, body["model"] = st2, raw2, alt
                break
        else:
            return False, "HTTP %s  %s" % (st, msg)

    try:
        d = json.loads(raw)
    except Exception:                                    # noqa: BLE001
        return False, "响应不是 JSON: %s" % raw[:160]
    items = d.get("data") or []
    if not items:
        return False, "data 为空: %s" % raw[:160]
    url = items[0].get("url")
    if not url:
        return False, "没有 url 字段，keys=%s" % list(items[0].keys())

    try:
        download(url, dest)
    except Exception as e:                               # noqa: BLE001
        return False, "下载失败: %s" % e

    got = png_size(dest)
    if save_prompt:
        _log_prompt(cid, target, prompt, d)

    ok, note = True, ""
    want = spec["want"]
    if not got:
        ok, note = False, "落盘文件不是合法 PNG"
    elif abs(got[0] - want[0]) > 64 or abs(got[1] - want[1]) > 64:
        # 服务端不严格执行 size（实测 4K → 3845x2157），低于 min_w 要当真问题报。
        sev = "WARN" if got[0] >= spec["min_w"] else "FAIL"
        if sev == "FAIL":
            ok = False
        note = "%s 目标 %dx%d 实得 %dx%d（服务端不严格执行 size）" % (
            sev, want[0], want[1], got[0], got[1])
    info = "%s  %.1f KB  key=%s" % (
        os.path.basename(dest),
        os.path.getsize(dest) / 1024.0, src)
    if note:
        info += "  " + note
    return ok, info


def spec_gap(target):
    """同一目标的最小请求间隔（秒）。

    官方 2026-09-30 公告：图像 1K 20 RPM、4K 1 RPM。实测 4K 连发两次必 429，
    冷却窗口 >2 分钟，所以 4K 目标给 65 秒间隔（宁可慢，别撞限流）。
    """
    return 65.0 if TARGETS[target]["size"] == "4K" else 3.5


def load_cases(only=None):
    with open(CASES_PATH, encoding="utf-8") as f:
        d = json.load(f)
    cases = d if isinstance(d, list) else d.get("cases", d)
    out = []
    for c in cases:
        cid = c.get("id")
        if only and cid != only:
            continue
        out.append(c)
    return out


def cmd_gen(args):
    cases = load_cases(args.case)
    if not cases:
        print("没有匹配的 case")
        return 1
    limit = args.limit or len(cases)
    print("目标 %s（%s）  待处理 %d 条"
          % (args.target, TARGETS[args.target]["label"], min(limit, len(cases))))
    bad = 0
    for i, c in enumerate(cases[:limit], 1):
        ok, info = gen_one(c, target=args.target, force=args.force,
                           min_gap=args.gap)
        print("  [%d/%d] %-14s %s %s"
              % (i, min(limit, len(cases)), c.get("id", "?"),
                 "OK  " if ok else "FAIL", info), flush=True)
        if not ok:
            bad += 1
    print("完成：成功 %d，失败 %d（产物在 out/agnes/）"
          % (max(0, min(limit, len(cases)) - bad), bad))
    return 1 if bad else 0


def cmd_all(args):
    cases = load_cases()
    spec = TARGETS[args.target]
    gap = spec_gap(args.target)
    print("全库 %d 条 × 目标 %s（%s）" % (len(cases), args.target, spec["label"]))
    print("最小间隔 %.1fs，预计约 %.0f 分钟" % (gap, len(cases) * gap / 60.0))
    if not args.yes:
        print("这是长跑，确认请加 --yes")
        return 1
    ok_n = 0
    for i, c in enumerate(cases, 1):
        if not args.force and os.path.isfile(
                os.path.join(OUT_DIR, "%s.%s.png" % (c.get("id"), args.target))):
            print("  [%d/%d] %-14s 已有，跳过" % (i, len(cases), c.get("id")))
            continue
        ok, info = gen_one(c, target=args.target, force=args.force, min_gap=gap)
        print("  [%d/%d] %-14s %s %s"
              % (i, len(cases), c.get("id"), "OK  " if ok else "FAIL", info), flush=True)
        if ok:
            ok_n += 1
    print("完成：%d/%d（产物在 out/agnes/）" % (ok_n, len(cases)))
    return 0


def cmd_status(args):
    cases = load_cases()
    print("产物目录: %s" % OUT_DIR)
    for target, spec in TARGETS.items():
        want = spec["want"]
        have, miss, bad = [], [], []
        for c in cases:
            cid = c.get("id", "?")
            p = os.path.join(OUT_DIR, "%s.%s.png" % (cid, target))
            if not os.path.isfile(p):
                miss.append(cid)
                continue
            got = png_size(p)
            (have if (got and got[0] >= spec["min_w"]) else bad).append(
                (cid, got))
        print("\n[%s] %s  目标 %dx%d" % (target, spec["label"], want[0], want[1]))
        print("  已有 %d / 共 %d" % (len(have) + len(bad), len(cases)))
        if bad:
            print("  尺寸不足或非法 %d 条:" % len(bad))
            for cid, got in bad[:10]:
                print("    %-14s %s" % (cid, got))
        if miss and args.verbose:
            print("  缺 %d 条: %s" % (len(miss), ", ".join(miss[:12])))
    if os.path.isfile(PROMPT_LOG):
        n = sum(1 for _ in open(PROMPT_LOG, encoding="utf-8"))
        print("\nprompt 日志: %s（%d 条）" % (PROMPT_LOG, n))
    return 0


def cmd_check(args):
    key, src = load_key()
    if not key:
        print("FAIL 没有 API key")
        return 1
    st, raw = _post(API_HOST, "/v1/chat/completions",
                    {"model": "agnes-3.0-flash", "max_tokens": 4,
                     "messages": [{"role": "user", "content": "hi"}]},
                    {"Authorization": "Bearer " + key}, timeout=60)
    print("文本端点 %s  key=%s" % (st, src))
    if st == 200:
        try:
            d = json.loads(raw)
            u = d.get("usage")
            print("  usage=%s  credit=%s  → 无 credit 字段即不扣 WorkBuddy 积分"
                  % (json.dumps(u), (u or {}).get("credit", "(absent)")))
        except Exception:                                # noqa: BLE001
            pass
    st2, raw2 = _post(API_HOST, "/v1/images/generations",
                      {"model": IMAGE_MODEL, "prompt": "a red dot", "size": "1K",
                       "n": 1},
                      {"Authorization": "Bearer " + key})
    print("图像端点 %s  model=%s" % (st2, IMAGE_MODEL))
    if st2 == 200:
        try:
            d2 = json.loads(raw2)
            print("  task_id=%s" % d2.get("task_id", ""))
            print("  usage=%s  → 无 credit 即不扣积分" % json.dumps(d2.get("usage")))
        except Exception:                                # noqa: BLE001
            pass
    else:
        print("  %s" % raw2[:160])
    return 0 if (st == 200 and st2 == 200) else 1


def main():
    # --target 只挂在**子命令**上。挂父 parser 的做法试过，两种位置都认不出来：
    #   `agnes_cover.py --target wide gen` → 子命令认不出（argparse 在子命令
    #   之后不再解析父级选项）；`agnes_cover.py gen --target wide` → 同理。
    # 所以老老实实给每个子命令各加一份，最直白也最不会踩坑。
    ap = argparse.ArgumentParser(
        description="Agnes 生图支路（万物解释者配图，不消耗 WorkBuddy 积分）")
    sub = ap.add_subparsers(dest="cmd")

    def add_target(p):
        p.add_argument("--target", default="square", choices=sorted(TARGETS),
                       help="wide=横版头条/B站封面 square=方图正文插图 "
                            "tall=竖版小红书卡片")
        return p

    p_gen = add_target(sub.add_parser(
        "gen", help="逐条生成（默认先跑 1 条看效果）"))
    p_gen.add_argument("--case", help="只处理这一条 case id")
    p_gen.add_argument("--limit", type=int, default=1)
    p_gen.add_argument("--force", action="store_true", help="覆盖已有图")
    p_gen.add_argument("--gap", type=float, default=3.0, help="最小请求间隔秒")
    p_gen.set_defaults(fn=cmd_gen)

    p_all = add_target(sub.add_parser("all", help="全库批量"))
    p_all.add_argument("--force", action="store_true")
    p_all.add_argument("--gap", type=float, default=3.0)
    p_all.add_argument("--yes", action="store_true", help="确认长跑")
    p_all.set_defaults(fn=cmd_all)

    p_st = sub.add_parser(
        "status", help="对账：哪些有图、尺寸是否达标（三个目标都列）")
    p_st.add_argument("--verbose", action="store_true")
    p_st.set_defaults(fn=cmd_status, target="square")

    p_ck = add_target(sub.add_parser(
        "check", help="连通性与鉴权探测，不落盘"))
    p_ck.set_defaults(fn=cmd_check)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
