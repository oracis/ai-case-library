#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前置守卫：幂等键 / 标题 / 内容形态 / 封面 的**单一判据源**。

为什么独立成模块（2026-11-04）
------------------------------
此前这四件事散在各处，且都只量了「本地有没有」而没量「远端会不会重复」：

* 去重：`publish_one` 九平台的 `GATES` 里没有任何一条查「这个 case 在本平台
  发过/建过草稿没有」；B站/小红书的 FIFO 分支更是直接 `send-draft`，连闸都不进。
  `publish_slot.decide()` 只管档期与日配额，不认 case 级幂等。
* 标题：`gen_platform_md.make_title()` 末尾 `return t[:100]` 硬切，而
  `audit_cover_title.py` 里的平台上限（头条 30 / 小红书 20 / 公众号 64 / B站 80）
  发布链根本不读。
* 内容形态：`csdn_rules.clean()` 只服务 CSDN，`gen_platform_md.py` 五个 builder
  仍在生成 `> 平台：X ｜ 状态：本地稿` 水印与罐头来源句，知乎/博客园/两朵云
  没有闸 ⇒ 直接带着「自曝批量」的痕迹发出去。
* 封面：`_gate_cover` 只查本地 PNG 在不在，不查发布命令有没有真的带封面参数；
  9 个平台里只有 4 个配了 cover 闸。
  → 2026-11-05 补第 ⑤ 道 `check_cover_remote()`：**平台侧**封面字段读出来是
  什么（判据源 `scripts/cover_verify.py`）。第 ④ 道管「本地有没有图」，
  第 ⑤ 道管「远端是不是真有」——那条 `⛔ 本地有 PNG ≠ 平台侧封面已设置`
  的旧注脚，到这里才有判据。

本模块**全部只读**：查 SQLite 用 `mode=ro`，不建表、不写台账、不碰浏览器。
`publish_one.py` 的四道新闸只是把它包成 `(ok, detail)`。

用法（体检，可直接跑）::

    python -X utf8 scripts/publish_guard.py --platform csdn --case chessigma
    python -X utf8 scripts/publish_guard.py --scan-titles
