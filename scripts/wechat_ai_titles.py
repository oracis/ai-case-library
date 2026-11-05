#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 LLM 给公众号产物批量改写「Name：MRR $X」机械模板标题（2026-11-04）。

为什么用 AI、以及「人工」到底在哪
----------------------------------
上一轮把 175 篇标题里的口径旁注清掉后，主体仍是千篇一律的
「产品名：MRR $835」。改写**动作**当然该交给模型（项目已接三级 LLM 链，
见 PLAYBOOK-llm-modelscope）；真正不能交给模型的是**事实裁定**：
  · 数字不许换算、不许编、不许把「累计数」当「当期数」；
  · 产品名必须原样保留（台账按标题认亲，改了会认不出 / 建重复草稿）；
  · 已发布文章一个字都不能动。
这三件事本文件全部落成**代码硬校验 + 抽检报告**，不需要谁敲 175 遍。

设计（借 `xhs_ai_titles.py` 的「AI 只出文案、本地硬校验」范式）
  · 每条只喂 `cases.json` 的事实原文（name / one_liner / what_it_does /
    verdict / metrics.headline），模型不许自己算数；
  · 只输出 JSON {"titles": {cid: 完整标题}}；不合格 → 带原因重试一次 →
    仍不合格就**保留原标题**，绝不硬写；
  · 覆盖表 `data/wechat_title_overrides.json` 落盘缓存，断点续跑不重复烧额度；
  · `make_article.build_titles()` 优先读覆盖表 ⇒ 重建产物不会退回模板。

范围（用户批准方案 ① 的 175 篇）
  · 默认 `--scope 175`：上一轮 baseline 里清过旁注、且不在 13 篇保护集合内的
    产物。实测这批 **0 篇已发布**。
  · `--scope all`：全部未带旁注的模板标题（190 篇），自动排除 5 篇已发布
    ⇒ 185 篇。比默认多 10 篇，须用户另行确认才跑。
  · 保护集合 = `wechat_published.status=published` ∪
    `publish_manifest.wechat.state=published` ∪
    `platform_posts(wechat,published)`，再加 13 篇上一轮保护对象。

用法：
  python scripts/wechat_ai_titles.py --check                    # 只读列计划
  python scripts/wechat_ai_titles.py --generate --limit 3 --dry  # 试跑 3 条
  python scripts/wechat_ai_titles.py --generate                # 全量生成覆盖表
  python scripts/wechat_ai_titles.py --apply                   # 备份后写入 h1
  python scripts/wechat_ai_titles.py --verify                  # 验收 + 零改动证明
  python scripts/wechat_ai_titles.py --check --diversify       # 看句式分桶与现状
  python scripts/wechat_ai_titles.py --generate --diversify    # 第二轮：句式多样化
  python scripts/wechat_ai_titles.py --generate --diversify --only <cid>  # 单条重做

第二轮（--diversify）：上一轮 175 条实测 0 个问句，模型把「句式互相错开」
当耳旁风。改成按 cid 确定性分桶、逐条指定目标句式、机器逐条校验；
句式不符 → 带原因重试 → 仍不符就**沿用上一版合格标题**（不丢条目）。
生成前另存一份「上一轮」回滚点 `_backups/wechat-titles-diversify-2026-11-04/`。
"""
import argparse
import glob
import html
import json
import os
import pathlib
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ai_verify as A                                  # noqa: E402
import atomicio                                        # noqa: E402
import clean_title_caliber as C                         # noqa: E402
import publish_guard as G                              # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARTICLES = os.path.join(ROOT, "out", "articles")
DATA = os.path.join(ROOT, "data")
OVERRIDES = os.path.join(DATA, "wechat_title_overrides.json")
BACKUP = os.path.join(ROOT, "_backups", "wechat-ai-titles-2026-11-04")
# 第二轮句式多样化的独立回滚点：存「上一轮 AI 标题」全文 + 覆盖表快照。
# 主 BACKUP 保留的是最初 `Name：MRR $X` 模板，--verify 仍用它做零改动基线。
DIVERSIFY_BACKUP = os.path.join(
    ROOT, "_backups", "wechat-titles-diversify-2026-11-04")
BASELINE = os.path.join(
    ROOT, "_backups", "title-caliber-2026-11-04", "baseline.json")
BATCH = 10
LIMIT = G.TITLE_MAX["wechat"]        # 64

# 模板形状：产品名：MRR $X / 产品名：近 30 天收入 $X
TEMPLATE = re.compile(r"^.+：(?:MRR|近 30 天收入)")
NUM = re.compile(r"\d[\d,.]*")

# 标题禁金额（2026-11-04 用户口径：**所有标题**都不出现金额）。
# ⚠ 判据要分得清「金额」和「规模/条数」：粗正则把
#   「100 万用户」「5 万个招聘页」「576 人付费」全当成金额误判过 26 条，
#   它们是可核验的规模事实，不在禁列。只认真正的货币形态：
#   货币符号 + 数字、数字 + 货币单位、「百万年薪」这类金额叙述。
# 与封面口径一致（`wechat_publish.cover_desc()` 也硬禁金额），
# 这样标题与封面第二行不会一个有钱一个没钱。
# 单一判据源：正则在 `publish_guard.TITLE_MONEY`，这里只做别名 ——
# 生成器（本脚本）与发布闸（publish_one 的 title 闸）必须同一条判据，
# 否则会出现「生成时放过、发布时拦下」的对不上。
MONEY = G.TITLE_MONEY


def has_money(t):
    return bool(MONEY.search(t or ""))


# 工商信息噪声：部分 `one_liner` / `what_it_does` 带「由 X 于 2026 年创立」
# 「官网 …」，拼进标题就变成公司简介而不是产品说明。
_BIZ_NOISE = re.compile(r"创立|成立|创始人|官网|团队成员|总部|注册资本|投资方")
# 标题**主体**写成工商简介的三种形状（比 _BIZ_NOISE 窄：不误伤
# 「当创始人懒得在官网上介绍自己」这类正常叙述）。
_BIZ_GATE = re.compile(
    r"(?:创立|成立)于?\s*\d|\d+\s*[年月日].{0,8}(?:创立|成立)|创始人\s*[A-Za-z]")
BAD_WORDS = (
    # 2026-11-05：生成器把「实时」二字去掉后，旁注变成「TrustMRR 核对」。
    # 两种形态都要拦 —— 旧串对应存量稿，新串对应新晋升的候选；只留旧串会
    # 让模型照抄新旁注时绕过本地校验。
    "TrustMRR 实时核对", "TrustMRR 核对", "非订阅制", "公开口径",
    "数据来源", "本站", "索引",
    "深度拆解", "万字长文", "保姆级", "一文搞懂", "建议收藏", "深度好文",
    "神器", "颠覆", "赋能", "底层逻辑", "闭环思维", "方法论全解",
    "海外小生意", "一人公司", "你也能做", "月入过万", "我赚了", "我的产品",
    "ydtgo.top", "加我", "私信", "扫码", "关注我",
)

SYSTEM = """你是公众号「万物解释者」的标题编辑。这个号写海外小生意的长文拆解：从一个具体反差讲起，把机制讲透，克制、不煽情、不喊口号。

任务：给清单里每条案例重写一个**中文标题**，替换掉现在千篇一律的「产品名：MRR $数字」机械模板。

