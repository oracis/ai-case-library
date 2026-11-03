#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布档期：每个平台「今天该发 AI case 还是游戏」。

规则（2026-11-02 用户定）
------------------------
1. 每个平台的内容组合是**两类各一篇**：一篇 AI case、一篇游戏（游戏厅栏目）。
2. 平台日配额只有 1 篇时 ⇒ **按天轮流**：今天发 AI case，隔天发游戏，再隔天 AI …
3. 日配额 ≥2 的平台，可以同一天把两类各发一篇（先补「最近没发过的那类」）。

为什么要有这个文件
------------------
在此之前「发哪篇」的判断散落在各处：`send-draft` 只看「存入最久」，
`publish` 只看「本地有没有草稿」，没人管「这个平台上一轮发的是哪一类」。
结果就是 11-02 一天之内头条连发 11 篇游戏厅，AI case 一篇没发；
而其他 8 个平台清一色只有 AI case。规则不落到代码里，下次还会重演。

判据一律**只读各平台台账**，不新增平行台账（避免又一份会漂移的清单）。
唯一例外是 `data/publish_kind_log.json`：头条的游戏厅稿走 `ext-ep-*`，
从来不进 `toutiao_published.json`，只能由这里补记。

用法
----
    python scripts/publish_slot.py                    # 九平台总览
    python scripts/publish_slot.py --platform xhs     # 单平台
    python scripts/publish_slot.py --json             # 给别的脚本吃
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# 平台顺序 = 输出顺序，按「主力 → 长尾」排
PLATFORMS = [
    ("wechat", "公众号"),
    ("toutiao", "头条"),
    ("xhs", "小红书"),
    ("bilibili", "B站"),
    ("cnblogs", "博客园"),
    ("zhihu", "知乎"),
    ("csdn", "CSDN"),
    ("tencentcloud", "腾讯云"),
    ("aliyun", "阿里云"),
]

# 单日发布上限。带「实测」的来自被平台挡过的真实报错，其余是保守值。
DAILY_QUOTA = {
    "wechat": 1,        # 订阅号一天 1 次群发
    "toutiao": 3,       # 2026-11-02 禁言后自设护栏（TT_DAILY_MAX 同值）
    "xhs": 1,           # 保守：真上限未测出，按「一天一篇」走
    "bilibili": 1,      # 实测「已达到当日投稿上限」
    "cnblogs": 2,       # 社区积分每日 3 次发布，留 1 次余量
    "zhihu": 1,         # 保守
    "csdn": 2,          # 实测 400300011「今天发表文章数量已达到限制的2篇」
    "tencentcloud": 3,  # 社区积分细则 901380：每日 3 次
    "aliyun": 1,        # 保守
}

KIND_LABEL = {"ai": "AI case", "game": "游戏"}


# ---------------------------------------------------------------- 基础工具

def _load(name):
    p = os.path.join(DATA, name)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                   # noqa: BLE001
        return None


def today(day=None):
    return day or time.strftime("%Y-%m-%d")


def kind_of(case_id, _extra=None):
    """一篇稿属于哪一类：`game` 还是 `ai`。

    判据只有一条 —— **游戏厅系列是 `ext-ep-NNN-*` 命名的外部稿**。
    别拿 cases.json 的 category 猜：那里没有「游戏」这个分类，
    而 `chessigma`（国际象棋）恰恰是正经 AI case，按关键词猜会误判。
    """
    cid = str(case_id or "").strip()
    if cid.startswith("ext-ep-"):
        return "game"
    extra = _extra if _extra is not None else _kind_log()
    for item in extra.get("game_ids") or []:
        if cid == item:
            return "game"
    return "ai"


def _kind_log():
    d = _load("publish_kind_log.json") or {}
    if not isinstance(d, dict):
        return {}
    return d


# ---------------------------------------------------------------- 台账读取
# 每个 reader 都返回 [(时间字符串, case_id)]，按时间升序。
# 口径：只收**真的发出去了**的记录，草稿不算（草稿不是「发过这一类」）。

