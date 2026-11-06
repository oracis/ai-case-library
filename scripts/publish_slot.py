#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布档期：每个平台「今天该发 AI case 还是游戏」。

规则（2026-11-02 用户定）
------------------------
1. 每个平台的内容组合是**两类各一篇**：一篇 AI case、一篇游戏（游戏厅栏目）。
2. 平台日配额只有 1 篇时 ⇒ **按天轮流**：今天发 AI case，隔天发游戏，再隔天 AI …
3. 日配额 ≥2 的平台，可以同一天把两类各发一篇（先补「最近没发过的那类」）。
4. **两类必须交替（2026-11-05 用户定）**：连着两篇同类型（跨日或同日）都算破坏交替。
   轮到的类无稿时**默认不发**（blocked），不再自动回退另一类——正是旧 fallback 让
   B站 11-04/11-05 连发两天 AI case。确要连发同类，显式 `--allow-same-kind` 越过。
5. **轮到无稿 ⇒ 从源补稿，不轮空（2026-11-05 用户定）**：AI 线无现成稿时输出
   `need_draft=True`，从案例库挑**收录最久**（verified_at 最早）的 eligible case 补写
   草稿再发布——发的是轮到的类，与交替规则不冲突。四平台走 `--gen`（gen_case_drafts，
   选稿 `pick_by_oldest`）；腾讯云只收技术向稿，走人工重写流程（参照 mort.md 口径），
   模板拼装会被驳「技术无关」。

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
    python scripts/publish_slot.py                    # 十平台总览（B站一号+二号）
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

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db  # noqa: E402  SQLite 统一台账：读端切到 platform_posts 表

# 平台顺序 = 输出顺序，按「主力 → 长尾」排
PLATFORMS = [
    ("wechat", "公众号"),
    ("toutiao", "头条"),
    ("xhs", "小红书"),
    ("bilibili", "B站"),
    ("bilibili2", "B站二"),
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
    # 同一平台的第二个账号：**额度按账号各算各的**（B站 lv0~lv3 每天 1 篇
    # 是按号算的），别以为一号发过了二号就不能发。
    "bilibili2": 1,
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


GAME_PREFIX = "ext-ep-"


def _norm_id(cid):
    """游戏期 id 的两种写法归一：产物侧 `ext-ep-001-x`，台账侧 `ep-001-x`。

    2026-10-30 博客园/知乎两处台账记的是**不带** `ext-` 的写法（post_id 已
    published），而 `game_all()` 只产出 `ext-` 前缀。不归一 ⇒ 已发过的期被
    判成「没发过」，下一轮原样再发一遍（11-03 实测：博客园已发 ep-001，
    仍被选成今日候选，差点重发）。
    """
    cid = str(cid or "").strip()
    if cid.startswith("ext-"):
        # 只剥 "ext-"，保留 "ep-"：ext-ep-001-x → ep-001-x
        return cid[4:]
    return cid


def kind_of(case_id, _extra=None):
    """一篇稿属于哪一类：`game` 还是 `ai`。

    判据：**游戏厅系列**，两种写法都算 —— 产物侧 `ext-ep-NNN-*`、台账侧
    `ep-NNN-*`（见 `_norm_id`）。别拿 cases.json 的 category 猜：那里没有
    「游戏」这个分类，而 `chessigma`（国际象棋）恰恰是正经 AI case，
    按关键词猜会误判。
    """
    cid = str(case_id or "").strip()
    if cid.startswith(GAME_PREFIX):
        return "game"
    if cid.startswith("ep-") and cid[3:6].isdigit() and cid[6:7] == "-":
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
    """公众号：优先从 SQLite ledger 读 platform='wechat' state='published'。"""
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, "wechat", state="published")
        if posts:
            out = []
            for p in posts:
                at = p.get("published_at") or p.get("updated_at") or ""
                if at:
                    out.append((str(at), p["case_id"]))
            return out
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        db.close_conn()
    # fallback：从 JSON 读（迁移期兼容旧数据）
    d = _load("wechat_published.json") or {}
    out = []
    for cid, v in d.items():
        if not isinstance(v, dict):
            continue
        at = v.get("verified_at") or v.get("published_at") or ""
        if at:
            out.append((str(at), cid))
    return out


_PLATFORM_FILE_MAP = {
    "toutiao": "toutiao_published.json",
    "xhs": "xhs_published.json",
    "bilibili": "bili_published.json",
}


