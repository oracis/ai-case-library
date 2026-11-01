# -*- coding: utf-8 -*-
"""用 AI 把过长的标题「缩略改写」成平台能整句放下的短标题。

为什么需要
----------
各平台标题上限不一样，而自动生成的长标题是**按内容拼**的，撞上限就被
平台从中间硬切：

    头条 30 字：AgentsGuild：近 30 天已验证收入 ，为游戏开发    ← 切在逗号后
    B站  30 字：ClawLink：MRR $2,219，TrustMRR 实                ← 切在词中间

切完既不通顺、又白白丢掉了后半句的信息。正确做法不是「截断」，而是
**重新缩略**：保留产品名 + 一个卖点，总长压到上限以内，语气与原题一致。

设计取舍
--------
· **不改原稿**：只在建稿/改稿时把标题换短，正文一个字不动。
· **有缓存**：`data/short_titles.json` 按「原标题+上限」存结果，
  同一条重跑不再花 LLM 额度（免费档高峰期 429 很常见）。
· **只用免费端点**：主端点（DeepSeek）是付费且是推理模型、吃 max_tokens，
  这里走 `ai_verify.AI_FB2_*`（glm-4-flash）。
· **硬校验**：结果必须 ≤ limit、无省略号、不含换行；不合格就退回本地
  规则缩略（保留产品名 + 首个短句），绝不让「截断」再出现。

用法
----
    python -X utf8 scripts/short_title.py --text "很长的原标题……" --limit 24
    python -X utf8 scripts/short_title.py --case clawlink --limit 24
    python -X utf8 scripts/short_title.py --case clawlink --limit 24 --use   # 只打印，可 --apply 落盘
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "short_titles.json")

SYS = (
    "你是中文标题编辑。把给定的长标题改写成更短的一句话标题。"
    "硬性要求："
    "① 总字数不超过 {limit} 个字（中文/字母/数字/标点各算 1 个字符）；"
    "② 保留产品名原文；"
    "③ 只保留一个最有力的卖点，不要罗列；"
    "④ 不要用省略号、不要用『…』、不要换行、不要引号包裹；"
    "⑤ 不要添加原标题里没有的数字或结论；"
    "⑥ 语气与原标题一致，别写成广告口号 —— **禁用**「神器/必备/震惊/"
    "爆火/神器级/天花板/王炸」这类词；"
    "⑦ 优先保留原标题里的具体名词（产品名、动作、人群），"
    "宁可少一层信息，也不要换成空泛形容词。"
    "只输出标题本身，不要任何解释。"
)


# 免费档模型（glm-4-flash）经常无视「别写广告词」这条，光靠 prompt 压不住，
# 只能结果侧再拦一道：命中就重试一次，仍命中就退回规则缩略。
BANNED = ("神器", "必备", "震惊", "爆火", "天花板", "王炸", "吊打",
          "神器级", "绝了", "封神", "逆天")


def _banned(s):
    return [w for w in BANNED if w in (s or "")]


def _len(s):
    """按平台口径数字符（不按字节、不按词）。"""
    return len(s or "")


def _cache_load():
    if not os.path.isfile(CACHE):
        return {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_save(d):
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CACHE)


def _fallback(text, limit):
    """本地规则缩略：产品名 + 第一个短句，宁可短也不截断。

    判据是「切在标点上」，没有标点可切就整句保留（超过上限也保留，
    让人看见问题，而不是悄悄切一半）。
    """
    t = (text or "").strip()
    if _len(t) <= limit:
        return t
    m = re.match(r"^([^：:]{1,20})[：:]\s*(.+)$", t)
    head, body = (m.group(1), m.group(2)) if m else ("", t)
    parts = re.split(r"[，,。；;！!？?]", body)
    out = ""
    for p in parts:
        cand = (head + "：" + p) if head else p
        if _len(cand) <= limit:
            out = cand
        else:
            break
    return out or (head or t)[:limit]


def _clean(s):
    s = (s or "").strip().strip("《》\"'“”‘’")
    s = re.sub(r"\s+", " ", s)
    s = s.replace("…", "").replace("...", "")
    return s.strip()


def condense(text, limit=24, refresh=False, verbose=True):
    """返回 (短标题, 来源)。来源 ∈ cache / ai / fallback。"""
    text = (text or "").strip()
    if not text:
        return "", "empty"
    if _len(text) <= limit:
        return text, "already-short"

    key = "%d|%s" % (limit, text)
    cache = _cache_load()
    if not refresh and key in cache:
        got = cache[key]
        if got and _len(got) <= limit:
            return got, "cache"

    got, src = "", "fallback"
    try:
        import ai_verify as A
        if A.AI_FB2_BASE and A.AI_FB2_KEY and A.AI_FB2_MODEL:
            msgs = [{"role": "system", "content": SYS.format(limit=limit)},
                    {"role": "user", "content": text}]
            for attempt in (1, 2):
                raw = A._llm_once(msgs, temperature=0.3, max_tokens=120,
                                  base=A.AI_FB2_BASE, key=A.AI_FB2_KEY,
                                  model=A.AI_FB2_MODEL)
                cand = _clean(str(raw or "").split("\n")[0])
                bad_w = _banned(cand)
                if cand and _len(cand) <= limit and "…" not in cand and not bad_w:
                    got, src = cand, "ai"
                    break
                if verbose:
                    why = ("含禁用词%s" % "、".join(bad_w)) if bad_w else \
                          ("%d 字超限" % _len(cand))
                    print("[warn] 第 %d 次结果不合格（%s）：%r"
                          % (attempt, why, cand[:40]), flush=True)
                if bad_w or attempt == 1:
                    msgs = msgs + [
                        {"role": "assistant", "content": cand},
                        {"role": "user", "content":
                         "不合格：%s。重写，只用具体名词，不用任何形容词，"
                         "不超过 %d 个字。" % (why, limit)}]
    except Exception as e:
        if verbose:
            print("[warn] AI 调用失败：%s，退回规则缩略" % e, flush=True)

    if not got:
        got = _fallback(text, limit)

    cache[key] = got
    _cache_save(cache)
    return got, src


def case_title(cid):
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    if isinstance(cases, dict):
        cases = cases.get("cases") or []
    for c in cases:
        if c.get("id") == cid:
            return (c.get("title") or "").strip() or str(c.get("name") or "").strip()
    return ""


def main():
    ap = argparse.ArgumentParser(description="AI 缩略改写标题（不改正文）")
    ap.add_argument("--text", default="", help="原标题")
    ap.add_argument("--case", default="", help="按案例 id 取原标题")
    ap.add_argument("--limit", type=int, default=24, help="字数上限（默认 24）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重新生成")
    a = ap.parse_args()
    text = a.text or case_title(a.case)
    if not text:
        raise SystemExit("没有标题：给 --text 或 --case")
    got, src = condense(text, a.limit, refresh=a.refresh)
    print("原题（%d 字）：%s" % (_len(text), text))
    print("短题（%d 字）：%s    [%s]" % (_len(got), got, src))
    return 0 if _len(got) <= a.limit else 1


if __name__ == "__main__":
    sys.exit(main())
