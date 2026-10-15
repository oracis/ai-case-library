#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给存量案例回填骨架补全段：流量从哪来（traffic_sources）+ 站长是谁（founder）。

背景
----
2026-10-14 把案例页拉齐到固定的 7 段叙事（见 verify_rules.CONTENT_SOFT）：
「它到底做什么 → 钱从哪来 → **流量从哪来** → **站长是谁** → 为什么这事能成
→ 支撑证据 → 可以搬走什么」。新写的案例在 promote 时就带上这两段，
但全库 60 条老案例都没有 —— 它们入库时还没有这个骨架。

铁律（来自项目记忆，不是新增的）
--------------------------------
1. **`founder` 是事实，不是观点。** 编一个创始人比留空危害大得多：
   读者会拿「前 ArcSight 老兵」和「随机注册的 indie hacker」当同类样本比较，
   而整个「一个人能不能做」的排行榜就建立在样本可类比之上。
   ⇒ 模型判不准时**一律写「未公开」**，并在 `founder_confidence` 里留痕。
   ⇒ 「未公开」是合法取值，不是失败。
2. **`traffic_sources` 是推断，但必须能被现有证据支撑。** 只准从
   `what_it_does` / `how_it_makes_money` / `why_it_works` / `signals` 里
   已有的事实推，不准引入外部知识 —— 引入就等于把没核实的东西写进了
   一个主打「每个数字都核过」的库。
3. **改 `data/cases.json` 必须字节级还原原格式。** 本库同时存在
   indent=2+**LF**（cases.json）与 indent=2+**CRLF**（candidates.json）
   两种换行，写错一次就是全量 diff。本脚本**自动探测**并在写回后自检，
   还原不成功直接中止（宁可不动，也不要污染台账）。

用法
----
    python scripts/backfill_narrative.py --list          # 看哪些缺（只读）
    python scripts/backfill_narrative.py --dry --limit 3 # 试跑，不写盘
    python scripts/backfill_narrative.py --limit 5       # 实际写回
    python scripts/backfill_narrative.py --only aeo-engine
    python scripts/backfill_narrative.py --force         # 已填过的也重填
