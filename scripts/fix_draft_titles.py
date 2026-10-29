# -*- coding: utf-8 -*-
"""给五平台草稿做「标题禁金额」+ 收入口径纠错。

为什么是硬要求
--------------
本库有一条铁律：**标题不许带金额**（PRD §6.1 #19）。

理由不是审美。公众号那边有个金额清洗器，标题里的数字会被剥掉，
剩下的是病句——「从 0 到 40K MRR，这个营销工具是如何……」被剥成
「从 0 到 ，这个营销工具是如何……」。所以禁的不是「醒目」，
是「会被机器改坏」。

更硬的一层是口径：50 条草稿里有 7 条标题把**年化收入当月收入**、
**六个月累计当月收入**——这跟反向样本那 33 条里 23 条「ARR 当 MRR」
是同一个错，只不过错在了我们自己要发出去的稿子上。

    klar（4 条）  「30 天赚 130K」   ← €130K 是 ARR（年化），不是 30 天进账
    3ak （3 条）  「月入 $70K」      ← $70K 是六个月累计，MRR 是 $17-19K

做法
----
1. 只动首行（标题），且**只对含金额的标题动手**——已经合规的跳过，
   这样脚本可以反复跑，也不会覆盖人工改过的标题。
2. 正文里另外 6 处口径硬伤用精确替换修掉，找不到原文就报错。
3. 非收入数字保留：「15 万粉受众」「3 周搭建」「六年」「30 天」这些
   是产品的形状，不是收入，禁掉会让标题失去信息量。

用法
----
    python -X utf8 scripts/fix_draft_titles.py            # 改
    python -X utf8 scripts/fix_draft_titles.py --check    # 只体检
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFTS = os.path.join(ROOT, "drafts")

# 标题里不许出现的：货币符号、收入口径缩写、「数字 + 货币单位」，
# 以及最容易漏的一类——**裸写的金额**（「30天赚130K」里既没有货币符号
# 也没有「万」，但谁都读得懂那是钱）。第一版正则只认符号，
# 结果 klar 三条「赚130K」全部漏网，所以补了末尾两条。
MONEY_RE = re.compile(
    r"[¥$€]"                      # 货币符号
    r"|MRR|ARR"                   # 收入口径缩写
    r"|\d\s*(?:万美元|万欧元|万美金|美金|欧元)"
    r"|月入|月收入|月流水"          # 会把累计/年化说成月度的说法
    r"|年入|年收入|营收|销售额"      # 中文金额表述：「年入百万」
    r"|赚\s*[¥$€]?\s*\d"          # 「赚$10M」「赚130K」
    r"|\d+\s*[KM](?![a-zA-Z])"    # 裸写金额：「130K」「10M」
)

# ---------------------------------------------------------------------------
# 新标题。键是 (平台, 案例 id)。
# 判据：不含 MONEY_RE，以「# 」开头，长度不夸张。
# ---------------------------------------------------------------------------
TITLE_FIXES = {
    # -------------------------------------------------------------- bilibili
    ("bilibili", "3ak-track-field"):
        "# 两个田径生如何用 15 万粉受众和 AI 打造 App？先受众后产品",
    ("bilibili", "flodesk"):
        "# 【拆解】Flodesk：不按订阅人数涨价，把邮件工具做成设计工具",
    ("bilibili", "getdavid"):
        "# 【AI赋能】用 AI 给小店造站，再用纸质明信片获客：拆解这套组合拳",
    ("bilibili", "headshotpro"):
        "# 【AI工具】拆解一个 solo 运营的 AI 证件照爆款：SEO + 联盟 + 企业采购",
    ("bilibili", "klar"):
        "# 三个瑞典大学生如何用 AI 工具做备考产品？不会写代码，全靠 Lovable",
    ("bilibili", "pdfai"):
        "# 今天拆解一个 solo 做的 AI 工具：域名本身就是它的增长引擎",
    ("bilibili", "plausible"):
        "# 今天拆解一个不卖数据的网站分析工具：AGPL 开源，收入全靠订阅",
    ("bilibili", "sleek-design"):
        "# 【AI 工具】Sleek：三周搭建，零付费营销，靠 X 和 Reddit 起量",
    ("bilibili", "upvoty"):
        "# 今天拆解一个 bootstrapped 六年的 SaaS：用户反馈投票板 + 公开路线图",
    # ---------------------------------------------------------------- juejin
    ("juejin", "3ak-track-field"):
        "# 两位田径生如何用 AI 和社交媒体打造爆款运动 App",
    ("juejin", "flodesk"):
        "# Flodesk：设计优先的邮件营销平台，被 YC 拒绝后自筹做起来",
    ("juejin", "getdavid"):
        "# David AI：AI Agent 造站 + 纸质明信片获客的硬核增长打法",
    ("juejin", "klar"):
        "# Klar：用 Lovable 搭建的 AI 备考工具，30 天内跑通年化收入",
    ("juejin", "pdfai"):
        "# PDF.ai 的 AI 工具增长黑客之路：精准域名 + 定价迁移",
    ("juejin", "plausible"):
        "# Plausible Analytics：用 AGPL 开源 + 云托管订阅做起来的隐私分析工具",
    ("juejin", "upvoty"):
        "# Upvoty：用户反馈投票板的增长之路，六年自举没融过资",
    # --------------------------------------------------------------- toutiao
    ("toutiao", "3ak-track-field"):
        "# 两个零编程经验的田径生：靠 15 万粉受众 + AI 做出训练 App",
    ("toutiao", "flodesk"):
        "# 零融资的邮件营销平台 Flodesk：把邮件工具做成了设计工具",
    ("toutiao", "getdavid"):
        "# AI 给小店造站，再寄一张纸质明信片：这套线下获客打法拆解",
    ("toutiao", "headshotpro"):
        "# solo 运营的 AI 证件照工具 HeadshotPro：SEO + 联盟怎么打",
    ("toutiao", "klar"):
        "# 三个瑞典大学生用 Lovable 搭出 AI 备考工具，零广告费起量",
    ("toutiao", "pdfai"):
        "# 精准域名就是它的 SEO：PDF.ai 如何靠搜索意图起量",
    ("toutiao", "plausible"):
        "# 不追踪用户的网站分析工具：开源三年，靠订阅者养活",
    ("toutiao", "refactoringui"):
        "# 出版四年的开发者设计书：一次性买断，长尾还在卖",
    ("toutiao", "sleek-design"):
        "# AI 设计工具 Sleek：三周搭建，零付费营销起量",
    ("toutiao", "upvoty"):
        "# 六年 bootstrapped 的用户反馈工具：靠「Alternatives to」内容截流",
    # ---------------------------------------------------------------- wechat
    ("wechat", "3ak-track-field"):
        "# 先有 15 万粉受众，后有产品：两个田径生如何用 AI 做训练 App？",
    ("wechat", "flodesk"):
        "# 一个被 YC 拒绝的邮件工具：靠「一口价 + 无限发」做成了大生意",
    ("wechat", "getdavid"):
        "# AI 造站 + 纸质明信片：一家给小店做网站的公司怎么用线下动作获客",
    ("wechat", "headshotpro"):
        "# 一个人做的 AI 证件照：Fortune 500 团队也在买",
    ("wechat", "klar"):
        "# 三个不会写代码的大学生：靠 Lovable 做出来的 AI 备考工具",
    ("wechat", "pdfai"):
        "# 一个人、0 员工、0 融资：这个「跟 PDF 对话」的工具是怎么做起来的？",
    ("wechat", "plausible"):
        "# 开源三年、10 个人、没有投资人：这个隐私分析工具是怎么活下来的？",
    ("wechat", "refactoringui"):
        "# 先有受众，后做书：一个写给程序员的设计书是怎么卖起来的",
    ("wechat", "sleek-design"):
        "# 三周搭出来的 AI 设计工具：零付费营销是怎么起量的",
    ("wechat", "upvoty"):
        "# bootstrapped 六年：这个用户反馈工具靠什么活到今天",
    # ------------------------------------------------------------------- xhs
    ("xhs", "3ak-track-field"):
        "# 两个田径生用 AI 搞钱，15 万粉受众是他们的启动器！",
    ("xhs", "flodesk"):
        "# 🚀 Flodesk：邮件营销里的颜值担当，零融资做成大生意！",
    ("xhs", "getdavid"):
        "# AI 造站 + 一张明信片，这获客方式太酷了！🔥",
    ("xhs", "klar"):
        "# 三个瑞典大学生，不会写代码也做出了 AI 备考工具🤯",
    ("xhs", "pdfai"):
        "# 🚀 PDF.ai：跟任何 PDF 对话，一个人的生意也能做大！",
    ("xhs", "plausible"):
        "# 🚀 Plausible Analytics：不追踪用户的分析工具，10 人团队做起来了！",
    ("xhs", "refactoringui"):
        "# 🚀 写给程序员的设计书，不靠订阅也能长卖！",
    ("xhs", "sleek-design"):
        "# 🚀 3 周搭出来的 AI 设计工具，零付费营销也起量了！🤯",
    ("xhs", "upvoty"):
        "# 🚀 用「用户反馈投票板」做起来的生意，六年没融过资",
}

# ---------------------------------------------------------------------------
# 正文口径纠错（精确替换，找不到原文就报错）
# ---------------------------------------------------------------------------
BODY_FIXES = [
    # --- 3ak：$70K 是六个月累计，MRR 是 $17-19K，标题/正文都错成了月入
    ("toutiao", "3ak-track-field",
     "该App上线首月收入达到10K美元，六个月后月收入更是高达70K美元。",
     "该App上线首月收入达到10K美元，六个月内累计约70K美元。"),
    ("bilibili", "3ak-track-field",
     "打造了一款App，上线首月收入10K，六个月后达到70K！🎉",
     "打造了一款App，上线首月 10K，六个月累计约 70K！🎉"),
    ("juejin", "3ak-track-field",
     "他们在上线首月实现了 $10K 的收入，六个月后达到 $70K。",
     "他们在上线首月实现了 $10K 的收入，六个月累计约 $70K。"),
    # 「月入数万」没单位，人民币语境下会被读成几万人民币，差 6 倍
    ("wechat", "3ak-track-field",
     "打造出一个月入数万的App？今天，我们就来揭秘这个惊人的故事。",
     "打造出一个月入近两万美元的App？今天，我们就来揭秘这个惊人的故事。"),
    ("wechat", "3ak-track-field",
     "两个田径生用AI打造爆款App，月入数万，这是对创新和努力的最好证明。",
     "两个田径生用AI打造爆款App，月入近两万美元，这是对创新和努力的最好证明。"),
    # --- klar：€130K 是 ARR（年化），不是 30 天赚到的钱
    ("wechat", "klar",
     "三个大学生可以创造出价值130K的AI工具？",
     "三个大学生可以做出年化收入（ARR）达到 130K 的 AI 工具？"),
    ("xhs", "klar",
     "竟然用AI工具在30天内赚了130K欧元！😱",
     "竟然用AI工具在30天内跑出了130K欧元的ARR！😱"),
]


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write(path, txt):
    io.open(path, "w", encoding="utf-8", newline="\n").write(txt)


def md_files():
    out = []
    if not os.path.isdir(DRAFTS):
        return out
    for plat in sorted(os.listdir(DRAFTS)):
        pdir = os.path.join(DRAFTS, plat)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if fn.endswith(".md"):
                out.append((plat, fn[:-3], os.path.join(pdir, fn)))
    return out


def apply_title_fixes():
    """只替换含金额的标题。返回改动条数。"""
    n = 0
    for (plat, cid), new in sorted(TITLE_FIXES.items()):
        path = os.path.join(DRAFTS, plat, cid + ".md")
        if not os.path.isfile(path):
            raise AssertionError("找不到草稿 %s" % path)
        txt = read(path)
        lines = txt.split("\n")
        old = lines[0]
        assert old.startswith("# "), "%s 首行不是标题：%r" % (path, old[:40])
        if not MONEY_RE.search(old):
            continue                      # 已经合规，不动
        if old == new:
            continue
        assert not MONEY_RE.search(new), "%s 新标题仍带金额：%s" % (path, new)
        lines[0] = new
        write(path, "\n".join(lines))
        n += 1
        print("  ✓ 标题 %-9s %-16s" % (plat, cid))
    return n


def apply_body_fixes():
    n = 0
    for plat, cid, old, new in BODY_FIXES:
        path = os.path.join(DRAFTS, plat, cid + ".md")
        txt = read(path)
        if new in txt:
            continue                      # 已改过
        if old not in txt:
            raise AssertionError("%s 里找不到待修原文（草稿被改过？）" % path)
        write(path, txt.replace(old, new, 1))
        n += 1
        print("  ✓ 正文 %-9s %-16s" % (plat, cid))
    return n


def audit():
    """全目录体检：返回仍带金额的标题列表。"""
    bad = []
    for plat, cid, path in md_files():
        head = read(path).split("\n")[0]
        if MONEY_RE.search(head):
            bad.append("%s/%s  %s" % (plat, cid, head[:60]))
    return bad


def main(argv):
    check_only = "--check" in argv

    # 表自身自检
    for key, t in TITLE_FIXES.items():
        assert t.startswith("# "), "%s 新标题不是 # 开头" % (key,)
        assert not MONEY_RE.search(t), "%s 新标题仍带金额：%s" % (key, t)
    for plat, cid, old, new in BODY_FIXES:
        assert old.strip() and new.strip() and old != new, (plat, cid)

    if check_only:
        bad = audit()
        print("标题带金额的草稿 %d 条：" % len(bad))
        for b in bad:
            print("   ", b)
        return 1 if bad else 0

    n_title = apply_title_fixes()
    n_body = apply_body_fixes()
    print("\n改标题 %d 条，改正文 %d 处。" % (n_title, n_body))

    bad = audit()
    if bad:
        print("⚠ 仍有 %d 条标题带金额：" % len(bad))
        for b in bad:
            print("   ", b)
        return 1
    print("✓ 复查：drafts/ 全目录标题已无金额。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