def _rows_wechat():
    d = _load("wechat_published.json") or {}
    out = []
    for cid, v in d.items():
        if not isinstance(v, dict):
            continue
        at = v.get("verified_at") or v.get("published_at") or ""
        if at:
            out.append((str(at), cid))
    return out


def _rows_list(fname, keys=("at", "published_at"), id_keys=("case_id", "id")):
    d = _load(fname) or []
    if isinstance(d, dict):
        d = list(d.values())
    out = []
    for it in d:
        if not isinstance(it, dict):
            continue
        at = ""
        for k in keys:
            if it.get(k):
                at = str(it[k])
                break
        cid = ""
        for k in id_keys:
            if it.get(k):
                cid = str(it[k])
                break
        if at and cid:
            out.append((at, cid))
    return out


def _rows_dict(fname, status_ok=("published",), ts_keys=("ts", "published_ts")):
    """cnblogs / zhihu 那种 {case_id: {...}} 结构。"""
    d = _load(fname) or {}
    out = []
    for cid, v in d.items():
        if not isinstance(v, dict):
            continue
        if status_ok and (v.get("status") or "") not in status_ok:
            continue
        at = ""
        for k in ts_keys:
            if v.get(k):
                at = str(v[k])
                break
        if at:
            out.append((at, cid))
    return out


def _rows_csdn():
    # csdn_drafts 里 blocked_by_quota 也算「试过但没发出去」，不收
    return _rows_dict("csdn_drafts.json", status_ok=("published",))


def _rows_yun(which):
    d = _load("yun_finalize.json") or {}
    out = []
    for cid, v in d.items():
        if not isinstance(v, dict) or cid.startswith("__community__"):
            continue
        if which == "tencentcloud":
            at = v.get("tc_published_at") or ""
        else:
            at = v.get("al_published_at") or ""
        if at:
            out.append((str(at), cid))
    return out


READERS = {
    "wechat": _rows_wechat,
    "toutiao": lambda: _rows_list("toutiao_published.json"),
    "xhs": lambda: _rows_list("xhs_published.json"),
    "bilibili": lambda: _rows_list("bili_published.json"),
    "cnblogs": lambda: _rows_dict("cnblogs_drafts.json"),
    "zhihu": lambda: _rows_dict("zhihu_drafts.json"),
    "csdn": _rows_csdn,
    "tencentcloud": lambda: _rows_yun("tencentcloud"),
    "aliyun": lambda: _rows_yun("aliyun"),
}


def records(platform):
    """该平台全部「真发出去了」的记录，升序。

    合并两处来源：平台台账 + `publish_kind_log.json` 的补记
    （头条 ext-ep-* 不进 toutiao_published.json，只能靠补记）。
    """
    rows = []
    try:
        rows = list(READERS[platform]())
    except Exception:                                   # noqa: BLE001
        rows = []
    extra = (_kind_log().get("records") or {}).get(platform) or []
    for it in extra:
        if isinstance(it, dict) and it.get("at") and it.get("case_id"):
            rows.append((str(it["at"]), str(it["case_id"])))
    rows.sort()
    return rows


# ---------------------------------------------------------------- 候选池

# 各平台「稿子长在哪」。判「这一篇能不能发出去」看的是产物在不在，
# 不是「库里有没有这个 case」—— 2026-11-02 之前就是没做这层判断，
# 才会把 11 篇连头条都没做二版的稿子当成「9 个平台都能发」。
PACK = {
    "toutiao": ("out/toutiao", "dir"),
    "bilibili": ("out/bili", "dir"),
    "xhs": ("out/xhs", "dir"),
    "wechat": ("out/articles", "html"),
    "cnblogs": ("drafts/newbatch/cnblogs", "md"),
    "zhihu": ("drafts/newbatch/zhihu", "md"),
    "csdn": ("drafts/newbatch/csdn", "md"),
    "tencentcloud": ("drafts/newbatch/tencentcloud", "md"),
    "aliyun": ("drafts/newbatch/aliyun", "md"),
}


