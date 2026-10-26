# -*- coding: utf-8 -*-
"""补掉 drafts/ 里 LLM 留下的「（数据待补充）」占位符。

背景
----
`tmp/gen_platform_drafts.py` 批量生成五平台草稿时，模型常在
「为什么能成 / 方法论」「可复制性」「我的判断」三节交白卷，
留下一句「虽然具体的数据待补充，但我们可以……」或直接一个
`（数据待补充）`。这些草稿一旦推到平台上会当场露馅，
所以推送前必须补掉。

纪律
----
1. **不编事实**。每一句的内容都来自 `data/candidates.json` 对应条目的
   `what_it_does` / `how_it_makes_money` / `metric_note` / `note` / `sources`，
   数字必须能在那几段原文里找到。
2. **按平台调性改写**，不是把同一段复制到五个平台：
   wechat 正式长句、toutiao 资讯体、xhs 口语 + emoji、bilibili 分点解说。
3. **幂等**。只替换「含占位符的行」，替换后文件里不再含占位符；
   再跑一次是 no-op（`main()` 会报告「已无占位符」并退出）。

用法
----
    python -X utf8 scripts/fill_draft_placeholders.py          # 补全部
    python -X utf8 scripts/fill_draft_placeholders.py --check  # 只检查不写
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAFTS = os.path.join(ROOT, "drafts")

PLACEHOLDER = "待补充"

# ---------------------------------------------------------------------------
# 填充内容。键是 (平台, 案例 id)，值是按「占位符在文件里出现的顺序」排列的
# 替换串。数量必须与该文件的占位符行数严格相等 —— 脚本会当场校验，
# 防止「补了两处漏了第三处」这种半拉工程。
# ---------------------------------------------------------------------------
FILLS = {
    # ---------------------------------------------------------------- wechat
    ("wechat", "upvoty"): [
        # 为什么能成 / 方法论
        "创始人 Mike Slaats 的顺序是反着来的：先用一天搭个落地页，"
        "数周拿到 200+ 注册才动手写产品，而且上线即收费、不设免费档。"
        "发布之后靠两条腿走路——写「Alternatives to …」系列截流竞品的不满用户，"
        "再按用户点名要求接 GitHub / Slack / Jira 集成，集成接完就变成了留存。"
        "指标用 Baremetrics 管，重留存、轻虚荣指标，获客成本压在 $500/月以内。",
        # 可复制性
        "能抄的是三件：落地页验证再写码、「Alternatives to」内容截流、"
        "用集成换留存——都不需要钱，只需要耐心。抄不了的是「六年」本身："
        "$40K MRR 是第六年的数，不是第六个月的数；还有那个启动条件——"
        "创始人前作 Vindy（荷兰/比利时的家居改善平台，年收入约 $1M）"
        "一直在用利润养着它。数字也要分清：创始人一手披露的里程碑是"
        "「上线数月 $1K MRR」，$40K 是第三方同量级口径"
        "（TrustMRR Stripe 授权快照 2026-07 约 ¥28.4万）。",
        # 我的判断
        "最值得学的其实不是「Alternatives to」这个技巧，"
        "而是它对顺序的坚持：先收钱再写代码，先留存再增长。"
        "这两条在今天反而更稀缺——大家习惯先做产品，再想怎么卖。",
    ],
    ("wechat", "plausible"): [
        # 为什么能成 / 方法论
        "它走的是 open-core：AGPL 开源 + 云托管订阅，自托管版本永远免费。"
        "产品本身就是卖点——2.5KB 脚本、无 cookie、不需要同意横幅，"
        "这在 GDPR 之后的欧洲不是加分项，而是入场券。"
        "收入 100% 来自订阅者，无广告无数据变现，10 人团队、无外部投资人。"
        "官方博客披露了 2019-05 到 2022-06 的完整 MRR 时间线：从 $64 走到 $83,637。",
        # 可复制性
        "技术上没什么不可复制的，真正难的是顺序：open-core 不是一种变现方式，"
        "而是「先付出三年再谈变现」的顺序——开源版免费、云托管收费，"
        "中间要熬过没人付费的那段。如果你需要收入来维持开发，这个顺序是反的。"
        "另外那个位置现在也不空了，同赛道的开源分析工具已经排了一排。",
        # 我的判断
        "这个案例最容易犯的错，是把 $1M ARR 当现值读：那是 2022-06 "
        "创始人博客披露的历史里程碑（$83,637 MRR = $1.003M ARR），"
        "此后未再公开，第三方 2025 年估 $1M+ 只能作参照。"
        "它真正的启示是另一条——一个 10 人、自筹、无投资人的团队，"
        "靠「不追踪用户」这件事本身收钱，能收到年入百万美元。",
    ],
    ("wechat", "3ak-track-field"): [
        # 为什么能成 / 方法论
        "顺序是这个案例的全部：两个人是大学田径队友，"
        "Christian Rac（IG 4.25 万粉）和 Braylin Byrd（IG 11.2 万粉）合计 15 万+ 粉，"
        "产品在上线前数月就往视频、简介和 Shopify 店里埋「3AK」这个伏笔，"
        "所以上线当天就有受众。技术上两人零编程经验，"
        "用 Rork 搭 App、Claude 验错、Supabase + RevenueCat，"
        "两周上线，成本不到 $500，毛利约 80%。"
        "定价 $10/月 或 $30/年，是照着 V-Bucks、Robux 的青少年零花钱心理定的。",
    ],
    ("wechat", "flodesk"): [
        # 为什么能成 / 方法论
        "它切进的是一个极老的赛道，靠两个维度做出差异："
        "一是定价——$19/月 无限发送，竞品按订阅人数涨价，它按功能定价，"
        "把「无限」做成卖点；二是审美——自定义字体与配色的品牌优先设计，"
        "把邮件工具做成了设计工具。增长上没有销售团队，"
        "全靠口碑和用户成功案例（Miss Excel、The Everygirl）。"
        "2018 年被 YC 拒绝后自筹起步，上线第二周即盈利，至今零外部融资。",
        # 可复制性
        "可学的是「一口价 + 无限」这个定价结构，"
        "和「把工具做成审美产品」这个定位，两者都不需要资本。"
        "不可学的是它现在的规模：100,000+ 小企业客户、"
        "2025 年经平台发出 130 亿封邮件、新增 3.14 亿订阅者——"
        "$36M+ ARR 是 2026-01 官方新闻稿与官网双源披露的数，不是起点。",
    ],
    ("wechat", "pdfai"): [
        # 为什么能成 / 方法论
        "域名就是它的 SEO：pdf.ai 这个精准域名让 50%+ 的流量"
        "直接来自「PDF AI」相关搜索，获客几乎不花钱。"
        "第二层是定价迁移——早期用 $99 的一次性买断验证付费意愿，"
        "之后从个人 $10/月 转向企业 $500/月，客单价翻 50 倍。"
        "创始人 Damon Chen 2014 年从中国移民美国，"
        "做了 9 年 Cisco 网络工程师后辞职，全程 0 员工 0 融资。",
    ],
    ("wechat", "refactoringui"): [
        # 为什么能成 / 方法论
        "它是「先有受众、后做产品」的教科书："
        "作者多年在 Twitter 和博客上攒了 5 万+ 开发者受众，"
        "书一发布就转化存量粉丝，没有冷启动这一步。"
        "产品形态是给不会设计的程序员写的具体战术"
        "（少用边框、用对比代替装饰），一次性买断、无订阅。"
        "更长的一条线是它验证了「内容产品养开源框架」这条路："
        "2016 年第一本书首周 $61,392 → Tailwind UI 2020-02 上线首日 $400K "
        "→ 2022-09 累计 $2.5M+。",
    ],
    # --------------------------------------------------------------- toutiao
    ("toutiao", "getdavid"): [
        # 方法论（原句后面紧跟三条关键数字，替换时把结论写进来）
        "打法拆成四步：AI agent 挑出 350 家没有网站的小店 → 挨个把站造好 → "
        "寄带二维码的纸质明信片（单张约 $7，一轮 350 张约 $400）→ "
        "扫码进站转月费订阅。真正让它跑通的其实是最后一步："
        "341 封妥投、20% 扫码率，再配合真实来电跟进——"
        "线上自动化负责执行，线下真实动作负责建立信任，"
        "纯数字外联差一个量级。",
    ],
    ("toutiao", "headshotpro"): [
        "它的方法论可以拆成三层。产品层：一次性 $29 的低门槛（不是订阅），"
        "自建 Stable Diffusion + DreamBooth pipeline，30 小时搭出 v1，"
        "照片数据 7-30 天自动删除——隐私本身就是卖点。获客层："
        "SEO 抓「AI headshot」这个搜索意图，联盟营销占收入 15%+（$50K+/月），"
        "再加 Fortune 500 团队采购（含 HubSpot 销售团队）。发行层："
        "创始人 Danny Postma 是连续创业者，X 10 万+ 粉，"
        "前作 Headlime 做到 $20K MRR 时被 Jasper 以逾 $1M 收购——"
        "冷启动能力是上一个产品攒下来的。",
    ],
    ("toutiao", "klar"): [
        "工具链是它最可抄的部分：三个创始人（Isabel Storgårds、"
        "Andreas Melander、Eric Götborg，乌普萨拉大学学生，两人已休学）"
        "不会写代码，产品全程用 Lovable 搭，模型层混用 Claude / GPT / Gemini，"
        "在 25 平米的公寓里做出来的。需求侧也准：上传课件和历年真题，"
        "AI 生成练习题、闪卡、预测卷，数据存欧盟本地。"
        "启动靠校园口碑——上线两天 2,500 注册、4,000 排队，全程零付费广告。",
    ],
    ("toutiao", "plausible"): [
        "它走的是 open-core：AGPL 开源 + 云托管订阅，自托管永久免费；"
        "产品本身就是卖点——2.5KB 脚本、无 cookie、不需要同意横幅，"
        "在 GDPR 之后的欧洲这不是加分项，而是入场券。"
        "收入 100% 来自订阅者，10 人团队、无外部投资人、自筹盈利。"
        "根据创始人官方博客披露的数据，该平台在 2022 年 6 月的 ARR "
        "达到 83,637 美元（$83,637 MRR = $1.003M ARR），"
        "当时已有 7,000+ 付费订阅、50,000+ 网站在用、月页面浏览超 10 亿次。",
    ],
    ("toutiao", "upvoty"): [
        "Upvoty 的打法是「先验证、后写码」：创始人 Mike Slaats "
        "用一天搭出落地页，数周内拿到 200+ 注册才动手做产品，"
        "上线即收费、不设免费档。之后靠两条腿走路——"
        "写「Alternatives to …」系列截流竞品的不满用户，"
        "再按用户点名接 GitHub / Slack / Jira 集成，集成接完就成了留存。"
        "它能 bootstrap 六年，前提是创始人前作 Vindy"
        "（荷兰/比利时家居改善平台，年收入约 $1M）一直在用利润养着它。",
    ],
    # ------------------------------------------------------------------- xhs
    ("xhs", "flodesk"): [
        "🚀 为什么能成？它切的是个超老的赛道，靠两招杀出来👇 "
        "① 定价：$19/月 无限发，竞品按订阅人数涨价，它按功能定价，"
        "「无限」就是卖点；② 审美：自定义字体/配色，"
        "把邮件工具做成了设计工具📨 "
        "而且它 2018 年被 YC 拒了之后自筹起步，上线第二周就盈利，"
        "到现在零外部融资！",
    ],
    ("xhs", "pdfai"): [
        "🔍 为什么能成？域名就是它的 SEO——pdf.ai 这个精准域名带来 50%+ 的"
        "搜索流量，获客几乎不花钱🔥 定价上先拿 $99 买断验证付费意愿，"
        "再从个人 $10/月 切到企业 $500/月，客单价翻 50 倍💰 "
        "创始人 Damon Chen 做了 9 年 Cisco 网络工程师后辞职，"
        "全程 0 员工 0 融资。",
    ],
    # -------------------------------------------------------------- bilibili
    ("bilibili", "pdfai"): [
        # 为什么能成 / 方法论（后面 1.2.3. 列表保留）
        "它的增长几乎是零成本的：pdf.ai 这个精准域名让 50%+ 的流量"
        "直接来自「PDF AI」相关搜索，域名本身就是 SEO。"
        "第二层是定价迁移——早期 $99 的一次性买断验证付费意愿，"
        "之后从个人 $10/月 转向企业 $500/月，客单价翻 50 倍。"
        "创始人 Damon Chen 做了 9 年 Cisco 网络工程师后辞职，"
        "全程 0 员工 0 融资。",
        # 我的判断
        "这个案例最容易被误读的是数字：「几百万美金 ARR」是创始人自述的量级，"
        "第三方汇编（2024 年底口径）约 $1.3-1.5M，引用时必须带口径，"
        "别当成精确值。另外它和 Chatbase（Yasser Elsaid，$9M ARR）"
        "是两个产品，网上经常混着说——别搞错。",
    ],
    ("bilibili", "upvoty"): [
        # 为什么能成 / 方法论（后面 1.2.3. 列表保留）
        "顺序是反着来的：先用一天搭个落地页，数周拿到 200+ 注册才动手写产品，"
        "而且上线即收费、不设免费档。发布之后靠两条腿走路——"
        "写「Alternatives to …」系列截流竞品的不满用户，"
        "再按用户点名接 GitHub / Slack / Jira 集成，集成接完就变成了留存。"
        "指标用 Baremetrics 管，重留存轻虚荣指标，获客成本压在 $500/月以内。",
    ],
}


# ---------------------------------------------------------------------------
# 顺带修掉的口径错误（一次性，精确替换，找不到原文就报错）
#
# headshotpro 的 $280-300K 月流水在 `candidates.json` 的 metric_note 里被明确
# 标为「第三方估算，无官方确认，不作案例数字」，xhs 草稿却写成「官方数据显示」，
# 还把它放进了标题——两处都是硬的：一处把不可靠口径冒充官方，
# 一处违反「标题禁金额」。jeujin 同案例的写法（明写「第三方估算，无官方确认」）
# 是对的，照那个口径改。
# ---------------------------------------------------------------------------
MISCALIBER_FIXES = [
    ("xhs", "headshotpro",
     "# 🚀 HeadshotPro：AI 职业证件照神器，solo 运营月入 $280K+，"
     "你还在用普通照片吗？",
     "# 🚀 HeadshotPro：AI 职业证件照神器，一个人做出 Fortune 500 "
     "团队都在买的产品"),
    ("xhs", "headshotpro",
     "📈 官方数据显示，HeadshotPro 上线以来累计营收 $10M+，"
     "生成 1,700 万张头像，月流水 $280-300K（$3.4-3.6M ARR）。"
     "这背后，离不开它独特的 SEO+联盟打法，以及强大的开发者/营销受众群体。🚀",
     "📈 官网 About 页披露的是累计口径：上线以来累计营收 $10M+，"
     "生成 1,700 万张头像，200,000+ 次拍摄，Fortune 500 团队在用"
     "（含 HubSpot 销售团队）。网上流传的「月流水 $280-300K」是第三方估算，"
     "官方没确认过，别拿它当数。🚀"),
]


def apply_miscaliber_fixes():
    """改口径冒充。返回实际修改的条数。"""
    n = 0
    for plat, cid, old, new in MISCALIBER_FIXES:
        path = os.path.join(DRAFTS, plat, cid + ".md")
        txt = io.open(path, encoding="utf-8").read()
        if new in txt:          # 已经改过，幂等
            continue
        if old not in txt:
            raise AssertionError("%s 里找不到待修原文（草稿被改过？）" % path)
        io.open(path, "w", encoding="utf-8", newline="\n").write(
            txt.replace(old, new, 1))
        n += 1
        print("  ✓ 口径纠错 %-9s %s" % (plat, cid))
    return n


def scan(platform=None):
    """找出所有含占位符的草稿文件。返回 [(platform, case_id, path)]。"""
    hits = []
    if not os.path.isdir(DRAFTS):
        return hits
    for plat in sorted(os.listdir(DRAFTS)):
        if platform and plat != platform:
            continue
        pdir = os.path.join(DRAFTS, plat)
        if not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(pdir, fn)
            txt = io.open(path, encoding="utf-8").read()
            if PLACEHOLDER in txt:
                hits.append((plat, fn[:-3], path))
    return hits


def patch_file(path, fills):
    """把文件里每一行含占位符的行，按顺序换成 fills 里的内容。

    返回替换处数。fills 数量与占位符行数不符直接抛错 —— 宁可不做，
    也不做一半。
    """
    lines = io.open(path, encoding="utf-8").read().split("\n")
    idx = [i for i, ln in enumerate(lines) if PLACEHOLDER in ln]
    if len(idx) != len(fills):
        raise AssertionError(
            "%s：占位符 %d 处，但只准备了 %d 段替换内容"
            % (path, len(idx), len(fills)))
    for i, new in zip(idx, fills):
        if PLACEHOLDER in new:
            raise AssertionError("%s：替换内容里还带着占位符" % path)
        lines[i] = new
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    return len(idx)


def main(argv):
    check_only = "--check" in argv

    # 1) 表本身的自检：每条内容都不许再含占位符
    for key, fills in FILLS.items():
        for s in fills:
            assert PLACEHOLDER not in s, key

    hits = scan()

    if check_only:
        print("含占位符的文件 %d 个：" % len(hits))
        for plat, cid, path in hits:
            n = sum(1 for ln in io.open(path, encoding="utf-8")
                    if PLACEHOLDER in ln)
            ready = len(FILLS.get((plat, cid), []))
            flag = "OK" if ready == n else "缺 %d 段" % (n - ready)
            print("  %-9s %-16s %d 处  准备 %d 段  %s"
                  % (plat, cid, n, ready, flag))
        return 0

    # 口径纠错独立于占位符：就算占位符全补完了，这一项也要照跑
    n_fix = apply_miscaliber_fixes()
    if n_fix:
        print("另修口径错误 %d 处。" % n_fix)

    if not hits:
        print("drafts/ 里已经没有占位符了，什么都不用做。")
        return 0

    done, files = 0, 0
    for plat, cid, path in hits:
        fills = FILLS.get((plat, cid))
        if not fills:
            print("  ⚠ 跳过 %s/%s：没准备内容" % (plat, cid))
            continue
        n = patch_file(path, fills)
        done += n
        files += 1
        print("  ✓ %-9s %-16s 补 %d 处" % (plat, cid, n))

    # 2) 收尾校验：整个 drafts/ 不许再出现占位符
    left = scan()
    print("\n补完 %d 个文件 / %d 处占位符。" % (files, done))
    if left:
        print("⚠ 仍有 %d 个文件含占位符：" % len(left))
        for plat, cid, path in left:
            print("   ", plat, cid)
        return 1
    print("✓ 复查：drafts/ 全目录已无「%s」。" % PLACEHOLDER)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
