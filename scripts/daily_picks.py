#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**每日待核选题卡**：把 triage 打分最高的几条候选推到眼前（对标字流选题卡）。

## 它解决什么

triage.py 打分早就有了，但结论躺在 `--json` 里没人看 —— 缺的不是分，
是「每天早上有几条值得动的条目出现在眼前」。本脚本就是这一步：

    python -X utf8 scripts/daily_picks.py            # 生成今日卡（stdout + 落盘）
    python -X utf8 scripts/daily_picks.py --top 8    # 多推几条
    python -X utf8 scripts/daily_picks.py --no-save  # 只看stdout，不写文件

## 选什么

- 只推 **ready / deep / backfill** 三级（ready 最优先）：ready=可直接进稿，
  deep=可深核，backfill=缺固定材料、按 hint 补。later/drop 不占人脑子。
- held（人工挂起）天然不入选 —— triage 已把它们压到 later。
- **近 2 天推过的自动靠后**（`data/daily_picks_state.json` 记最近 14 天），
  池子够就轮新面孔，不够再回头推旧的 —— 对应「写过的题不天天重复」，
  但不做 60 天硬排除：待核条目没动就得一直可见，只是降权。

## 输出去哪

1. stdout（automation 抓这个送到对话里）；
2. `out/daily-picks/<日期>.md`（同日重跑覆盖＝幂等，atomicio 落盘）。

