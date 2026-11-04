#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 data/cases.json 生成各文本平台适配的草稿 markdown（不调 LLM，离线、数据驱动）。

用法：
    python -X utf8 scripts/gen_platform_md.py --ids chessigma,cod-dex \
        --platforms zhihu,cnblogs,csdn,aliyun,tencentcloud

落盘到 drafts/newbatch/<platform>/<slug>.md
只写缺的（除非 --overwrite）。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def load_cases():
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        return {c["id"]: c for c in json.load(f)}


def money(v):
    try:
        return "$%s" % format(int(round(float(v))), ",")
    except Exception:
        return str(v)


def metric_head(c):
    m = c.get("metrics") or {}
    head = m.get("headline") or ""
    return head


# 🚨 标题口径（2026-11-04 用户驳回：「太笼统，没有给出必要合理的信息」）
# 旧模板一律是「<名> 技术拆解：一个海外独立产品的工程与增长」，只有名字、没有事实。
# 现在的规则：**名字 + 它到底做什么（一句话第一分句）+ 一个可核验数字**。
# KaTeX 平台（CSDN/腾讯云/阿里云）标题里不能留 `$`，所以金额统一写成「18,380 美元」。

def _plain_money(s):
    """`$18,380`→`18,380 美元`、`$40K`→`4 万美元`、`$1M`→`100 万美元`（标题不留 `$`）。"""
    def rep_m(m):
        try:
            return "%g 万美元" % (float(m.group(1)) * 100)
        except Exception:                                   # noqa: BLE001
            return m.group(0)

    def rep_k(m):
        try:
            return "%g 万美元" % (float(m.group(1)) / 10.0)
        except Exception:                                   # noqa: BLE001
            return m.group(0)
    s = re.sub(r"\$([0-9][0-9.]*)\s*[Mm]\b", rep_m, s)
    s = re.sub(r"\$([0-9][0-9.]*)\s*[Kk]\b", rep_k, s)
    s = re.sub(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", r"\1 美元", s)
    return s


def _strip_meta(s):
    """去掉标题里不该出现的口径旁注：`，TrustMRR 实时核对`、`公开口径：`。"""
    s = re.sub(r"[，,]?\s*(TrustMRR\s*(实时)?核对)[^，,。]*$", "", s)
    s = re.sub(r"^\s*公开口径[:：]\s*", "", s)
    return s.strip()


def _cut(s, limit):
    """按标点截断，不留半个词组。"""
    s = s.strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    pos = max(head.rfind(x) for x in "，,、；;：:")
    if pos >= limit * 0.5:
        return head[:pos]
    return head.rstrip("，,、；;：: 的")


def _hook(c, limit=26):
    """一句话的第一分句：丢掉书名号/加粗，保留「具体做什么」。"""
    s = (c.get("one_liner") or c.get("what_it_does") or "").strip()
    s = re.split(r"[。！？]", s)[0]
    s = s.replace("「", "").replace("」", "").replace("**", "")
    if not s:
        s = (c.get("industry") or c.get("category") or "").strip()
    s = _strip_meta(_plain_money(s))
    return _cut(s, limit).rstrip("，,、 的")


def _proof(c, limit=30):
    """挑一个带数字的事实：优先增长曲线，其次当期收入、用户量。"""
    m = c.get("metrics") or {}
    for k in ("growth", "headline", "customers"):
        v = (m.get(k) or "").strip()
        if not re.search(r"[0-9]", v):
            continue
        v = re.split(r"[；;。]", v)[0].strip()
        if v:
            return _cut(_strip_meta(_plain_money(v)), limit).rstrip("，,、")
    return ""


def make_title(c, money=True):
    """平台标题统一口径：名字 + 它做什么 + 一个可核验数字。"""
    name = c.get("name") or c.get("id")
    hook = _hook(c)
    t = "%s：%s" % (name, hook)
    if money:
        pf = _proof(c)
        # 尾巴别重复（hook 里已经写了 MRR，就别再来一次 MRR …）
        if pf and pf not in t and not (hook.endswith("MRR") and pf.startswith("MRR")):
            cand = "%s，%s" % (t, pf)
            if len(cand) <= 62:
                t = cand
    return t[:100]


def webfix(s):
    # CSDN 会把段落内两个 $ 当行内公式，逐个 $ 转义成 \$
    return s.replace("$", "\\$")


def build_zhihu(c):
    name = c.get("name")
    one = c.get("one_liner") or ""
    verdict = c.get("verdict") or ""
    how = c.get("how_it_makes_money") or ""
    why = c.get("why_it_works") or []
    play = c.get("playbook") or []
    head = metric_head(c)
    lines = [
        "# %s" % make_title(c),
        "",
        "> 平台：zhihu ｜ 案例：%s（%s）｜ 状态：本地稿，未推送" % (name, c["id"]),
        "",
        "先说结论：**%s**" % (verdict or one),
        "",
        "公开口径：%s。" % head,
        "",
        "## 一、它到底解决了什么",
        "",
        (c.get("what_it_does") or one).strip(),
        "",
        "## 二、钱从哪来",
        "",
        how.strip() if isinstance(how, str) else "\n".join("- %s" % x for x in how),
        "",
        "## 三、为什么它能成",
        "",
    ]
    if isinstance(why, list):
        lines += ["- %s" % x for x in why]
    else:
        lines.append(str(why))
    lines += ["", "## 四、能抄的做法", ""]
    if isinstance(play, list):
        lines += ["- %s" % x for x in play]
    else:
        lines.append(str(play))
    lines += ["", "## 五、给想做类似产品的你", ""]
    lines.append("数据是实时核对过的（TrustMRR 直连支付网关），口径带修正，别只拿一个数字讲故事。"
                 "免费产品看转化漏斗，付费产品看留存和复购——两件事别混。")
    lines += ["", "来源：官网 + TrustMRR 支付验证页 + 公开访谈。"]
    return "\n".join(lines)


def build_cnblogs(c):
    name = c.get("name")
    one = c.get("one_liner") or ""
    lines = [
        "# %s" % make_title(c),
        "",
        "> 平台：cnblogs ｜ 案例：%s（%s）｜ 状态：本地稿，未推送" % (name, c["id"]),
        "",
        "## 这个产品是什么",
        "",
        (c.get("what_it_does") or one).strip(),
        "",
        "公开口径：%s。" % metric_head(c),
        "",
        "## 商业模型怎么搭",
        "",
        (c.get("how_it_makes_money") or "").strip() if isinstance(c.get("how_it_makes_money"), str)
        else "\n".join("- %s" % x for x in (c.get("how_it_makes_money") or [])),
        "",
        "## 工程上值得抄的点",
        "",
    ]
    play = c.get("playbook") or []
    lines += ["- %s" % x for x in play] if isinstance(play, list) else [str(play)]
    lines += ["", "## 为什么能跑起来", ""]
    why = c.get("why_it_works") or []
    lines += ["- %s" % x for x in why] if isinstance(why, list) else [str(why)]
    lines += ["", "一句话总结：**%s**" % (c.get("verdict") or ""),
              "", "数据来源：官网、TrustMRR 支付验证页、公开访谈，数字带口径带修正。"]
    return "\n".join(lines)


def build_csdn(c):
    return webfix(build_cnblogs(c))


def build_aliyun(c):
    name = c.get("name")
    one = c.get("one_liner") or ""
    lines = [
        "# %s" % make_title(c),
        "",
        "> 平台：aliyun ｜ 案例：%s（%s）｜ 状态：本地稿，未推送" % (name, c["id"]),
        "",
        "## 背景",
        "",
        (c.get("what_it_does") or one).strip(),
        "",
        "## 数据与口径",
        "",
        "公开口径：%s。" % metric_head(c),
        "",
        "## 可复制的工程实践",
        "",
    ]
    play = c.get("playbook") or []
    lines += ["- %s" % x for x in play] if isinstance(play, list) else [str(play)]
    lines += ["", "## 总结", "",
              (c.get("verdict") or ""),
              "", "数据来源：官网、TrustMRR 支付验证页、公开访谈。"]
    return "\n".join(lines)


def build_tencentcloud(c):
    # 腾讯云只收技术向：标题不许带金额（有驳回单），换成工程口吻
    body = build_aliyun(c)
    lines = body.split("\n")
    lines[0] = "# %s" % make_title(c, money=False)
    return "\n".join(lines)


BUILDERS = {
    "zhihu": build_zhihu,
    "cnblogs": build_cnblogs,
    "csdn": build_csdn,
    "aliyun": build_aliyun,
    "tencentcloud": build_tencentcloud,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--platforms", required=True)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    cases = load_cases()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    plats = [x.strip() for x in a.platforms.split(",") if x.strip()]
    made = 0
    for cid in ids:
        c = cases.get(cid)
        if not c:
            print("跳过未知 case:", cid)
            continue
        for plat in plats:
            b = BUILDERS.get(plat)
            if not b:
                print("未知平台:", plat)
                continue
            d = os.path.join(ROOT, "drafts", "newbatch", plat)
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, cid + ".md")
            if os.path.exists(fp) and not a.overwrite:
                print("已有跳过:", plat, cid)
                continue
            txt = b(c)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(txt)
            made += 1
            print("生成:", plat, cid, len(txt), "字")
    print("完成，新增", made, "篇")


if __name__ == "__main__":
    main()