def pack_exists(platform, case_id):
    spec = PACK.get(platform)
    if not spec:
        return False
    rel, style = spec
    base = os.path.join(ROOT, rel)
    if style == "dir":
        return os.path.isdir(os.path.join(base, case_id))
    if style == "html":
        return os.path.isfile(os.path.join(base, case_id + ".html"))
    return os.path.isfile(os.path.join(base, case_id + ".md"))


def game_all():
    """游戏厅全集：ext-ep-NNN-*，按编号升序（= 发布时间顺序）。"""
    titles = _load("toutiao_titles.json") or {}
    ids = [k for k in titles if str(k).startswith("ext-ep-")]
    if not ids:                                   # 兜底：扫 out/articles
        adir = os.path.join(ROOT, "out", "articles")
        try:
            ids = [f[:-5] for f in os.listdir(adir)
                   if f.startswith("ext-ep-") and f.endswith(".html")
                   and "-b2" not in f]
        except OSError:
            ids = []
    return sorted(ids)


def game_pool(platform, ready_only=True):
    """该平台「还没发过且稿子已就绪」的游戏稿。"""
    sent = {cid for _, cid in records(platform) if kind_of(cid) == "game"}
    ids = [k for k in game_all() if k not in sent]
    if ready_only:
        ids = [k for k in ids if pack_exists(platform, k)]
    return ids


def game_missing_pack(platform):
    """还没发、但草稿包也没做的游戏稿数量（= 要补的活）。"""
    sent = {cid for _, cid in records(platform) if kind_of(cid) == "game"}
    return [k for k in game_all()
            if k not in sent and not pack_exists(platform, k)]


def ai_pool(platform, ready_only=True):
    """该平台还没发过的 AI case，按 cases.json 顺序。"""
    sent = {cid for _, cid in records(platform)}
    cases = _load("cases.json") or []
    ids = [c.get("id") for c in cases
           if c.get("id") and c.get("id") not in sent]
    if ready_only:
        ids = [k for k in ids if pack_exists(platform, k)]
    return ids


# ---------------------------------------------------------------- 决策

# 平台级冻结期（`{平台: 解冻日}`，解冻日当天之前一律不发、不论配额）。
# 2026-11-02 用户拍板「头条号解禁后再动」：处罚中心写的是 11-02→11-09
# 禁言 7 天，11-09 为最后一天、到期自动解除 ⇒ 本地闸设 11-10（宁可晚一天）。
# 冻结期内连建草稿都不做——写接口同样记账。
FROZEN = {
    "toutiao": "2026-11-10",
}


def frozen_until(platform, day=None):
    """还在冻结期就返回解冻日，否则 None。"""
    until = (FROZEN.get(platform) or "").strip()
    return until if until and today(day) < until else None


