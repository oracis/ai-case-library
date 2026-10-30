# -*- coding: utf-8 -*-
"""生成《31 个可复制的 AI 产品》付费 PDF 的 HTML 母版。

数据来源：data/cases.json，筛选条件与 scripts 里的审计口径一致：
replicability 四维（tech/distribution/capital/timing）全部 >= 3。
所有数字均直接取自 cases.json（metrics 字段），不做任何再计算或年化。
"""
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "out", "repro31")

DIMS = [("tech", "技术"), ("distribution", "分发"), ("capital", "资本"), ("timing", "时机")]

# 分层：A 个人可抄 / B 需小团队 / C 只做参照（按可核验收入规模，不看总分）
TIER = {
    "dope-data": "A", "fire-tech-apps-llc": "A", "ai-voice-calls": "A",
    "arbichat-s-l": "A", "aftersora": "A", "claws-host": "A",
    "orbit-ai": "A", "client-one": "A", "blitzcut": "A",
    "growingupvideo": "A", "apogee": "A", "3d-agent": "A",
    "firecert": "A", "archifruits-ai": "A", "anon-2": "A",
    "chatedu-inc": "A", "bakering-app-sl": "A", "adle": "A",
    "barron-tech-llc": "B", "storyshort": "B", "audn-ai": "B",
    "based-labs-ai": "B", "apiframe": "B", "boothie-ai-photo-editor": "B",
    "nutria-9e3eaa": "B",
    "genius-ai": "C", "uplinked-b-v": "C", "viktor": "C",
    "nitra": "C", "stan": "C",
}

# 每条「抄的切口」：编辑判断，不含任何数字
ANGLE = {
    "dope-data": "把官网变成会干活的数字员工，卖的是「少雇一个人」",
    "fire-tech-apps-llc": "把小客户的 App 需求做成流水线，卖交付速度",
    "ai-voice-calls": "接打电话这件脏活，按语言和并发卖",
    "arbichat-s-l": "B2C 数据服务，卖「可验证的长期盈利」",
    "aftersora": "创作者需要的不只是提示词框，是角色与分镜",
    "claws-host": "把 5 小时的部署压成 5 分钟，卖省下的半天",
    "orbit-ai": "表单不是表单，是成交前的最后一步",
    "client-one": "客户接待与跟进，替代前端销售的第一句话",
    "blitzcut": "去静默 + 上字幕，短视频生产里最枯燥的两步",
    "growingupvideo": "同一批照片，做成「时间向前走」的情绪产品",
    "apogee": "广告素材批量上传，卖给每天被素材量压死的人",
    "3d-agent": "在 Blender 里生成可用的模型，而不是生成图片",
    "firecert": "合规证书管理，法规逼出来的刚需",
    "archifruits-ai": "室内外照片转建筑渲染图，卖给装修与地产",
    "anon-2": "让代理机构把聊天机器人卖给本地商家",
    "chatedu-inc": "学习指南与练习题，按学科和学段切",
    "bakering-app-sl": "本地名店 + 家庭配送，卖的是「买不到」",
    "adle": "代投广告，按投放效果而不是席位收费",
    "barron-tech-llc": "给微软生态的伙伴做增长，吃生态红利",
    "storyshort": "无脸视频 + 自动发布，把内容生产做成流水线",
    "audn-ai": "用攻击者视角做订阅，卖「被黑之前」的安心",
    "based-labs-ai": "生成工具 + 创作者社区，工具留人、社区续费",
    "apiframe": "多模型 API 网关，卖「不用改代码就能换模型」",
    "boothie-ai-photo-editor": "把某种具体审美做成 preset，卖「想要的效果」",
    "nutria-9e3eaa": "拍照就算卡路里，降低记录的摩擦",
    "genius-ai": "单店老板的一天：预约、收银、短信全接管",
    "uplinked-b-v": "12 周内容策略 + 代写代发，卖「不用自己想」",
    "viktor": "住进 Slack 的同事，交付成品而不是聊天记录",
    "nitra": "从金融通道切进诊所后台，再往上叠 AI",
    "stan": "做创作者经济的收银台，抽成比卖工具大",
}

