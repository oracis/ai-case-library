#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 AI 给全部案例改写小红书标题（落盘 data/xhs_title_overrides.json）。

为什么存在：make_xhs_title 的规则兜底遇到长 one_liner 会硬切成残句
（「ChatGP」「客服机」），或全体退化成「海外小生意」。规则负责下限，
这份覆盖表负责上限。

用法：
    python scripts/xhs_ai_titles.py            # 生成/更新覆盖表
    python scripts/xhs_ai_titles.py --force    # 已有的覆盖也重新生成
    python scripts/xhs_ai_titles.py --dry      # 只打印发给 AI 的提示词

做法：
  1. 每条案例只把【金额前缀 + 名称 + one_liner + headline】给 AI；
  2. AI 只产出「：」后面的业务短语（金额前缀本地拼装，保证口径不被改写）；
  3. 本地校验：≤TITLE_MAX、非通用词、不与别的标题撞车；不合格回退规则标题。
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A        # noqa: E402
import xhs_publish as X      # noqa: E402

OVERRIDES = os.path.join(ROOT, "data", "xhs_title_overrides.json")
GENERIC = ("海外小生意", "一人公司生意", "海外小项目", "拆解一个")

# 「产品名：」超过这么多字就**省略产品名**、只留中文短语。
# ⚠ 阈值怎么定的（2026-10-15 实测）：14 字时 GrowingUpVideo 只剩 5 字
#   预算，AI 必然超或压烂；降到 13 后 fastwrite.io（占 13、剩 7 字）
#   同样反复超 1 字。⇒ 13 字及以上一律省略产品名更干净。
LONG_NAME = 13

SYSTEM = """你是小红书运营编辑。案例库要批量产出笔记标题，规则引擎只能机械截断，
现在请你重写每条的「业务短语」部分。

规则：
1. 只输出 JSON：{"titles": {"<case_id>": "<业务短语>", ...}}，不要解释。
2. 业务短语 **≤9 个汉字**（给「月收$398K：」这类金额前缀留位置），也不要省略号。
   ⚠ 字数是硬要求，超 1 个字就会被机器拒收。宁可短，不要写满。
3. 必须说清「这是个什么生意」，用具体名词，禁止出现「海外小生意」「一人公司生意」
   这类空话，禁止残句（比如切一半的英文品牌名）。
4. 口语化、有画面感，像小红书标题的后半句。例：
   「帮电商品牌全网打假」「文档一键变客服机器人」「让品牌进 AI 答案」。
5. 英文产品名可以保留，但若占太多字就用中文说清它做什么。
6. 37 条要互相错开句式，别都长一个样。"""

# ⚠ --cn 模式专用：产出**整条标题**「产品名：中文短语」。
#   10-15 定：标题禁金额红线后，旧的「AI 只出短语 + 本地拼金额前缀」流程
#   会被 strip_title_money 连产品名一起剥掉（实测 `月收$13K：AI写作助手…`
#   → `AI写作助手，提升学术写作`，品牌名丢失）。overrides 分支是整条
#   返回的，所以直接让 AI 产完整形态。
SYSTEM_CN = """你是小红书运营编辑。案例库要批量产出**小红书笔记标题**的后半句。

规则：
1. 只输出 JSON：{"titles": {"<case_id>": "<中文短语>", ...}}，不要解释。
2. 你**只写冒号后面那段中文短语**，**不要写产品名、不要写冒号**！
   （产品名和冒号由程序统一拼在前面。）
3. 短语必须说清「这是个什么生意」，用具体名词、口语化、有画面感。
   好例：「帮电商品牌全网打假」「文档一键变客服机器人」「Shopify 广告追踪」
        「VPS 托管服务」「AI 短视频制作」
   ✗ 反例（禁）：「海外小生意」「一人公司生意」这类空话；
     「AI学术」这种 1-2 词标签堆砌（不够 4 个汉字，太干巴没信息）。
4. **必须是中文**：不要把英文句子搬进来（专有名词如 Shopify / KDP / VPS 保留即可）。
5. **绝对不许出现金额/收入数字**（`$`、`美元`、`万`、`K` 都要禁）。
   金额是正文讲的事，标题里出现就是违规。
6. 各条要错开句式，别都长一个样。

⚠⚠ **字数是硬约束，每条上限不同**：
清单每行格式为 `id | 产品名 | 中文业务说明 | 给你的短语最多M字`，
M 已经扣掉产品名和冒号占用的字数，**你只需写 ≤M 字的短语**。
⚠ 必须逐条按各自的 M 精确控制。**宁可短 2-3 字，也不要超**——
超 1 个字这条就被机器拒收。请自己数一遍。"""