def decide(platform, day=None):
    """返回今天这个平台该发什么。

    kind: "ai" / "game" / None（今天不该发）
    """
    day = today(day)
    rows = records(platform)
    todays = [r for r in rows if r[0].startswith(day)]
    quota = DAILY_QUOTA.get(platform, 1)
    last = rows[-1] if rows else None
    out = {
        "platform": platform,
        "day": day,
        "quota": quota,
        "today_count": len(todays),
        "today_kinds": sorted({kind_of(cid) for _, cid in todays}),
        "last": {"at": last[0], "case_id": last[1], "kind": kind_of(last[1])} if last else None,
    }

    # 冻结优先于配额：冻结期内不计数、不选稿，直接判「不发」
    fz = frozen_until(platform, day)
    if fz:
        out.update(kind=None, frozen=fz,
                   reason="禁言冻结中，到 %s 才动（用户 11-02 拍板）" % fz)
        return out

    if len(todays) >= quota:
        out.update(kind=None, reason="今日已发 %d 篇，达到上限 %d" % (len(todays), quota))
        return out

    if todays:
        # 配额还有剩：优先补今天没发过的那一类
        kinds = set(out["today_kinds"])
        want = "game" if "ai" in kinds else "ai"
        why = "今日已发 %s，配额还剩 %d ⇒ 补另一类" % (
            "、".join(KIND_LABEL[k] for k in kinds), quota - len(todays))
    elif last:
        want = "game" if kind_of(last[1]) == "ai" else "ai"
        why = "上次（%s）发的是 %s ⇒ 轮到 %s" % (
            last[0][:10], KIND_LABEL[kind_of(last[1])], KIND_LABEL[want])
    else:
        want, why = "ai", "该平台没有任何发布记录 ⇒ 从 AI case 起步"

    if want == "game":
        pool = game_pool(platform)
        missing = game_missing_pack(platform)
    else:
        pool = ai_pool(platform)
        missing = []

    # 轮到的那一类没稿 ⇒ 回退到另一类，别让该平台空转。
    # 两种情况都会走到这里：① 游戏稿在该平台已全部发完（头条就是）；
    # ② 平台稿还没生成（其余 7 个平台）。回退时把缺口如实带出去。
    if not pool:
        alt = "ai" if want == "game" else "game"
        alt_pool = ai_pool(platform) if alt == "ai" else game_pool(platform)
        if alt_pool:
            out["fallback"] = "「%s」无稿可发，本轮改发 %s" % (KIND_LABEL[want], KIND_LABEL[alt])
            want, pool = alt, alt_pool
            missing = []
            why = why + "；" + out["fallback"]

    out.update(kind=want, reason=why, candidates=pool[:5], pool_size=len(pool))
    if missing:
        out["missing_pack"] = len(missing)
    if not pool:
        if missing:
            out["blocked"] = ("游戏缺 %d 篇平台产物，AI case 也没有现成稿 ⇒ 该平台今天无稿可发"
                              % len(missing))
        else:
            out["blocked"] = "两类都没稿可发（游戏已发完，AI case 也缺产物）"
    return out


def report(platforms=None, day=None):
    rows = []
    for key, cn in PLATFORMS:
        if platforms and key not in platforms:
            continue
        d = decide(key, day)
        d["name"] = cn
        rows.append(d)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="发布档期：今天每个平台该发 AI case 还是游戏")
    ap.add_argument("--platform", action="append", help="只看某平台（可多次）")
    ap.add_argument("--day", help="预演某天（YYYY-MM-DD），默认今天")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    rows = report(args.platform, args.day)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    print("档期 %s ｜ 规则：两类各一篇，日配额 1 篇的平台按天轮流\n" % today(args.day))
    print("%-8s %-6s %-6s %-12s %-12s %s" % ("平台", "今日", "上限", "上次发的", "今天该发", "依据 / 缺口"))
    print("-" * 104)
    for d in rows:
        today_s = "%d 篇" % d["today_count"] if d["today_count"] else "—"
        last_s = KIND_LABEL[d["last"]["kind"]] if d["last"] else "无记录"
        if d["last"]:
            last_s += " " + d["last"]["at"][5:10]
        want = KIND_LABEL[d["kind"]] if d["kind"] else "不发"
        print("%-8s %-6s %-6s %-12s %-12s %s" % (
            d["name"], today_s, d["quota"], last_s, want, d["reason"]))
        if d.get("blocked"):
            print("         └ ⚠ %s" % d["blocked"])
        elif d.get("missing_pack"):
            print("         └ 另有 %d 篇游戏稿未发、且该平台稿未生成" % d["missing_pack"])
        if d.get("candidates"):
            head = ", ".join(d["candidates"][:3])
            print("         └ 可发（%d 篇）：%s%s" % (
                d.get("pool_size") or 0, head,
                " …" if (d.get("pool_size") or 0) > 3 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
