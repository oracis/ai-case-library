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
        "# 一个海外小产品怎么赚钱：%s" % name,
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
        "# %s 技术拆解：一个海外独立产品的工程与增长" % name,
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
        "# 云原生视角下的独立产品增长：%s" % name,
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
    return build_aliyun(c)


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
