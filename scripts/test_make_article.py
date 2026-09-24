# -*- coding: utf-8 -*-
"""make_article.py 的测试。

重点盯三件事：
  1. 人工表（EDITORIAL_WEIGHT / TITLE_OVERRIDES）里的 id 必须真实存在 ——
     写错一个 id 会静默失效，排名悄悄跑偏，最难查
  2. 正文里绝不能出现裸 URL —— 公众号正文放外链有风险，来源只能待在
     HTML 注释里（这条一旦回归，是发布事故）
  3. 生成过程只写 --out 目录，不碰 data/、static/、dist/

不联网。用法：python scripts/test_make_article.py
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

passed = 0
fails = []


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "make_article", os.path.join(ROOT, "scripts", "make_article.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def quiet(fn, *a, **kw):
    """跑一个会狂打印、而且可能 sys.exit 的函数。把输出吞掉，返回 (rc, 输出)。"""
    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf):
            rc = fn(*a, **kw)
    except SystemExit as e:                      # 脚本用 sys.exit 报错，别让它掀掉整个测试
        rc = e.code if isinstance(e.code, int) else 1
    if rc is None:
        rc = 0
    return rc, buf.getvalue()


def strip_comments(text):
    """去掉 HTML 注释，剩下的才是会真正发出去的内容。"""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def main():
    m = load_mod()
    cases = m.load_cases(m.CASES_PATH)
    by_id = {c.get("id"): c for c in cases}

    print("\n[1] 数据加载")
    chk("cases.json 读得到案例", len(cases) > 0, "%d 条" % len(cases))
    chk("每条都有 id", all(c.get("id") for c in cases))
    chk("id 不重复", len(by_id) == len(cases))

    print("\n[2] 人工表里的 id 都真实存在")
    bad = [k for k in m.EDITORIAL_WEIGHT if k not in by_id]
    chk("EDITORIAL_WEIGHT 无错 id", not bad, "、".join(bad) if bad else "")
    bad2 = [k for k in m.TITLE_OVERRIDES if k not in by_id]
    chk("TITLE_OVERRIDES 无错 id", not bad2, "、".join(bad2) if bad2 else "")
    chk("手改标题都是 3 条备选",
        all(len(v) == 3 for v in m.TITLE_OVERRIDES.values()))

    print("\n[3] 传播力打分")
    chk("基础分非负", all(m.virality_base(c) >= 0 for c in cases))
    # 纠错是基础分的主要驱动：同金额量级下，多的应该更高
    nitra = by_id["nitra"]
    chk("纠错 2 条的基础分 > 纠错 1 条",
        m.virality_base(nitra) > 2.0 * 1, "nitra=%.1f" % m.virality_base(nitra))
    chk("权重只加不减到负分之外",
        all(m.virality_score(c) > -10 for c in cases))

    print("\n[4] 分数排序稳定（不随原顺序变化）")
    # 走真的 pick()，而不是在测试里另写一遍 sorted —— 否则排序 bug 修在
    # make_article 里、测试却还在用自己的那套，等于没测到。
    # 关键在 jar dict：pick 读 args.id/min_score/top，用 Namespace 喂进去即可。
    import argparse
    args = argparse.Namespace(id=None, min_score=None, top=None)
    a = [c["id"] for _, c in m.pick(cases, args)]
    b = [c["id"] for _, c in m.pick(list(reversed(cases)), args)]
    chk("同一批案例排序一致", a == b, "%s vs %s" % (a[:3], b[:3]))
    chk("nitra 排第一", a[0] == "nitra", "实际 %s" % a[0])
    # 同分两条的先后必须稳定：gojiberryai 与 aeo-engine 都是 4.0
    ties = [(round(m.virality_score(c), 2), c["id"]) for c in cases]
    tie_ids = [i for s, i in ties if s == round(m.virality_score(
        next(x for x in cases if x["id"] == "aeo-engine")), 2)]
    if len(tie_ids) > 1:
        chk("并列的两条按 id 定先后", tie_ids == sorted(tie_ids), "、".join(tie_ids))

    print("\n[5] 正文分段")
    counts = {c["id"]: len(m.build_sections(c)) for c in cases}
    thin = [k for k, n in counts.items() if n < 5]
    chk("每条案例都 >= 5 段", not thin,
        "过短：%s" % "、".join(thin) if thin else "%d 条" % len(cases))

    heads = {c["id"]: [h for h, _ in m.build_sections(c)] for c in cases}
    chk("每篇都有「能不能搬回国内」",
        all("能不能搬回国内" in v for v in heads.values()))
    chk("每篇都有「一个人能不能做」",
        all("一个人能不能做" in v for v in heads.values()))
    chk("每篇都以「我的判断」收尾",
        all(v[-1] == "我的判断" for v in heads.values()))
    chk("钩子段不带小标题（head 为 None）",
        all(m.build_sections(c)[0][0] is None for c in cases))

    print("\n[6] Markdown 渲染")
    c = by_id["nitra"]
    titles, manual = m.build_titles(c)
    md, hits = m.render_markdown(c, titles, manual, m.build_sections(c), 9.5)
    chk("含三条备选标题注释", md.count("备选标题") == 2)
    chk("含传播力分注释", "传播力分 9.5" in md)
    chk("无待补占位符（已废止）", "【待补：" not in md)
    chk("含核对用来源注释", "核对用来源" in md)
    chk("带 ## 小标题", "\n## 它是干什么的" in md)
    # 2026-09-21「去 AI 味」改造的回归防线：这几条一旦回退，正文立刻变回填表
    chk("章节不再带「一、二、三」编号", "\n## 一、" not in md)
    chk("无「一句话：」表单前缀", "一句话：" not in md)
    chk("无「收入模式：」重复行", "收入模式：" not in md)
    chk("评分表只输出一次（曾因 rows 未清空连打 3 遍）",
        md.count("| 付费意愿 |") == 1)

    print("\n[7] 正文里不能有裸 URL（发布红线）")
    leaked = []
    for c2 in cases:
        t, man = m.build_titles(c2)
        text, _ = m.render_markdown(c2, t, man, m.build_sections(c2), 1.0)
        body = strip_comments(text)
        if "http" in body:
            leaked.append(c2["id"])
    chk("正文无外链", not leaked, "、".join(leaked) if leaked else "")

    html_leak = []
    for c2 in cases:
        t, man = m.build_titles(c2)
        text, _ = m.render_html(c2, t, man, m.build_sections(c2), 1.0)
        if "http" in strip_comments(text):
            html_leak.append(c2["id"])
    chk("HTML 正文无外链", not html_leak, "、".join(html_leak) if html_leak else "")

    print("\n[8] 敏感词")
    out, hits = m.sanitize("轻松月入十万，躺赚，稳赚不赔")
    chk("宣传性词汇被替换",
        "月入" not in out and "躺赚" not in out and "稳赚" not in out, out)
    chk("替换词按最长优先命中", "持续营收" in out, out)
    out2, hits2 = m.sanitize("月入三万")
    chk("单独「月入」替换成月营收", out2 == "月营收三万" and hits2 == ["月入"], out2)
    out2, hits2 = m.sanitize("赚钱的生意一点都不性感")
    chk("分析性词汇不动", out2 == "赚钱的生意一点都不性感" and not hits2)
    chk("分析性词汇进提醒", bool(m.scan_warn("赚钱的生意")), 
        "、".join(m.scan_warn("赚钱的生意")))
    chk("干净文本无提醒", m.scan_warn("这是一段干净的话") == [])

    print("\n[9] 命令行行为")
    old_argv = sys.argv
    try:
        with tempfile.TemporaryDirectory() as tmp:
            nope = os.path.join(tmp, "should-not-exist")
            sys.argv = ["make_article.py", "--list", "--out", nope]
            rc, out = quiet(m.main)
            chk("--list 退出码 0", rc == 0)
            chk("--list 打印了全部案例", out.count("\n") > len(cases))
            chk("--list 不创建输出目录", not os.path.exists(nope))

        sys.argv = ["make_article.py", "--id", "no-such-id-xyz"]
        rc2, out2 = quiet(m.main)
        chk("--id 不存在时退出码非 0", rc2 != 0)
        chk("--id 不存在时给出提示", "找不到案例" in out2)

        sys.argv = ["make_article.py"]
        rc3, out3 = quiet(m.main)
        chk("不给参数时打印用法且不崩", rc3 == 0 and "至少给一个" in out3)

        print("\n[10] 生成产物（写到临时目录）")
        with tempfile.TemporaryDirectory() as tmp:
            outdir = os.path.join(tmp, "articles")
            sys.argv = ["make_article.py", "--all", "--format", "both",
                        "--out", outdir]
            rc4, out4 = quiet(m.main)
            chk("--all 退出码 0", rc4 == 0)
            files = os.listdir(outdir)
            chk("生成 2 倍案例数的文件", len(files) == 2 * len(cases),
                "%d 个" % len(files))
            chk("md 和 html 都是成对的",
                all(f[:-3] + ".html" in files for f in files if f.endswith(".md")))
            chk("文件名安全（不含路径分隔符）",
                all("/" not in f and "\\" not in f for f in files))
            sample = open(os.path.join(outdir, "nitra.md"), encoding="utf-8").read()
            chk("产物是 UTF-8 中文", "中文媒体" in sample)
            chk("产物里无待补占位（已废止）", "【待补：" not in sample)
            chk("产物里无裸 URL",
                "http" not in strip_comments(sample))
            chk("传播力高的先写（nitra 存在）", "nitra.md" in files)

            print("\n[10b] 公众号 HTML 的键值行（一体化评分表）")
            # 2026-09-21：键值行从「······」点线凑对齐换成真两端对齐的卡片行。
            # 这条守的是「不要退回点线」——微信实测认 display:flex / 圆角，
            # 自建清洗脚本曾把它们砍掉，导致只能用点线填充。
            hs = open(os.path.join(outdir, "nitra.html"), encoding="utf-8").read()
            chk("键值行渲成两端对齐卡片行",
                "justify-content:space-between" in hs)
            chk("不再用点线凑对齐", "······" not in hs)
            chk("首行圆角上、末行圆角下（拼成一整张表）",
                "border-top-left-radius" in hs and "border-bottom-right-radius" in hs)
            chk("行间用细线分隔", "border-top:1px solid #eaeef2" in hs)
            # ⚠ nowrap 只在本地预览/非微信环境有效。公众号编辑器会把 white-space
            # 覆盖成 break-spaces（实测草稿 100000117），所以它**不是**防挤压护栏，
            # 真正的护栏是下面的「按行宽分流」。
            chk("短值行保留 nowrap（本地预览用）", "white-space:nowrap" in hs)
            chk("md 侧仍是真表格",
                "| 维度 | 内容 |" in sample)
            # 2026-09-21 修复：_flush_html_rows 输出后没清空 rows，
            # 同一个 6 行评分表在正文里被连打 3 遍。这条钉死只输出一次。
            chk("html 评分表只输出一次（rows 已消费）", hs.count("付费意愿") == 1)
            chk("html 章节不带「一、二、三」编号",
                ">一、它是干什么的</h2>" not in hs)

            print("\n[10c] 键值行按行宽分流（长值不再挤扁标签）")
            # 2026-09-21 修复：微信把 white-space 覆盖成 break-spaces，值一长
            # flex 收缩就把标签压成一列一个字（「备注」竖排，草稿 100000117
            # 实测）。所以按整行宽度分流：长行 →「标签：正文」左对齐；
            # 短行 → 保持两端对齐。
            chk("短值判定：付费意愿 3/5",
                m._disp_em("付费意愿") + m._disp_em("3/5") <= m.LONG_ROW_EM)
            chk("长值判定：一串说明文字 > 阈值",
                m._disp_em("备注") + m._disp_em(
                    "TrustMRR 页面由 RevenueCat 支付侧 API 验证，115 个活跃订阅"
                    "，App Store 评分 3.6/5") > m.LONG_ROW_EM)
            P = []
            m._flush_html_rows(P, [("备注", "这是一段很长的说明文字，" * 8)])
            long_html = "".join(P)
            chk("长值行不套 flex", "display:flex" not in long_html)
            chk("长值行渲成「标签：正文」左对齐",
                '：<span style="color:#24292f;">' in long_html)
            P2 = []
            m._flush_html_rows(P2, [("客户", "115")])
            short_html = "".join(P2)
            chk("短值行仍两端对齐", "justify-content:space-between" in short_html)
            chk("短值行值仍用金色强调", "color:#8a5a00" in short_html)
            _rows = [("客户", "115")]
            m._flush_html_rows([], _rows)
            chk("_flush_html_rows 消费 rows（防同一张表连打多遍）", _rows == [])
    finally:
        sys.argv = old_argv

    print("\n[10d] 钩子：引号句 + 我去核了一遍（2026-09-22）")
    try:
        old_argv = sys.argv
        m = load_mod()
        # _as_quote：已有引号的**原样保留**，没有的补上——不剥、不嵌套
        chk("_as_quote 保留已有「」",
            m._as_quote("「Nitra 有 7000 家诊所在用」")
            == "「Nitra 有 7000 家诊所在用」")
        chk("_as_quote 给裸断言补引号",
            m._as_quote("该 App 售价 $75K") == "「该 App 售价 $75K」")
        # 半截引号属数据异常，只要求不抛异常、且不会剥掉已有引号
        half = m._as_quote("1Lookup「处于 FOR SALE」")
        chk("_as_quote 对半截引号不抛异常", isinstance(half, str) and half)
        chk("_as_quote 不剥掉已有引号内容", "「处于 FOR SALE」" in half)
        chk("_as_quote 对空串安全", m._as_quote("") == "")
        chk("_as_quote 对 None 安全", m._as_quote(None) == "")

        # 钩子两段：第一段带引号，第二段以「我去核了一遍」起头
        cases = m.load_cases(m.CASES_PATH)
        with_corr = [c for c in cases if c.get("corrections")]
        chk("库里有带 corrections 的案例可测", len(with_corr) >= 10)
        bad_quote, bad_prefix, has_old = [], [], []
        for c in with_corr:
            secs = m.build_sections(c)
            head, hook = secs[0]
            chk_head = (head is None)
            if not (chk_head and len(hook) == 2):
                bad_quote.append(c["id"] + "(结构)"); continue
            if not (hook[0].startswith("「") and hook[0].endswith("」")):
                bad_quote.append(c["id"])
            if not hook[1].startswith("我去核了一遍："):
                bad_prefix.append(c["id"])
            if "反复引用" in hook[1] or "反复引用" in hook[0]:
                has_old.append(c["id"])
        chk("钩子第 1 段都是引号句", not bad_quote, "、".join(bad_quote[:5]))
        chk("钩子第 2 段以「我去核了一遍」起头",
            not bad_prefix, "、".join(bad_prefix[:5]))
        chk("旧指代句「这句在中文网上被反复引用」已清除",
            not has_old, "、".join(has_old[:5]))

        # 内部黑话不得泄漏进发布正文
        jargon = ("候选", "本次核实", "抓取原文", "入库时")
        leaks = []
        for c in cases:
            for corr in (c.get("corrections") or []):
                for f in ("claim", "truth"):
                    t = corr.get(f) or ""
                    if any(j in t for j in jargon):
                        leaks.append("%s.%s" % (c["id"], f))
        chk("corrections 里无内部黑话泄漏", not leaks, "、".join(leaks[:5]))
    except Exception as e:
        chk("[10d] 钩子口径", False, "%s" % e)
    finally:
        sys.argv = old_argv

    print("\n[10e] 第二条纠错：不立小标题、不断行（2026-09-23）")
    try:
        m = load_mod()
        # 用户反馈：「还有一条被抄错的」这个小标题和下面那行「流传的说法：…」
        # 被排版断成两行后，读起来是半句话。现在整句并进同一个段落，
        # 该节的 head 用 m.NO_HEAD（空串：不出 h2，也不会被当成钩子引文块）。
        cases = m.load_cases(m.CASES_PATH)
        second = [c for c in cases if len(c.get("corrections") or []) > 1]
        chk("库里有带两条以上纠错的案例可测", len(second) >= 5)
        bad_head, bad_join, bad_html, bad_md = [], [], [], []
        for c in second:
            secs = m.build_sections(c)
            hits = [s for s in secs if "还有一条被抄错的" in "".join(s[1] or [])]
            if len(hits) != 1:
                bad_head.append("%s(段落数%d)" % (c["id"], len(hits)))
                continue
            head, paras = hits[0]
            if head != m.NO_HEAD:
                bad_head.append(c["id"])
            if not paras[0].startswith("**还有一条被抄错的流传的说法**："):
                bad_join.append(c["id"])
            titles, manual = m.build_titles(c)
            if "还有一条被抄错的</h2>" in m.render_html(c, titles, manual, secs, 0.0):
                bad_html.append(c["id"])
            if "## 还有一条被抄错的" in m.render_markdown(c, titles, manual, secs, 0.0)[0]:
                bad_md.append(c["id"])
        chk("该节 head 为 NO_HEAD（不立小标题）", not bad_head, "、".join(bad_head[:5]))
        chk("「还有一条被抄错的」已并进本句", not bad_join, "、".join(bad_join[:5]))
        chk("HTML 里不再出现该小标题", not bad_html, "、".join(bad_html[:5]))
        chk("Markdown 里不再出现该小标题", not bad_md, "、".join(bad_md[:5]))
    except Exception as e:
        chk("[10e] 第二条纠错不断行", False, "%s" % e)

    print("\n[10f] 列表项不再用悬挂缩进（2026-09-24 用户反馈）")
    try:
        bad_hang = []
        for c in m.load_cases(m.CASES_PATH):
            titles, manual = m.build_titles(c)
            secs = m.build_sections(c)
            html = m.render_html(c, titles, manual, secs, 0.0)
            # 症状：无项目符号时单行顶格、多行首行顶格续行缩进，视觉错乱
            if "text-indent:-14px" in html or "padding-left:14px;text-indent" in html:
                bad_hang.append(c["id"])
        chk("HTML 里不再出现 text-indent 悬挂缩进", not bad_hang, "、".join(bad_hang[:5]))
    except Exception as e:
        chk("[10f] 列表项悬挂缩进", False, "%s" % e)

    print("\n[11] 不污染工作区")
    dirty = [p for p in ("data", "static", "dist")
             if os.path.isdir(os.path.join(ROOT, p))
             and any(f.endswith(".bak") for f in os.listdir(os.path.join(ROOT, p)))]
    chk("data/static/dist 无 .bak 残留", not dirty, "、".join(dirty) if dirty else "")

    print("\n" + "=" * 62)
    if fails:
        print("  结果：%d 通过 / %d 失败" % (passed, len(fails)))
        for f in fails:
            print("    - %s" % f)
        return 1
    print("  结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
