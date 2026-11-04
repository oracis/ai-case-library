#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 发布前合规闸：把《CSDN 社区内容创作规范》里「审核将不予通过」的条目做成机器可查的词表。

依据（2026-11-04 通读，原文 https://blog.csdn.net/blogdevteam/article/details/113122012）：
  一.4 营销/推广引流 —— 营销链接 / 引导交易 / 纯广告（以推广产品、盈利为目的）
  一.7.1 标题夸张（震惊/重磅/99%/世界之最…）、悬念（竟然是…/……）、强迫（不看后悔…）
  一.7.2 题文不符、封面与标题不符
  一.7.3 标题格式：错别字、纯数字/过短、标点不规范
  一.3 流量作弊：批量发布重复、无意义的低质内容
  一.10 风险投资：网赚、荐股、诱导投资

⚠ 我们的 AI 案例稿天然踩两条：①通篇 MRR/定价/变现 = 网赚口吻（一.4 纯广告 + 一.10 诱导投资）；
  ②五平台同结构模板 = 批量低质（一.3）。所以闸不是「劝退」，是逼出改法：
  金额只留一句背景、去掉验证平台与官网引流、标题给事实不给惊悚。

用法：
    python -X utf8 scripts/csdn_rules.py drafts/newbatch/csdn/chessigma.md
    python -X utf8 scripts/csdn_rules.py --file a.md --strict      # 有 warn 也失败
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 规则表
# (规则名, 正则, 规范条目) —— 命中即 **block**（会大概率驳回）
TITLE_BLOCK = [
    ("标题夸张", r"震惊|惊爆|传疯|吓掉|重磅|要命|就在刚刚|全世界|所有男人|99\s*%|"
                 r"世界之最|最高级|最佳|最烂|绝对|必看|必藏|疯传", "一.7.1.1"),
    ("标题悬念", r"竟然是|竟是这样|结果却|没想到|内幕|揭秘|真相|……|\.\.\.", "一.7.1.2"),
    ("标题强迫", r"胆小慎入|不看后悔|别怪我没提醒|不得不看|一定要看完|绝对要收藏", "一.7.1.3"),
    ("标题带金额", r"[$¥€£]\s*\d|\d[\d,.]*\s*(万美元|万美金|美金|美元|万元|MRR|ARR)", "一.10 风险投资"),
    ("标题空壳", r"^(?:.{2,20})?(技术拆解|深度解析|全解析|一文读懂)$", "一.7.3.4 信息表达不全"),
]

BODY_BLOCK = [
    ("站外联系方式", r"微信|weixin|wechat|\bvx\b|v信|公众号|QQ\s*群|企鹅群|钉钉|telegram|电报|"
                     r"加我|私聊|私信我|扫码|二维码|进群|加群", "一.4 营销/推广引流"),
    ("交易引导", r"领红包|免费领取|资料包|优惠券|限时特惠|折扣|下单|购买链接|付费咨询|"
                 r"代做|接单|报价|开通会员|充值", "一.4 营销/推广引流"),
    ("网赚口吻", r"月入|躺赚|稳赚|暴富|被动收入|睡后收入|搞钱|网赚|撸羊毛|回本|副业变现", "一.10 风险投资"),
    ("第三方引流", r"trustmrr\.com|官网\s*https?://|点击.{0,6}(链接|官网)|前往.{0,6}官网", "一.4 营销链接"),
]

# 命中 **warn**（不一定驳回，但累计多了会被判低质）
TITLE_WARN = [
    ("标题过短", r"^.{0,11}$", "一.7.3.4"),
    ("标题标点堆叠", r"[，,、：:；;]{2,}|[！!？?。.]$", "一.7.3.5"),
    ("标题纯数字/符号", r"^[\d\W]+$", "一.7.3.4"),
]
BODY_WARN = [
    ("金额密度", r"[$¥€£]\s*\d|\d[\d,.]*\s*(美元|万|亿|MRR|ARR|收入|营收)", "一.4 纯广告"),
    ("管线水印", r"^>\s*平台[:：]|状态[:：]\s*本地稿|未推送", "一.3 批量低质"),
    ("模板化收尾", r"数据来源[:：]\s*官网、TrustMRR|数字带口径带修正", "一.3 批量低质"),
]