USER_TMPL = """案例清单（id | 金额前缀 | 产品名 | 一句话业务 | 收入口径原文）：

%s

请给每条产出一个业务短语。金额前缀只是给你参考口径，不用输出它。"""

# ⚠ 2026-10-15：当 one_liner 是**英文**时（augora/podawaa/publbee/vid-ai
#   等 4 条），素材里没有中文，AI 会跟着写英文短语 ⇒ 小红书标题出英文。
#   所以定向改写时额外喂中文 what_it_does，并强制中文输出。
USER_TMPL_CN = """案例清单（id | 产品名 | 中文业务说明）：

%s

请给每条产出完整标题「产品名：中文业务短语」。产品名原样保留。"""

# 超长条目的定向压缩提示词：给出原答案 + 精确目标字数，只要 JSON。
RETRY_TMPL = """下面这些标题**超长了**，请压缩后重给。格式：
`id | 产品名 | 当前标题 | 字数要求`

%s

要求：
- **只输出 JSON**：{"titles": {"<id>": "<压缩后的完整标题>", ...}}
- 每条必须 **≤ 字数要求里给的数字**，这是硬指标，**请自己数一遍**。
- 保留产品名原样，只压缩冒号后的中文短语（删修饰词、砍从句）。
- ⚠⚠ **压缩的底线**：必须保住「产品名：中文短语」这个结构，
  且冒号后的中文**至少 4 个汉字、能读成短语**。
  只许删冗词，不许把短语砍成 1-2 个词的标签堆砌
  （反例：`fastwrite.io AI学术`、`Pet Plus Us AI宠物`——这种直接作废）。
- ⚠ 若按字数实在压不到合格短语，就**保留原样**别硬压，我会在本地处理。
- 仍然不许出现金额数字，不许用「海外小生意」这类空话。"""


def load_key():
    sec = {}
    p = os.path.join(ROOT, "data", "secrets.json")
    if os.path.exists(p):
        sec = json.load(open(p, encoding="utf-8"))
    A.configure(api_key=os.environ.get("CASE_LIB_AI_KEY") or sec.get("ai_key"),
                base=os.environ.get("CASE_LIB_AI_BASE") or sec.get("ai_base"),
                model=os.environ.get("CASE_LIB_AI_MODEL") or sec.get("ai_model"))


def lead_of(c):
    h = (c.get("metrics") or {}).get("headline") or ""
    amt = X.first_amount(h)
    if not amt:
        return None
    return ("%s成交" % amt) if "成交" in h else ("月收%s" % amt)


def build_listing(cases, old, cn=False, budget_map=None):
    """cn 模式：让 AI 产出**整条标题**（含产品名），而不是只出后半句短语。

    为什么改成整条（2026-10-15）：旧脚本让 AI 只出「业务短语」、
    本地用 `lead`（金额前缀）拼在前面。但 10-14 定的红线是「标题禁金额」，
    `make_xhs_title` 出口的 `strip_title_money` 会把金额前缀**连产品名
    一起剥掉** ⇒ 结果是 `AI写作助手，提升学术写作`（13 字、无品牌名）。
    而 `make_xhs_title` 的 overrides 分支是**整条返回**的，所以让 AI 直接
    产出「产品名：中文短语」完整形态，才既保留品牌又不带金额。

    ⚠ 清单每行带上**本条标题的字数上限**：AI 没法凭感觉估中文字符数，
    光在 system 里喊「≤20 字」它会按 20 写然后全部超（实测 5/12 超）。
    必须把精确预算喂给它，并额外告知「产品名：」已占几字。
    """
    rows = []
    for c in cases:
        if cn:
            wd = c.get("what_it_does") or ""
            if not isinstance(wd, str):
                wd = (wd[0] if isinstance(wd, list) and wd else "")
            name = (c.get("name") or "").strip()
            n = (budget_map or {}).get(c["id"], X.TITLE_MAX)
            tail = "（名字太长，这条只要短语）" if (
                not name or len(name) + 1 >= LONG_NAME) else ""
            rows.append("%s | %s | %s | 给你的短语最多%d字%s"
                        % (c["id"], name or "（无）",
                           wd.split("。")[0].strip(), n, tail))
        else:
            rows.append("%s | %s | %s | %s" %
                        (c["id"], lead_of(c) or "（无金额）",
                         c.get("name", ""), X.short_desc(c)))
    return (USER_TMPL_CN if cn else USER_TMPL) % "\n".join(rows)