def _rows_list(platform, keys=("at", "published_at"), id_keys=("case_id", "id")):
    """通用 list 型台账：优先从 SQLite ledger 读 platform 对应的 published 记录。"""
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, platform, state="published")
        if posts:
            out = []
            for p in posts:
                at = ""
                for k in keys:
                    if p.get(k):
                        at = str(p[k])
                        break
                if not at:
                    # 2026-11-05 修：published_at（真实发表时点）优先于 updated_at
                    # （补账/同步时刻）—— 否则补账记录全被算成「今天发的」，误触日配额。
                    at = p.get("published_at") or p.get("updated_at") or ""
                cid = p["case_id"]
                if at and cid:
                    out.append((at, cid))
            return out
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        db.close_conn()
    # fallback：从 JSON 读（迁移期兼容旧数据）
    fname = _PLATFORM_FILE_MAP.get(platform, platform + "_published.json")
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


def _rows_dict(platform, status_ok=("published",), ts_keys=("ts", "published_ts", "published_at")):
    """cnblogs / zhihu / csdn 那种 {case_id: {...}} 结构。优先从 SQLite ledger 读。"""
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, platform)
        if posts:
            out = []
            for p in posts:
                if status_ok and (p.get("state") or "") not in status_ok:
                    continue
                at = ""
                for k in ts_keys:
                    if p.get(k):
                        at = str(p[k])
                        break
                if not at:
                    # 2026-11-05 修：published_at（真实发表时点）优先于 updated_at
                    # （补账/同步时刻）—— 否则补账记录全被算成「今天发的」，误触日配额。
                    at = p.get("published_at") or p.get("updated_at") or ""
                if at:
                    out.append((at, p["case_id"]))
            return out
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        db.close_conn()
    # fallback：从 JSON 读（迁移期兼容旧数据）
    fname_map = {
        "cnblogs": "cnblogs_drafts.json",
        "zhihu": "zhihu_drafts.json",
        "csdn": "csdn_drafts.json",
    }
    fname = fname_map.get(platform, platform + "_drafts.json")
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
    return _rows_dict("csdn", status_ok=("published",))


def _rows_yun(which):
    """两朵云：优先从 SQLite ledger 读 platform='tencentcloud'/'aliyun' state='published'。"""
    platform = "tencentcloud" if which == "tencentcloud" else "aliyun"
    conn = db.get_conn()
    try:
        posts = db.get_posts_by_platform(conn, platform)
        if posts:
            out = []
            for p in posts:
                if p.get("case_id", "").startswith("__community__"):
                    continue
                if which == "tencentcloud":
                    sent = p.get("state") == "published" or bool(p.get("remote_id"))
                    cand = (p.get("published_at"), p.get("updated_at"))
                else:
                    sent = bool(p.get("published_at") or p.get("remote_id"))
                    cand = (p.get("published_at"), p.get("updated_at"))
                if not sent:
                    continue
                at = next((str(x) for x in cand if x), "")
                out.append((at, p["case_id"]))
            return out
    except Exception:                                   # noqa: BLE001
        pass
    finally:
        db.close_conn()
    # fallback：从 JSON 读（迁移期兼容旧数据）
    d = _load("yun_finalize.json") or {}
    out = []
    for cid, v in d.items():
        if not isinstance(v, dict) or cid.startswith("__community__"):
            continue
        if which == "tencentcloud":
            sent = v.get("tc_state") == "published" or bool(v.get("tc_article"))
            cand = (v.get("tc_published_at"), v.get("tc_state_at"), v.get("updated"))
        else:
            sent = bool(v.get("al_published_at") or v.get("al_article"))
            cand = (v.get("al_published_at"), v.get("al_state_at"), v.get("updated"))
        if not sent:
            continue
        at = next((str(x) for x in cand if x), "")
        out.append((at, cid))
    return out