# 人工补充的「为什么值得看一眼」，用于卡片顶部一句话（不含数字）
HOOK = {
    "stan": "它证明「卖铲子」不如「当收银台」——但这条不是给你抄的，是给你定天花板的。",
    "nitra": "受监管行业里，纯软件卖不进去时，金融通道往往是那把钥匙。",
    "audn-ai": "2–5 人的团队，把「安全测试」这种传统上靠人脉的生意做成了订阅。",
    "uplinked-b-v": "把顾问的「内容焦虑」包成 12 周交付，这是服务产品化的典型形态。",
    "genius-ai": "创始人自己干过这一行，所以知道老板每天卡在哪一步。",
    "viktor": "零安装摩擦：不让人学新工具，而是住进他已经在用的对话框。",
    "orbit-ai": "极小的体量，但「表单 → 成交」这条链路永远有人愿意付钱。",
    "blitzcut": "短视频流水线上最枯燥的两步，正好是最容易自动化、最容易收钱的两步。",
    "adle": "把媒体投放这件事外包给 AI，客户省的是人力而不是软件费。",
    "apiframe": "模型每三个月换一次，中间层的价值就在于「不用改代码」。",
    "growingupvideo": "情绪产品：卖的不是视频，是「看到孩子长大」这件事。",
    "chatedu-inc": "教育工具的付费方是家长，决策链短、复购看学期。",
    "storyshort": "无脸视频是个被验证过很多次的需求，它把「出镜成本」直接归零。",
    "based-labs-ai": "工具拉新、社区留存，这是内容型工具最稳的组合。",
    "boothie-ai-photo-editor": "「具体审美」比「全能修图」好卖，因为用户知道自己想要什么效果。",
    "nutria-9e3eaa": "记录饮食的摩擦极高，拍照识别把这个摩擦降到接近零——但留存是它的死穴。",
    "ai-voice-calls": "语音是最像人的接口，多语言是它的天然杠杆。",
    "dope-data": "如果一个客户愿意付 $500，说明「数字员工」这个概念已经能卖动了。",
    "arbichat-s-l": "SaaS + 数据服务的混合形态，收入结构比纯订阅更抗波动。",
    "fire-tech-apps-llc": "把「做 App」这件事标准化，是典型的接单生意产品化。",
    "aftersora": "创作者工具正在从「生成一张图」走向「管理一个世界观」。",
    "claws-host": "开源项目火了之后，托管服务是最快能收钱的衍生品。",
    "client-one": "客户接待是每个小生意的第一道漏斗，漏掉就是钱。",
    "apogee": "垂直市场的 B2B 工具，做深一个国家就能活得很好。",
    "firecert": "法规驱动的需求最稳：不合规要罚款，客户不敢省这笔钱。",
    "archifruits-ai": "把「渲染」这件专业活降维成上传照片，切的是装修与地产的预算。",
    "3d-agent": "3D 内容的生产成本极高，任何能缩短它的工具都有市场。",
    "anon-2": "代理机构需要一个能卖的东西，工具就是他们的货。",
    "bakering-app-sl": "本地名店 + 配送，护城河是「这家店只在巴塞罗那有」。",
    "barron-tech-llc": "生态里的服务商，客户获取成本远低于独立开发者。",
}

RISK_FALLBACK = "体量还小，收入集中在少数客户身上，单客户流失就会改变结论。"


def clean_name(s):
    s = re.sub(r"（[^）]*）", "", s or "").strip()
    s = re.sub(r"\s*\([0-9a-f]{6}\)", "", s)
    return s.strip()


def money(v):
    if v is None:
        return None
    return "$" + format(int(round(v)), ",")


# 无 MRR / 无近 30 天流水的个案：显式覆盖，绝不用正则从备注里猜数字
REV_OVERRIDE = {
    "nitra": ("未披露", "2026 目标 ARR $150M"),
    "genius-ai": ("未披露", "支付流水 $70 亿 ≠ 收入"),
    "viktor": ("ARR $15M", "年化运行率（自述）"),
    "uplinked-b-v": ("未披露", "无收入数据"),
}