def validate(cases, got, budget_map, cn=False):
    """只接受：长度达标、非通用、无省略号、不撞车。

    cn=True（整条标题模式）额外拦：金额数字（标题红线）、无中文、
    缺产品名。返回 (接受表, 拒绝原因)。
    """
    ok, bad, seen = {}, {}, {}
    for c in cases:
        cid = c["id"]
        t = (got.get(cid) or "").strip()
        if not t:
            bad[cid] = "AI 没给"
            continue
        t = re.sub(r"[…。！？]+$", "", t).strip().strip('"“”')
        if cn:
            # ⚠ cn 模式下 AI 只出**中文短语**，产品名和冒号由本地拼
            #   （assemble）。所以校短语本身，成品长度在后面校。
            # AI 有时会自己带上产品名/冒号 ⇒ 剥掉，交给本地统一拼。
            # ⚠ 剥完**所有**判据都要重新过一遍 —— 先判长度会误拒
            #   「产品名：短语」形态的好答案。
            if "：" in t:
                t = t.split("：", 1)[1].strip()
            if not re.search(r"[一-鿿]", t):
                bad[cid] = "无中文：%s" % t
                continue
            # ⚠ 可读性下限（2026-10-15）：多轮压缩会把短语压成标签堆砌
            #   ——实测出过 `AI学术`、`AI宠物`、`KDP工具`：只有 2 个汉字，
            #   读不成句。烂标题比超长更糟（超长会被拒，烂的会直接发出去）。
            if len(re.findall(r"[一-鿿]", t)) < 4:
                bad[cid] = "短语被压没（不足4个汉字）：%s" % t
                continue
            if any(g in t for g in GENERIC):
                bad[cid] = "通用词：%s" % t
                continue
            # ⚠ 标题红线：禁金额/收入数字（10-14 定，金额是正文的事）
            if re.search(r"[$￥€£]\s*\d", t) or \
               re.search(r"[0-9][0-9,\.]*\s*(美元|美金|刀|万|元)", t):
                bad[cid] = "含金额（标题红线）：%s" % t
                continue
            if len(t) > budget_map[cid]:
                bad[cid] = "超长(%d>%d)：%s" % (len(t), budget_map[cid], t)
                continue
        else:
            if len(t) > budget_map[cid]:
                bad[cid] = "超长(%d>%d)：%s" % (len(t), budget_map[cid], t)
                continue
            if len(t) < 4:
                bad[cid] = "太短：%s" % t
                continue
            if any(g in t for g in GENERIC):
                bad[cid] = "通用词：%s" % t
                continue
        key = t
        if key in seen:
            bad[cid] = "与 %s 撞车：%s" % (seen[key], t)
            continue
        seen[key] = cid
        ok[cid] = t
    return ok, bad


def assemble(c, phrase):
    """本地拼成品标题：产品名 + 冒号 + 中文短语。

    ⚠ 为什么要本地拼（2026-10-15）：AI 被要求产整条标题时**系统性省略
    产品名和冒号**（实测 10/12 条：5 条缺名、5 条无冒号），而它给的
    短语质量并不差 —— 说明这是格式遵循问题，不是内容能力问题。
    交给本地拼，格式 100% 可控，还省掉跟 AI 争字数的往返。
    ⚠ 长产品名（+冒号 ≥14 字，如 GrowingUpVideo）会吃掉几乎全部预算，
    这类直接省略产品名，用纯中文短语。
    """
    name = (c.get("name") or "").strip()
    if not name or len(name) + 1 >= LONG_NAME:
        return phrase                    # 长名/无名 ⇒ 纯短语
    return "%s：%s" % (name, phrase)


def map_by_order(got, cases):
    """AI 偶尔把 id 编成 `"1"/"2"/"3"` 而不是真实 case_id
    （实测智谱小批量时就这么干，2026-10-15）。

    ⚠ 按**清单出现顺序**映射回真实 id。只在该 id 不在 cases 里、
      且 key 是纯数字时启用 —— 真实 id 被误映射的风险为零。
    """
    if not isinstance(got, dict):
        return {}
    ids = [c["id"] for c in cases]
    out = {}
    for k, v in got.items():
        ks = str(k).strip()
        if ks in ids:
            out[ks] = v
            continue
        if ks.isdigit():
            i = int(ks) - 1
            if 0 <= i < len(ids):
                out[ids[i]] = v
    return out


