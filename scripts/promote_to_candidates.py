#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 inbox 里核通过的条目搬进候选池，**顺带把实测数字与口径一起搬**。

为什么不能直接用现成的 promote
-----------------------------
仓库里的 `promote` 是 server 的 `/api/candidates/<id>/promote`，它**只搬运
候选上已有的字段** —— 这是有意的设计（PRD.md 第 142 行：promote 之前必须先
跑 `contentpack_ready`，否则会造出空壳案例）。所以「进候选池」这一步没有
现成脚本，而它恰恰是深核前必须做的一步。

为什么必须重拉数字，不能搬缓存
------------------------------
采集队列的数字是**一个月前**的快照（`last_harvest.json` 停在 09-23），
而 TrustMRR 的数字每天都在变。2026-10-10 实测搬缓存会踩两个坑：

    · sonora-ai   缓存 MRR $3,970→  实测 $88.80（差 45 倍）。
                  月线显示 09-23 之后单日收入从 $172 掉到 $0~13，订阅 500+→ 5，
                  产品实际上已经死了。搬缓存等于把一具尸体写成案例。
    · vid-ai      缓存 customers=6,626，实测付费订阅只有 726 ——
                  差一个数量级，因为 `customers` 采的是累计注册用户（契约见
                  DATA_SCHEMA.md:212，该字段是自由文本）。

所以本脚本的职责是：**实拉 → 核对 → 搬最新的 → 把口径写进 metric_note**。
不负责写正文（那是 `fill_case_text.py` / `contentpack_ready.py` 的活）。

用法
----
    python scripts/promote_to_candidates.py --dry-run    # 只看会改什么（默认）
    python scripts/promote_to_candidates.py --write --ids podawaa,vid-ai
    python scripts/promote_to_candidates.py --write --all  # 所有核通过的