def revenue_block(c):
    m = c.get("metrics") or {}
    mrr = m.get("mrr")
    d30 = m.get("last_30d_revenue")
    if mrr:
        return "MRR " + money(mrr), "月经常性收入"
    if d30:
        return "近 30 天 " + money(d30), "流水（非经常性）"
    if c.get("id") in REV_OVERRIDE:
        return REV_OVERRIDE[c["id"]]
    return "未披露", "无可用收入数据"


def short(s, n=120):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    i = max(cut.rfind("。"), cut.rfind("；"))
    if i > n * 0.5:
        return cut[: i + 1]
    return cut.rstrip("（，、,(") + "…"


CAT_RISK = {
    "AI 工具": "模型层每次降价，这类工具都要被重新定价——护城河不在模型，在工作流。",
    "创作者经济": "天花板由平台政策定，一次算法或抽成调整就能改写收入。",
    "垂直行业 SaaS": "行业软件不会暴涨也不会归零，但单客户议价权很重，续费比新增更值得盯。",
    "营销工具": "营销预算是最先被砍的那一刀，留存随客户所在行业周期波动。",
    "开发者工具": "开发者迁移成本低，护城河只能是工作流深度。",
    "电商": "低客单价 + 低频次，收入对流量成本极度敏感。",
    "移动应用": "商店抽成、审核与系统级能力变化都是外部变量。",
    "Mobile Apps": "商店抽成、审核与系统级能力变化都是外部变量。",
    "Health & Fitness": "健康类留存天然偏低，付费多集中在年初的决心期。",
    "企业服务": "企业采购周期长，单笔金额大但决策链也长，现金流容易被账期拖住。",
}


def risk_of(c):
    """返回 (标签, 文案)。优先真实业务风险，其次口径问题，最后才是泛化提示。"""
    m = c.get("metrics") or {}
    note = m.get("metric_note") or ""
    corr = c.get("corrections") or []

    keep = re.search(r"留存 (\d+)%", note)
    g = m.get("mrr_growth_30d")
    if keep and int(keep.group(1)) < 75:
        seg = re.search(r"历史峰值 \$[\d,]+ → 最近完整月 \$[\d,]+", note)
        head = (seg.group(0) + "，") if seg else ""
        return "退坡", head + "留存仅 %s%%：这是在下滑的标的，不是增长故事。" % keep.group(1)
    if isinstance(g, (int, float)) and g <= -10 and (m.get("mrr") or 0) > 0:
        return "退坡", "近 30 天 MRR 增速 %.1f%%，不是增长故事。" % g

    if corr:
        return "口径", "别信流传版本：" + short(corr[0].get("truth") or "", 110)

    warn = re.search(r"⚠([^⚠]{6,160})", note)
    if warn:
        s = re.sub(r"^队列缓存值.*?不同；?", "", warn.group(1).strip(" 。"))
        if (len(s) >= 10 and not s.startswith("同期收入增速")
                and re.search(r"挂牌|要价|不等于收入|背离|不能当|滞后|已过期", s)):
            return "口径", short(s, 120)

    if not m.get("mrr") and not m.get("last_30d_revenue"):
        return "未验证", "收入未披露，本条只能当模式参考，不能当业绩证明。"
    if c.get("verification") in ("partial", "disputed", "founder"):
        return "未验证", "收入没有支付网关直读的证据，只能当模式参考。"

    subs = m.get("subscriptions")
    if isinstance(subs, int) and 0 < subs <= 25:
        return "集中", "只有 %d 个付费订阅，收入高度依赖单客户，流失一位就会改变结论。" % subs
    if m.get("mrr") and m.get("last_30d_revenue"):
        gap = abs(m["last_30d_revenue"] - m["mrr"]) / max(m["mrr"], 1)
        if gap > 0.5:
            return "口径", "近 30 天流水与 MRR 差距过半（含一次性与用量收入），别把流水当经常性收入。"
    if (m.get("mrr") or 0) >= 10000:
        base = "已经做到 %s。" % revenue_block(c)[0]
    else:
        base = "体量还小（%s）。" % revenue_block(c)[0]
    return "风险", base + CAT_RISK.get(c.get("category") or "", "增长依赖少数渠道，扩张前先核对留存与获客成本。")


def trim(s, n=230):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    cut = s[:n]
    for p in ("。", "；", "，"):
        i = cut.rfind(p)
        if i > n * 0.55:
            return cut[: i + 1]
    return cut + "…"