不写任何 data/*.json 源数据；唯一的状态文件是轮换记录。
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import triage  # noqa: E402
import atomicio  # noqa: E402

STATE = os.path.join(ROOT, "data", "daily_picks_state.json")
OUT_DIR = os.path.join(ROOT, "out", "daily-picks")
ROTATE_DAYS = 14          # 轮换记录保留天数
BACK_OFF_DAYS = 2         # 最近推过的降权窗口

GRADE_PRIORITY = {"ready": 0, "deep": 1, "backfill": 2}


def _raw_lookup(data):
    """(scope, id) → 原始记录摘要。

    ⚠ 同一 id 可能候选池、队列各有一条（候选是队列晋升的影子），
      两边的 metrics 结构不同（实测 blabla：队列有 mrr=5769，候选没有）。
      必须用 triage item 自带的 scope 精确取，不能按裸 id 混用。
    """
    look = {}
    for scope, key in (("candidate", "candidates"), ("inbox", "inbox")):
        for rec in data.get(key) or []:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            look[(scope, rec["id"])] = {
                "one_liner": (rec.get("one_liner") or "").strip(),
                "source_url": rec.get("source_url") or "",
                "blocking": (rec.get("blocking") or "").strip(),
                "note": (rec.get("note") or "").strip(),
                "metrics": rec.get("metrics") if isinstance(rec.get("metrics"), dict) else {},
            }
    return look


def _recent_ids():
    """最近 BACK_OFF_DAYS 天推过的 id 集合（读失败按空处理，不挡出卡）。"""
    try:
        with open(STATE, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        return set(), {}
    if not isinstance(hist, dict):
        return set(), {}
    today = datetime.date.today()
    recent = set()
    for k, ids in hist.items():
        try:
            d = datetime.date.fromisoformat(k)
        except ValueError:
            continue
        if (today - d).days < BACK_OFF_DAYS:
            recent.update(ids or [])
    return recent, hist


def _save_state(today_ids):
    """记录今天推了什么，裁掉 14 天前的旧记录。"""
    try:
        with open(STATE, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = {}
    if not isinstance(hist, dict):
        hist = {}
    today = datetime.date.today().isoformat()
    # 同日重跑**合并**（不是覆盖）：每次运行推的都算「今天见过了」，
    # 否则 A、B 两轮互相踢，卡片在两批条目之间来回跳。
    prev = set(hist.get(today) or [])
    hist[today] = sorted(prev | set(today_ids))
    keep = today
    out = {}
    for k in sorted(hist):
        try:
            if (datetime.date.today() - datetime.date.fromisoformat(k)).days <= ROTATE_DAYS:
                out[k] = hist[k]
        except ValueError:
            continue
    out.setdefault(keep, today_ids)
    atomicio.write_json(STATE, out)


def build_card(top):
    data = triage.load_all()
    result = triage.triage_all(candidates=data["candidates"], inbox=data["inbox"],
                               index=triage.build_index(data))
    items = [i for i in result["items"] if i["grade"] in GRADE_PRIORITY]
    look = _raw_lookup(data)
    recent, _hist = _recent_ids()
    # 排序：级优先 → 近 2 天推过的降权 → 小额收入降权 → 分数
    # ⚠ 小额判定**不能**用 triage 的 revenue 字段 —— 队列条目它常为 None
    #   （实测 $5,769 的 Blabla 也是 None），要直接读原始 metrics 数字。
    #   沿用晋升门槛 TINY_REVENUE=300：$3/$5 的条目技术上「可深核」，
    #   但配不上每天的人工时间。
    def _metrics_value(rec):
        metrics = rec.get("metrics") or {}
        vals = [metrics.get(k) for k in ("mrr", "last_30d_revenue", "all_time")]
        nums = [float(v) for v in vals if isinstance(v, (int, float))]
        return max(nums) if nums else None

    def _tiny(i):
        rec = look.get((i["scope"], i["id"])) or {}
        v = _metrics_value(rec)
        return 1 if (v is not None and v < triage.TINY_REVENUE) else 0

    items.sort(key=lambda i: (GRADE_PRIORITY[i["grade"]],
                              # ready 不参与轮换：待办要一直可见，直到办完
                              (i["id"] in recent) if i["grade"] != "ready" else False,
                              _tiny(i),
                              -i["score"]))
    picks = items[:top]

    s = result["summary"]
    parts = []
    for g in ("ready", "deep", "backfill"):
        n = s["by_grade"].get(g, 0)
        if n:
            parts.append("%s %d" % (g, n))
    today = datetime.date.today()
    week = "一二三四五六日"[today.weekday()]
    lines = []
    lines.append("# 今日待核 · %s（周%s）" % (today.isoformat(), week))
    lines.append("")
    lines.append("池子概况：%s（零成本初筛，未联网）" % (" · ".join(parts) or "无可推进条目"))
    lines.append("")
    if not picks:
        lines.append("今日没有 ready/deep/backfill 条目 —— 该跑一轮采集（harvest）了。")
        text = "\n".join(lines)
        return text, [], result
    lines.append("本卡推 %d 条（近 2 天推过的靠后，轮换见 %s）：" % (len(picks), STATE))
    lines.append("")
    for rank, i in enumerate(picks, 1):
        raw = look.get((i["scope"], i["id"])) or {}
        tag = "候" if i["scope"] == "candidate" else "队"
        lines.append("## %d. [%s·%d分·%s] %s"
                     % (rank, tag, i["score"], i["grade"], i.get("name") or i["id"]))
        if raw.get("one_liner"):
            lines.append("- 是什么：%s" % raw["one_liner"][:90])
        head = (raw.get("metrics") or {}).get("headline") or ""
        if head:
            lines.append("- 收入口径：%s" % head[:80])
        lines.append("- 下一步：%s" % (i["action"] or "")[:80])
        if i["grade"] == "backfill" and i.get("hint"):
            lines.append("- 缺什么：%s" % i["hint"][:80])
        extra = raw.get("blocking") or raw.get("note")
        if extra:
            lines.append("- 注意：%s" % extra[:90])
        if raw.get("source_url"):
            lines.append("- 来源：%s" % raw["source_url"])
        lines.append("")
    text = "\n".join(lines)
    return text, [i["id"] for i in picks], result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成每日待核选题卡")
    ap.add_argument("--top", type=int, default=5, help="推几条（默认 5）")
    ap.add_argument("--no-save", action="store_true",
                    help="只打印，不写卡文件、不记轮换状态")
    args = ap.parse_args()

    text, picked_ids, result = build_card(args.top)
    print(text)
    if args.no_save:
        return 0
    today = datetime.date.today().isoformat()
    out_path = os.path.join(OUT_DIR, "%s.md" % today)
    atomicio.write_text(out_path, text + "\n")
    _save_state(picked_ids)
    print("---")
    print("卡已落盘 %s；轮换状态 %s（今日 %d 条）"
          % (out_path, STATE, len(picked_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