硬性要求（每条都会被机器逐字校验，不合格直接作废）：
1. 只输出 JSON：{"titles": {"<id>": "<完整标题>", ...}}。不要解释、不要 markdown 围栏。
2. **必须原样完整保留「产品名」**（照抄清单第一行「产品名：」后面的写法，别按正文里的大小写改写，例如清单写 `3D-AGENT` 就不能写成 `3D-Agent`）。台账按标题认亲；少一个字都会对不上。不要求用冒号。
3. **数字只能照抄清单里出现过的原文**：不许自己算、不许换算单位、不许编年份/百分比/倍数。宁可整条不带数字。
3.2 **🚨 标题里一律不许出现金额**（用户 2026-11-04 口径，所有标题都适用）：`$`/`€`/`£`/`¥` + 数字、`美元`/`美金`/`万元`/`块钱`、`MRR $X`、`近 30 天收入 $X`、`百万年薪` 全部禁止，一个都不行。
   原因是金额本身**不准确且是动态的**（是抓取时点的快照，明天就变），写进标题必然过期，又和封面第二行对不上。
   → **标题的落点是「这个产品是什么、替谁解决什么问题」**，像产品说明一样把形态讲清楚：做什么动作、给谁用、替代了原来的什么做法。不要换成另一组数字来凑（用户数/收入/排名都别堆），用产品形态和场景说话。
3.5 **「近 30 天收入」「MRR」这类口径短语本身不是标题**，别让它当主语或整条标题。
4. 每条 12–64 字（中文/字母/数字/标点各算 1 字）。不要省略号、不要换行、不要引号包裹、不要书名号套整句。
5. **句式必须互相错开**：这一批里问句、判断句、场景句、对比句、白描句都要用到；**禁止全写成「产品名：短语」**，至少一半的标题不含中文冒号「：」。
6. 从「这个产品是做什么的、给谁用、替代了原来哪种做法」切入，用具体名词和动作，说人话。
   ⚠ 别写成官网说明书腔（「提供一体化解决方案」「致力于打造」「全方位赋能」）；像跟人当面介绍这个产品一样。
   好例（仅供参考，别照抄句式）：
     「1Lookup 把电话号码真假做成了一次 API 调用」
     「为什么一个查邮箱可达性的接口值得单独卖钱」
     「20-0 卖的不是阵容，是球迷那点参与感」
     「先有 15 万粉受众，后有产品」
7. 禁：空话（「海外小生意」「一人公司」「拆解一个」）、AI 套话（「深度拆解」「保姆级」「赋能」「神器」）、口径旁注（「TrustMRR 核对」「TrustMRR 实时核对」「非订阅制」「公开口径」）、把事实写成「我赚了」的第一人称业绩、自家域名与外链、引流、标题党、预测和「你也能做」式结论。
8. 不要评价产品好坏，不要写「值不值得抄」，只给一句能点开文章的标题。
9. **不得引入清单里没有的形态、客户、场景或形容词**：清单写「将 3D 打印机转化为绘图仪」，就不能写成「把旧打印机变成绘图仪」。标题里每个具体名词都要能在清单里找到。"""

USER_TMPL = """下面是每条案例的**公开事实原文**（数字只能从这里取）：

%s

请给每条一个完整标题，按第 1 条的格式只输出 JSON。"""

RETRY_HINT = """