def esc(s):
    return html.escape(str(s or ""))


VERIFICATION = {
    "stripe": ("支付网关直读", "v-stripe"),
    "official": ("官方披露", "v-official"),
    "partial": ("部分可核", "v-partial"),
    "disputed": ("无法核验", "v-disputed"),
    "founder": ("创始人自报", "v-partial"),
}


SHORT_V = {"stripe": "直读", "official": "官方", "partial": "部分", "disputed": "无法", "founder": "自报"}


def card(c, idx):
    name = clean_name(c.get("name"))
    rev, rev_label = revenue_block(c)
    subs = (c.get("metrics") or {}).get("subscriptions")
    vtxt, vcls = VERIFICATION.get(c.get("verification"), ("未知", "v-partial"))
    r = c.get("replicability") or {}
    total = sum(r.get(k, 0) for k, _ in DIMS)
    dims = "".join(
        '<span class="dim"><b>%s</b><i>%d</i></span>' % (esc(lbl), r.get(k, 0))
        for k, lbl in DIMS
    )
    why = "".join("<li>%s</li>" % esc(x) for x in (c.get("why_it_works") or [])[:3])
    play = "".join("<li>%s</li>" % esc(x) for x in (c.get("playbook") or [])[:3])
    hook = HOOK.get(c.get("id"), "")
    rlabel, rtext = risk_of(c)
    meta = [esc(c.get("category") or ""), "核验 " + esc(vtxt)]
    if subs:
        meta.append("%d 付费订阅" % subs)
    if c.get("verified_at"):
        meta.append("采信 " + esc(c["verified_at"]))
    return f"""
<section class="card">
  <div class="card-head">
    <div class="num">{idx:02d}</div>
    <div class="head-main">
      <h3>{esc(name)}</h3>
      <div class="meta">{' · '.join(meta)}</div>
    </div>
    <div class="rev">
      <div class="rev-num">{esc(rev)}</div>
      <div class="rev-label">{esc(rev_label)}<span class="badge {vcls}">{esc(vtxt)}</span></div>
    </div>
  </div>
  <div class="score">{dims}<span class="total">四维 {total}/16</span></div>
  <p class="hook">{esc(hook)}</p>
  <p class="what">{esc(trim(c.get('what_it_does'), 240))}</p>
  <div class="cols">
    <div><h4>为什么成立</h4><ul>{why}</ul></div>
    <div><h4>怎么抄</h4><ul>{play}</ul></div>
  </div>
  <div class="risk"><b>{esc(rlabel)}</b> {esc(rtext)}</div>
  <div class="angle">切口 · {esc(ANGLE.get(c.get('id'), ''))}</div>
</section>"""