# 阈值：允许的密度上限（超过就是「通篇讲钱」）
MAX_MONEY_HITS = 3
MAX_ADS_TAIL = 6          # 「收入/变现/订阅/定价」类词上限


def _hits(patterns, text):
    out = []
    for name, pat, rule in patterns:
        m = re.findall(pat, text, re.I | re.M)
        if m:
            sample = m[0] if isinstance(m[0], str) else (m[0][0] if m[0] else "")
            out.append({"rule": name, "spec": rule, "count": len(m),
                        "sample": (sample or "")[:24]})
    return out


def scan(title, body):
    """返回 {block:[...], warn:[...]}。block 非空 ⇒ 不许发。"""
    title = (title or "").strip()
    body = body or ""
    block = _hits(TITLE_BLOCK, title) + _hits(BODY_BLOCK, body)
    warn = _hits(TITLE_WARN, title) + _hits(BODY_WARN, body)

    money = len(re.findall(r"[$¥€£]\s*\d|\d[\d,.]*\s*(?:美元|万|亿|MRR|ARR)", body))
    if money > MAX_MONEY_HITS:
        block.append({"rule": "金额密度超限", "spec": "一.4 纯广告",
                      "count": money, "sample": "%d 处 > 上限 %d" % (money, MAX_MONEY_HITS)})

    ads = len(re.findall(r"收入|营收|变现|订阅|定价|付费|转化率|客单价", body))
    if ads > MAX_ADS_TAIL:
        block.append({"rule": "商业化叙述过重", "spec": "一.4 纯广告",
                      "count": ads, "sample": "%d 处 > 上限 %d" % (ads, MAX_ADS_TAIL)})
    return {"block": block, "warn": warn}


def clean(body):
    """发布前的确定性清洗：剥管线水印、抹验证平台、把口径旁注去掉。

    ⚠ 只做「删掉一定不该出现的东西」，不改写内容（改写走 LLM 管线）。
    """
    keep = []
    for ln in (body or "").split("\n"):
        if ln.startswith("> 平台："):
            continue                                   # 内部管线水印
        ln = re.sub(r"[，,]\s*TrustMRR\s*(实时)?核对[。.]?", "", ln)
        ln = re.sub(r"^公开口径[:：]\s*", "", ln)
        ln = ln.replace("TrustMRR 支付验证页", "公开资料")
        keep.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def read_md(path):
    """取 (title, body)：首行 `# 标题`，正文不含标题行。"""
    with open(path, encoding="utf-8") as f:
        md = f.read()
    lines = md.split("\n")
    title = ""
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        lines = lines[1:]
    return title, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="CSDN 发布前合规检查")
    ap.add_argument("file", help="markdown 稿件")
    ap.add_argument("--strict", action="store_true", help="有 warn 也算失败")
    a = ap.parse_args()
    title, body = read_md(a.file)
    r = scan(title, body)
    print("标题：%s" % title)
    print("正文：%d 字" % len(body))
    if not r["block"] and not r["warn"]:
        print("✓ 未命中任何违规词条")
        return 0
    for tag, items in (("✗ 会驳回", r["block"]), ("⚠ 低质风险", r["warn"])):
        for it in items:
            print("%s %s（规范 %s，%d 处，样例「%s」）"
                  % (tag, it["rule"], it["spec"], it["count"], it["sample"]))
    if r["block"]:
        print("\n→ 结论：不许发。改法见 .workbuddy/memory/PLAYBOOK-platforms.md「CSDN 合规」")
        return 1
    return 1 if a.strict else 0


if __name__ == "__main__":
    sys.exit(main())