def call_ai(system, prompt, model=None):
    """调 AI 拿 JSON，智谱失败自动切 DeepSeek。

    ⚠ 为什么要有兜底（2026-10-15）：智谱 `glm-4-flash` 有已知怪癖 ——
    `response_format=json_object` + 长生成会 **HTTP 500 或读超时**
    （小 JSON 正常）。标题改写正是长生成 ⇒ 默认端点抽风时不能直接失败，
    换付费但稳的 DeepSeek 兜住。
    """
    sysmsg = system
    models = [model] if model else [None, os.environ.get(
        "CASE_LIB_AI_FALLBACK_MODEL", "deepseek-chat")]
    last = None
    for m in models:
        if m:
            A.configure(model=m)
        try:
            raw = A.llm([{"role": "system", "content": sysmsg},
                         {"role": "user", "content": prompt}],
                        temperature=0.6)
            json.loads(raw)          # 先验能不能解析，不合法算这次失败
            print("  （端点 %s 成功）" % (m or A.AI_MODEL))
            return raw
        except Exception as e:                        # noqa: BLE001
            last = e
            print("  [!] 端点 %s 失败：%s" % (m or A.AI_MODEL, str(e)[:90]))
    raise SystemExit("所有端点都失败，最后一个错：%s" % last)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AI 重写小红书标题")
    ap.add_argument("--dry", action="store_true", help="只打印提示词")
    ap.add_argument("--force", action="store_true",
                    help="已有覆盖的案例也重新生成（默认跳过）")
    ap.add_argument("--model", help="临时换模型")
    ap.add_argument("--only", help="逗号分隔的 case id，只改这些（其余不动）")
    ap.add_argument("--cn", action="store_true",
                    help="强制中文短语：素材改喂中文 what_it_does，"
                         "并校验必须含中文、禁金额（one_liner 是英文时必用）")
    args = ap.parse_args(argv)

    load_key()
    cases = X.load_cases()
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        by = {c["id"]: c for c in cases}
        missing = want - set(by)
        if missing:
            print("找不到 case：%s" % sorted(missing))
            return 1
        # ⚠ --only 默认**无视**旧覆盖（用户点名要的就是重写它们），
        #   否则「已有覆盖就跳过」会把点名项全部过滤掉。
        cases = [by[i] for i in sorted(want)]
    elif not args.force:
        old = {}
        if os.path.exists(OVERRIDES):
            old = json.load(open(OVERRIDES, encoding="utf-8"))
        cases = [c for c in cases if c["id"] not in old]
    if not cases:
        print("全部案例都有覆盖了（--force 可重生成）。")
        return 0

    old_ov = {}
    if os.path.exists(OVERRIDES):
        old_ov = json.load(open(OVERRIDES, encoding="utf-8"))
    budget_map = {}
    for c in cases:
        if args.cn:
            # cn 模式：AI 只出**短语**，预算 = TITLE_MAX - 「产品名：」 - 余量。
            # ⚠ 留 3 字余量：实测 AI 会无脑写满再超，事后压缩反而压出
            #   「fastwrite.io AI学术」这种烂标题（结构全毁）。
            name = (c.get("name") or "").strip()
            head = len(name) + 1 if (name and len(name) + 1 < LONG_NAME) else 0
            # ⚠ 余量只留 1 字（实测 AI 会无脑写满再超，事后压缩反而压出
            #   「fastwrite.io AI学术」这种烂标题）；下限 8 字保证还能
            #   写出 ≥4 汉字的通顺短语（6 字预算只能挤出「AI学术」这种）。
            budget_map[c["id"]] = max(8, X.TITLE_MAX - head - 1)
        else:
            lead = lead_of(c)
            budget_map[c["id"]] = (X.TITLE_MAX - len(lead) - 1
                                   if lead else X.TITLE_MAX)

    prompt = build_listing(cases, old_ov, cn=args.cn, budget_map=budget_map)
    if args.dry:
        print("=== SYSTEM ===\n%s\n=== USER ===\n%s"
              % (SYSTEM_CN if args.cn else SYSTEM, prompt))
        return 0

    print("发 %d 条给 AI（首选 %s%s）..." % (len(cases), A.AI_MODEL,
                                           "，整条中文标题" if args.cn else ""))
    raw = call_ai(SYSTEM_CN if args.cn else SYSTEM, prompt, args.model)
    got = json.loads(raw).get("titles", {})
    # ⚠ AI 会把 id 编成 "1"/"2"/"3"（实测），按清单顺序映射回真实 id
    got_ord = map_by_order(got, cases)
    if got_ord != got:
        print("（AI 用了序号当 id，已按清单顺序映射回 %d 条）" % len(got_ord))
        got = got_ord
    ok, bad = validate(cases, got, budget_map, cn=args.cn)

    # ⚠ 2026-10-15：超长是**最常见**的拒绝原因（AI 估不准中文字数）。
    #   做**多轮**定向压缩：每轮把目标字数按超出量下调，对超长条目
    #   连同精确目标重新问一次，直到收下或轮数用尽。
    #   ⚠ 只给「超长」的条目重试；通用词/缺产品名/含金额重问也白问。
    # ⚠ 2026-10-15：超长是**最常见**的拒绝原因（AI 估不准中文字数）。
    #   做**多轮**定向压缩：每轮按超出量下调目标字数，只把**还没收下**
    #   的超长条目连同精确目标重新问一次。
    #   ⚠ 只重试「超长」：通用词/压没/含金额重问也白问（是内容问题不是长度）。
    #   ⚠ 已进 ok 的条目**永不再压** —— 实测压了会把
    #     `AI写作工具提升学术`(11字) 压成 `AI写作`(5字) 这种烂标签。
    pending = {cid for cid, why in bad.items()
               if why.startswith("超长") and cid not in ok}
    for rnd in range(1, 4):
        if not pending:
            break
        retry, budget = [], {}
        for cid in list(pending):
            c = next((c for c in cases if c["id"] == cid), None)
            cur = got.get(cid, "")
            if not c or not cur or cid in ok:
                pending.discard(cid)
                bad.pop(cid, None)
                continue
            over = len(cur) - budget_map[cid]
            target = max(8, budget_map[cid] - max(over, 1) - 1)
            budget[cid] = target
            retry.append(c)
            bad.pop(cid, None)             # 旧拒绝理由作废，等新判据
        if not retry:
            break
        print("\n第 %d 轮压缩重试 %d 条（给精确目标字数）：" % (rnd, len(retry)))
        rp = RETRY_TMPL % "\n".join(
            "%s | %s | %s | 必须≤%d字（现在%d字，请砍掉%d字以上）"
            % (c["id"], (c.get("name") or "").strip(), got.get(c["id"], ""),
               budget[c["id"]], len(got.get(c["id"], "")),
               len(got.get(c["id"], "")) - budget[c["id"]])
            for c in retry)
        try:
            got2 = json.loads(call_ai(SYSTEM_CN, rp, args.model)) \
                .get("titles", {})
            got2 = map_by_order(got2, retry)
            ok2, bad2 = validate(retry, got2, budget, cn=True)
            ok.update(ok2)
            for cid, why in bad2.items():
                bad.pop(cid, None)         # 旧理由作废
                bad[cid] = why
                if why.startswith("超长") and cid in budget:
                    got[cid] = got2.get(cid, "")      # 用新答案继续压
                    pending.add(cid)
                elif why.startswith("短语被压没") and cid in budget:
                    # ⚠ 压没了 = 长度换不来可读性，别再压了，落终局
                    bad[cid] = "压缩后不可读：%s" % got2.get(cid, "")
        except Exception as e:                          # noqa: BLE001
            print("  [!] 重试异常：%s" % str(e)[:90])
            break

    # cn 模式：本地拼「产品名：短语」，再校**成品**总长
    # （短语预算已含余量，这里是兜底断言，不是主判据）。
    final, over = {}, []
    for cid, phrase in ok.items():
        c = next((c for c in cases if c["id"] == cid), None)
        full = assemble(c, phrase) if c else phrase
        if len(full) > X.TITLE_MAX:
            over.append((cid, full, len(full)))
            continue
        final[cid] = full
    if over:
        print("\n⚠ 拼装后仍超 %d 字、不予采纳：%s"
              % (X.TITLE_MAX, [c for c, _f, _n in over]))
        for cid, full, n in over:
            bad[cid] = "拼装后超长(%d>%d)：%s" % (n, X.TITLE_MAX, full)
        ok = final
    else:
        ok = final

    merged = dict(old_ov)
    for cid, full in ok.items():
        if args.cn:
            merged[cid] = full      # 已是完整标题，make_xhs_title 整条返回
            continue
        c = next(c for c in cases if c["id"] == cid)
        lead = lead_of(c)
        # 旧模式：AI 只出短语，本地拼金额前缀（口径不被 AI 改写）
        merged[cid] = ("%s：%s" % (lead, full)) if lead else full
    with open(OVERRIDES, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)

    print("\n接受 %d 条，拒绝 %d 条 → data/xhs_title_overrides.json"
          % (len(ok), len(bad)))
    for cid, t in sorted(ok.items()):
        print("  ✓ %-22s %-24s (%d字)" % (cid, t, len(t)))
    for cid, why in sorted(bad.items()):
        print("  ✗ %-22s %s" % (cid, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