def _rows_games_bili2():
    """B站二号：读**游戏仓**台账（二号真账簿不在本仓）。

    读法已统一到 `games_state`（2026-11-06）：`publish_one` 的 `dedupe`
    闸也在读同一个模块，两处口径不许再各写一份 —— 此前这里独自实现了一份
    「只认 `published`、时间取 `verified_at`」的读法，而 dedupe 压根没读，
    于是二号「发过没发过」只有档期知道、闸不知道。

    仍归一成产物侧写法 `ext-<slug>`（`_norm_id` 会再剥掉 `ext-`）。
    """
    try:
        import games_state as GS
    except ImportError:
        GS = None
    if GS is not None:
        return [(r["at"], "ext-" + r["case"])
                for r in GS.published("bilibili2") if r["at"]]
    # 兜底：模块缺失时退回旧读法（宁可能跑，不许静默返回「今天没发过」）——
    # ⚠ 这分支只该在部署残缺时出现，正常路径走上面。
    p = os.path.join(GAMES_ROOT, "data", "publish_state.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for slug, art in (d.get("articles") or {}).items():
        r = ((art or {}).get("platforms") or {}).get("bilibili2") or {}
        if r.get("state") != "published":
            continue
        at = str(r.get("published_at") or r.get("verified_at")
                 or r.get("attempted_at") or "")
        if at:
            out.append((at, "ext-" + slug))     # 归一成产物侧写法，_norm_id 会剥 ext-
    return out


READERS = {
    "wechat": _rows_wechat,
    "toutiao": lambda: _rows_list("toutiao"),
    "xhs": lambda: _rows_list("xhs"),
    "bilibili": lambda: _rows_list("bilibili"),
    "bilibili2": lambda: _rows_games_bili2(),
    "cnblogs": lambda: _rows_dict("cnblogs"),
    "zhihu": lambda: _rows_dict("zhihu"),
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
    # 同一篇在同一天被记了多条：台账的 `at` + 补账时刻（verified_at）、
    # 台账 + kind_log 补记，都会各算一条。不去重则 `today_count` 虚高 ——
    # 2026-11-06 B站一号就被记成「今日 2 篇」（ep-001 的 13:41 与 13:45）。
    # quota=1 时结论不变，但 quota=2 的平台（博客园/CSDN）会因此少发一篇。
    uniq = {}
    for at, cid in rows:
        uniq.setdefault((at[:10], _norm_id(cid)), (at, cid))
    return sorted(uniq.values())


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


# 游戏仓：一期游戏稿落 10 份 `content/<slug>/platform/<平台>.md`（2026-11-02 建、
# 11-03 补齐 110 份）。跨仓路径只此一处，`GAMES_ROOT` 可覆盖（换机/CI）。
GAMES_ROOT = os.environ.get("GAMES_ROOT", r"D:/open-source-games")

# 哪些平台的发布脚本是「喂一个 md 文件」（`--file`）。只有这些平台，游戏仓的
# platform/<平台>.md 才算可直接发的产物。
# ⚠ 头条/小红书/B站**不算**：它们 `--case` 打开的是 out/<平台>/<case>/ 那套
# 二版产物（标题模板 + 配图），游戏仓的 md 不能顶替 —— 记进来会让档期以为
# 「有稿」然后发布时打空。
MD_FILE_PLATFORMS = ("csdn", "cnblogs", "zhihu", "tencentcloud", "aliyun")


def game_pack(platform, case_id):
    """游戏仓里这一期该平台的平台稿；不存在返回 None。"""
    if platform not in MD_FILE_PLATFORMS:
        return None
    cid = str(case_id or "")
    if not cid.startswith("ext-"):
        return None                                   # AI case 不走这条路
    p = os.path.join(GAMES_ROOT, "content", cid[len("ext-"):],
                     "platform", "%s.md" % platform)
    return p if os.path.isfile(p) else None


def pack_of(platform, case_id):
    """该平台这一篇的产物路径（不存在返回 None）。发布与判档期共用这一个判据。"""
    if platform == "bilibili2":
        # 二号产物是**单文件** `out/articles/<cid>-b2.html`：`-b2` 后缀是防止
        # 两号共用同一个 ext-<slug>.html（谁后摆位谁覆盖，一号会突然发出二号的
        # 免费版，见 bridge.ext_id 注释）。不是目录、也不吃游戏仓 platform/*.md。
        p = os.path.join(ROOT, "out", "articles", str(case_id) + "-b2.html")
        return p if os.path.isfile(p) else None
    spec = PACK.get(platform)
    if spec:
        rel, style = spec
        base = os.path.join(ROOT, rel)
        if style == "dir":
            p = os.path.join(base, case_id)
            if os.path.isdir(p):
                return p
        else:
            p = os.path.join(base, case_id + (".html" if style == "html" else ".md"))
            if os.path.isfile(p):
                return p
    return game_pack(platform, case_id)


def pack_exists(platform, case_id):
    return pack_of(platform, case_id) is not None


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
    # 用归一口径比：台账里可能是 ep-001-x，产物侧是 ext-ep-001-x。
    # 不按 kind_of 过滤 —— 它认得出两种写法，但 sent 里同时混着 AI case，
    # 归一后直接求差更省事（AI case id 不会与 ep-NNN- 撞）。
    sent = {_norm_id(cid) for _, cid in records(platform)}
    ids = [k for k in game_all() if _norm_id(k) not in sent]
    if ready_only:
        ids = [k for k in ids if pack_exists(platform, k)]
    return ids


def game_missing_pack(platform):
    """还没发、但草稿包也没做的游戏稿数量（= 要补的活）。"""
    sent = {_norm_id(cid) for _, cid in records(platform)}
    return [k for k in game_all()
            if _norm_id(k) not in sent and not pack_exists(platform, k)]


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


# 只走单一类型的平台：**不参与「两类交替」**（2026-11-06 加二号时定）。
#   bilibili2 目前只有游戏稿通道：`publish/bridge.py --platform bilibili2`
#   从游戏仓取 free 变体、stage 出 `out/articles/<cid>-b2.html` 并换 h1；
#   AI case 没有二号专属产物的生成路径。
#   ⚠ 若让二号参与交替：上一轮发游戏 ⇒ 今天 want=ai ⇒ 池永远空 ⇒ 每天
#     blocked，等于加了个永远轮不到的位置（就是这次要修的病）。
ONLY_KINDS = {
    "bilibili2": "game",
}


def decide(platform, day=None, allow_same_kind=False):
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

    # 单一类型平台：直接定死，不跟昨日/今日的类别走（见 ONLY_KINDS 注释）
    forced = ONLY_KINDS.get(platform)
    if forced:
        why = "该平台只走「%s」单一通道 ⇒ 固定发这一类，不做两类交替" % (
            KIND_LABEL[forced])
        want = forced

    if want == "game":
        pool = game_pool(platform)
        missing = game_missing_pack(platform)
    else:
        pool = ai_pool(platform)
        missing = []

    # 轮到的那一类没稿，分两种（2026-11-05 用户定）：
    # ① 轮到 AI 且 AI 池空 ⇒ 不轮空、也不回退游戏（破坏交替），输出 need_draft=True，
    #   由 publish_one --gen 从案例库补写「收录最久」的 eligible case（腾讯云走
    #   技术向人工重写，见 gen_case_drafts 模块 docstring 的⛔说明）。
    # ② 轮到游戏且游戏池空（头条式源耗尽）⇒ 默认 blocked；连发同类要显式
    #   allow_same_kind=True（实测教训：B站 11-04 AI → 11-05 fallback 又发 AI）。
    if not pool and want == "ai":
        out["need_draft"] = True
        out.update(kind="ai", reason=why + "；「AI case」无现成稿 ⇒ 按「无稿补写」规则"
                                      "从源生成收录最久的一篇（publish_one --gen）")
        return out
    if not pool:
        alt = "ai" if want == "game" else "game"
        alt_pool = ai_pool(platform) if alt == "ai" else game_pool(platform)
        if alt_pool and allow_same_kind:
            out["fallback"] = "「%s」无稿可发，--allow-same-kind 显式改发 %s（同类连发，破坏交替）" % (
                KIND_LABEL[want], KIND_LABEL[alt])
            want, pool = alt, alt_pool
            missing = []
            why = why + ";" + out["fallback"]
        elif alt_pool:
            out["blocked"] = ("轮到「%s」无稿可发；回退另一类会连发同类、违反「两类交替」规则" 
                              "⇒ 本轮不发（--allow-same-kind 可越）" % KIND_LABEL[want])
            # blocked 也带 kind=None + reason：main() 渲染与下游消费都假定键存在
            out.update(kind=None, reason=out["blocked"])
            return out

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


def report(platforms=None, day=None, allow_same_kind=False):
    rows = []
    for key, cn in PLATFORMS:
        if platforms and key not in platforms:
            continue
        d = decide(key, day, allow_same_kind=allow_same_kind)
        d["name"] = cn
        rows.append(d)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="发布档期：今天每个平台该发 AI case 还是游戏")
    ap.add_argument("--platform", action="append", help="只看某平台（可多次）")
    ap.add_argument("--day", help="预演某天（YYYY-MM-DD），默认今天")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--allow-same-kind", action="store_true",
                    help="轮到的类无稿时显式回退另一类（连发同类，破坏交替，默认拦）")
    args = ap.parse_args(argv)

    rows = report(args.platform, args.day, allow_same_kind=args.allow_same_kind)
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