⚠ 下面这些条目被机器拒了。请**只针对这些 id** 修，其它 id 不用给。
每行格式：`id | 拒收原因 | 你上一版写的标题`。
硬要求：
· 最常见的原因是**漏了产品名** —— 必须把清单里「产品名：」后面的写法**原样**放进标题（别改大小写）；
· 只改被指出的毛病，**别整条重写、别动已有数字**；
· 若原因是**含金额**，把金额整段删掉，改用用户数/时间跨度/具体动作，别换成「年入百万」这类叙述；
· 若原因是中文数字读法，改回不写数字或写原文里的非金额数字；
· 若原因是**句式不符**，严格按该条清单里的「目标句式」写，别又写成陈述句。
仍然只输出 JSON。
%s"""

# ---------- 句式多样化（2026-11-04 第二轮） ----------
# 上一轮 175 条全量跑完实测 **0 个问句**：SYSTEM 第 5 条「句式互相错开」
# 只是给整批的软建议，模型会系统性收敛成陈述句。这一轮改成
# **确定性分桶 + 逐条指定目标句式 + 机器逐条校验**：
#   · 句式不符 → 带原因重试一次 → 仍不符就**沿用上一版标题**（绝不丢条目）；
#   · 分桶按全局 sorted(cid) 下标取模，与批次无关，--only 单条重做也落在同一桶；
#   · 每 10 条一批恰好每种 2 条，天然避免「一批全是问句」的偷懒。
STYLE_BUCKETS = ("question", "contrast", "scene", "condition", "plain")
STYLE_LABEL = {
    "question": "疑问句｜必须用「？」收尾，且含「为什么 / 凭什么 / 谁 / 怎么 / 什么 / 吗 / 呢」之一；"
                "示例「为什么一个查邮箱可达性的接口值得单独卖钱？」",
    "contrast": "对比句｜用「不是 X，而是 Y」「卖的不是 X，是 Y」「先有 X，后有 Y」「X 之外还有 Y」这类转折",
    "scene": "场景句｜从使用者动作或具体场景起头（「当…」「把…」「用…」「每次…」），产品名放到后半句",
    "condition": "条件句｜含「只有…才」「如果」「一旦」「除非」「前提」「…之后」这类条件/时序",
    "plain": "白描判断句｜一句具体陈述，不含中文冒号「：」、不以「？」收尾，且不许与上一版逐字相同",
}
_Q_WORDS = ("为什么", "凭什么", "为何", "谁", "怎么", "什么", "哪", "吗", "呢",
            "难道", "多久", "多少")
_CONTRAST_PAT = re.compile(
    r"不是.{0,22}(?:而是|是|，)|并不|与其.{0,18}不如|先.{0,18}后|"
    r"比的不是|之外还有|换来的不是|表面.{0,12}实际|看起来.{0,12}其实|"
    r"不靠.{0,14}靠|没有.{0,14}而是")
_SCENE_PAT = re.compile(
    r"^(?:当|每次|把|用|从|在|一|打开|上传|收到|做完|进到|先|给|替|帮|让|"
    r"做|点|扫|填|拍|输入|描述|跑一遍|搜|查)", re.I)
_COND_PAT = re.compile(
    r"只有|如果|一旦|除非|前提|之后|才(?:能|收|有|算|让|会|开始|敢)|"
    r"得先|除非|不然|否则")
# 白描句的谓词表：只为拦「产品名 + 名词短语」这种标签式标题，
# 不负责判断文风。刻意不收「管/理/更/新」—— 正是要拦住
# 「…持续更新财务管理的工具」这类没有谓词的短语。
_PRED_PAT = re.compile(
    r"把|用|让|替|为|做|卖|收|换|给|靠|拆|接|跑|搬|省|拼|装|算|查|抓|盯|"
    r"放|拿|赚|生成|整理|变成|做成|替代|砍|压|搭|修|发|写|读|看|听|说|"
    r"找|存|同步|接住|打通|排|扫|填|拍|输入|描述")


def style_buckets(cids):
    """{cid: bucket}，按全局 sorted 下标取模 —— 稳定、均匀、可复现。"""
    out = {}
    for i, cid in enumerate(sorted(cids)):
        out[cid] = STYLE_BUCKETS[i % len(STYLE_BUCKETS)]
    return out


def check_style(t, bucket, prev="", name=""):
    """句式是否命中分配的桶。返回拒绝原因（空串 = 通过）。

    ⚠ 判据刻意宽松（每类 3–6 个特征、命中任一即过）：上一轮「形状闸」
    全量误拒 17 次、真拦 0 次的教训。句式不符只让该条**沿用上一版**，
    不会把好标题判掉，也不会硬写出坏标题。
    """
    t = (t or "").strip()
    if "｜" in t:
        return "机械拼接符「｜」（本地补名痕迹），不是自然标题"
    if bucket == "question":
        if not re.search(r"[？?]\s*$", t):
            return "句式不符：疑问句要用「？」收尾"
        if not any(w in t for w in _Q_WORDS):
            return "句式不符：疑问句要带疑问词（为什么/凭什么/谁…）"
    elif bucket == "contrast":
        if not _CONTRAST_PAT.search(t):
            return "句式不符：对比句要有「不是 X，而是 Y」这类转折"
        # 「3D-AGENT 卖的不是工具，而是效率」—— 转折后半段只有 2 个字，
        # 等于什么也没说。要求后半段至少 5 字。
        for m in re.finditer(r"而是([^，,。；;？?]{1,20})", t):
            if len(m.group(1).strip()) < 5:
                return "句式不符：对比句后半段太虚（「而是效率」这种没交代是什么）"
    elif bucket == "scene":
        hit = bool(_SCENE_PAT.search(t))
        if not hit and name and name.lower() in t.lower():
            hit = t.lower().find(name.lower()) >= 8   # 产品名确实被挪到后半句
        if not hit:
            return "句式不符：场景句要从动作/场景起头，别拿产品名开头"
    elif bucket == "condition":
        if not _COND_PAT.search(t):
            return "句式不符：条件句要有「只有…才 / 如果 / 之后」这类标记"
    elif bucket == "plain":
        # 2026-11-04：用户口径「标题 = 产品的简要介绍」⇒「名称：一句说明」
        # 是最自然的产品说明形态。冒号本身不是模板病（模板病是
        # 「名称：MRR $X」，已由 MONEY 闸拦）；原先禁冒号把模型逼进本地
        # 降级，降级产物反而是说明书腔「为…提供一体化解决方案」，更差。
        if re.search(r"[？?]\s*$", t):
            return "句式不符：白描句不要问号"
        if re.search(r"(?:从哪来|从何而来|凭什么|为什么|怎么办|是谁)\s*[。.]?$", t):
            return "句式不符：白描句别用疑问尾巴收尾（要么补问号改问句，要么改陈述）"
        # 试跑抓到「Account & Expense Manager 持续更新财务管理的工具」——
        # 这是「产品名 + 名词短语」标签，不是句子。白描必须带动作或判断。
        if not _PRED_PAT.search(t):
            return "句式不符：白描句要成句（含动作/判断），别只写「产品名 + 名词短语」"
        if prev and t == prev.strip():
            return "句式不符：与上一版逐字相同（等于没改）"
    return ""


def fix_question_shape(t, name):
    """question 桶专用形状修正（**只动标点/前缀，不改事实**）。

    20 条试跑实测 question 桶 0 命中，模型两种系统性毛病：
      · 问号写在中间：「为什么球迷愿意掏钱？MRR $835」（还常漏产品名）；
      · 补了产品名又丢问号：「为什么持续更新的财务工具能卖出 MRR $632？」。
    与 `inject_name` 的 `｜` 硬拼接不同，这里用的是自然中文形态：
    漏名补「Name：」前缀（问句桶允许冒号），问号归位到句末。
    ⚠ 修完必须重跑 `validate()` 全部判据，不是无条件放行。
    """
    t = (t or "").strip().strip('"“”「」').strip()
    if not t:
        return t
    if name and name.lower() not in t.lower():
        t = "%s：%s" % (name, t)
    if re.search(r"[？?]", t[:-1]):            # 问号在中间 ⇒ 换逗号、末尾补问号
        t = t.replace("？", "，").replace("?", "，").rstrip("。.，,；; ")
        t = t.rstrip("？?") + "？"
    elif not re.search(r"[？?]\s*$", t):       # 有疑问词却没问号 ⇒ 补上
        t = t.rstrip("。.，,；; ") + "？"
    return re.sub(r"，{2,}", "，", t)


def style_report(titles, cids=None):
    """验收用：按句式给现存标题分类，返回 {bucket: n}。"""
    buckets = style_buckets(cids or list(titles))
    out = dict((k, 0) for k in STYLE_BUCKETS)
    out["沿用/未命中"] = 0
    for cid, t in titles.items():
        b = buckets.get(cid)
        if not b:
            continue
        out[b if not check_style(t, b) else "沿用/未命中"] += 1
    return out


DIVERSIFY_SYSTEM = """