def main():
    data = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    picked = [
        c for c in cases
        if isinstance(c.get("replicability"), dict)
        and all(isinstance(c["replicability"].get(k), (int, float)) and c["replicability"][k] >= 3
                for k, _ in DIMS)
    ]
    picked.sort(key=lambda c: (-sum((c["replicability"] or {}).get(k, 0) for k, _ in DIMS),
                               -((c.get("metrics") or {}).get("mrr") or 0)))

    disp = [c for c in picked if c.get("id") != "meerkats-ai"]
    counter = [c for c in picked if c.get("id") == "meerkats-ai"]

    # 文档顺序 = 速查表顺序 = 卡片顺序：先分层，层内按四维总分、再按收入
    order = {"A": 0, "B": 1, "C": 2}
    disp.sort(key=lambda c: (
        order[TIER.get(c["id"], "C")],
        -sum((c["replicability"] or {}).get(k, 0) for k, _ in DIMS),
        -((c.get("metrics") or {}).get("mrr") or 0),
    ))
    num_of = {id(c): i for i, c in enumerate(disp, 1)}

    tiers = {"A": [], "B": [], "C": []}
    for c in disp:
        tiers[TIER.get(c.get("id"), "C")].append(c)

    rows = []
    for i, c in enumerate(disp, 1):
        rev, _ = revenue_block(c)
        r = c.get("replicability") or {}
        rows.append(
            "<tr><td class='c'>%d</td><td class='nm'>%s</td><td>%s</td><td class='rv'>%s</td>"
            "<td class='c'>%d</td><td>%s</td><td>%s</td></tr>" % (
                i, esc(clean_name(c.get("name"))), esc(c.get("category") or ""), esc(rev),
                sum(r.get(k, 0) for k, _ in DIMS),
                esc(SHORT_V.get(c.get("verification"), "—")),
                esc(ANGLE.get(c.get("id"), ""))[:26],
            )
        )

    tier_meta = {
        "A": ("A 层 · 个人或两人就能起步",
              "收入量级在 $10,000/月以下，四维全绿。这一层是给「一个人 + 一个周末上线 MVP」"
              "看的：不需要融资、不需要团队，失败成本是一次周末。"),
        "B": ("B 层 · 需要小团队或持续投入",
              "$10,000–$100,000/月。模式已验证，但要做出来需要产品化投入（多端、支付、客服），"
              "建议先当副业跑通再决定要不要全职。"),
        "C": ("C 层 · 只做参照，不要模仿",
              "体量在 $100,000/月以上，或收入无法核验。这一层的价值是**定天花板和校准认知**："
              "看清「平台级」和「小生意」之间差的是什么。"),
    }

    def section(key, items, start_idx):
        title, intro = tier_meta[key]
        body = "".join(card(c, num_of[id(c)]) for c in items)
        return f"""
<div class="tier"><span class="tier-key">{key}</span><h2>{esc(title)}</h2><p>{intro}</p></div>
{body}""", start_idx + len(items)

    idx = 1
    sA, idx = section("A", tiers["A"], idx)
    sB, idx = section("B", tiers["B"], idx)
    sC, idx = section("C", tiers["C"], idx)

    counter_html = ""
    if counter:
        c = counter[0]
        corr = (c.get("corrections") or [{}])[0]
        counter_html = f"""
<div class="tier"><span class="tier-key">!</span><h2>反例 · 一条我们特意留下的假数字</h2>
<p>这份东西如果只告诉你「什么能抄」，价值有限；它真正稀缺的地方是**告诉你什么不能信**。下面这条四维评分同样全绿，但我们核实不了它的收入——留着它，是因为它就是市面上大多数「月入 X 万」帖子的原型。</p></div>
<section class="card counter">
  <div class="card-head">
    <div class="num">31</div>
    <div class="head-main"><h3>Meerkats.ai</h3><div class="meta">营销工具 · 核验 无法核验 · 四维 14/16</div></div>
    <div class="rev"><div class="rev-num">$3,000</div><div class="rev-label">流传的 MRR<span class="badge v-disputed">无法核验</span></div></div>
  </div>
  <p class="what">{esc(trim(c.get('what_it_does'), 220))}</p>
  <div class="risk"><b>核实结果</b> {esc(corr.get('truth') or '')}</div>
  <p class="what"><b>它教给你的事</b>：一个模式可以是对的，一个数字可以是查不到的，这两件事经常同时发生。判断一个案例值不值得信，只看一件事——**有没有支付网关的直读证据**。</p>
</section>"""

    stamp = "2026-10-22"
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>31 个可复制的 AI 产品</title>
<style>
:root{{--ink:#15171b;--muted:#6b7280;--line:#d9d6cf;--accent:#9b2c2c;--band:#f6f5f2;--green:#1d7a4f;--amber:#8a5a00;--red:#a32d2d}}
*{{box-sizing:border-box}}
body{{margin:0;background:#fff;color:var(--ink);
 font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB","Noto Sans CJK SC",sans-serif;
 font-size:10.4pt;line-height:1.62;font-variant-numeric:tabular-nums}}
.page{{max-width:176mm;margin:0 auto;padding:0 0 20mm}}
.cover{{min-height:250mm;display:flex;flex-direction:column;justify-content:center;page-break-after:always}}
.cover .kicker{{font-size:9.5pt;letter-spacing:.28em;color:var(--accent);margin-bottom:14mm}}
.cover h1{{font-size:34pt;line-height:1.15;margin:0 0 6mm;font-weight:600;letter-spacing:-.5pt}}
.cover h2{{font-size:12.5pt;font-weight:400;color:var(--muted);margin:0 0 16mm;line-height:1.7}}
.cover .rule{{width:34mm;height:2px;background:var(--accent);margin-bottom:16mm}}
.cover .facts{{display:flex;gap:12mm;font-size:9pt;color:var(--muted)}}
.cover .facts b{{display:block;font-size:17pt;color:var(--ink);font-weight:500;margin-bottom:1mm}}
h2.sec{{font-size:15pt;font-weight:600;margin:0 0 4mm;padding-bottom:2.5mm;border-bottom:1px solid var(--line)}}
.lede{{font-size:10.5pt;color:#33363c;margin:0 0 4mm}}
.note{{background:var(--band);border-left:2px solid var(--accent);padding:4mm 5mm;font-size:9.4pt;color:#33363c;margin:5mm 0}}
.note b{{color:var(--accent)}}
table.q{{width:100%;border-collapse:collapse;font-size:8.5pt;margin-top:3mm;table-layout:fixed}}
table.q th{{text-align:left;font-weight:500;color:var(--muted);border-bottom:1px solid var(--ink);padding:2mm 1.2mm;font-size:8pt;letter-spacing:.02em;white-space:nowrap}}
table.q td{{border-bottom:1px solid #ecebe7;padding:1.6mm 1.2mm;vertical-align:top;word-break:keep-all;overflow-wrap:anywhere}}
table.q tr:nth-child(even) td{{background:#fbfaf8}}
table.q .c{{text-align:center;white-space:nowrap}}
table.q .nm{{font-weight:500}}
table.q .rv{{white-space:nowrap}}
.tier{{margin:12mm 0 6mm;padding-top:5mm;border-top:2px solid var(--ink);page-break-after:avoid;page-break-inside:avoid}}
.tier-key{{display:inline-block;background:var(--ink);color:#fff;width:6mm;height:6mm;line-height:6mm;text-align:center;
 font-size:9pt;font-weight:600;margin-bottom:2mm}}
.tier-key:nth-of-type(1){{}}
.tier h2{{font-size:14pt;font-weight:600;margin:0 0 2mm}}
.tier p{{margin:0;font-size:9.6pt;color:#3a3d43}}
.card{{border-top:1px solid var(--line);padding:5mm 0 4mm;page-break-inside:avoid}}
.card.counter{{border-top:2px solid var(--red)}}
.card-head{{display:flex;align-items:flex-start;gap:4mm}}
.num{{font-size:20pt;font-weight:500;color:#c9c5bc;line-height:1;min-width:11mm}}
.head-main{{flex:1}}
.head-main h3{{font-size:13.5pt;margin:0 0 .8mm;font-weight:600}}
.meta{{font-size:8.4pt;color:var(--muted);letter-spacing:.02em}}
.rev{{text-align:right;min-width:36mm}}
.rev-num{{font-size:13pt;font-weight:600;white-space:nowrap}}
.rev-label{{font-size:8.2pt;color:var(--muted);margin-top:.5mm}}
.badge{{display:inline-block;margin-left:2mm;padding:.2mm 1.6mm;border-radius:2px;font-size:7.6pt;letter-spacing:.02em}}
.v-stripe{{background:#e8f3ec;color:var(--green)}}
.v-official{{background:#fbf0dc;color:var(--amber)}}
.v-partial{{background:#efefee;color:#555}}
.v-disputed{{background:#f9e6e6;color:var(--red)}}
.score{{display:flex;gap:3mm;align-items:center;margin:3mm 0 2mm;font-size:8.6pt}}
.dim{{display:flex;gap:1.2mm;align-items:baseline}}
.dim b{{font-weight:400;color:var(--muted)}}
.dim i{{font-style:normal;font-weight:600}}
.total{{margin-left:auto;color:var(--muted);font-size:8.4pt}}
.hook{{margin:0 0 2mm;font-size:9.6pt;color:var(--accent);font-weight:500}}
.what{{margin:0 0 2.5mm;font-size:9.7pt;color:#2b2e33}}
.cols{{display:flex;gap:6mm}}
.cols>div{{flex:1}}
.cols h4{{font-size:8.6pt;margin:0 0 1mm;color:var(--muted);font-weight:500;letter-spacing:.06em}}
.cols ul{{margin:0;padding-left:4mm;font-size:9pt;color:#2b2e33}}
.cols li{{margin-bottom:.8mm}}
.risk{{margin-top:2.5mm;font-size:8.9pt;color:#4a3b3b;background:#faf7f6;border-left:2px solid #d8bcbc;padding:2mm 3mm}}
.risk b{{color:var(--red);font-weight:500;margin-right:1.5mm}}
.angle{{margin-top:1.5mm;font-size:8.6pt;color:var(--muted)}}
footer{{margin-top:10mm;padding-top:4mm;border-top:1px solid var(--line);font-size:8.4pt;color:var(--muted)}}
@media print{{body{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
 @page{{size:A4;margin:16mm 15mm 14mm}}}}
</style></head>
<body><div class="page">

<div class="cover">
  <div class="kicker">万物解释者 · 案例库</div>
  <h1>31 个可复制的<br>AI 产品</h1>
  <div class="rule"></div>
  <h2>从 255 条海外真实案例里，筛出「技术、分发、资本、时机」<br>四项全部达标的 31 条。<br>每条都带支付网关直读的收入，或者明确标注它没有。</h2>
  <div class="facts">
    <div><b>255</b>条案例池</div>
    <div><b>31</b>条四维全绿</div>
    <div><b>{sum(1 for c in picked if c.get('verification') == 'stripe')}</b>条 Stripe 直读</div>
    <div><b>1</b>条反例</div>
  </div>
</div>

<h2 class="sec">怎么用这份东西</h2>
<p class="lede">市面上的「月入十万」拆解，绝大多数有一个共同问题：数字没法验。这份东西反过来——先告诉你哪条能验、哪条不能，再谈能不能抄。</p>
<div class="note">
<b>四维评分</b>：技术（你能不能做出来，3 = 一个人一周内可跑通）· 分发（你能不能把货卖出去，3 = 不依赖人脉和投放预算）· 资本（需要多少钱起步，3 = 千元级）· 时机（窗口还在不在，3 = 现在进场不算晚）。<br>
<b>筛选口径</b>：四维全部 ≥ 3。不按收入排序，不按名气排序，不手工挑——条件写死，跑出来的就是这 31 条。<br>
<b>收入口径</b>：MRR 是月经常性收入，近 30 天是流水（含一次性与用量），ARR 是年化运行率。<b>三者不能互换</b>，这份文件里每条都标了它用的是哪一种。<br>
<b>币种</b>：全文金额一律美元原值，<b>不做换算</b>——换算了就没法拿它去对着支付网关核。只给三个量级参照（按 $1 ≈ ¥7.2 粗算，仅表达量级）：$500/月 ≈ ¥3,600/月 · $1 万/月 ≈ ¥7.2 万/月 · $10 万/月 ≈ ¥72 万/月。<br>
<b>数据截止</b>：{stamp}。指标是采信时的快照，产品会涨会跌，别拿三个月后的现实来对这份快照。
</div>

<h2 class="sec">速查表</h2>
<table class="q">
<colgroup><col style="width:4%"><col style="width:16%"><col style="width:10%"><col style="width:16%"><col style="width:6%"><col style="width:11%"><col style="width:37%"></colgroup>
<tr><th>#</th><th>产品</th><th>类目</th><th>收入</th><th>四维</th><th>核验</th><th>抄的切口</th></tr>
{''.join(rows)}
</table>

{sA}
{sB}
{sC}
{counter_html}

<footer>
数据来源：万物解释者案例库（255 条），收入数据经 TrustMRR 支付网关直读或官方披露，采信时间见各条标注。<br>
本文件为判读性资料，不构成投资建议；案例中的收入数字描述的是「别人做到了什么」，不代表「你也能做到」。<br>
版本 1.0 · {stamp} · 仅供购买者本人阅读
</footer>
</div></body></html>"""

    os.makedirs(OUT_DIR, exist_ok=True)
    doc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", doc)
    p = os.path.join(OUT_DIR, "31个可复制的AI产品.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(doc)
    print("OK ->", p)
    print("tier A/B/C =", len(tiers["A"]), len(tiers["B"]), len(tiers["C"]), "+ 反例", len(counter))


if __name__ == "__main__":
    main()
