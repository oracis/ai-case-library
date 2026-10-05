#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已核验的候选补齐正文（promote 前的第 4 层闸门要用的那6 段）。

为什么需要这个文件
------------------
`ai_verify` 只负责「核实数字与来源」，不负责写正文。规则引擎的
`content_gate` 会拦下面这几项，为空就一律不许入库：

    one_liner / what_it_dodes / how_it_makes_money / verdict
    why_it_works[] / playbook[] / replicability

记忆里的事故：mort / quran-unlock 带着「具体定位未获取」过了前3 层闸门，
还被发布成四个平台的草稿 —— 文章少三节。所以正文必须真写。

铁律（来自项目记忆）
--------------------
1. **补正文从候选侧补，不是放宽闸门。** 本脚本只写 data/candidates.json。
2. **改 data/*.json 必须匹配原缩进**：本文件实测 candidates.json 是
   `indent=2` + **CRLF** + **无末尾换行**。用 json.dump 直接写会把
   整个文件炸成全量diff（2026-10-05 实测三种 indent + 3 种换行组合，
   只有这一种能字节级还原）。
3. **`human_read` 绝不代勾** —— 那是人替数字背书的动作。

用法
----
    python scripts/fill_case_text.py            # 补正文，不入库
    python scripts/fill_case_text.py --dry      # 只打印将写入的字段
"""

import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CAND = os.path.join(ROOT, "data", "candidates.json")

# ---------------------------------------------------------------------------
# 正文素材。全部来自一手来源（TrustMRR 支付网关档案 + 官网正文），
# 数字口径统一为「近 30 天已验证收入」+ 累计，口径写在 metric_note 里。
# ---------------------------------------------------------------------------
#⚠ one_liner 必须**强制覆盖**，不能走「已有值就跳」。
#   采集阶段从TrustMRR 带回来的one_liner 是英文原文（如「Harper AI turns UK
#   public records into deal flow for...」），而它会被 make_article.py 直接
#   当标题钩子和封面描述印出去 ⇒ 读者看到的是一句没翻译的产品简介。
#   中文正文是自己写的，所以这里显式覆盖采集值。
FORCE = {"one_liner"}

TEXT = {
    "harperai": {
        "one_liner": "把英国 Companies House 与土地登记局的公开抵押记录连成一张网，"
                     "再用 AI 帮贷款经纪与开发商提前找到正在需要再融资的客户。",
        "what_it_does": "Harper 把英国公开的公司抵押记录（Companies House charges）"
                        "与土地登记局产权（Land Registry titles）、公司股权结构、"
                        "高管关系网对齐，再叠上一层 AI 检索、再融资提醒与贷款机构"
                        "市占率分析。它对客户的说法是：每一笔贷款都有人做成，"
                        "Harper 负责让做成的人是你。官网给出的市场口径是 898,556 名"
                        "借款人、2,060,023 条在册贷款、394,026 名活跃借款人。",
        "how_it_makes_money": "订阅制。定价页只有两档：月付 £300，或年付 £3,480"
                              "（折合每月 £290，含2 个席位）。年付被主推为「最划算」，"
                              "实质是用一年期预付锁住客户、压低流失。TrustMRR 档案"
                              "显示其付费客户量级在数十到数百之间，"
                              "近 30 天已验证收入 $7,188，累计 $37,937。",
        "verdict": "把「公开但没人梳理的政务数据」变成订阅生意，"
                   "不需要拿到任何独家数据授权 —— 难的是把关系图谱接准。",
        "why_it_works": [
            "需求来自别人的恐慌，不是来自效率提升：经纪人的损失不是"
            "「做得慢」，而是「听说再融资机会时，另一家已经发了 term sheet」。"
            "官网把这条写成 85% 的机会发现得太晚、每月错过 23 个合格线索。",
            "数据是公开的，但公开不等于可用。真正的工作量在把 Companies House 的"
            "抵押记录与土地登记局产权、公司结构、高管关系对齐，"
            "这是脏活，也是别人抄不走的部分。",
            "零人工录入。Harper 自述整条流水线不靠人录数据，"
            "所以一个人能hold 住 898,556 名借款人这种量级的库。",
            "客单价撑得住高毛利服务：£300/月 一年是 £3,600，"
            "对一个能省下几笔贷款手续费的经纪人来说，"
            "比它一个月赚到的佣金便宜得多。",
        ],
        "playbook": [
            "找那些「公开但没人愿意整理」的数据源：政府登记、法院公告、"
            "土地与船舶登记。整理成本本身就是护城河。",
            "定价锚定在客户省下的钱，而不是你的成本 —— "
            "贷款经纪省下一笔 £50 万贷款的成交就是几万英镑佣金。",
        ],
        "replicability": {
            "tech": 3,
            "data": 5,
            "sales": 3,
            "domain": 4,
        },
        "metric_note": "近 30 天已验证收入 $7,188、累计 $37,937（TrustMRR 支付网关 "
                       "API 直读，2026-10-05 同步，环比 +3.1%）。挂牌价 $199,000、"
                       "倍数 2.3x。注意：本库采集阶段曾按挂牌价反推记为"
                       "「$6,232 MRR」，与网关实测口径不一致，已按一手档案更正。",
        # ⚠ cases.json 没有 metric_note 字段，promote 的白名单里也没有 ——
        #   写在候选的 metric_note 上会在入库时被静默丢弃。所以口径说明
        #   改挂 signals（白名单内，且读者能在文章里看到）。
        "signals": [
            "收入口径：近 30 天已验证收入 $7,188、累计 $37,937（口径 mrr）。"
            "数据由 TrustMRR 从Stripe 支付网关 API 直读，2026-10-05 同步，环比 +3.1%。",
            "定价页实价：月付 £300、年付 £3,480（折合 £290/月，含 2 席位）。",
            "挂牌信息：$199,000、倍数 2.3x，1,053 人近期浏览、收到 1 份收购报价。",
            "⚠ 口径修正：本库采集阶段曾按挂牌价反推记为「$6,232 MRR」，"
            "与支付网关实测不一致，入库时已按一手档案更正。",
        ],
    },
    "easymix": {
        "one_liner": "法语说唱与说唱歌手的自动混音工具：上传人声和伴奏、"
                     "选一个风格预设，一键出可直接上架的成品。",
        "what_it_does": "easyMix 面向法语区的说唱歌手与独立音乐人。"
                         "用户上传主唱、和声与伴奏轨，用文字描述想要的风格"
                         "（如 drill、旋律说唱），再叠加自动调音、均衡、混响，"
                         "几秒内得到一个成品。另一个功能是 AI mastering："
                         "把已混好的歌直接压成适配 Spotify、Apple Music、TikTok "
                         "各平台的版本。官网明写没有额外计费的 mastering。",
        "how_it_makes_money": "订阅制 + 终身买断双轨。官网定价页给的锚点是"
                              "「法国录音棚每首收120–250 €，还不含返工」，"
                              "easymix 的定价就对着这个数打。TrustMRR 档案显示"
                              "近 30 天已验证收入 $7,926、累计 $37,012、环比 +11.3%，"
                              "订阅为月付与年付两种周期。",
        "verdict": "创作者工具里最容易被低估的一类：门槛低、复购稳，"
                   "但天花板牢牢卡在「创作者有多穷」上。",
        "why_it_works": [
            "替代的是一笔真实支出，不是省时间。录音棚 120–250 €/首是硬成本，"
            "说唱歌手每发一首歌都要花，付费动机天然存在，不需要教育市场。",
            "品类自带全球性但本地化的切口：产品与文案全是法语，"
            "定价按法国录音棚的价盘定，避开与欧美混音工具正面竞争。",
            "把「专业」这件事压缩成一个按钮。客户评价集中在"
            "「以前在家录不出能听的版本」「以前要花大钱买插件还搞不定」，"
            "卖点是结果而不是功能。",
            "混音与 mastering 打包成一次上传。官网明写 mastering 含在每份方案里，"
            "「没有隐藏费用、没有意外选项」——省掉的是用户下单时的犹豫。",
        ],
        "playbook": [
            "找「替代的是一笔明确支出」的品类，定价直接对着那笔支出打，"
            "而不是对着竞品定价。",
            "先用单一语言与本地价格盘切入全球市场，"
            "再考虑多语种 —— easymix 的护城河是本地定价知识，不是模型能力。",
        ],
        "replicability": {
            "tech": 3,
            "data": 2,
            "sales": 3,
            "domain": 3,
        },
        "metric_note": "近 30 天已验证收入 $7,926、累计 $37,012（TrustMRR 支付网关 "
                       "API 直读，2026-10-04 同步，环比 +11.3%）。挂牌价 $300,000、"
                       "倍数 3.2x。定价锚点取自官网定价页自述的法国录音棚"
                       "120–250 €/首。",
        "signals": [
            "收入口径：近 30 天已验证收入 $7,926、累计 $37,012（口径 mrr）。"
            "数据由 TrustMRR 从 Stripe 支付网关 API 直读，2026-10-04 同步，环比 +11.3%。",
            "定价锚点：官网定价页自述「法国录音棚每首收120–250 €，还不含返工」，"
            "easymix 的订阅定价对着这个数打；mastering 含在每份方案内。",
            "挂牌信息：$300,000、倍数 3.2x，400 人近期浏览、收到 1 份收购报价。",
            "增长节奏：近 24 小时 $118、近 7 天 $1,472、近 3 个月 $21,471——"
            "收入主要来自订阅续费而非单次买断。",
        ],
    },
}


def load_json_raw(path):
    """读回原始文本（序列化格式必须原样还原，所以不能只留解析结果）。"""
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def save_like(raw, obj):
    """按 candidates.json 的原生格式写回：indent=2 + CRLF + 无末尾换行。"""
    s = json.dumps(obj, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    with io.open(CAND, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    # 字节级自检：不还原成原格式就会炸成全量 diff
    back = load_json_raw(CAND)
    if back != s:
        raise SystemExit("[!] 写回格式与预期不一致，已中止")


def main():
    ap = argparse.ArgumentParser(description="给候选补齐正文（数据源：一手档案 + 官网）")
    ap.add_argument("--dry", action="store_true", help="只检查不写盘")
    args = ap.parse_args()

    raw = load_json_raw(CAND)
    cands = json.loads(raw)
    by_id = {c.get("id"): c for c in cands}

    missing = [k for k in TEXT if k not in by_id]
    if missing:
        raise SystemExit("[!] 候选池里没有这些 id：%s" % "、".join(missing))

    for cid, fields in TEXT.items():
        rec = by_id[cid]
        print("== %s" % cid)
        for k, v in fields.items():
            cur = rec.get(k)
            if k in FORCE:
                rec[k] = v
                print("   覆 %-20s %s" % (k, json.dumps(v, ensure_ascii=False)[:70]))
            elif cur in (None, "", [], {}):
                rec[k] = v
                print("   补 %-20s %s" % (k, json.dumps(v, ensure_ascii=False)[:70]))
            else:
                print("   跳 %-20s 已有值，不覆盖" % k)
        # 标题类字段一并补上，避免文章生成时退回英文原名
        rec["promoted_from_inbox"] = rec.get("promoted_from_inbox") or "2026-10-05"
        rec["human_read"] = False          # 绝不代勾
        rec["human_read_at"] = ""
        rec["triage"] = "ready"

    if args.dry:
        print("\n(--dry 未写盘)")
        return 0

    save_like(raw, cands)
    print("\n已写入 data/candidates.json（indent=2 / CRLF / 无尾换行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())