【本轮额外要求：逐条指定句式】
每条素材里都有「目标句式：」，这是**逐条硬约束**，机器会一条条验：
· 疑问句必须以「？」收尾且带疑问词；对比句必须有「不是 X，而是 Y」这类转折；
· 场景句从使用者动作起头、产品名放后半句；条件句要有「只有…才 / 之后」；
· 白描句不含中文冒号、不问句、不许与「上一版标题」逐字相同。
素材里的「上一版标题」是上一轮已通过事实校验的标题：**事实、产品名、金额照抄可以，
整句照抄不行** —— 这一轮只换句式与切入点。
⚠ 不要把所有条目都写成陈述句；这是本轮唯一目的。
⚠ 别为了凑句式自己换名词或加形容词：上一版标题里的具体名词可以照抄
（如「消费级 3D 打印机」不能写成「旧打印机」），素材里没有的定性词一律不许加。"""


def numset(s):
    """抽取一段文本里所有阿拉伯数字 token（去千分位），用于「数字必须来自原文」。"""
    out = set()
    for x in NUM.findall(s or ""):
        x = x.replace(",", "").rstrip(".")
        if x:
            out.add(x)
    return out


def load_cases():
    import case_store as CS
    return CS.load_by_id()


def published_ids():
    """只看 published 状态的三源并集；草稿/计划记录不算「已发布」。"""
    out = set()
    wp = atomicio.read_json(os.path.join(DATA, "wechat_published.json"), {}) or {}
    out |= {k for k, v in wp.items()
            if isinstance(v, dict) and v.get("status") == "published"}
    mf = atomicio.read_json(os.path.join(DATA, "publish_manifest.json"), {}) or {}
    out |= {k for k, v in mf.items()
            if isinstance(v, dict) and isinstance(v.get("wechat"), dict)
            and v["wechat"].get("state") == "published"}
    db = os.path.join(DATA, "ledger.db")
    if os.path.isfile(db):
        try:
            conn = sqlite3.connect(
                pathlib.Path(db).absolute().as_uri() + "?mode=ro", uri=True)
            out |= {r[0] for r in conn.execute(
                "SELECT case_id FROM platform_posts "
                "WHERE platform='wechat' AND state='published'")}
            conn.close()
        except sqlite3.Error:
            pass
    return out


def legacy_protected():
    """上一轮按批准没动的 13 篇 = 用**清理前**的 h1 复算五源保护集合。

    ⚠ 不能拿 baseline 里的「核对」二字直接判：baseline 存的是清理前
    188 篇**全部**带旁注的 h1，那样会把 188 篇都误当保护对象。
    """
    base = atomicio.read_json(BASELINE, {}) or {}
    titles = {cid: C.plain(inner) for cid, inner in base.items()}
    return C.protected_ids(titles) if titles else set()


def protected_ids():
    """已发布 ∪ 上一轮 13 篇保护对象。"""
    return published_ids() | legacy_protected()


def scan_titles():
    """{cid: h1 纯文本}，全仓产物。"""
    out = {}
    for p in sorted(glob.glob(os.path.join(ARTICLES, "*.html"))):
        inner = C.h1_inner(C.read(p))
        if inner:
            out[os.path.basename(p)[:-5]] = C.plain(inner)
    return out


def targets(scope="175"):
    """返回 [(cid, old_title)]，按批准范围 + 保护集合过滤。

    scope=money（2026-11-04 新增）：**只看「当前标题仍带金额」的未保护产物**，
    覆盖表内外都算、也不要求是 `Name：MRR $X` 模板 —— 用户口径是
    「所有标题不出现金额」，人工写过的带金额标题（如 jobright）同样要改。
    """
    cur = scan_titles()
    # 保护集合取并集：published ∪ 上一轮 13 篇 ∪ 「台账里存过同名标题」。
    # 最后一项是 `clean_title_caliber.protected_ids()` 的口径 —— 改标题会
    # 让台账认亲错位，宁可少改。实测 `growingupvideo` / `homy` 只靠这一项
    # 才被保护住（它们有台账记录，但状态字段不是 published）。
    # ⚠ 用当前 h1 复算会有模板撞车的假阳性（34 条 `Name：MRR $X` 互撞），
    #   但假阳性只会「多保护」不会「误改」，且其中真带金额的只有 2 条。
    prot = protected_ids()
    if scope == "money":
        # ⚠ money 分支**不叠加** `C.protected_ids(cur)`（台账标题文本反查）：
        # 实测 growingupvideo / homy 只是公众号**草稿**
        # （wechat_published.status=draft，有 appmsgid 未发表），文本反查
        # 命中 ≠ 已发表；而用户口径是「所有标题不出现金额」，草稿标题该改。
        # 真已发布仍由 published_ids() ∪ legacy_protected() 挡住。
        pass
    if scope == "money":
        allowed_ids = {c for c, t in cur.items()
                       if c not in prot and has_money(t)}
    elif scope == "175":
        base = atomicio.read_json(BASELINE, {}) or set()
        allowed_ids = set(base) - prot
    else:
        allowed_ids = set(cur) - prot
    out = []
    ov = load_overrides()
    for cid in sorted(allowed_ids):
        t = cur.get(cid, "")
        if not t or "核对" in t:
            continue
        if scope != "money" and not TEMPLATE.match(t) and cid not in ov:
            continue                       # 只改「Name：MRR $X」机械模板
        out.append((cid, t))
    return out


def material_of(cid, c):
    """喂给模型的事实原文。数字白名单 = 这段文本里的全部数字。"""
    m = c.get("metrics") or {}
    head = C.clean_h1(str(m.get("headline") or ""))
    one = str(c.get("one_liner") or "")
    wd = c.get("what_it_does") or ""
    if isinstance(wd, list):
        wd = wd[0] if wd else ""
    wd = re.split(r"[。！？]", str(wd))[0][:110]
    vd = c.get("verdict") or ""
    if isinstance(vd, list):
        vd = vd[0] if vd else ""
    vd = re.split(r"[。！？]", str(vd))[0][:100]
    name = str(c.get("name") or cid)
    txt = ("[%s] 产品名：%s\n业务：%s\n公开数字原文：%s\n形态：%s\n一句判断：%s"
           % (cid, name, one[:120], head[:120], wd, vd))
    return txt, name


def build_listing(batch, cases, styles=None, prev=None):
    rows = []
    for cid, _ in batch:
        txt, _ = material_of(cid, cases.get(cid, {}))
        if styles:
            _b = styles.get(cid)
            txt += "\n目标句式：%s" % (
                STYLE_LABEL[_b] if _b else
                "产品说明句｜一句讲清这个产品做什么、给谁用，不要写成公司简介")
        if prev and prev.get(cid):
            txt += "\n上一版标题：%s（事实可照抄，整句不许照抄）" % prev[cid]
        rows.append(txt)
    return USER_TMPL % "\n\n".join(rows)


def call_ai(batch, cases, retry_notes=None, styles=None, prev=None):
    sysmsg = SYSTEM
    if styles:
        sysmsg += DIVERSIFY_SYSTEM
    if retry_notes:
        sysmsg += RETRY_HINT % "\n".join(
            "%s | %s | 上一版：%s" % (cid, why, prev_text)
            for cid, why, prev_text in retry_notes)
    raw = A.llm(
        [{"role": "system", "content": sysmsg},
         {"role": "user", "content": build_listing(batch, cases, styles, prev)}],
        temperature=0.8, max_tokens=3000)
    obj = A.extract_json(raw)
    return (obj.get("titles") or {}) if isinstance(obj, dict) else {}


def validate(cid, t, name, allowed, seen, raw_text="", style=None, prev="",
             no_money=True):
    """硬校验。返回拒绝原因（空串 = 通过）。

    🚨 中文数字这一栏是 3 条试跑逼出来的：模型把 `$835` 读成
    「八三一美元」、`$4,666` 约成「四千多美元」、`$2,179` 约成
    「两千美元」—— 全都没有阿拉伯数字，`numset()` 子集校验完全拦不住。
    红线是「不许换算 / 不许把累计数当当期数」，所以中文数字串与
    「原文里没有的中文金额单位」一律拒收。

    🚨 `no_money` 默认 True（2026-11-04 用户口径：所有标题不出现金额）。
    这是**产品口径**不是事实判据，所以单列一个开关：既有回归锁里那些
    刻意带 `$835` 去测「中文数字换算」「糊块」的用例显式传 False，
    否则它们测的规则会被金额闸抢先拦掉。

    `style` 只在 --diversify 一轮传入（默认 None ⇒ 不影响既有回归锁）：
    句式不符不是事实错误，重试仍不符就沿用上一版合格标题。
    """
    if not t:
        return "AI 没给"
    t = t.strip().strip('"“”「」').strip()
    if len(t) < 12:
        return "太短(%d 字)：%s" % (len(t), t)
    if len(t) > LIMIT:
        return "超长(%d>%d)：%s" % (len(t), LIMIT, t[:70])
    if no_money and has_money(t):
        return "标题含金额（本项目口径：标题一律不出现金额）：%s" % t[:70]
    if name:
        # 产品名变体容忍：`myterrace.net` 被模型写成 `myterrace-net`、
        # `AI Toolbox` 写成 `AIToolbox`。归一化掉 . - _ 和空格再比，
        # 「把产品名翻译成中文」（AI Toolbox → AI 工具站）仍然算缺名。
        _norm = lambda x: re.sub(r"[.\-_\s]", "", x.lower())   # noqa: E731
        if _norm(name) not in _norm(t):
            return "缺产品名「%s」：%s" % (name[:24], t[:70])
    rest = t.replace(name, "", 1) if name else t
    if re.search(r"[一二三四五六七八九十百千万亿两零〇]{2,}", rest):
        return "中文数字串（疑似把金额改成读法/约数）：%s" % t[:70]
    for unit in ("美元", "美金", "万元", "万美元", "块钱", "刀"):
        if unit in t and unit not in (raw_text or ""):
            return "中文金额单位「%s」不在素材原文" % unit
    # 句式多样化试跑抓到：「把消费级 3D 打印机改成绘图仪」被写成
    # 「把**旧**打印机接上软件」。为了凑场景句自己加定性形容词 = 事实漂移。
    # 只在素材原文里也找不到该词时拒，避免误伤原文本就写着的「贵/免费」。
    for adj in ("旧", "老旧", "过时", "二手", "昂贵", "轻松"):
        if adj in t and adj not in (raw_text or ""):
            return "定性形容词「%s」不在素材原文" % adj
    extra = numset(t) - allowed
    if extra:
        return "数字不在素材原文（疑似换算/编造）：%s" % ",".join(sorted(extra)[:4])
    # 工商简介闸：模型会把「公司简介」当标题 —— 「Clear Food 成立于 2026 年
    # 6 月 16 日」「Landon Ritchie 12月16日创立 Chartvio」「AI Toolbox …
    # 创始人 Adi Leviim（2–5 人团队）」。用户要的是**产品说明**，不是工商信息。
    # 判据只认「日期 + 创立/成立」「创始人 + 英文名」，不误伤
    # 「当创始人懒得在官网上介绍自己」这类正常叙述。
    if _BIZ_GATE.search(t):
        return "标题写成工商简介（成立/创立/创始人），不是产品说明：%s" % t[:70]

    for w in BAD_WORDS:
        if w in t:
            return "禁词「%s」" % w
    # 英文词闸（2026-11-04 全量跑逼出来的）：模型会把**别的产品名**写进标题
    # —— 「AgentsKit」被写成 `claudethings 是帮开发者省时间的 AI 工具`，
    # 「Calsy」被写成 `Calzy`。这类幻觉产品名/错拼过得了所有既有判据
    # （没数字、没金额、含产品名靠本地补名也能过），只能靠白名单拦：
    # 标题里的英文词必须在「素材原文 ∪ 产品名」里出现过。
    known = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{3,}",
                                           (raw_text or "") + " " + (name or ""))}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{3,}", t):
        if w.lower() not in known:
            return "英文词「%s」不在素材原文（疑似幻觉产品名/错拼）" % w
    # 真正的坏形状是「口径 + 金额」零分隔糊块：
    #   「已跑出近30天$31,677」「近 30 天已验证收入$31,677」。
    # ⚠ 不能拿「汉字 + 近 + 数字」判：「拿到近 30 天收入 $924」
    #   「AgentsKit 近 30 天收入 $1,139」都是合法中文（全量实测误拒 17 次、
    #   真拦 0 次）。判「汉字紧接 `$`（中间零空格）」最准：合法标题的
    #   `$` 前一定是空格或 `MRR ` 之类，只有糊块会写成「收入$31,677」。
    if (re.search(r"[一-鿿]\$", t)
            or re.search(r"近\s?\d+\s?天[^\s，,。；;：:]{0,4}\$", t)):
        return "口径与金额连写成糊块（缺分隔）：%s" % t[:70]
    for f in G.scan_title("wechat", t):
        if f["level"] == "block":
            # 守卫里也有金额闸；no_money=False 是给「测别的判据」的用例
            # 开口子，这里要一致放行，否则自己绕过自己。
            if f["rule"] == "标题带金额" and not no_money:
                continue
            return "守卫 block：%s" % f["rule"]
        if f["rule"] in ("标题带口径旁注", "AI 味套话", "空壳/机械模板标题"):
            return "守卫 warn：%s" % f["rule"]
    if style:
        miss = check_style(t, style, prev, name)
        if miss:
            return miss
    if t in seen:
        return "与 %s 撞车" % seen[t]
    return ""


def normalize_name(t, name):
    """把产品名大小写变体统一回 `cases.json` 的原文，保证台账能按标题认亲。

    实测 `3D-AGENT`（name）在 one_liner / verdict 里被写成 `3D-Agent`，
    模型跟着正文写 ⇒ 校验放过会留下认亲错位，硬拒又白丢一条好标题。
    """
    if not name or name in t:
        return t
    return re.sub(re.escape(name), name, t, flags=re.I)


def inject_name(t, name, cid):
    """模型反复漏写产品名时的本地兜底（借 `xhs_ai_titles.assemble` 的思路）。

    只在「重试仍不合格且唯一毛病是缺产品名」时触发，且补完还要重新过
    `validate()` 全部判据 —— 不是无条件放行。三种插入形态按 cid 稳定选，
    避免兜底的那些条目又变成同一个「产品名：短语」模板。
    """
    t = (t or "").strip().strip('"“”「」').strip()
    if not name or name.lower() in t.lower():
        return normalize_name(t, name)
    # 2026-11-04：**不再用「｜」拼接**。`check_style` 把「｜」判成
    # 机械拼接痕迹（实测拼出「ADAWCAG｜一个…从哪来」），整条被拒后又掉进
    # 质量更差的本地降级。改用两种自然中文形态：
    #   · 场景/条件开头 ⇒ 名字后置（「一旦把腹肌照拍给 AI，AbMaxx」）
    #   · 其余          ⇒ 「名字：一句说明」（正是用户要的「产品说明」形态）
    if _SCENE_PAT.search(t):
        return "%s，%s" % (t.rstrip("。！？"), name)
    return "%s：%s" % (name, t)


def local_no_money(c, name):
    """AI 两轮都带金额、且上一版本身也带金额时的**本地降级**。
    不是再套一遍模板 —— 素材只有 `cover_desc / one_liner / what_it_does`
    三个字段，按**子句级**丢掉含金额的子句后拼成「产品名：一句产品说明」。
    与 `wechat_publish.cover_desc()`（封面第二行）同源，所以标题和封面
    天然一致，不会一个有钱一个没钱。

    ⚠ 只做「减法 + 拼接」，不造事实：所有词都来自案例原文。
    返回空串 = 没有可用素材（上层按「保留原题 + 报错」处理）。
    """
    # 只用「一句话定位」类字段：`what_it_does` 虽然更具体，但常带
    # 「由 X 于 2026 年 3 月创立」「官网…」这类工商信息，拼进标题就是
    # 「AgentsKit：…由创始人 Mohit Vaswani 于 2026 年 7 月 1 日创立」
    # （实测 18 条降级里 7 条长这样），比不写还糟。
    for key in ("cover_desc", "one_liner"):
        v = c.get(key) or ""
        if isinstance(v, list):
            v = v[0] if v else ""
        # ⚠ 英文逗号不能一律当分隔符：千分位就是英文逗号，`$12,775`
        # 会被切成 `$12` 和 `775`，前者被丢、后者残留成「775，为社区…」
        # （实测）。只在**后面不跟数字**时才算分隔。
        parts = [s.strip() for s in re.split(r"[，。；;、]|,(?!\d)", str(v))
                 if s.strip() and not has_money(s)
                 and not _BIZ_NOISE.search(s)
                 and not re.search(r"[一二三四五六七八九十百千万亿两零〇]{2,}", s)]
        if not parts:
            continue
        desc = "，".join(parts)
        if name and name.lower() in desc.lower():
            cand = desc
        elif name:
            cand = "%s：%s" % (name, desc)
        else:
            cand = desc
        cand = cand[:LIMIT].rstrip("，,、：:。 的")
        # 截断可能停在半个词上（「…官网」）；再收一次尾
        cand = re.sub(r"(?:由|的|和|与|为|在|把|是)$", "", cand).rstrip("，,、：: ")
        if len(cand) >= 12:
            return cand
    return ""


def load_overrides():
    d = atomicio.read_json(OVERRIDES, {}) or {}
    return d.get("titles", {}) if isinstance(d, dict) else {}


def save_overrides(titles, meta=None, extra=None):
    base = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": A.AI_MODEL,
            "endpoint": A.LAST_ENDPOINT.get("tag", "")}
    base.update(extra or {})
    obj = {"titles": dict(sorted(titles.items())), "meta": meta or base}
    atomicio.write_json(OVERRIDES, obj, indent=2, sort_keys=False, newline="")


def snapshot_prev_round(prev_titles, cids):
    """生成前留一份「上一轮」回滚点（只写一次，断点续跑不覆盖）。

    主 BACKUP 里是**最初模板**，不方便回滚到上一轮 AI 标题；这一份
    连产物带覆盖表一起存，出问题可直接拷回。
    """
    marker = os.path.join(DIVERSIFY_BACKUP, "overrides-before.json")
    if os.path.isfile(marker):
        return 0
    os.makedirs(DIVERSIFY_BACKUP, exist_ok=True)
    atomicio.write_json(marker, {"titles": dict(sorted(prev_titles.items()))},
                        indent=2, newline="")
    import shutil
    n = 0
    for cid in cids:
        src = os.path.join(ARTICLES, cid + ".html")
        dst = os.path.join(DIVERSIFY_BACKUP, "out__articles__%s.html" % cid)
        if os.path.isfile(src) and not os.path.isfile(dst):
            shutil.copy2(src, dst)
            n += 1
    print("上一轮回滚点：%d 份产物 + 覆盖表快照 → %s"
          % (n, os.path.relpath(DIVERSIFY_BACKUP, ROOT)))
    return n


def cmd_check(args):
    tg = targets(args.scope)
    ov = load_overrides()
    todo = (list(tg) if args.diversify
            else [(c, t) for c, t in tg if c not in ov or args.force])
    print("范围 scope=%s ｜ 模板标题 %d 篇 ｜ 已生成 %d ｜ 待生成 %d"
          % (args.scope, len(tg), len(tg) - len(todo), len(todo)))
    print("保护集合（已发布 + 13 篇）：%d 篇" % len(protected_ids()))
    print("已发布三源：%d 篇 ｜ 与本次范围求交：%d 篇"
          % (len(published_ids()),
             len(published_ids() & set(c for c, _ in tg))))
    if args.diversify:
        st = style_buckets([c for c, _ in tg])
        print("目标句式：" + " / ".join(
            "%s %d" % (k, sum(1 for v in st.values() if v == k))
            for k in STYLE_BUCKETS))
        print("现状命中：" + " / ".join(
            "%s %d" % (k, v) for k, v in style_report(ov).items()))
    for cid, t in todo[:6]:
        print("  %-42s %s" % (cid[:42], t[:70]))
    return 0


def cmd_generate(args):
    cases = load_cases()
    tg0 = targets(args.scope)
    ov = load_overrides()
    # diversify 一轮：ov 是「上一版合格标题」，句式不符就沿用，绝不丢条目。
    fallback = dict(ov)
    tg = tg0
    if args.only:
        # 定向重做：把这几条从缓存里摘掉，只重生成它们。
        # 用途：验收发现某条标题有断面/事实错，不必为了 1 条烧 175 条额度。
        ids = {x.strip() for x in args.only.split(",") if x.strip()}
        ov = {k: v for k, v in ov.items() if k not in ids}
        # --only 是「按 id 强制重做」：不在本 scope 范围内的 id 也纳入
        # （用于修「已无金额但写成公司简介」这类 scope 之外的问题条目）。
        cur_all = scan_titles()
        in_tg0 = {c for c, _ in tg0}
        tg = ([(c, t) for c, t in tg0 if c in ids]
              + [(c, cur_all.get(c, "")) for c in sorted(ids)
                 if c not in in_tg0 and cur_all.get(c)])
    # styles / prev 必须按**最终** tg 算：--only 会引入 scope 之外的 id，
    # 用 tg0 算会让它们没有句式桶（build_listing 直接 KeyError）也没有「上一版」。
    styles = style_buckets([c for c, _ in tg]) if args.diversify else None
    # prev = 「上一版」= 覆盖表 ∪ 当前 h1（覆盖表外的历史产物没进过覆盖表，
    # 但它们的旧题同样要算「上一版」，否则「禁照抄」判据对它们失效、
    # 「带金额就不能沿用」的降级判断也会漏）。
    prev = dict(fallback)
    for _cid, _t in tg:
        prev.setdefault(_cid, _t)
    if args.diversify:
        todo = list(tg)                     # 整轮重做句式，忽略缓存
        if not args.dry:
            snapshot_prev_round(prev, [c for c, _ in tg])
    else:
        if args.force:
            ov = {}
        todo = [(c, t) for c, t in tg if c not in ov or args.force]
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("没有待生成条目（覆盖表已齐）。加 --force 可重生成。")
        return 0
    print("待生成 %d 条 ｜ 批次 %d ｜ 主端点 %s ｜ dry=%s ｜ 句式多样化=%s"
          % (len(todo), BATCH, A.AI_MODEL, bool(args.dry), bool(args.diversify)))
    if styles:
        print("目标句式：" + " / ".join(
            "%s %d" % (k, sum(1 for v in styles.values() if v == k))
            for k in STYLE_BUCKETS))
    accepted, rejected = {}, []
    merged = dict(fallback)               # 所有批次都失败时 final 也要有定义
    degraded = []                     # 本地降级成功但 AI 两轮都不合格：留原因供复盘
    local_fixed = 0
    seen = {}
    # 定向重做（--only）时豁免**自己**的旧题：否则模型重答同一素材、
    # 给出与上一版相近的答案就判「撞车」，等于被自己的旧稿堵死
    # （实测整轮重跑 20 条里 10 条撞车、全部沿用旧题）。
    exempt = set()
    if args.only:
        exempt = {prev.get(c) for c in
                  {x.strip() for x in args.only.split(",") if x.strip()}}
        exempt.discard(None)
    exempt.discard("")
    for t in prev.values():
        if t in exempt:
            continue
        seen[t] = "(上一版，不许照抄)" if args.diversify else "(已有覆盖)"
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            got = call_ai(batch, cases, styles=styles, prev=prev)
        except Exception as e:                       # noqa: BLE001
            print("  ✗ 批次 %d 调用失败：%s" % (i // BATCH + 1, str(e)[:120]))
            rejected.extend((cid, "LLM 调用失败：%s" % str(e)[:80])
                            for cid, _ in batch)
            continue
        notes = []
        for cid, old in batch:
            txt, name = material_of(cid, cases.get(cid, {}))
            allowed = numset(txt)
            why = validate(cid, str(got.get(cid) or ""), name, allowed, seen, txt,
                           style=styles.get(cid) if styles else None,
                           prev=prev.get(cid, "") if prev else "")
            if why:
                notes.append((cid, why, str(got.get(cid) or "")))
            else:
                t = normalize_name(
                    str(got[cid]).strip().strip('"“”「」').strip(), name)
                accepted[cid] = t
                seen[t] = cid
        # 不合格的带原因再给一次机会
        if notes:
            sub = [(cid, old) for cid, old in batch
                   if cid in {n[0] for n in notes}]
            try:
                got2 = call_ai(sub, cases, notes, styles, prev)
            except Exception as e:                  # noqa: BLE001
                got2 = {}
            still = []
            for cid, why, _prev_answer in notes:
                txt, name = material_of(cid, cases.get(cid, {}))
                sty = styles.get(cid) if styles else None
                why2 = validate(cid, str(got2.get(cid) or ""), name,
                                numset(txt), seen, txt, style=sty,
                                prev=prev.get(cid, "") if prev else "")
                if why2:
                    # question 桶专用：问号归位 + 漏名补自然前缀，修完全量复校。
                    # 20 条试跑里 question 桶 0 命中，纯靠 prompt 救不回来。
                    if args.diversify and sty == "question":
                        src = str(got2.get(cid) or _prev_answer or "")
                        cand = fix_question_shape(src, name)
                        if cand != src:
                            why3 = validate(cid, cand, name, numset(txt), seen, txt,
                                            style=sty,
                                            prev=prev.get(cid, "") if prev else "")
                            if not why3:
                                accepted[cid] = cand
                                seen[cand] = cid
                                local_fixed += 1
                                continue
                    # 兜底：唯一毛病是漏产品名时，本地把名字插回去再全量复校。
                    # 实测 `20-0`（纯数字名）模型两轮都漏，重试提示无效。
                    # 2026-11-04：`inject_name` 改成「名字：说明 / 句子，名字」
                    # 两种自然形态后，diversify 轮**也启用**它 —— 之前禁用是
                    # 因为「｜」会拼出机械断面，导致大批条目掉进质量更差的
                    # 本地降级（全量实测 18 条降级里 15 条就是这个原因）。
                    if why2.startswith("缺产品名"):
                        src = str(got2.get(cid) or _prev_answer or "")
                        cand = inject_name(src, name, cid)
                        why3 = validate(cid, cand, name, numset(txt), seen, txt,
                                        style=sty,
                                        prev=prev.get(cid, "") if prev else "")
                        if not why3:
                            accepted[cid] = cand
                            seen[cand] = cid
                            continue
                        why2 = "%s；本地补名仍不合格：%s" % (why2, why3)
                    # 上一版**本身不合规**（带金额 / 写成公司简介 / 超长）时
                    # 不能拿它当降级目标 ——「拒收 ⇒ 沿用上一版」等于把问题
                    # 原样留下来。改用本地子句级拼装；不强制句式
                    # （无金额、是产品说明是硬目标，句式是软的）。
                    _pv = prev.get(cid, "")
                    if (not _pv) or has_money(_pv) or len(_pv) > LIMIT \
                            or _BIZ_GATE.search(_pv):
                        cand = local_no_money(cases.get(cid, {}), name)
                        why3 = (validate(cid, cand, name, numset(txt), seen, txt)
                                if cand else "素材里没有可用的非金额子句")
                        if not why3:
                            accepted[cid] = cand
                            seen[cand] = cid
                            local_fixed += 1
                            degraded.append((cid, why2))
                            continue
                        why2 = "%s；本地去金额降级也失败：%s" % (why2, why3)
                    still.append((cid, "%s；重试仍不合格：%s" % (why, why2)))
                else:
                    t = normalize_name(
                        str(got2[cid]).strip().strip('"“”「」').strip(), name)
                    accepted[cid] = t
                    seen[t] = cid
            rejected.extend(still)
        colon = sum(1 for cid, _ in batch
                    if "：" in accepted.get(cid, "")[:24])
        print("  批次 %2d/%2d  接受 %2d  拒 %2d  冒号句 %2d  %.1fs"
              % (i // BATCH + 1, (len(todo) + BATCH - 1) // BATCH,
                 sum(1 for cid, _ in batch if cid in accepted),
                 len([1 for cid, _ in batch if cid not in accepted]),
                 colon, time.time() - t0))
        if not args.dry:
            merged = dict(fallback if args.diversify else ov)
            merged.update(accepted)
            extra = None
            if args.diversify:
                extra = {"diversify": True,
                         "style_targets": {k: sum(1 for v in styles.values()
                                                  if v == k)
                                           for k in STYLE_BUCKETS}}
            save_overrides(merged, extra=extra)
    kept = len(todo) - len(accepted)
    print("\n合计接受 %d（其中问句形状修正 %d）｜ 沿用上一版 %d ｜ 耗时 %.1fs ｜ 实际端点 %s/%s"
          % (len(accepted), local_fixed, kept, time.time() - t0,
             A.LAST_ENDPOINT.get("tag", "-"), A.LAST_ENDPOINT.get("model", "-")))
    if styles:
        final = dict(merged) if not args.dry else dict(fallback)
        if args.dry:
            final.update(accepted)
        # 只统计本轮 todo（--dry/--limit 小批时别把没跑的 165 条算成「未命中」），
        # 但分桶仍按全局 tg0 取，保证与全量跑的桶一致。
        scoped = {c: final.get(c, "") for c, _ in todo}
        print("句式验收（本轮 %d 条）：" % len(todo) + " / ".join(
            "%s %d" % (k, v) for k, v in
            style_report(scoped, [c for c, _ in tg0]).items()))
    if args.dry:
        print("\n=== 试跑结果（未落盘） ===")
        for cid, t in list(accepted.items())[:20]:
            print("  %-40s %-12s %s" % (cid[:40],
                                        styles.get(cid, "-") if styles else "-", t))
    # 每次都覆盖写（包括空表）：否则上一轮的拒收记录会留下来误导人。
    os.makedirs(BACKUP, exist_ok=True)
    atomicio.write_json(os.path.join(BACKUP, "rejected.json"),
                        {c: w for c, w in rejected}, indent=2, newline="")
    if degraded:
        print("本地降级 %d 条（AI 两轮都不合格，已按素材子句拼装）：" % len(degraded))
        for cid, why in degraded[:10]:
            print("  ▽ %-34s %s" % (cid[:34], why[:100]))
    if rejected:
        print("拒收清单 → %s" % os.path.relpath(
            os.path.join(BACKUP, "rejected.json"), ROOT))
        for cid, why in rejected[:8]:
            print("  ✗ %-36s %s" % (cid[:36], why[:90]))
    return 0


def set_h1(text, new_title):
    """把新标题写进 h1（HTML 转义），只动第 2 捕获组。"""
    m = C.H1.search(text)
    if not m:
        return text, False
    inner = html.escape(new_title, quote=False)
    return text[:m.start(2)] + inner + text[m.end(2):], True


def cmd_apply(args):
    ov = load_overrides()
    tg = dict(targets(args.scope))
    if args.only:
        # --only 落盘：不要求条目还在 scope 范围内（改完金额后它已经不在
        # money 范围了，但覆盖表里的新标题还没写进 h1）。
        cur = scan_titles()
        tg = {c: cur.get(c, "") for c in
              {x.strip() for x in args.only.split(",") if x.strip()} if c in ov}
    todo = {c: t for c, t in ov.items() if c in tg}
    if not todo:
        print("覆盖表为空或不在范围内，无需落盘。")
        return 0
    os.makedirs(BACKUP, exist_ok=True)
    n = 0
    for cid, title in todo.items():
        p = os.path.join(ARTICLES, cid + ".html")
        dst = os.path.join(BACKUP, "out__articles__%s.html" % cid)
        if not os.path.isfile(dst):
            import shutil
            shutil.copy2(p, dst)
            n += 1
    changed = 0
    for cid, title in todo.items():
        p = os.path.join(ARTICLES, cid + ".html")
        old = C.read(p)
        new, ok = set_h1(old, title)
        if ok and new != old:                # 内容没变就别重写（避免刷 mtime）
            atomicio.write_text(p, new, newline="")
            changed += 1
    print("已备份 %d 份原件 → %s" % (n, os.path.relpath(BACKUP, ROOT)))
    print("已写入 %d 篇新标题（只改 `<h1>`；其余 %d 篇内容未变，跳过写盘）"
          % (changed, len(todo) - changed))
    return 0


def body_delta_allowed(rebuilt, cur):
    """正文差异是否属于**已知且被授权**的那一种：内部黑话清洗。

    为什么需要它（2026-11-05）：`--verify` 的正文判据是「把备份的 h1 换回
    期望值后应与当前文件逐字节相同」。11-05 跑了 `scrub_jargon --artifacts`
    （39 个文件「入库时→收录时」之类），基线因此过期，192 条里有 33 条判红。
    实测证明这 33 条 **100% 等于清洗结果、0 条无法解释**（159 条原样一致）。

    🚨 修法不是重取基线（那等于给以后所有改动白送通行证），而是把**唯一**
    被授权的那一种变换写进判据：把 rebuilt 再洗一遍，必须正好等于 cur。
    任何额外的正文改动都会让这个等式不成立，照样判红。
    """
    if rebuilt == cur:
        return True
    try:
        import scrub_jargon as _SJ
        again, _h = _SJ.scrub_artifact(rebuilt)
    except Exception:                          # 少一个依赖不该造出假绿
        return False
    return again == cur


def cmd_verify(args):
    ov = load_overrides()
    prot = protected_ids()
    cases = load_cases()
    errs = []
    checked = 0
    # ⚠ 落盘后 h1 已不再是模板形状，绝不能用 targets() 重新筛（会全 skip ⇒ 假绿）。
    #    覆盖表里的每一条都必须逐篇验。
    for cid, title in ov.items():
        if cid in prot:
            errs.append("%s 是保护对象，却出现在覆盖表里" % cid)
            continue
        p = os.path.join(ARTICLES, cid + ".html")
        if not os.path.isfile(p):
            errs.append("%s 产物不见了" % cid)
            continue
        cur = C.read(p)
        inner = C.h1_inner(cur)
        if C.plain(inner or "") != title:
            errs.append("%s h1 与覆盖表不一致：%r" % (cid, C.plain(inner or "")))
            continue
        checked += 1
        bak = os.path.join(BACKUP, "out__articles__%s.html" % cid)
        if not os.path.isfile(bak):
            errs.append("%s 缺备份" % cid)
            continue
        orig = C.read(bak)
        rebuilt, _ = set_h1(orig, title)
        if not body_delta_allowed(rebuilt, cur):
            errs.append("%s 除 h1 外还有改动" % cid)
        # 数字仍须来自原文
        if cid in cases:
            txt, name = material_of(cid, cases[cid])
            extra = numset(title) - numset(txt)
            if extra:
                errs.append("%s 数字不在素材原文：%s" % (cid, extra))
            rest = title.replace(name, "", 1)
            if re.search(r"[一二三四五六七八九十百千万亿两零〇]{2,}", rest):
                errs.append("%s 中文数字串（金额读法/约数）" % cid)
    # 13 篇上一轮保护对象必须仍是旧标题（带旁注）
    for cid in sorted(legacy_protected()):
        p = os.path.join(ARTICLES, cid + ".html")
        if not os.path.isfile(p):
            continue
        t = C.plain(C.h1_inner(C.read(p)) or "")
        if "核对" not in t:
            errs.append("%s 上一轮保护对象被改动（应仍带口径旁注）" % cid)
    # 守卫体检
    blocks, warns = 0, {}
    frozen = published_ids() | legacy_protected()
    for p in sorted(glob.glob(os.path.join(ARTICLES, "*.html"))):
        t = C.plain(C.h1_inner(C.read(p)) or "")
        if not t:
            continue
        # 已发布 / 上一轮按批准保留的 13 篇：改不了，金额闸只提示不判红
        # （与 `publish_guard._scan_all_titles` 同一口径）。
        cid = os.path.basename(p)[:-5]
        for f in G.scan_title("wechat", t, published=cid in frozen):
            if f["level"] == "block":
                blocks += 1
            else:
                warns[f["rule"]] = warns.get(f["rule"], 0) + 1
    print("守卫：block %d ｜ warn %s" % (blocks, warns))
    print("句式分布：" + " / ".join(
        "%s %d" % (k, v) for k, v in style_report(ov).items()))
    print("验收：覆盖表 %d 条 ｜ 逐篇核对 %d ｜ 保护 %d ｜ 错误 %d"
          % (len(ov), checked, len(prot), len(errs)))
    for e in errs[:15]:
        print("  ✗ " + e)
    if not errs:
        print("✅ 除 `<h1>` 外零字节改动；数字全部来自素材原文；"
              "已发布与 13 篇保护对象未动。")
    return 1 if errs else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 LLM 批量改写公众号模板标题")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只读列计划（默认）")
    g.add_argument("--generate", action="store_true", help="调 LLM 生成覆盖表")
    g.add_argument("--apply", action="store_true", help="备份后写入产物 h1")
    g.add_argument("--verify", action="store_true", help="验收与零改动证明")
    ap.add_argument("--dry", action="store_true", help="试跑：调 AI 但不落盘")
    ap.add_argument("--diversify", action="store_true",
                    help="句式多样化：逐条指定目标句式重做（问句/对比/场景/条件/白描）；"
                         "句式不符则沿用上一版合格标题，绝不丢条目")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条")
    ap.add_argument("--only", default="",
                    help="只重做指定 case id（逗号分隔），忽略已有覆盖")
    ap.add_argument("--force", action="store_true", help="已有覆盖也重新生成")
    ap.add_argument("--scope", choices=("175", "all", "money"), default="175",
                    help="175=用户批准范围（默认）；all=全部未发布模板标题；"
                         "money=只挑当前标题仍带金额的未发布产物（2026-11-04 禁金额口径）")
    a = ap.parse_args(argv)
    if a.generate:
        return cmd_generate(a)
    if a.apply:
        return cmd_apply(a)
    if a.verify:
        return cmd_verify(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