⚠️ 写入前后必须核对换行格式（红线 7）：`candidates.json` 是 LF，
`inbox.json` 是 CRLF，两者不同。本脚本按各文件原生格式写回。
"""

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import audit_delisted as AD                                   # noqa: E402
import triage as T                                             # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INBOX = os.path.join(ROOT, "data", "inbox.json")
CANDS = os.path.join(ROOT, "data", "candidates.json")
ARCHIVE = os.path.join(ROOT, "data", "inbox_archive.json")


def _write_like(path, rows):
    """按**该文件自己的原生换行格式**写回（红线 7：三种格式并存）。

    实测 2026-10-10：`inbox.json` / `cases.json` 是 CRLF，`candidates.json` 是 LF。
    用默认 `open()` 写会把整份文件的换行全部改掉，diff 全红。
    """
    with open(path, "rb") as f:
        crlf = f.read().count(b"\r\n") > 0
    nl = "\r\n" if crlf else "\n"
    out = json.dumps(rows, ensure_ascii=False, indent=2).replace("\n", nl)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    os.replace(tmp, path)
    return crlf


def archive_from_inbox(ids):
    """把已晋升的条目从 inbox 移出，留底到 inbox_archive.json（**不删除**）。

    🚨 不移出会留下一个真实的假象：条目同时存在于两个池子里，
    `triage` 的 `duplicate_of` 会把它判成「已在候选池，不必再进一次队列」
    ⇒ grade 落到 `drop`。于是测试 `test_判归档的都没数字` 立刻炸：
    一条**有数字**（MRR $65,990）却被判「建议归档」。
    数据没问题，是状态不一致 —— 晋升这件事只做了一半。
    """
    with open(INBOX, encoding="utf-8", newline="") as f:
        inbox = json.load(f)
    keep = [i for i in inbox if i.get("id") not in ids]
    spill = [i for i in inbox if i.get("id") in ids]
    if not spill:
        return 0, len(inbox)

    today = "2026-10-10"
    existing = []
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE, encoding="utf-8", newline="") as f:
            existing = json.load(f)
    for it in spill:
        it["archived_at"] = today
        it["archive_reason"] = "已晋升到候选池（promote_to_candidates.py）"
    _write_like(ARCHIVE, existing + spill)
    _write_like(INBOX, keep)
    return len(spill), len(keep)

# 允许入库的最低月收入（美元）。与 triage.TINY_REVENUE 同源，
# 但这里额外要求**实测**（不是缓存），因为深核之后就该按一手数判定。
MIN_MRR = 1000.0

# 实测 MRR 相对缓存的允许偏差。超过说明这段时间发生了实质变化
#（暴涨、暴跌、换支付网关），必须让人看一眼，不自动搬。
MAX_DRIFT = 0.35

# 月收入跌幅超过这个比例 ⇒ 产品在萎缩，不适合写成「增长案例」
COLLAPSE = -0.30

# 🚨 **历史峰值留存率**下限（2026-10-10 实测补的第二个判据）。
#
# 只看「最近一个月环比」会漏掉最该拦的那类：产品曾经很红、然后腰斩，
# 而腰斩发生在几个月前 —— 最近一个月反而是平的，看起来一切正常。
# 实测两个对照：
#
#     linkpost   峰值 $35,909 → 最近完整月 $5,702，留存 15.9%
#                （03 月冲到 $35k，06 月掉到 $5.4k，之后一直横盘）
#     appalchemy 峰值 $17,427 → $5,860，留存 33.6%
#
# 这类条目的「当前 MRR」完全够门槛，量级也够，但它们讲的是
# 「怎么把一个爆款做死」而不是「怎么把生意做起来」——
# 写成案例会是**用幸存者偏差讲故事**。留存 60% 以下一律不自动晋升。
#
# 为什么不是「一律拦掉」：留存低不等于没价值，它可能值得写成
# 「衰退案例」。所以这里只**不自动晋升**，由人决定要不要写、怎么写。
PEAK_RETENTION = 0.60

# 🚨 **人工破例名单**（2026-10-10，作者决定）。
#
# 留存判据会误伤一种形态：**高位长期横盘后温和下滑**。
# 它和「爆款后崩塌」不是一回事 ——
#   linkpost  峰值 $35,909 → $5,702（留存 16%）  暴涨后腰斩
#   vid-ai    峰值 $89,846 → $41,609（留存 46%）  $85k 高位横盘 8 个月，
#                                                之后连续 4 个月每月降约 10%
# 后者是「稳定的大盘生意」，不是「被腰斩的爆款」，且它仍在挂牌出售
# （要价 $1.5M、倍数 2.87x），本身就是「体量撑得住」的反证。
#
# 破例必须留痕：写进 metric_note，读者能看到它在降，不靠藏。
OVERRIDE_RETAIN = {
    "vid-ai": "作者破例：高位横盘 8 个月后缓降，非爆款崩塌；仍在挂牌出售",
}


def fmt_money(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "?"
    return "${:,.0f}".format(v)


def calendar_month_growth(monthly, synced_at):
    """用**完整月**算环比，残月必须排除。

    🚨 2026-10-10 实测踩到的坑：TrustMRR 的月度时间线最后一行是**当月至今**。
    今天是 10 号，`vid-ai` 的 10 月行只有 $9,643，直接拿它跟 9 月的 $41,609
    比会得出「暴跌 77%」的假结论，而它真实的 MRR 环比只跌了 19%。
    判趋势只能用 `Current MRR`（TrustMRR 自己算好的口径），或者剔除残月。
    """
    if not monthly or not synced_at:
        return None, None
    now = synced_at[:7]
    full = [m for m in monthly if m["month"] < now]
    if len(full) < 2:
        return None, None
    prev, cur = full[-2], full[-1]
    if not prev["revenue"]:
        return None, None
    return (cur["revenue"] - prev["revenue"]) / prev["revenue"], cur["month"]


def peak_retention(live):
    """历史峰值 → 最近完整月的留存率。数据不足返回 None。

    只用**完整月**，残月必须剔除（同 calendar_month_growth 的理由）。
    """
    monthly = live.get("monthly") or []
    synced = live.get("synced_at")
    if not monthly or not synced:
        return None, None, None
    now = synced[:7]
    full = [m["revenue"] for m in monthly if m["month"] < now]
    if len(full) < 2:
        return None, None, None
    peak = max(full)
    if peak <= 0:
        return None, None, None
    last = full[-1]
    return last / peak, peak, last


def check_one(rec, live):
    """核一条。返回 (是否通过, 原因, 实测数字dict, 附加信息dict)。

    附加信息里带破例说明与留存率，好让它们能写进口径备注 ——
    破例不是「悄悄放行」，必须在读者能看到的地方留痕。
    """
    rid = rec.get("id")
    extra = {}
    if live["state"] != "live":
        return False, "已下架（sitemap 无此 slug 且 .md 抓不到）", None, extra

    m = live["metrics"]
    mrr = m.get("mrr")
    if not isinstance(mrr, (int, float)) or mrr <= 0:
        return False, "实测 MRR 为 %s —— 收入未公开或已归零，不够成案" % mrr, m, extra

    if mrr < MIN_MRR:
        return False, "实测 MRR %s 低于成案门槛 %s" % (fmt_money(mrr), fmt_money(MIN_MRR)), m, extra

    growth, mth = calendar_month_growth(live.get("monthly"), live.get("synced_at"))
    if growth is not None and growth <= COLLAPSE:
        return False, ("%s 完整月环比 %+.1f%%（已剔除残月）—— 产品在萎缩，"
                       "不适合写成案例" % (mth, growth * 100)), m, extra

    # 峰值腰斩判据（见 PEAK_RETENTION 注释）。破例外必须写进口径说明。
    ret, peak, last = peak_retention(live)
    if ret is not None:
        extra.update(ret=ret, peak=peak, last=last)
        if ret < PEAK_RETENTION:
            if rid in OVERRIDE_RETAIN:
                extra["override"] = OVERRIDE_RETAIN[rid]
            else:
                return False, ("峰值 %s → 最近完整月 %s，留存仅 %.0f%%（< %.0f%%）—— "
                               "爆款后腰斩，讲的是「怎么把生意做死」，不自动晋升"
                               % (fmt_money(peak), fmt_money(last), ret * 100,
                                  PEAK_RETENTION * 100)), m, extra

    # 与缓存的偏差：超过阈值说明期间有实质变化，要人看一眼
    cached = (rec.get("metrics") or {}).get("mrr")
    if isinstance(cached, (int, float)) and not isinstance(cached, bool) and cached > 0:
        drift = (mrr - cached) / cached
        if abs(drift) > MAX_DRIFT:
            return False, ("实测 %s 与缓存 %s 偏差 %+.0f%%（> %d%%），"
                           "期间有实质变化，需人工确认口径"
                           % (fmt_money(mrr), fmt_money(cached), drift * 100,
                              MAX_DRIFT * 100)), m, extra
    extra["growth"] = growth
    return True, "通过", m, extra


def build_metric_note(rec, live, m, growth, ret=None, peak=None, last=None, override=None):
    """口径说明。**每个进案例的数字都必须带它**（红线 1）。"""
    bits = [
        "实测口径：MRR %s（TrustMRR 从支付网关 API 直读，%s 同步，%s）。"
        % (fmt_money(m.get("mrr")),
           str(live.get("synced_at"))[:10],
           live.get("verified_via") or "支付网关"),
        "近 30 天收入 %s、累计 %s、付费订阅 %s 户。"
        % (fmt_money(m.get("last_30d_revenue")), fmt_money(m.get("all_time")),
           m.get("subscriptions")),
    ]
    if growth is not None:
        bits.append("最近完整月环比 %+.1f%%（已剔除当月残月）。" % (growth * 100))
    else:
        bits.append("月度时间线不足两个月，不给环比结论。")

    # 🚨 **MRR 增速与收入增速必须一起写**（2026-10-10，cometly 触发）。
    # 两者可正负相反：cometly 收入 +35.4%（一次性大额入账）但 MRR −4.9%。
    # 只写收入增速会把「在跌」讲成「在涨」，而且数字本身完全合理、
    # 看一眼发现不了 —— 这是趋势字段特有的口径混用。
    mg, rg = m.get("mrr_growth_30d"), m.get("revenue_growth_30d")
    if mg is not None:
        bits.append("近 30 天 MRR 增速 %+.1f%%。" % mg)
    if rg is not None and mg is not None and abs(rg - mg) >= 5:
        bits.append("⚠ 同期**收入**增速为 %+.1f%%，与 MRR 增速不一致"
                    "（一次性入账/退款等会造成背离）—— 讲增长必须用 MRR 口径。"
                    % rg)

    # 🚨 趋势必须给全貌：只看最近一个月会漏掉「早已腰斩」（红线 2）
    if ret is not None:
        bits.append("⚠ 历史峰值 %s → 最近完整月 %s，留存 %.0f%%。"
                    % (fmt_money(peak), fmt_money(last), ret * 100))
    if override:
        bits.append("⚠ %s。" % override)

    trade = rec.get("trade") or {}
    if trade.get("price"):
        bits.append("挂牌 %s、倍数 %s（要价不等于收入）。"
                    % (trade.get("price"), trade.get("multiple") or "未披露"))
    # 快照说明要区分两种来源（红线 1：口径必须写清）
    #
    # 🚨 判据是「**缓存里到底有没有数字**」，不是「有没有 added_at」。
    # 反例就在真实数据里：`cometly` 有 added_at=2026-09-11，但当时
    # `mrr=None`（收入未公开）—— 按 added_at 分支会说「队列缓存值是
    # 09-11 采集时的快照」，可那时根本没有值，空话会让人误以为
    # 「缓存里有旧数字，只是不准」。
    old_mrr = (rec.get("metrics") or {}).get("mrr")
    if old_mrr is None:
        bits.append("⚠ 刷新前该条目**没有任何收入数字**"
                    "（早期入库时收入未公开），本次首次补齐。")
    elif rec.get("added_at"):
        bits.append("⚠ 队列缓存值是 %s 采集时的快照（当时 %s），与上述实测不同；"
                    "本条以实测为准。" % (rec["added_at"], fmt_money(old_mrr)))
    else:
        # 走 --refresh-in-pool 时 rec 就是候选池里那条，更老的老条目可能没有
        # added_at，但总得说清旧值是多少。
        bits.append("⚠ 刷新前记录的月收入为 %s，是旧快照；本条以本次实测为准。"
                    % fmt_money(old_mrr))
    return "".join(bits)


def to_candidate(rec, live, m, growth, ret=None, peak=None, last=None, override=None):
    """按候选池现有条目的字段结构生成一条。"""
    out = {k: rec.get(k) for k in (
        "id", "name", "name_en", "origin", "one_liner", "category",
        "models", "source_url", "website",
        "harvest_source", "founded_at", "founder_x", "founder_name",
        "founder_x_followers", "trustmrr_slug", "trade", "source_kind",
        # ⚠️ `added_at` / `promoted_from_inbox` 要一起搬：它们记录「这条什么时候
        # 进候选池」，是数据血缘。丢了就看不出「体量最大的 cometly 其实 9-11
        # 就躺在这里，只是当时没数据」（2026-10-10 实测差点丢）。
        "added_at", "promoted_from_inbox",
    ) if rec.get(k) not in (None, "")}

    # 🚨 不搬 `blocking`。它是**采集阶段**留下的阻塞说明（如「需人工确认收入口径
    # 与数据时效」），而本脚本的整个存在意义就是已经实拉核对过了 ——
    # 照搬等于把「待核实」的标记和「已核实」的事实放在一起，
    # 下游看到 blocking 还以为没核过。已由 metric_note 取代。
    # `verification` 同理：不搬 `partial`，下面按实测来源改写为 stripe。

    out["metrics"] = {
        "mrr": m.get("mrr"),
        "last_30d_revenue": m.get("last_30d_revenue"),
        "all_time": m.get("all_time"),
        "subscriptions": m.get("subscriptions"),
        # 🚨 两个 growth 都要落库（2026-10-10）：`mrr_growth_30d` 是本项目
        # 主口径（红线 1），`revenue_growth_30d` 是**收入**增速 ——
        # cometly 两者正负相反（MRR −4.9% / 收入 +35.4%）。
        # 只存一个就必然有人说错另一个，写作时会拿收入增速当 MRR 增速讲。
        "growth_30d": m.get("mrr_growth_30d"),
        "mrr_growth_30d": m.get("mrr_growth_30d"),
        "revenue_growth_30d": m.get("revenue_growth_30d"),
        "headline": "MRR %s，TrustMRR 实时核对" % fmt_money(m.get("mrr")),
        "metric_note": build_metric_note(rec, live, m, growth, ret, peak, last, override),
        "caliber": "mrr",
    }
    out["verification"] = "stripe"
    # ⚠️ 已晋升过的条目要保住**原始**晋升日期，不能被这次刷新覆盖成今天
    # （`to_candidate` 顶部已经把 `promoted_from_inbox` 搬过来了）。
    out.setdefault("promoted_from_inbox", "2026-10-10")
    out["refreshed_at"] = (live.get("synced_at") or "")[:10] or None
    if out["refreshed_at"] is None:
        del out["refreshed_at"]
    # `note` 要改写：老 note 常写着「未拿到收入数字」——刷新后那句话已不成立，
    # 留着等于自己打自己的脸。
    if (rec.get("metrics") or {}).get("mrr") is None:
        out["note"] = ("由 audit_delisted.py 实拉 TrustMRR 官方 "
                       "/startup/<slug>.md 核对。收入由支付网关 API 直读"
                       "（%s），非截图自报。此前该条目在候选池里没有任何"
                       "收入数字，本次首次补齐。" % (live.get("verified_via")
                                                  or "支付网关"))
    else:
        out["note"] = ("由 audit_delisted.py 实拉 TrustMRR 官方 "
                       "/startup/<slug>.md 核对。收入由支付网关 API 直读"
                       "（%s），非截图自报。" % (live.get("verified_via")
                                              or "支付网关"))
    out["data_freshness"] = live.get("synced_at")
    return out


def slug_of(rec):
    """拿 TrustMRR slug：优先显式字段，其次从 source_url 反推。

    🚨 早期入库的条目**没有 `trustmrr_slug` 字段**（那是采集时才加的），
    只有一个 `source_url`。`cometly` 就是这种：只有
    `https://trustmrr.com/startup/cometly`，没有 slug。
    直接 `rec["trustmrr_slug"]` 会 KeyError，而 `.get()` 返回 None
    会让 probe(None) 静默失败 —— 两种都很难查，所以统一走这个函数。
    """
    s = (rec.get("trustmrr_slug") or "").strip()
    if s:
        return s
    m = re.search(r"trustmrr\.com/startup/([^/?#]+)", rec.get("source_url") or "")
    return m.group(1) if m else ""


def refresh_in_pool(args, cands):
    """刷新**已在候选池里**的条目：补实测数字 + 口径说明，判据不过就明确拦下。

    🚨 为什么需要这条路径（2026-10-10，cometly 触发）：`cometly` 早早就进了
    候选池，但当时 TrustMRR 没公开收入，`metrics` 里 `mrr=None` ——
    晋升脚本帮不上忙，因为它只处理 inbox 的条目，而池内条目 inbox 里没有。

    与「新晋升」的关键差别：
      · **不新增条目**，也不动 inbox/ 归档；
      · 判据不通过的条目**不刷数字**，直接报告原因 —— 不能出现
        「刷了新数字但它其实已经腰斩」的情况；
      · 破例机制照旧生效，且理由写进 metric_note。
    """
    targets = [x.strip() for x in args.refresh_in_pool.split(",") if x.strip()]
    idx = {c.get("id"): c for c in cands}
    missing = [t for t in targets if t not in idx]
    if missing:
        print("候选池里没有这些 id：%s" % ", ".join(missing))
        return 1

    print("待刷新 %d 条（已在候选池，不新增）\n" % len(targets))
    live_by_id = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {}
        for t in targets:
            slug = slug_of(idx[t])
            if not slug:
                print("  ⚠ %-22s 找不到 TrustMRR slug（既无trustmrr_slug "
                      "也无 source_url），跳过" % t[:22])
                continue
            futs[ex.submit(AD.probe, slug)] = t
        for fut in cf.as_completed(futs):
            rid = futs[fut]
            try:
                live_by_id[rid] = fut.result()
            except Exception as e:                                    # noqa: BLE001
                live_by_id[rid] = {"state": "unknown", "err": type(e).__name__,
                                   "metrics": {}}

    passed, rejected, skipped = [], [], []
    for rid in targets:
        rec = idx[rid]
        slug = slug_of(rec)
        if not slug:
            skipped.append(rid)
            continue
        live = live_by_id.get(rid) or {}
        old_mrr = (rec.get("metrics") or {}).get("mrr")
        ok, why, m, extra = check_one(rec, live)
        line = "  %-22s %s" % (rid[:22], why)
        if m and m.get("mrr"):
            line += "  (实测 MRR %s，原%s)" % (
                fmt_money(m.get("mrr")),
                fmt_money(old_mrr) if isinstance(old_mrr, (int, float)) else "无")
        if extra.get("override"):
            line += "  ⚠ 已人工破例"
        print(("  ✅ " if ok else "  ❌ ") + line.strip())
        (passed if ok else rejected).append((rid, rec, live, m, extra))

    print("\n可刷新 %d / 需人工 %d / 跳过 %d"
          % (len(passed), len(rejected), len(skipped)))
    if rejected:
        print("⚠ 判据未过的条目**保持原样不刷数字** —— 不能出现"
              "「刷了新数字但它其实已经腰斩」。")
    if not passed:
        return

    if not args.write:
        print("\n[dry-run] 未写盘。加 --write 实际写入。")
        return

    updated = []
    for rid, rec, live, m, extra in passed:
        # ⚠️ 不搬旧的 `blocking`：它常写着「缺收入数据」「需人工确认」，
        # 而本脚本整个存在意义就是已经实拉核对过了 ——
        # 留着等于把「已核实」和「待核实」放在一起（`to_candidate` 已处理）。
        # 破例外则显式留一条，让人知道它是**带风险**通过的。
        fresh = to_candidate(rec, live, m, extra.get("growth"),
                             extra.get("ret"), extra.get("peak"),
                             extra.get("last"), extra.get("override"))
        if extra.get("override"):
            fresh["blocking"] = ("⚠ 人工破例晋升：%s。写作时必须"
                                "如实呈现下滑，不得当稳定增长案例。"
                                % extra["override"])
        cands[cands.index(rec)] = fresh
        updated.append(rid)

    crlf = _write_like(CANDS, cands)
    print("[write] 刷新 %d 条：%s" % (len(updated), ", ".join(updated)))
    print("换行格式保持 %s" % ("CRLF" if crlf else "LF"))
    return 0


def main():
    ap = argparse.ArgumentParser(description="核通过的 inbox 条目晋升到候选池")
    ap.add_argument("--ids", default="", help="逗号分隔的 id；留空 = 自动挑")
    ap.add_argument("--all", action="store_true", help="对所有 deep 条目尝试晋升")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--write", action="store_true", help="实际写入（默认只读）")
    ap.add_argument("--refresh-in-pool", default="",
                    help="刷新**已在候选池里**的条目（逗号分隔 id）。"
                         "这些条目不在 inbox 里，走的是另一条路径："
                         "补齐实测数字 + 口径说明 + 清掉过期的 blocking，"
                         "不新增条目、不归档。典型场景：早期入库时数字没抓到"
                         "（mrr=None），后来 TrustMRR 公开了。")
    args = ap.parse_args()

    with open(INBOX, encoding="utf-8", newline="") as f:
        inbox = json.load(f)
    with open(CANDS, encoding="utf-8", newline="") as f:
        cands = json.load(f)

    have = {c.get("id") for c in cands}
    inbox_idx = {i["id"]: i for i in inbox}

    # 🚨 **池内刷新**走独立分支（2026-10-10，cometly 触发）。
    # 它不能混进 inbox 那条路径：那些条目靠 `inbox_idx[rid]` 取记录，
    # 而池内条目 inbox 里没有 ⇒ KeyError。
    if args.refresh_in_pool:
        return refresh_in_pool(args, cands)

    if args.ids:
        targets = [i.strip() for i in args.ids.split(",") if i.strip()]
    elif args.all:
        targets = [i["id"] for i in inbox
                   if T.score_record(i, "inbox")["grade"] == "deep"
                   and i["id"] not in have
                   and str(i.get("harvest_source") or "") == "trustmrr"]
    else:
        targets = [i["id"] for i in inbox
                   if T.score_record(i, "inbox")["grade"] == "deep"
                   and i["id"] not in have
                   and str(i.get("harvest_source") or "") == "trustmrr"]

    if not targets:
        print("没有待晋升的条目。")
        return

    print("待核 %d 条\n" % len(targets))
    live_by_id = {}
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(AD.probe, inbox_idx[i]["trustmrr_slug"]): i for i in targets}
        for fut in cf.as_completed(futs):
            rid = futs[fut]
            try:
                live_by_id[rid] = fut.result()
            except Exception as e:
                live_by_id[rid] = {"slug": inbox_idx[rid]["trustmrr_slug"],
                                   "state": "unknown", "err": type(e).__name__,
                                   "metrics": {}}

    passed, rejected = [], []
    for rid in targets:
        rec, live = inbox_idx[rid], live_by_id.get(rid) or {}
        ok, why, m, extra = check_one(rec, live)
        line = "  %-22s %s" % (rid[:22], why)
        if m and m.get("mrr"):
            line += "  (实测 MRR %s)" % fmt_money(m.get("mrr"))
        if extra.get("override"):
            line += "  ⚠ 已人工破例"
        (passed if ok else rejected).append((rid, rec, live, m, extra))
        print(("  ✅ " if ok else "  ❌ ") + line.strip())

    print("\n通过 %d / 拒绝 %d" % (len(passed), len(rejected)))
    if not passed:
        return

    if not args.write:
        print("\n[dry-run] 未写盘。加 --write 实际写入。")
        return

    added = []
    for rid, rec, live, m, extra in passed:
        new = to_candidate(rec, live, m, extra.get("growth"),
                           extra.get("ret"), extra.get("peak"),
                           extra.get("last"), extra.get("override"))
        cands.append(new)
        added.append(rid)

    # candidates.json 原生是 LF（与 inbox 的 CRLF 不同，红线 7）
    crlf = _write_like(CANDS, cands)

    print("[write] 候选池 %d → %d 条：%s" % (len(cands) - len(added), len(cands),
                                        ", ".join(added)))
    print("        换行格式保持 %s" % ("CRLF" if crlf else "LF"))

    moved, left = archive_from_inbox(set(added))
    print("[write] inbox %d → %d 条（%d 条移出，留底到 inbox_archive.json，未删除）"
          % (left + moved, left, moved))

    print("\n下一步（顺序不能反）：")
    print("  1. python scripts/fill_case_text.py --dry     # 看正文缺口")
    print("  2. python scripts/contentpack_ready.py --dry # 校验内容包")
    print("  3. python scripts/verify_cookie.py --id %s   # 深核补 sources[]"
          % added[0])
    print("\n本次晋升：%s" % ", ".join(added))


if __name__ == "__main__":
    main()