"""

import argparse
import html
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "ledger.db")
SYNC_PATH = os.path.join(DATA, "longtail_sync.json")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 平台口径表

# 标题字数上限。前四个是既有实测口径（scripts/audit_cover_title.py）；
# 后五个是保守值，未实测到平台硬上限 —— 宁可早拦，不要把「没查过」写成「不限」。
TITLE_MAX = {
    "wechat": 64,
    "toutiao": 30,
    "xhs": 20,
    "bilibili": 80,
    # 二号口径同 B站（2026-11-06 加档期时补）：内部仍按 ≤30 字自我约束，
    # 但闸的上限跟平台一致，别让二号比一号多一道硬拦。
    "bilibili2": 80,
    "cnblogs": 100,
    "zhihu": 100,
    "csdn": 100,
    "tencentcloud": 100,
    "aliyun": 100,
}

# 一段里 ≥2 个 `$` 会被 KaTeX 吃成公式（PLAYBOOK-platforms）
KATEX_PLATFORMS = ("csdn", "tencentcloud", "aliyun")

# 技术社区：标题禁金额、正文禁通篇商业叙述、禁机械模板（CSDN 有驳回单）
TECH_PLATFORMS = ("csdn", "cnblogs", "zhihu", "tencentcloud", "aliyun")

# 标题禁金额（2026-11-04 用户口径）：**所有平台的所有标题**都不出现金额，
# 不只是技术平台。理由有两条 —— ① 标题应该是「产品是什么、解决什么问题」的
# 说明，不是收入播报；② 金额是抓取时点的**快照**，不准确且会变，写进标题
# 必然过期（与封面第二行口径一致，封面早就不显示金额）。
# ⚠ 判据要分得清「金额」和「规模」：「100 万用户」「5 万个招聘页」
#   「576 人付费」是可核验的规模事实，不在禁列（粗正则曾把它们误判 26 条）。
TITLE_MONEY = re.compile(
    r"[$€£¥]\s?\d"
    r"|\d[\d,.]*\s*(?:美元|美金|万元|万美元|万美金|块钱)"
    r"|\d[\d,.]*\s*元(?!素|数据)"
    r"|(?:百万|千万|十万|亿)\s*(?:年薪|美元|美金|元)"
    r"|(?:几|数|十几|几十|上百|上千)\s*[百万千亿]?\s*(?:美元|美金|元|刀)")

# 发布命令必须**显式**带 --cover 的平台。其余平台由各自脚本内部注入封面，
# 静态验不了，只能靠 VERIFY_HINT 的回读提示。
COVER_ARG_PLATFORMS = ("csdn", "tencentcloud", "aliyun")

# 数据时点（2026-11-05）：正文引了营收类数字就必须带「这是哪天的数」。
# `TIMEPOINT` 认三类写法 —— 生成器印的「数据时点」行、手写的「截至 2026-11-04」、
# 以及散稿里最常见的「2026-10-14 同步」。🚨 第一版只认前两种，把
# 「（TrustMRR 从支付网关直读，2026-10-14 同步）」全判成缺时点：
# 草稿 17 篇全误报、产物 261 篇里 202 篇是误报（真缺的只有 59 篇）。
# 一条天天误报的 warn 等于没有 warn。
TIMEPOINT = re.compile(
    r"数据时点|截至\s*20\d\d[-年]\d{1,2}"
    r"|20\d\d-\d\d-\d\d\s*(?:同步|抓取|核对|更新|核实)"
    r"|(?:抓取|同步|核对|核实|更新)(?:于|时间[:：]?)\s*20\d\d"
    r"|(?:时点|日期|截点|抓取日)\s*[:：]?\s*20\d\d")
MONEY_METRIC = re.compile(r"MRR|月经常性收入|年化收入|年经常性收入|\bARR\b")

# 所有平台都要求有封面资源（用户 2026-11-04：「封面有可能没设置」）
COVER_REQUIRED = ("wechat", "toutiao", "xhs", "bilibili", "bilibili2", "cnblogs",
                  "zhihu", "csdn", "tencentcloud", "aliyun")

# 「已发表」态：命中就不许再发
PUBLISHED_STATES = ("published", "auditing", "blocked", "审核中")
# 「远端已有草稿」态：命中就不许再建第二份
DRAFT_STATES = ("draft", "draft_saved", "pending", "building")

# ---------------------------------------------------------------- 标题词表

# 空壳/模板化标题（用户 2026-11-04 驳回「太笼统，没有给出必要合理的信息」；
# 词表来自 11-04 全仓 909 个产物标题的实扫，不是凭空列的）
TITLE_TEMPLATE = (
    r"技术拆解|深度拆解|深度解析|全解析|一文读懂|一文搞懂|一个海外独立产品的工程与增长|"
    r"一个海外小产品怎么赚钱|云原生视角下的独立产品增长|独立产品增长[:：]|"
    r"万字长文|保姆级|建议收藏|不看后悔|必看|必藏|震惊|重磅|揭秘|真相|"
    r"神器|颠覆|赋能|风口|赛道|复盘：一个|拆解：一个"
)

# AI 味套话（PLAYBOOK-content：智谱初稿 7/16 命中营销套话）
TITLE_FILLER = r"轻松|助您|速成|打造|一站式|赋能|抓手|闭环思维|底层逻辑|方法论全解"

# 硬切尾字：`t[:30]` 之类会把标题拦腰切断，留下半个词（11-02 实测
# 「…拿下 10」「…为师生、家」）。末尾是虚字/标点 = 强截断信号。
# 🚨 2026-11-04 校准：**裸数字只在「长度恰好等于上限」时算硬切证据**。
#    清掉 175 篇「Name：MRR $835，TrustMRR 实时核对」旁注后，标题普遍以
#    $835 的数字收尾，旧判据把 `\d` 一并算「半个词」⇒ 误报从 172 涨到 200。
#    短标题以金额数字收尾是正常文案，只有配合「恰好等于平台上限」才可疑。
_CUT_WORDS = (r"的|了|和|与|为|在|把|被|从|对|及|或|是|之|以|而|且|并|但|"
              r"就|都|也|还|将|让|给|这|其|一|个|[，,、：:；;「『（(]")
_CUT_TAIL = re.compile(r"(?:%s|\d)$" % _CUT_WORDS)          # block：含裸数字
_DANGLING_TAIL = re.compile(r"(?:%s)$" % _CUT_WORDS)        # warn：不含数字

# ---------------------------------------------------------------- 内容词表

# 管线水印 = 自曝批量（CSDN 一.3 的元凶）；只有 CSDN 的 rules.clean() 在剥
WATERMARK = re.compile(r"^>\s*平台[:：].{0,120}(本地稿|未推送|状态[:：])", re.M)
# 罐头来源句：对所有案例一视同仁，且可能是失实（抓到官网 ≠ 数字来自官方）。
# 🚨 2026-11-04 补：实扫 14 篇存量稿有 **4 种变体**，旧版只认 2 种，
#    「数据来源：产品官网公开信息、公开支付验证页。」整类漏网。
#    行首锚定是为了不误伤正文里的**行内**引用（csdn/shipfast 第 70 行
#    「…（数据来源：TrustMRR、Steal What Works）」是合法的一句出处）。
# 🚨 带时点、带具体出处的合法来源句**不算罐头**（掘金归档稿 6 篇吃过误报）：
#    「数据来源：产品公开定价页与应用商店公开信息（2026-10 快照）。」
#    ⇒ 只有当行里没有 20xx / 快照 / 同步 这类时点标记时才判罐头。
CANNED_SOURCE = re.compile(
    r"^\s*(?:数据来源|来源)[:：](?![^\n]*(?:20\d\d|快照|同步))"
    r"[^\n]{0,120}(?:官网|支付验证页|TrustMRR|公开访谈)[^\n]*$|"
    r"^\s*数据是实时核对过的[^\n]*$|数字带口径带修正|"
    r"TrustMRR\s*直连支付网关", re.M)
# 每篇同一串栏目 = 批量低质（腾讯云有驳回单，知乎 2026-04 公告重点治理）
MECHANICAL_SECTIONS = (
    r"^##\s*[一二三四五六七八九十]+、\s*(它到底解决了什么|钱从哪来|为什么它能成|"
    r"能抄的做法|给想做类似产品的你)",
    r"^##\s*(这个产品是什么|商业模型怎么搭|工程上值得抄的点|为什么能跑起来|背景|"
    r"数据与口径|可复制的工程实践|总结)$",
)
# 知乎导流：正文里出现联系方式变体 / 营销 CTA（知乎社区规范第一章）
ZHIHU_CTA = re.compile(
    r"加我|私信我|扫码|二维码|加群|进群|公众号|vx\b|v信|点击.{0,6}(链接|官网)|"
    r"前往.{0,6}官网|领取资料|关注我|完整版.{0,6}(公众号|博客|站)", re.I)


# ---------------------------------------------------------------- 工具

def norm_id(case):
    """与 `publish_slot._norm_id` 同规则：ext-ep-001-x → ep-001-x。

    不 import publish_slot，保持本模块可独立单测；一致性由
    `test_publish_guard.test_norm_matches_slot` 钉死。
    """
    cid = str(case or "").strip()
    return cid[4:] if cid.startswith("ext-") else cid


def _ro_conn(path=None):
    """只读连接；库不存在/打不开返回 None（不许为了「读」去建表）。"""
    p = path or os.environ.get("LEDGER_DB") or DB_PATH
    if not os.path.isfile(p):
        return None
    try:
        import pathlib
        uri = pathlib.Path(p).absolute().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except (sqlite3.Error, ValueError):
        return None


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def title_of(pack):
    """从产物取标题：md 首行 `# `、html `<h1>`、产物目录里的 `meta.json`。

    ⚠ HTML 实体要反转：`&amp;` 是 5 个字符但读者只看到 1 个 `&`，
    不反转会把「实际 61 字」误报成「65 字超上限」（判据要量人看到的东西）。
    """
    if not pack:
        return ""
    if os.path.isdir(pack):
        meta = _load_json(os.path.join(pack, "meta.json"), {}) or {}
        t = str(meta.get("title") or "") if isinstance(meta, dict) else ""
        return html.unescape(t).strip()
    try:
        with open(pack, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return ""
    m = re.search(r"^#\s+(.+)$", txt, re.M)
    if m:
        return html.unescape(m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", txt, re.S | re.I)
    if m:
        return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    return ""


def body_of(pack):
    if not pack or os.path.isdir(pack):
        return ""
    try:
        with open(pack, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------- ① 幂等

def check_dupe(platform, case, db_path=None, sync_path=None):
    """只读判「这个 case 在这个平台发过 / 建过草稿没有」。

    两路证据，任一命中即拦：
    1. SQLite `platform_posts`（唯一键 (case_id, platform)）——真值。
    2. `longtail_sync.json` 的历史**批次**记录——兜底。它的键是
       `<case>-<日期>-<序号>` 而不是 (case, platform)，所以重复建稿会
       静默追加第二条，11-04 实测 43 条里有 12 组重复标题（chessigma
       在 tencentcloud 4 次、cod-dex 在 aliyun 2 次）。

    返回 {ok, level, detail, hits}；ok=False 就是不许发。
    """
    cid = norm_id(case)
    hits = []

    conn = _ro_conn(db_path)
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT case_id, state, remote_id, draft_id, title, updated_at "
                "FROM platform_posts WHERE platform=?", (platform,)).fetchall()
            for r in rows:
                if norm_id(r["case_id"]) != cid:
                    continue
                state = (r["state"] or "").strip()
                if state in PUBLISHED_STATES or r["remote_id"]:
                    return {"ok": False, "level": "published", "hits": [dict(r)],
                            "detail": "台账已是 %s%s ⇒ 再发就是第二篇"
                                      % (state or "published",
                                         "（article %s）" % r["remote_id"] if r["remote_id"] else "")}
                if state in DRAFT_STATES or r["draft_id"]:
                    hits.append(dict(r))
                    return {"ok": False, "level": "draft", "hits": hits,
                            "detail": "远端已有草稿%s ⇒ 别再建第二份，直接发这一份"
                                      % (" draftId=%s" % r["draft_id"] if r["draft_id"] else "")}
        except sqlite3.Error:
            pass                                  # 表结构未升级时不当「没发过」放行？→ 交给 JSON 兜底
        finally:
            conn.close()

    # JSON 台账兜底：按 (case, platform) 聚合，不看批次键
    sync = _load_json(sync_path or SYNC_PATH, {}) or {}
    batches = []
    if isinstance(sync, dict):
        for key, row in sync.items():
            if not isinstance(row, dict):
                continue
            if norm_id(row.get("case") or "") != cid:
                continue
            plats = row.get("platforms")
            if isinstance(plats, dict) and platform in plats:
                batches.append((key, plats[platform]))
            elif isinstance(plats, list) and platform in plats:
                batches.append((key, None))
    if batches:
        return {"ok": False, "level": "json", "hits": batches,
                "detail": "longtail_sync.json 已有 %d 个建稿批次（%s）⇒ 先按标题查远端草稿箱，别再 --upload"
                          % (len(batches), ", ".join(str(k) for k, _ in batches[:2]))}

    return {"ok": True, "level": "ok", "hits": [],
            "detail": "无已发/已有草稿记录"}


# ---------------------------------------------------------------- ② 标题

def scan_title(platform, title, pack=None, published=False):
    """返回 findings：[{level, rule, detail}]；`block` 非空就不许发。"""
    t = (title or "").strip()
    out = []
    if not t:
        return [{"level": "block", "rule": "取不到标题",
                 "detail": "产物里没有 `# ` / `<h1>` / meta.json.title"}]

    limit = TITLE_MAX.get(platform, 100)
    if len(t) > limit:
        out.append({"level": "block", "rule": "标题超平台上限",
                    "detail": "%d 字 > %s 上限 %d 字 ⇒ 平台会截断或被判标题党"
                              % (len(t), platform, limit)})
    if len(t) == limit and _CUT_TAIL.search(t):
        out.append({"level": "block", "rule": "疑似硬截断",
                    "detail": "长度恰好等于上限且尾字是「%s」⇒ 像是 `t[:%d]` 切出来的"
                              % (t[-1], limit)})
    elif _DANGLING_TAIL.search(t) and len(t) > 12:
        out.append({"level": "warn", "rule": "标题尾字可疑",
                    "detail": "以「%s」收尾，核一下是不是被切了半个词" % t[-1]})

    if re.search(TITLE_TEMPLATE, t):
        out.append({"level": "block", "rule": "空壳/机械模板标题",
                    "detail": "命中笼统模板（%s）⇒ 要给「做什么 + 可核验数字」，不是栏目名"
                              % (re.search(TITLE_TEMPLATE, t).group(0))})
    if re.search(TITLE_FILLER, t):
        out.append({"level": "warn", "rule": "AI 味套话",
                    "detail": "命中「%s」⇒ 换成具体动作" % re.search(TITLE_FILLER, t).group(0)})
    if re.search(r"TrustMRR\s*(实时)?核对|公开口径[:：]|（非订阅制|\(非订阅制", t):
        out.append({"level": "warn", "rule": "标题带口径旁注",
                    "detail": "「TrustMRR 实时核对 / 公开口径 /（非订阅制」是数据层旁注，"
                              "既撑爆字数上限又像机器拼的 ⇒ 用 _strip_meta() 清掉"})

    if TITLE_MONEY.search(t):
        # 已发布产物（不可改）降为 warn：既提示口径漂移，又不让全仓体检
        # 因为历史稿一直红着（改已发表标题要走「修改已发布文章」，另说）。
        out.append({"level": "warn" if published else "block",
                    "rule": "标题带金额",
                    "detail": "标题只写产品说明：金额是抓取时点快照，不准确也会变；"
                              "改用产品形态/使用场景说话（已发布产物仅提示）"})
    if platform in KATEX_PLATFORMS and "$" in t:
        out.append({"level": "block", "rule": "标题含 `$`",
                    "detail": "KaTeX 平台会把两个 `$` 之间吃成公式 ⇒ 写「美元」"})

    # 标题与产物首行必须一致（PRD §6.1 #22：台账 title 漂过一次）
    if pack:
        real = title_of(pack)
        if real and real != t:
            out.append({"level": "block", "rule": "标题与产物首行不一致",
                        "detail": "闸拿到「%s」，产物首行是「%s」" % (t[:24], real[:24])})
    return out


# ---------------------------------------------------------------- ③ 内容形态

def scan_content(platform, text, pack=None):
    """返回 findings；技术平台把「自曝批量」判成 block，其余判 warn。"""
    s = text if text is not None else (body_of(pack) if pack else "")
    strict = platform in TECH_PLATFORMS
    lvl = "block" if strict else "warn"
    out = []

    if WATERMARK.search(s):
        out.append({"level": lvl, "rule": "管线水印",
                    "detail": "正文带 `> 平台：X ｜ 状态：本地稿` ⇒ 自曝批量（CSDN 一.3）"})
    if CANNED_SOURCE.search(s):
        out.append({"level": lvl, "rule": "罐头来源句",
                    "detail": "固定写「TrustMRR 直连支付网关/官网+公开访谈」＝对每条案例一视同仁，"
                              "且可能失实（抓到官网 ≠ 数字来自官方）"})
    mech = []
    for pat in MECHANICAL_SECTIONS:
        mech += re.findall(pat, s, re.M)
    if len(mech) >= 3:
        out.append({"level": lvl, "rule": "机械栏目骨架",
                    "detail": "%d 个固定小标题与别篇相同（%s）⇒ 换命题、各篇不同骨架"
                              % (len(mech), " / ".join(mech[:3]))})
    if platform == "zhihu" and ZHIHU_CTA.search(s):
        out.append({"level": "block", "rule": "知乎导流/联系方式",
                    "detail": "命中「%s」⇒ 知乎《社区规范》禁导流，删掉 CTA，只留必要来源"
                              % ZHIHU_CTA.search(s).group(0)})
    # 内部黑话：判据与清洗器同一个源（`scrub_jargon.JARGON`）。2026-11-05 之前
    # 只有 `test_make_article` 盯 corrections，于是 `metric_note` 里的「早期入库时」
    # 一路漏到了 168 处。这里对所有平台判 block —— 读者看到「候选资料 headline 字段」
    # 只会以为页面坏了。延迟 import：guard 要保持能被单独 import 的轻量。
    try:
        import scrub_jargon as _SJ
        m = _SJ.JARGON.search(s)
    except Exception:            # 少一个依赖不该让整个闸挂掉
        m = None
    if m:
        out.append({"level": "block", "rule": "内部黑话",
                    "detail": "命中「%s」⇒ 这是流水线自用词汇/字段名，读者看不懂；"
                              "跑 `scripts/scrub_jargon.py --check` 看清单" % m.group(0)})
    # 数据时点（2026-11-05，用户口径：「金额不准确，并且是动态的」）。
    # 只要正文引了 MRR / 年化收入，就必须说清「这是哪天的数、按什么口径」——
    # 否则读者会把抓取当天的快照当现值。只 warn 不 block：存量 261 篇产物都
    # 缺这一行，一上来就 block 等于把整个发布链停掉（那不是修问题，是掀桌子）。
    if (not TIMEPOINT.search(s)) and MONEY_METRIC.search(s):
        out.append({"level": "warn", "rule": "数据缺时点",
                    "detail": "正文引了 MRR/年化收入，但没有「数据时点」那行 ⇒ "
                              "金额是抓取日快照、会变，读者会当现值。新稿由 "
                              "`make_article.timepoint_row()` 自动带；手写长稿要人工补一句"
                              "（形如「以下数字为 2026-11-04 抓取时的快照」）"})
    return out


def strip_pipeline_marks(text):
    """确定性清洗：只删「一定不该出现的东西」，不改写内容、不编事实。

    供生成器调用；**不用于静默改写存量草稿**（那是「批量改文案」，须先确认）。
    """
    keep = []
    for ln in (text or "").split("\n"):
        if WATERMARK.match(ln.strip()):
            continue
        if CANNED_SOURCE.search(ln):
            continue
        keep.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


# ---------------------------------------------------------------- ④ 封面

def cover_of(case):
    """封面资源单一查找链：头条产物 → 小红书首卡 → B站产物 → 公众号缓存。

    旧 `publish_one.cover_of()` 只查 `out/toutiao/<case>/cover.*`；实测
    `out/xhs/<case>/card-1.png` 才是头条题图（PLAYBOOK-content 45 行），
    漏掉它就会把「有封面」判成「没有」。
    """
    cands = (
        os.path.join(ROOT, "out", "toutiao", case, "cover.png"),
        os.path.join(ROOT, "out", "toutiao", case, "cover.jpg"),
        os.path.join(ROOT, "out", "xhs", case, "card-1.png"),
        os.path.join(ROOT, "out", "bili", case, "cover.png"),
        os.path.join(ROOT, "out", "articles", case + "-cover.png"),
    )
    for p in cands:
        if os.path.isfile(p):
            return p
    return ""


def check_cover(platform, case):
    """返回 {ok, path, detail}。

    ⚠ 只保证「本地有资源 + 命令会带封面参数」；**平台侧是否真的落库**
    仍需 `VERIFY_HINT` 的回读（腾讯云 `tc_manage.py status` / CSDN
    `csdn_list_drafts.py`）。别把本地有图写成「封面已设置」。
    """
    p = cover_of(case)
    if platform not in COVER_REQUIRED:
        return {"ok": True, "path": p, "detail": "该平台不强制封面"}
    if not p:
        return {"ok": False, "path": "", "detail":
                "缺封面资源（查过 out/toutiao、out/xhs、out/bili、out/articles）"
                " ⇒ 腾讯云会静默失败，CSDN 会裸图发布"}
    extra = ""
    if platform in COVER_ARG_PLATFORMS:
        extra = "；发布命令必须带 --cover（随请求一次提交）"
    return {"ok": True, "path": os.path.relpath(p, ROOT), "detail":
            "本地封面存在%s；平台侧是否落库仍需回读" % extra}


# ---------------------------------------------------------------- ⑤ 封面回读

def check_cover_remote(platform, case):
    """⑤ 封面回读：**平台侧**到底有没有封面（判据源 `scripts/cover_verify.py`）。

    与 ④ `check_cover()` 的分工，别混：
      * ④ 静态 —— 本地有没有 PNG、发布命令会不会带 `--cover`
      * ⑤ 动态 —— 平台侧那个封面字段，读出来到底是 URL 还是空

    只有 `empty`（真读了、读出来是空的）才 block。
    `unknown` / `local` / `none` 一律 warn —— 「没查过」不等于「有问题」，
    但**必须说出来**；把没查过的算成通过，就是本仓库最常见的那类假绿。
    """
    try:
        import cover_verify as CV
    except Exception as e:                                 # noqa: BLE001
        return [{"level": "warn", "rule": "封面未回读",
                 "detail": "cover_verify 不可用：%s" % str(e)[:80]}]
    f = CV.finding(platform, case)
    return [] if f is None else [f]


# ---------------------------------------------------------------- 汇总 / CLI

def audit(platform, case, pack=None, db_path=None, sync_path=None):
    """四道体检一次跑完，返回 {gate: [finding,...]}。"""
    body = body_of(pack)
    title = title_of(pack)
    d = check_dupe(platform, case, db_path, sync_path)
    dupe = [] if d["ok"] else [{"level": "block", "rule": "重复发布/重复建稿",
                                "detail": d["detail"]}]
    c = check_cover(platform, case)
    cover = [] if c["ok"] else [{"level": "block", "rule": "缺封面", "detail": c["detail"]}]
    return {
        "dedupe": dupe,
        "title": scan_title(platform, title, pack),
        "template": scan_content(platform, body, pack),
        "cover": cover,
        "cover_remote": check_cover_remote(platform, case),
    }


def _published_wechat():
    """只读公众号已发布集合（三源）。给 `--scan-titles` 区分「已发布不能再改」。

    与 `wechat_ai_titles.published_ids()` 同口径但**不 import 它**：
    `wechat_ai_titles` 反过来 import 本模块，反向依赖会成环。
    """
    out = set()
    d = os.path.join(ROOT, "data")
    try:
        with open(os.path.join(d, "wechat_published.json"), encoding="utf-8") as f:
            wp = json.load(f)
        out |= {k for k, v in wp.items()
                if isinstance(v, dict) and v.get("status") == "published"}
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(d, "publish_manifest.json"), encoding="utf-8") as f:
            mf = json.load(f)
        out |= {k for k, v in mf.items()
                if isinstance(v, dict) and isinstance(v.get("wechat"), dict)
                and v["wechat"].get("state") == "published"}
    except (OSError, ValueError):
        pass
    db = os.path.join(d, "ledger.db")
    if os.path.isfile(db):
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            out |= {r[0] for r in conn.execute(
                "SELECT case_id FROM platform_posts "
                "WHERE platform='wechat' AND state='published'")}
            conn.close()
        except sqlite3.Error:
            pass
    return out


def _scan_all_titles():
    """只读全仓标题体检：按产物所在平台套上限，报超长与模板命中。"""
    import glob
    pub = _published_wechat()
    rows = []
    for plat in TITLE_MAX:
        pats = {
            "wechat": "out/articles/*.html",
            "toutiao": "out/toutiao/*/meta.json",
            "xhs": "out/xhs/*/meta.json",
            "bilibili": "out/bili/*/meta.json",
        }.get(plat, "drafts/newbatch/%s/*.md" % plat)
        for p in glob.glob(os.path.join(ROOT, pats)):
            # meta.json 要交给「目录」形态的读取分支，否则会被当纯文本扫 `# `
            src = os.path.dirname(p) if p.endswith("meta.json") else p
            t = title_of(src)
            if t:
                rows.append((plat, os.path.relpath(p, ROOT), t))
    bad = []
    for plat, rel, t in rows:
        cid = os.path.basename(rel)[:-5] if rel.endswith(".html") else ""
        # 已发布、以及「带口径旁注的历史遗留产物」都只提示不判红：
        # 后者是 #52 按用户批准保留没动的 13 篇（台账有记录、改标题会让
        # 已发表列表认亲错位）。新稿不会带旁注（生成器 `_clean_title_hook()`
        # 已修），所以这条豁免不会漏过新问题。
        legacy = bool(re.search(r"TrustMRR\s*(实时)?核对|（非订阅制", t))
        f = [x for x in scan_title(plat, t,
                                   published=(plat == "wechat" and (cid in pub or legacy)))
             if x["level"] == "block"]
        if f:
            bad.append((plat, rel, t, f))
    print("扫到 %d 个标题，命中 block %d 条" % (len(rows), len(bad)))
    for plat, rel, t, fs in bad[:40]:
        print("  [%s] %s (%d 字) %s" % (plat, rel, len(t), t[:60]))
        for x in fs:
            print("      └ %s：%s" % (x["rule"], x["detail"][:90]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="发布前置守卫（只读体检）")
    ap.add_argument("--platform", choices=sorted(TITLE_MAX))
    ap.add_argument("--case")
    ap.add_argument("--file", help="产物路径；缺省按平台目录推")
    ap.add_argument("--scan-titles", action="store_true", help="全仓标题体检")
    a = ap.parse_args(argv)
    if a.scan_titles:
        return _scan_all_titles()
    if not (a.platform and a.case):
        ap.error("要 --platform + --case，或 --scan-titles")
    if a.file:
        pack = a.file if os.path.isabs(a.file) else os.path.join(ROOT, a.file)
    else:
        pack = ""
    res = audit(a.platform, a.case, pack or None)
    print("%s / %s" % (a.platform, a.case))
    total = 0
    for gate, fs in res.items():
        print("  %-13s %s" % (gate, "✓" if not fs else "%d 条" % len(fs)))
        for f in fs:
            total += 1 if f["level"] == "block" else 0
            print("     %s %-18s %s" % ("✗" if f["level"] == "block" else "⚠",
                                       f["rule"], f["detail"]))
    print("\nblock %d 条 ⇒ %s" % (total, "不许发" if total else "可发（仍有 warn 要人工看）"))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