"""

import argparse
import io
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CASES = os.path.join(ROOT, "data", "cases.json")

UNKNOWN = "未公开"

# ---------------------------------------------------------------------------
# 原生序列化格式探测
# ---------------------------------------------------------------------------
#为什么必须探测而不是写死：cases.json 是 LF、candidates.json 是 CRLF，
# 两者 indent 都是 2、都没有末尾换行。写死任一个都会让另一个炸成全量 diff。
def detect_fmt(raw_bytes):
    """从原始字节猜出 (indent, 换行符, 是否有末尾换行)。"""
    crlf = raw_bytes.count(b"\r\n")
    lf = raw_bytes.count(b"\n")
    newline = "\r\n" if crlf and crlf == lf else "\n"
    tail_nl = raw_bytes.endswith(b"\n") or raw_bytes.endswith(b"\r\n")
    # indent：2 和 4 试一遍，取能原样还原的
    for indent in (2, 4):
        obj = json.loads(raw_bytes.decode("utf-8"))
        cand = json.dumps(obj, ensure_ascii=False, indent=indent)
        if newline != "\n":
            cand = cand.replace("\n", newline)
        if tail_nl:
            cand += newline
        if cand.encode("utf-8") == raw_bytes:
            return indent, newline, tail_nl
    raise SystemExit("[!] 认不出 %s 的原生格式（indent/换行/尾换行），"
                     "为避免全量 diff 已中止" % CASES)


def serialize(obj, indent, newline, tail_nl):
    s = json.dumps(obj, ensure_ascii=False, indent=indent)
    if newline != "\n":
        s = s.replace("\n", newline)
    if tail_nl:
        s += newline
    return s.encode("utf-8")


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------
# ⚠ 刻意压得很短：智谱端点 `response_format=json_object` 在**长生成**时
#   会HTTP 500 或读超时（小 JSON 正常）—— 见项目记忆。所以
#   不喂 signals（证据原文，最长），只喂已提炼过的判断句。
SYSTEM = (
    "你在给一个「海外软件生意拆解库」补两段叙事。库的规矩是**不编**："
    "拿不准的宁可写「未公开」。\n"
    "输出严格的 JSON，字段：\n"
    "  founder: string，创始人/创办团队。若主体是个人独立开发者写「个人独立开发者」；"
    "若公开信息里找不到明确的人，写「%s」，**绝不猜测姓名**。\n"
    "  founder_confidence: \"high\" 或 \"low\"。只有当主体身份能从给的材料里"
    "合理确定时才 high，否则 low。\n"
    "  traffic_sources: string[]，2-4 条获客渠道，每条 ≤ 25 字，"
    "必须能从给定材料推出来，禁止写「社交媒体营销」这类空话。\n"
    "只输出 JSON，不要解释。" % UNKNOWN
)


def build_prompt(c):
    """只喂已提炼的判断句，不喂 signals 原文（长度炸弹）。"""
    def cut(s, n=160):
        s = (s or "").strip().replace("\n", " ")
        return s[:n] + ("…" if len(s) > n else "")

    why = [cut(x, 70) for x in (c.get("why_it_works") or [])[:2]]
    play = [cut(x, 70) for x in (c.get("playbook") or [])[:1]]
    head = (c.get("metrics") or {}).get("headline") or ""

    return (
        "产品名：%s（%s）\n"
        "一句话：%s\n"
        "是什么：%s\n"
        "怎么赚钱：%s\n"
        "主指标：%s\n"
        "为什么能成：%s\n"
        "可搬走的动作：%s\n\n"
        "按上面材料输出 JSON。"
        % (cut(c.get("name"), 40), cut(c.get("name_en"), 40),
           cut(c.get("one_liner")),
           cut(c.get("what_it_dodes") or c.get("what_it_does")),
           cut(c.get("how_it_makes_money"), 200), cut(head, 60),
           "；".join(why), "；".join(play))
    )


def clean_traffic(v):
    """模型偶尔会返回字符串而不是数组，或塞进一整段话。逐条压到 25 字。"""
    if isinstance(v, str):
        v = [x for x in v.replace("；", ";").split(";") if x.strip()]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        x = str(x or "").strip().lstrip("0).、-").strip()
        if not x or len(x) < 4:
            continue
        out.append(x[:28])
    return out[:4]


def fetch_one(c, model_note=""):
    """调一次模型，返回 (founder, traffic_sources, confidence)。异常返回 None。"""
    import ai_verify as A                                    # 延迟导入，--list 时不加载
    raw = A.llm([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": build_prompt(c)}],
                temperature=0.1, max_tokens=900)
    obj = A.extract_json(raw)
    if not isinstance(obj, dict):
        return None

    conf = str(obj.get("founder_confidence") or "low").lower()
    founder = str(obj.get("founder") or "").strip()
    # 🚨 铁律 1：判不准就写「未公开」，不许把模型的猜测当事实写进库
    if conf != "high" or not founder or len(founder) < 2:
        founder = UNKNOWN
        conf = "low"
    if founder in ("N/A", "n/a", "未知", "不详", "none", "None"):
        founder = UNKNOWN

    traffic = clean_traffic(obj.get("traffic_sources"))
    return founder, traffic, conf, model_note


def backup(path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(ROOT, "_backups",
                       "cases.json.bak-before-narrative-%s" % stamp)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)
    print("    备份 → %s" % os.path.relpath(dst, ROOT))
    return dst


def main():
    ap = argparse.ArgumentParser(description="回填案例的「流量来源 / 站长是谁」两段")
    ap.add_argument("--list", action="store_true", help="只看哪些缺，不调模型")
    ap.add_argument("--dry", action="store_true", help="调模型但不写盘")
    ap.add_argument("--force", action="store_true", help="已填过的也重填")
    ap.add_argument("--only", action="append", default=[], metavar="ID",
                    help="只处理指定案例（可重复）")
    ap.add_argument("--limit", type=int, default=0, metavar="N",
                    help="最多处理 N 条（0 = 不限）")
    ap.add_argument("--path", default=CASES, metavar="FILE",
                    help="换输入输出文件（⚠ 单测请传临时文件，绝不要指向 "
                         "data/cases.json —— 曾覆盖过 41 条台账）")
    args = ap.parse_args()

    path = os.path.abspath(args.path)
    raw_bytes = open(path, "rb").read()
    indent, newline, tail_nl = detect_fmt(raw_bytes)
    cases = json.loads(raw_bytes.decode("utf-8"))
    print("原生格式：indent=%d 换行=%s 末尾换行=%s"
          % (indent, "CRLF" if newline == "\r\n" else "LF", tail_nl))

    # ---- 选要补的
    targets = []
    for c in cases:
        if args.only and c.get("id") not in args.only:
            continue
        has_f = bool(str(c.get("founder") or "").strip())
        has_t = bool([x for x in (c.get("traffic_sources") or []) if str(x).strip()])
        if args.force or not (has_f and has_t):
            targets.append(c)
    if args.limit:
        targets = targets[:args.limit]

    print("待补 %d 条 / 共 %d 条" % (len(targets), len(cases)))

    if args.list:
        for c in targets:
            print("  · %-26s %s" % (c.get("id"), cut_show(c.get("name"))))
        return 0
    if not targets:
        print("没有要补的")
        return 0

    import ai_verify as A
    print("LLM 端点：%s / %s" % (A.AI_BASE, A.AI_MODEL))
    print()

    ok = fail = skipped = 0
    for i, c in enumerate(targets, 1):
        cid = c.get("id")
        print("[%d/%d] %s  %s" % (i, len(targets), cid, cut_show(c.get("name"))))
        try:
            got = fetch_one(c, A.AI_MODEL)
        except Exception as e:                                # noqa: BLE001
            print("   [!] 失败：%s" % str(e)[:160])
            fail += 1
            continue
        if not got:
            print("   [!] 模型没给回可解析的 JSON，跳过")
            skipped += 1
            continue

        founder, traffic, conf, _ = got
        if not traffic:
            # 没有流量来源 = 模型没推出来。宁可留空让前端跳过这一段，
            # 也不要把一句空话塞进「固定骨架」里充数。
            print("   流量来源：模型没给出，这段留空")
            skipped += 1
        else:
            c["traffic_sources"] = traffic
            print("   流量来源：%s" % " / ".join(traffic))
        c["founder"] = founder
        print("   站长：%s（置信 %s）" % (founder, conf))
        ok += 1

    print("\n成功 %d · 失败 %d · 留空 %d" % (ok, fail, skipped))

    if args.dry:
        print("(--dry 未写盘)")
        return 0
    if not ok:
        print("没有可写的改动，放弃写盘")
        return 1

    backup(path)
    out = serialize(cases, indent, newline, tail_nl)
    with open(path, "wb") as f:
        f.write(out)

    # 字节级自检：格式没原样还原就是全量 diff，宁可中止
    back = open(path, "rb").read()
    if json.loads(back.decode("utf-8")) != cases:
        print("[!] 写回后回读不一致，已中止")
        return 1
    if back != out:
        print("[!] 字节级不一致，已中止")
        return 1
    print("已写入 %s（格式已还原）" % os.path.relpath(path, ROOT))
    return 0


def cut_show(s, n=28):
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())