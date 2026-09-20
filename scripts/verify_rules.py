#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核实规则引擎 —— 把「什么算核过了」写成代码。

为什么要有这个文件
------------------
核实的主体动作仍然是「人去看原文」，这不能交给机器。但**判断够了没有**
可以交给机器：如果规则散在脑子里，每次核实都会松一点，一个月后这个库
就退化成它本来要反对的那种二手转述聚合站。

所以规则做成数据 + 纯函数，前端画界面用它、后端判入库也用它，
两边不可能算出不同结论。

三层条件
--------
1. 门槛（gate）   —— 「一道成立就进库」。三道里只要被确认成立一道就放行；
                     只有「一道都没成立 + AI 还拿到了明确反证」才判不进库。
2. 硬性必填（must）—— 缺任何一项，发布按钮是灰的。
3. 质量分（bonus）—— 加分项，凑够 threshold 分才给「精品」，否则进「备选」。

刻意不做的事
------------
不抓网页、不判断真假、不给数字打分。它只检查「你有没有做过这件事」。
一个查得比人还慢的工具，最后一定没人用。
"""

# ---------------------------------------------------------------------------
# 第 1 层：门槛。三态：yes（有明确证据）/ no（有明确反证）/ unknown（没核到）
#
# 判定口径（2026-09-16 用户定的）：**一道成立就进库**。
# 只要有一道被确认为「是」，AI 找到的反证降级成「待人工复核」提醒，不再一票否决。
# 只有「一道都没成立，且 AI 拿到了明确反证」才拦下 —— 一道正面证据都找不到、
# 又确实有反面证据的候选，进库只会污染这个库。
# ---------------------------------------------------------------------------
GATES = [
    {
        "key": "still_alive",
        "label": "公司/产品目前仍在运营",
        "why": "死掉的项目收入数字没意义——它不再是一个可持续的生意。",
    },
    {
        "key": "is_business",
        "label": "这是一个真实生意，不是一次性收入或纯流量玩法",
        "why": "接一次外包、卖一个账号不算可复刻的生意模型。",
    },
    {
        "key": "solo_possible",
        "label": "有个人能做的路径（不必现在就能做）",
        "why": "这个库的读者是个人开发者。大客户销售、重资质、要团队的，"
               "可以留但不该按精写案例的方式写。",
    },
]

# ---------------------------------------------------------------------------
# 第 2 层：硬性必填。blocking=True 的那些全齐 + 必填字段非空，才允许发布
#
# blocking 字段是 2026-09-17 加的：把「人工核读」从拦发布降级成标记。
# 原来三项都拦，于是每条候选都卡在「还差 1 项」——而实测那一项勾完之后
# 案例上什么都不留（案例字段里既没有 human_read 也没有 musts），
# 等于每次提升都要求人点一下，换来的却是一份阅后即焚的承诺。
# 现在它不再拦发布，改成：不勾就挂一条 warnings，并把状态存到案例上，
# 「这条有没有人核过」从此可查 —— 承诺从一次性动作变成一条记录。
# ---------------------------------------------------------------------------
MUSTS = [
    {
        "key": "caliber_decided",
        "label": "已判定收入是六个口径里的哪一个",
        "why": "ARR / MRR / run-rate / 累计 / 流水 / 毛利，混一个就差十倍。",
        "blocking": True,
    },
    {
        "key": "caliber_consistent",
        "label": "已核对口径与数字一致（没把平台流水当收入、累计当年化）",
        "why": "库里的 Chatbase 就栽在累计收入被写成收入。",
        # 2026-09-17 用户定：改成标记，不拦发布。
        # 理由：AI 结构性地核不动它 —— 要判断「口径与数字一致」，得拿到支付侧
        # 页面（TrustMRR / Stripe），而那个页面经常抓不到或抓回 0 字，于是 AI
        # 一律答 False，每条候选都永久停在这一项上。它变成死胡同靠的是「拦」，
        # 不是「判断」：AI 的判断其实有价值（它真能看出两个不相等的月度值），
        # 所以留下的正确做法是**把判断和理由显出来**，而不是拿它当闸门。
        "blocking": False,
    },
    {
        "key": "human_read",
        "label": "我亲自看过原文（不是只看摘要或别人的转述）",
        "why": "这一条无法自动化。勾了它代表你为这条数字背书。",
        "blocking": False,
    },
]

# 非阻塞的必填项（目前只有人工核读标记）没勾时挂的提醒文案。
# 单独放一张表而不是塞进 MUSTS 里，是为了让 schema() 吐给前端的规则表保持干净。
PENDING_WARNINGS = {
    "human_read": {
        "label": "未经人工核读",
        "why": "「我亲自看过原文」还没人勾过。这一条不拦发布，但这条案例的数字"
               "没有人为它背书 —— 发布后这个状态会一路存到案例上。",
    },
    "caliber_consistent": {
        "label": "口径一致性未确认",
        "why": "没人确认过「这些数字的口径和它标的那个口径是一回事」。这一条不拦"
               "发布，但发布前请自己看一眼：库里的 Chatbase 就是累计收入被写成了"
               "收入，差一个量级。AI 的判断与理由存在草稿的 caliber_reason 里。",
    },
}

# 收入口径枚举 —— 与 scripts/verify.py 的 CALIBERS 保持一致
CALIBERS = [
    ("arr", "ARR", "年化经常性收入"),
    ("mrr", "MRR", "月度经常性收入"),
    ("run_rate", "run-rate", "把某月/某季乘 12 的估计值"),
    ("lifetime", "累计收入", "lifetime revenue，不是年收入"),
    ("gmv", "平台流水", "客户过手的钱，不是平台收入"),
    ("gross", "毛利/净利", "毛利不等于净利"),
]

CALIBER_KEYS = [k for k, _, _ in CALIBERS]

# 核实等级 —— 与 static/app.js 的 V_LABEL 保持一致
VERIFICATIONS = [
    ("stripe", "支付网关验证", "数字由支付数据自动验证，不接受截图"),
    ("official", "官方披露", "公司官方通稿或财报，署名担责"),
    ("partial", "口径待核", "有来源但口径或范围还没完全对清"),
    ("founder", "创始人自报", "创始人推文/帖子里说的，只能作方向参考"),
    ("disputed", "数字有出入", "多个来源互相矛盾，已在 corrections 记录"),
]

VERIFICATION_KEYS = [k for k, _, _ in VERIFICATIONS]

# 来源构成里哪些算「一手」。D 级（中文二手转述）单独列出来是为了让界面
# 提醒你：只有它的话，这条例子的数字不该被当成已核实。
SOURCE_TIERS = [
    ("stripe", "支付网关直连", "A", True),
    ("official", "公司官方通稿/财报", "A", True),
    ("press", "可信媒体独立报道", "B", False),
    # review = 第三方评测/核查站（SaaSXtra、Steal What Works、NeoDrop 这类）。
    # 它早就在用 —— ai_verify 的 kind 白名单里有、cases.json 里有十几条都标着它，
    # 唯独这张表没登记，于是「这个来源属于哪一档」一直没有被规则承认过。
    ("review", "第三方评测/核查站", "B", False),
    ("founder", "创始人自己的推文/帖子", "C", False),
    ("secondary", "中文二手转述（公众号/知识星球）", "D", False),
]
SOURCE_TIER_KEYS = [k for k, _, _, _ in SOURCE_TIERS]

# ---------------------------------------------------------------------------
# 等级与证据的对应关系：**声称的等级必须由来源撑住**
#
# 这个库卖的是「这些数字核过」。所以「核实等级」不是给人看的装饰 ——
# 标着「支付网关验证」，读者就会以为我们看到过 Stripe 后台。
# 而实际数据里一度有 11 条案例标着 stripe / official，登记的来源却只有
# press / review（第三方拆解站），一个支付或官方页面都没有。
# 那不是保守，那是虚标：它替读者完成了一次他们没授权我们做的信任背书。
#
# 做成纯函数是为了让它能被三处共用：定档、管理员发案例时的提醒、
# 以及 scripts/audit_evidence.py 的存量审计 —— 规则只写一遍。
# ---------------------------------------------------------------------------
# 等级强度。只用来比较「谁更强」，不参与打分
LEVEL_RANK = {
    "stripe": 4,
    "official": 3,
    "partial": 2,
    "founder": 1,
    "disputed": 0,
}

LEVEL_LABEL = {k: lab for k, lab, _ in VERIFICATIONS}
KIND_TIER = {k: tier for k, _lab, tier, _fh in SOURCE_TIERS}


def best_supported_level(kinds):
    """这组来源**最撑得起**哪个等级。撑不起任何等级时返回 None。

    判断顺序就是「证据强度」顺序：有支付网关就配得上 stripe，只有官方页面
    就退到 official，只有第三方报道就只能说「有来源但口径待核」，
    只剩创始人自述就是 founder 级。只有中文二手转述时返回 None ——
    那种来源连「口径待核」都不该给，它需要的是人工重核，不是换个标签。
    """
    ks = {str(x) for x in (kinds or [])}
    if "stripe" in ks:
        return "stripe"
    if "official" in ks:
        return "official"
    if ks & {"press", "review"}:
        return "partial"
    if "founder" in ks:
        return "founder"
    return None


def evidence_gap(verification, kinds):
    """声称的等级有没有来源撑住。返回 (建议等级 or None, 理由 or "")。

    三种结果：
      · (None, "")            —— 撑得住，不用改
      · (等级, 理由)          —— 虚标了，建议降到这个等级
      · (None, 理由非空)      —— 连最弱等级都撑不住，得人工重核，不能自动降
    """
    v = str(verification or "")
    if v not in LEVEL_RANK:
        return (None, "")
    top = best_supported_level(kinds)
    if top is None:
        return (None, "只有中文二手转述（或零来源），任何等级都不成立，需人工重核")
    if LEVEL_RANK[v] <= LEVEL_RANK[top]:
        return (None, "")
    kinds_txt = "、".join(sorted({str(x) for x in (kinds or [])}))
    return (top, "声称「%s」，但登记的来源只有 %s，够不到这个等级。"
                 "按证据把它降到「%s」。" % (LEVEL_LABEL[v], kinds_txt,
                                            LEVEL_LABEL[top]))

# ---------------------------------------------------------------------------
# 第 3 层：质量分。总分 100，达到 threshold 即「精品」
# ---------------------------------------------------------------------------
BONUS = [
    {
        "key": "founder_disclosure",
        "label": "创始人或公司官方公开披露过这个数字",
        "points": 20,
    },
    {
        "key": "pricing_confirmed",
        "label": "定价页可直接看到，客单价可判断",
        "points": 20,
    },
    {
        "key": "corrections_found",
        "label": "发现了别人的错误并已写进 corrections",
        "points": 15,
    },
    {
        "key": "replicable_low",
        "label": "复刻度门槛低（技术/获客/资金里没有硬门槛）",
        "points": 15,
    },
    {
        "key": "secondary_corroborated",
        "label": "除了来源，还有第三方独立数据交叉印证",
        "points": 15,
    },
    {
        "key": "solo_playbook",
        "label": "能写出至少三条「一个人怎么做」的具体动作",
        "points": 15,
    },
]

# 「精品」的质量分下限。低于它仍可入库，但进备选池。
TIER_THRESHOLD = 60

# 三道门槛全部成立的质量分加成（**自动推导，不能手勾**）。
# 为什么不放进 BONUS：BONUS 每一项都对应界面上的一个勾 —— 塞一个勾不动的项进去，
# 界面会多出一个永远勾不上的第七个勾选框。所以它单独算，
# 由 gates 推导：三道都确认成立，说明这条被核得最实诚，离精品就该更近。
GATE_ALL_BONUS = {
    "key": "gates_all_yes",
    "label": "三道门槛全部成立",
    "points": 10,
}

TIER_PREMIUM = "premium"
TIER_STANDARD = "standard"
TIER_BACKUP = "backup"
TIER_LABEL = {
    TIER_PREMIUM: "精品池",
    TIER_STANDARD: "实核池",
    TIER_BACKUP: "备选池",
}

# 三个架位，从可信到待核。前端按它分档渲染区块，后台按它出筛选项 ——
# 顺序写在这里，两边就不会各排各的。
TIER_ORDER = [TIER_PREMIUM, TIER_STANDARD, TIER_BACKUP]

# 每一档的展示文案。desc 是给读者看的一句话：这一档的数字能信到什么程度。
TIER_META = {
    TIER_PREMIUM: {
        "label": "精品池",
        "short": "精品",
        "desc": "有一手来源（支付网关直连 / 公司官方披露）撑住，数字可以直接引用。",
    },
    TIER_STANDARD: {
        "label": "实核池",
        "short": "实核",
        "desc": "有独立第三方来源（可信媒体 / 评测核查站）撑住了口径，但还没拿到一手证据 ——"
                "引用前建议点回原文自己再看一眼。",
    },
    TIER_BACKUP: {
        "label": "备选池",
        "short": "备选",
        "desc": "只有创始人自报，或来源互相矛盾 —— 只能作方向参考，不能当成已核实的数字用。",
    },
}

# ---------------------------------------------------------------------------
# 案例的默认定档政策（2026-09-20 放宽为三档）
#
# 档位表达的是「这条案例的数字能信到什么程度」。有两个信号都该影响它：
#   1. 来源有多硬 —— 一手（支付网关 / 官方）> 第三方（媒体 / 评测）
#   2. 核实做得多完整 —— 质量分
#
# 上一版只有两个架位，政策是「有一手来源 → 精品，否则 → 备选」。跑下来发现
# 17 条备选里**全部都有第三方来源**（press / review），只是拿不到一手证据。
# 把它们和「只有创始人自报」的案例塞进同一个「备选」，等于抹掉了
# 「有独立来源」和「没人核过」的区别 —— 读者看不出哪些其实已经有出处。
#
# 所以放宽成三档：
#   精品池 premium  —— 有一手来源（stripe / official），或质量分够线
#   实核池 standard —— verification 为 partial：有第三方来源撑住了口径
#   备选池 backup   —— 其余：只有创始人自报 / 来源矛盾 / 无来源
#
# 放宽的是「哪些算有出处」，不是「把什么都算精品」：一手证据仍然是进精品池的
# 唯一硬通道，第三方来源只够进实核池。质量分照旧算、照旧显示。
#
# 边界要说清楚：这**不改变** evaluate() 的评分逻辑 —— 那个 60 分线仍然管
# 「这次核实够不够格发布」。两者是不同的问题：一个判「能不能收」，
# 一个判「收进来之后摆哪儿」。
# ---------------------------------------------------------------------------
FIRST_HAND_KINDS = ("stripe", "official")


def first_hand_kinds(rec):
    """取一条记录里一手来源的 kind 集合。

    草稿既有 source_kinds（扁平 kind 列表）又有 sources（带 kind 的字典列表）；
    案例通常只有 sources。两条路都认，省得调用方还要知道数据从哪来。
    """
    out = set()
    for k in (rec.get("source_kinds") or []):
        if str(k) in FIRST_HAND_KINDS:
            out.add(str(k))
    for s in (rec.get("sources") or []):
        if isinstance(s, dict) and (s.get("kind") or "") in FIRST_HAND_KINDS:
            out.add(s["kind"])
    return out


def default_case_tier(rec):
    """给一个已发布案例定默认档位。返回 (tier, 理由)。

    顺序：一手来源 > 第三方来源（口径待核）> 质量分。理由写在上面那段注释里。
    """
    fh = first_hand_kinds(rec)
    if fh:
        return TIER_PREMIUM, "有一手来源（%s），按默认政策进精品池" % "/".join(sorted(fh))
    v = str(rec.get("verification") or "")
    if v == "partial":
        return TIER_STANDARD, "有独立第三方来源、口径待核，进实核池"
    score = rec.get("quality_score")
    if isinstance(score, int) and score >= TIER_THRESHOLD:
        return TIER_PREMIUM, "质量分 %d ≥ %d" % (score, TIER_THRESHOLD)
    if v == "founder":
        return TIER_BACKUP, "只有创始人自报，且没有一手来源"
    if v == "disputed":
        return TIER_BACKUP, "来源互相矛盾，且没有一手来源"
    return TIER_BACKUP, "没有一手来源，质量分也未达 %d" % TIER_THRESHOLD


def _as_set(seq):
    return {str(x) for x in (seq or [])}


def score(v):
    """按人工勾中质量分算分。返回 (得分, 命中项, 未命中项)。

    只算 BONUS 里那六项 —— 三道门槛的自动加成不在这里（见 evaluate 里的 gate_bonus），
    因为它不是勾出来的，是从 gates 推导出来的。
    """
    hits = _as_set(v.get("bonus") or [])
    got, hit_labels, miss = 0, [], []
    for b in BONUS:
        if b["key"] in hits:
            got += b["points"]
            hit_labels.append(b["label"])
        else:
            miss.append(b["key"])
    return got, hit_labels, miss


def evaluate(v, metric_value=None):
    """核心判定。v 是核实配置，返回一个可直接给前端渲染的结构化结果。

    参数 metric_value 可选：这条案例准备写进库的那个数字（用于口径一致性人工确认，
    本函数不做数字校验——那需要联网查原文，不在规则引擎职责内）。
    """
    v = v or {}
    gates = _as_set(v.get("gates"))
    denied = _as_set(v.get("gates_denied"))
    musts = _as_set(v.get("musts"))
    sources = _as_set(v.get("source_kinds"))
    bonus = _as_set(v.get("bonus") or [])

    # ---- 门槛（三态：yes / no / unknown）----
    # 「一道成立就进库」：只要有一道被确认成立，AI 找到的反证就不再一票否决，
    # 而是降级成「待人工复核」提醒 —— AI 的一次检索不该替人把候选判死，
    # 真人复核前，一道成立足以让这条留在库里。
    # 反过来：一道都没成立 + AI 拿到了明确反证 → 这条进库就是污染，拦下。
    gate_keys = {g["key"] for g in GATES}
    confirmed = bool(gates & gate_keys)
    denied_gates = [g for g in GATES if g["key"] in denied]
    blocking = bool(denied_gates) and not confirmed
    failed_gates = denied_gates if blocking else []
    unverified_gates = [g for g in GATES
                        if g["key"] not in gates and g["key"] not in denied]

    # ---- 硬性必填 ----
    # 必填分两档：blocking=True 的拦住发布；blocking=False 的（人工核读标记）
    # 只挂一条提醒，不拦。缺哪档就走哪档，同一个列表不分叉，免得两处口径漂移。
    warnings = []
    missing_all = [m for m in MUSTS if m["key"] not in musts]
    missing_musts = [m for m in missing_all if m.get("blocking", True)]
    for m in missing_all:
        if m.get("blocking", True):
            continue
        w = PENDING_WARNINGS.get(m["key"]) or {}
        warnings.append({
            "key": "pending_" + m["key"],
            "label": w.get("label") or ("尚未满足：%s" % m["label"]),
            "why": w.get("why") or m["why"],
        })

    # 被放行但 AI 拿到过反证的那些门槛：不拦，但必须在界面上明晃晃挂着
    flagged_denied = [] if blocking else denied_gates
    for g in flagged_denied:
        warnings.append({
            "key": "denied_" + g["key"],
            "label": "AI 找到反证：%s" % g["label"],
            "why": g["why"] + "（另有门槛成立所以放行，请你亲自确认这条反证属不属实。）",
        })

    if not v.get("verification") or v.get("verification") not in VERIFICATION_KEYS:
        missing_musts.append({
            "key": "verification",
            "label": "未选择核实等级",
            "why": "六个等级必须择一，它是这个库对外承诺的核心字段。",
        })
    if not v.get("caliber") or v.get("caliber") not in CALIBER_KEYS:
        missing_musts.append({
            "key": "caliber",
            "label": "未选择收入口径",
            "why": "ARR / MRR / 累计 / 流水……选错一个数字就差十倍。",
        })
    if not sources:
        missing_musts.append({
            "key": "source_kinds",
            "label": "未登记任何来源",
            "why": "每条案例都要能点回原文，否则读者无法自己复核。",
        })
    else:
        # 没有一手来源 / 只有中文二手转述：不再一票否决（用户明确要求放开），
        # 降级为「待人工复核」提醒 —— 由人决定是否采信，而不是让 AI 直接打死。
        primary = {k for k, _, _, is_p in SOURCE_TIERS if is_p}
        if not (sources & primary):
            warnings.append({
                "key": "primary_source_kind",
                "label": "来源里没有一手来源（只有媒体或二手转述）",
                "why": "本库发现过的所有数字错误，都出在中转述这一层。建议你亲自打开原文复核。",
            })
        if sources == {"secondary"}:
            warnings.append({
                "key": "secondary_only",
                "label": "来源只有中文二手转述",
                "why": "这正是库里两个真实错误共同的成因，不能当已核实发布。",
            })

    # ---- 质量分 ----
    got, hit_labels, _ = score(v)
    # 三道门槛全部被确认成立（且没有任何反证）→ 自动加成。
    # 这里的「全部成立」必须是净的：既可以说是 yes、又被记了反证的脏数据不算。
    all_gates_yes = all(g["key"] in gates and g["key"] not in denied for g in GATES)
    gate_bonus = GATE_ALL_BONUS["points"] if all_gates_yes else 0
    if gate_bonus:
        got += gate_bonus
        hit_labels = hit_labels + [GATE_ALL_BONUS["label"]]
    # 档位走和「案例摆哪儿」同一套政策（default_case_tier），否则后台预览说
    # 「备选」、实际发布却进了实核池，两边打架。质量分是本轮核实算出来的，
    # 充当政策的最后一条兜底。
    tier, _tier_why = default_case_tier({
        "source_kinds": sorted(sources),
        "verification": v.get("verification"),
        "quality_score": got,
    })

    # 待复核（warnings）不参与判定：它只是提醒，不是否决
    publishable = not failed_gates and not missing_musts
    # 门槛被明确否决时不谈档次：它是「不进库」
    if failed_gates:
        tier = None

    return {
        "ok": publishable,
        "publishable": publishable,
        "gates_failed": [
            {"key": g["key"], "label": g["label"], "why": g["why"]} for g in failed_gates
        ],
        # 所有 AI 拿到过明确反证的门槛（含已放行的那些），界面要能单独画出
        "denied_gates": [
            {"key": g["key"], "label": g["label"], "why": g["why"]} for g in denied_gates
        ],
        "unverified_gates": [
            {"key": g["key"], "label": g["label"], "why": g["why"]} for g in unverified_gates
        ],
        "missing": missing_musts,
        "missing_count": len(missing_musts),
        "warnings": warnings,
        "warning_count": len(warnings),
        # 人工核读标记的状态。server 提升案例时抄到案例上，让「这条有没有人核过」
        # 从此可查 —— 时间戳由 PUT /verification 在第一次勾上时写入，
        # 请求体里带什么都会被忽略（否则前端能自己伪造一个背书时间）。
        "human_read": "human_read" in musts,
        "human_read_at": str(v.get("human_read_at") or ""),
        "bonus_score": got,
        "bonus_max": sum(b["points"] for b in BONUS) + GATE_ALL_BONUS["points"],
        # 界面要单独点出这一笔：它不是人勾的，是三道门槛全成立自动给的
        "gate_bonus": gate_bonus,
        "gate_bonus_label": GATE_ALL_BONUS["label"] if gate_bonus else "",
        "bonus_hits": hit_labels,
        "threshold": TIER_THRESHOLD,
        "tier": tier,
        "tier_label": TIER_LABEL.get(tier, ""),
        "verdict": _verdict(publishable, failed_gates, missing_musts,
                            unverified_gates, warnings, tier, got, flagged_denied),
    }


def _verdict(publishable, failed_gates, missing_musts, unverified_gates, warnings,
             tier, got, flagged_denied=()):
    if failed_gates:
        return "门槛未过，不进库（%d 项被否决，且无一成立）" % len(failed_gates)
    if missing_musts:
        return "还差 %d 项必填" % len(missing_musts)
    # 档位名统一从 TIER_META 取 —— 三档都不会漏，加档也不必回来改这里
    short = (TIER_META.get(tier) or {}).get("short") or TIER_LABEL.get(tier) or "未定档"
    # 够发布了但有项目 AI 核不动 / AI 明确反证过 —— 写进判定，别让人以为已核实
    if unverified_gates or warnings:
        n = len(unverified_gates) + len(warnings)
        bad = "，含 %d 项 AI 反证" % len(flagged_denied) if flagged_denied else ""
        return "可发布 · %s（质量分 %d，%d 项待人工复核%s）" % (short, got, n, bad)
    return "可发布 · %s（质量分 %d）" % (short, got)


def blank():
    """一份空白的核实配置，给界面做初始值。"""
    return {
        "gates": [],
        "musts": [],
        "bonus": [],
        "caliber": "",
        "verification": "",
        "source_kinds": [],
        "note": "",
        "updated_at": "",
    }


def schema():
    """把规则本身吐给前端，界面按它渲染 —— 规则改了界面自动跟着变。"""
    return {
        "gates": GATES,
        "musts": MUSTS,
        "bonus": BONUS,
        "calibers": [{"key": k, "label": a, "desc": b} for k, a, b in CALIBERS],
        "verifications": [{"key": k, "label": a, "desc": b} for k, a, b in VERIFICATIONS],
        "source_tiers": [
            {"key": k, "label": a, "tier": t, "primary": p} for k, a, t, p in SOURCE_TIERS
        ],
        "threshold": TIER_THRESHOLD,
        "tier_labels": TIER_LABEL,
        # 三档的顺序与展示文案：前端分档渲染、后台出筛选项都按它来
        "tier_order": TIER_ORDER,
        "tier_meta": TIER_META,
        # 不是可勾项，只是告诉界面「还有这么一笔自动加成分存在」
        "gate_bonus": dict(GATE_ALL_BONUS),
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="核实规则自检 / 打印规则表")
    ap.add_argument("--schema", action="store_true", help="打印完整规则表（JSON）")
    ap.add_argument("--demo", action="store_true", help="跑几个判定样例")
    args = ap.parse_args()

    if args.schema:
        print(json.dumps(schema(), ensure_ascii=False, indent=2))
        raise SystemExit(0)

    # 默认跑自检：确认几组典型输入得到预期结论
    cases = [
        ("什么都没填",
         {},
         {"ok": False, "tier": TIER_BACKUP}),
        ("一道都没成立且有反证 → 不进库",
         {"gates": [], "gates_denied": ["still_alive"], "musts": [],
          "verification": "official", "caliber": "arr", "source_kinds": ["official"]},
         {"ok": False, "tier": None}),
        # 有一手来源（official）→ 直接进精品池，不再看质量分
        ("一道成立抵消反证 → 放行，且一手来源直接进精品",
         {"gates": ["solo_possible"], "gates_denied": ["still_alive"],
          "musts": [m["key"] for m in MUSTS], "verification": "official",
          "caliber": "arr", "source_kinds": ["official"]},
         {"ok": True, "tier": TIER_PREMIUM}),
        # 只有二手来源：已放开 —— 不再拦发布，只在 warnings 里提醒要亲自复核
        ("只有二手来源",
         {"gates": [g["key"] for g in GATES], "musts": [m["key"] for m in MUSTS],
          "verification": "partial", "caliber": "arr", "source_kinds": ["secondary"]},
         {"ok": True}),
        # 放宽后的中间档：有第三方来源、口径待核 → 实核池（上一版会被打成备选）
        ("只有第三方来源、口径待核 → 实核",
         {"gates": [g["key"] for g in GATES], "musts": [m["key"] for m in MUSTS],
          "verification": "partial", "caliber": "arr", "source_kinds": ["review"]},
         {"ok": True, "tier": TIER_STANDARD}),
        # 没有来源、只有创始人自报 → 备选（这才是真正意义上的「待核」）
        ("只有创始人自报 → 备选",
         {"gates": [g["key"] for g in GATES], "musts": [m["key"] for m in MUSTS],
          "verification": "founder", "caliber": "arr", "source_kinds": ["founder"]},
         {"ok": True, "tier": TIER_BACKUP}),
        ("齐全且高分 → 精品",
         {"gates": [g["key"] for g in GATES], "musts": [m["key"] for m in MUSTS],
          "verification": "stripe", "caliber": "mrr",
          "source_kinds": ["stripe", "official"],
          "bonus": [b["key"] for b in BONUS]},
         {"ok": True, "tier": TIER_PREMIUM}),
        # 三道全成立的加成能实打实改档次：没有一手来源时，靠质量分顶进精品
        ("三道全成立把 50 分顶过线",
         {"gates": [g["key"] for g in GATES], "musts": [m["key"] for m in MUSTS],
          "verification": "founder", "caliber": "arr", "source_kinds": ["founder"],
          "bonus": ["founder_disclosure", "corrections_found", "replicable_low"]},
         {"ok": True, "tier": TIER_PREMIUM}),
    ]

    bad = 0
    for name, inp, want in cases:
        r = evaluate(inp)
        for k, v in want.items():
            if r.get(k) != v:
                print("  [FAIL] %s → %s 期望 %r 实际 %r" % (name, k, v, r.get(k)))
                bad += 1
                break
        else:
            print("  [OK] %-22s → %s" % (name, r["verdict"]))
    print()
    print("自检：%d 项，失败 %d" % (len(cases), bad))
    raise SystemExit(1 if bad else 0)
