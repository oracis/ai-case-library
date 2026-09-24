#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 自动核实流水线 —— 把「找卡点 → 补材料 → 核实 → 发布」跑成一条命令。

为什么要有这个文件
------------------
候选池里每条都挂着「卡在哪」，以前要人一条条开网页对口径、找一手来源。
这些活机器干得了：检索、抓原文、对口径都是确定性劳动；规则引擎又能
客观判断"够了没有"。所以分工是——

    机器：找卡点 → 搜原文 → 抓正文 → 按规则填草稿 → 判定 →（可选）发布
    人：  打开工作台扫一眼来源，勾「我亲自看过原文」，为数字背书

`human_read` 这一项**任何模式都不自动勾**：它是这个库唯一 "必须由人承担" 的
动作，勾了就代表为这条数字背书。它不拦发布（不勾也能入库），但案例上会把它和
核读时间戳一起存下来 —— 所以 AI 更不能代勾，那等于伪造一条人工核读记录。

用法
----
    python scripts/ai_verify.py --plan                # 离线：只列卡点，不联网
    python scripts/ai_verify.py --limit 5             # AI 预核 5 条（只存草稿）
    python scripts/ai_verify.py --id gojiberry-ai     # 单条
    python scripts/ai_verify.py --limit 5 --publish   # 够格的直接发布成案例
    python scripts/ai_verify.py --all --publish --min-score 60

环境变量（LLM 走 OpenAI 兼容接口，DeepSeek / Kimi / GLM 都行）
    CASE_LIB_AI_KEY     必填（--plan 除外）
    CASE_LIB_AI_BASE    默认 https://api.deepseek.com
    CASE_LIB_AI_MODEL   默认 deepseek-chat
    CASE_LIB_ADMIN_PASSWORD   后台密码（写草稿/发布要登录 5053）

设计约束
--------
- 零依赖：只用标准库。检索用 Bing（国内可达）+ DuckDuckGo 兜底，
  抓正文用 html.parser 剥标签。
- 不编造：prompt 只许依据抓回来的原文判断；来源 URL 必须来自检索结果。
- 不过闸门不发布：发布走 server 的 /promote，规则引擎是唯一闸门，
  本脚本不自己写 cases.json。
"""

import argparse
import html as html_mod
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_rules as VR                                        # noqa: E402
import triage as TR                                              # noqa: E402

ADMIN_BASE = os.environ.get("CASE_LIB_ADMIN_BASE", "http://127.0.0.1:5053")
AI_BASE = (os.environ.get("CASE_LIB_AI_BASE") or "https://api.deepseek.com").rstrip("/")
AI_KEY = os.environ.get("CASE_LIB_AI_KEY", "")
AI_MODEL = os.environ.get("CASE_LIB_AI_MODEL", "deepseek-chat")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

GATE_KEYS = [g["key"] for g in VR.GATES]
MUST_KEYS = [m["key"] for m in VR.MUSTS]
BONUS_KEYS = [b["key"] for b in VR.BONUS]


def configure(api_key=None, base=None, model=None):
    """运行期改 LLM 配置（后台 GUI 保存的 key 在 server 进程里生效就靠它）。

    不传的项保持原值。模块级常量在函数体内读取，所以改全局立刻生效。
    """
    global AI_KEY, AI_BASE, AI_MODEL
    if api_key:
        AI_KEY = api_key
    if base:
        AI_BASE = base.rstrip("/")
    if model:
        AI_MODEL = model


class AdminError(Exception):
    """连不上后台 / 登录失败 / 存草稿被拒。CLI 里转成非零退出，服务端里当任务错误。"""


# ---------------------------------------------------------------------------
# 纯函数：挑候选、算卡点、组搜索词（可离线单测）
# ---------------------------------------------------------------------------
def triage_index():
    """建一份初筛用的索引（已发布案例 / 候选池 / 草稿）。

    两处用它：排序（哪条先核）、跳过（草稿已判定可发布的别再核）。
    读不到某份数据不致命 —— 索引缺一份顶多少一条去重线索，不该让流水线起不来。
    """
    out = {}
    for name in ("cases", "candidates", "inbox", "verifications"):
        try:
            out[name] = load_json(name)
        except Exception:
            out[name] = {} if name == "verifications" else []
    return TR.build_index(out)


def _priority(c):
    """人工显式标的优先级。0 = 人已经写了「这条最重要」。"""
    b = c.get("blocking") or ""
    return 0 if ("第一" in b or "高优先级" in b) else 1


def pick_candidates(cands, limit=None, ids=None, include_small=False,
                    index=None, skip_ready=True):
    """从候选池挑出值得核实的条目。

    自动跳过：占位条目（名字带（ 的观察/待发现）、headline 还是「未获取」的、
    标了「体量太小」的（除非 include_small）、以及**初筛判定材料已齐的**
    （那种条目缺的是人点发布，不是 AI 核实 —— 核它等于重复花钱）。

    排序（2026-09-20 改）：
      1. 人显式标的「高优先级复核」置顶 —— 机器分不许覆盖人的判断；
      2. 其余按初筛分（triage）降序 —— 分数高的先核。

    改之前是「按 blocking 文本粗分三档 + 原始顺序」，等于按录入顺序核：
    采到什么就先核什么，队列里最值得核的那条可能永远排在最后。
    """
    if ids:
        want = [i.strip() for i in ids if i.strip()]
        return [c for c in cands if c.get("id") in want]

    index = index if index is not None else TR.Index()
    pool = []
    for c in cands:
        name = c.get("name") or ""
        head = (c.get("metrics") or {}).get("headline") or ""
        blocking = c.get("blocking") or ""
        if name.startswith("（"):                 # （待发现）（方法论）（基准线）…
            continue
        if "未获取" in head:
            continue
        if not include_small and "体量太小" in blocking:
            continue
        r = TR.score_record(c, "candidate", index)
        if skip_ready and r["grade"] == "ready":
            continue
        pool.append((c, r))

    pool.sort(key=lambda pair: (_priority(pair[0]), -pair[1]["score"],
                                pair[0].get("id") or ""))
    out = [c for c, _ in pool]
    return out[:limit] if limit else out


def current_blockers(draft):
    """把规则引擎的判定翻译成「卡在哪」清单。

    返回 (kind, key, label) 三元组列表：key 给人快速扫（--plan 里紧凑打印），
    label 给 LLM（它需要完整语义才能判断这条卡点怎么补）。
    """
    r = VR.evaluate(draft or VR.blank())
    out = [("gate", g["key"], g["label"]) for g in r["gates_failed"]]
    out += [("must", m["key"], m["label"]) if isinstance(m, dict)
            else ("must", str(m), str(m)) for m in r["missing"]]
    return out, r


def build_queries(cand):
    """给一条候选生成检索词。头两条找官网/定价，后两条找收入披露。"""
    name = cand.get("name_en") or cand.get("name") or ""
    head = (cand.get("metrics") or {}).get("headline") or ""
    qs = [
        "%s official site pricing" % name,
        '"%s" MRR ARR revenue founder' % name,
    ]
    if "TrustMRR" in head or "trustmrr" in (cand.get("note") or ""):
        qs.append("TrustMRR %s" % name)
    if cand.get("one_liner"):
        qs.append('"%s" %s' % (name, cand["one_liner"][:40]))
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:4]


# 与「核实一个软件生意」无关的来源域名 —— 直接丢掉，不写进 sources。
#
# 两类真实污染（都在这条流水线上实际发生过）：
#   1) 产品名恰好是常见英文词时，AI 会去查词典：
#      eloquent 那次拿回来的全是 dictionary.cambridge.org / iciba.com 的词条释义。
#   2) 中文二手转述里夹带的音乐 / 影视平台（music.163.com / y.qq.com）。
# 这类来源一旦进了 sources，会拉高「来源条数」、干扰一手来源判定，
# 让一条本该被否的条目看起来「有多个来源互相印证」。
SOURCE_DENY = (
    # 词典 / 翻译
    "dictionary.cambridge.org", "dictionary.com", "merriam-webster.com",
    "collinsdictionary.com", "thefreedictionary.com", "vocabulary.com",
    "wordreference.com", "oxfordlearnersdictionaries.com", "ldoceonline.com",
    "iciba.com", "dict.youdao.com", "youdao.com", "fanyi.baidu.com",
    "linguee.com", "bab.la", "deepl.com", "translate.google.com",
    # 音乐 / 影视
    "music.163.com", "y.qq.com", "kugou.com", "kuwo.cn", "xiami.com",
    "spotify.com", "music.apple.com", "bilibili.com", "douyin.com",
    "iqiyi.com", "youku.com", "v.qq.com", "netflix.com",
    # 电商 / 软件下载站
    "amazon.com", "ebay.com", "taobao.com", "tmall.com", "jd.com",
    "softonic.com", "download.com", "cnet.com",
    # 中文问答 / 题库 / 作业帮 —— 2026-09-24 le19emetrou 那轮实测：cn.bing.com
    # 查不到小众外文站时会吐「无结果兜底模块」，一次塞进 5 个这类页，
    # 且 irrelevant_sources_dropped 仍为 0（当时没进名单）。
    "baidu.com", "sogou.com", "so.com", "zybang.com", "koolearn.com",
    "jyeoo.com", "21cnjy.com", "xuexi.la", "360doc.com", "doc88.com",
    "docin.com", "iteslj.org",
    # 中文二手 / 转载站（2026-09-24 第二轮）：同上，cn.bing 查不到时会吐聚合页。
    # 实测 mort 那轮 5 个名额有 3 个给了 uuyc.163.com 与 blog.gitcode.com，
    # stealth-company 那轮混进 zhihu.com/explore。
    # 项目原则是「一手证据 > 二手转述」——拿不到一手宁可让 AI 说「无法核实」，
    # 也不能用中文转载站的转述冒充证据。
    "zhihu.com", "csdn.net", "gitcode.com", "jianshu.com", "juejin.cn",
    "cnblogs.com", "oschina.net", "51cto.com", "163.com", "sina.com.cn",
    "ai-bot.cn", "mcpworld.com",
)


def source_host(url):
    """取 URL 的主机名（去掉 www.）。取不到返回空串。"""
    m = re.match(r"https?://([^/\s]+)", (url or "").strip())
    if not m:
        return ""
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def is_irrelevant_source(url):
    """这个来源域名是否与核实案例无关（词典 / 音乐 / 电商…）。"""
    host = source_host(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in SOURCE_DENY)


def domains_of(sources):
    out = set()
    for s in sources or []:
        m = re.match(r"https?://([^/]+)", s.get("url") or "")
        if m:
            out.add(m.group(1).lower().replace("www.", ""))
    return out


def map_ai_to_draft(cand, ai, publish_mode, model_name=""):
    """把 AI 的回答映射成服务端认的草稿。

    每一个勾都要求有结构性证据，不裸信 AI 的布尔值：
    - 门槛三态：只有 AI 明确说 no 才记进 gates_denied；拿不准不判死。
      反证是否构成否决由 verify_rules 定：**一道成立就进库**，反证降级成提醒
    - 有无一手来源不再阻塞（由 verify_rules 从来源构成算成「待人工复核」提醒）
    - bonus 各项：与来源构成、修正表对得上
    - human_read：**任何模式都不勾**。它不拦发布，但案例上会把它和时间戳一起
      存下来当「有没有人核过」的凭据，AI 代勾就是伪造记录
    """
    # 先过滤掉词典 / 音乐这类无关来源，再取前 6 条。
    # 顺序很重要：如果先截断再过滤，被丢掉的垃圾会白占名额。
    sources = []
    irrelevant_dropped = 0
    for s in (ai.get("sources") or []):
        url = str(s.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            continue
        if is_irrelevant_source(url):
            irrelevant_dropped += 1
            continue
        kind = s.get("kind") if s.get("kind") in (
            "stripe", "official", "press", "review", "founder", "secondary") else "press"
        sources.append({"label": str(s.get("label") or "")[:80],
                        "url": url, "kind": kind})
        if len(sources) >= 6:
            break

    kinds = {s["kind"] for s in sources}
    has_primary = bool(kinds & {"stripe", "official"})

    # 门槛三态：yes → 通过；"no" → 明确反证（写进 gates_denied）；
    # unknown / 没答 / 越界值 → 一律不算被否，留给人复核。
    #
    # 关键：布尔 False 不当作 "no"。老提示词教过模型「不确定就 false」，
    # 那个 False 携带的是「拿不准」而不是「已证伪」——当反证处理就等于
    # 把这次修好的批量误杀原封不动放回来。真有反证时应按提示词答字符串 "no"。
    gates = []
    denied = []
    for k in GATE_KEYS:
        gv = ai.get(k)
        s = gv.strip().lower() if isinstance(gv, str) else ""
        if gv is True or s == "yes":
            gates.append(k)
        elif s == "no":
            denied.append(k)

    musts = []
    caliber = ai.get("caliber") if ai.get("caliber") in VR.CALIBER_KEYS else ""
    if caliber:
        musts.append("caliber_decided")
        if ai.get("caliber_consistent") is True:
            musts.append("caliber_consistent")
    # human_read **任何模式都不由 AI 勾**。它是「有没有人核过」的唯一凭据，
    # 而案例上会连时间戳一起存下来（见 server.py 的 apply_human_read）——
    # AI 代勾等于伪造一条人工核读记录。2026-09-17 之前 publish_mode 会代勾，
    # 那是因为它拦发布、不勾就发不出去；现在它不拦了，代勾就只剩「造假」一个效果。
    #
    # 已经勾上的必填项，AI 这一轮不许抹掉。一个勾代表「有人断言过这件事」——
    # AI 某轮答 False 只是它**那次**没核到（十有八九是支付侧页面没抓下来），
    # 不等于之前那个断言错了。静默删掉会让候选在「已齐」与「被卡住」之间来回跳：
    # 实测过，草稿里已有 caliber_consistent 的候选重跑一遍 AI 就变回「还差 1 项」。
    # AI 观点变化记录在 caliber_reason / caliber_consistent_ai 里给人看，而不是靠删勾。
    musts = set(musts) | {k for k in (cand.get("_old_musts") or []) if k in MUST_KEYS}
    musts = [k for k in MUST_KEYS if k in musts]      # 按规则表顺序，结果稳定

    # 等级由来源构成推导（结构说话），AI 只在拿不准时兜底；
    # AI 说 stripe 但来源里根本没有 stripe → 降回 partial
    if has_primary and "stripe" in kinds:
        verification = "stripe"
    elif "official" in kinds:
        verification = "official"
    else:
        verification = ai.get("verification") if ai.get("verification") in VR.VERIFICATION_KEYS else "partial"
    if verification == "stripe" and "stripe" not in kinds:
        verification = "partial"

    corrections = []
    for x in (ai.get("corrections") or [])[:4]:
        if str(x.get("claim") or "").strip():
            corrections.append({
                "claim": str(x.get("claim"))[:200],
                "truth": str(x.get("truth") or "")[:200],
                "source": str(x.get("source") or "")[:300],
            })

    bonus = ai.get("bonus") or {}
    hits = []
    if bonus.get("founder_disclosure") and (kinds & {"official", "founder", "stripe"}):
        hits.append("founder_disclosure")
    if bonus.get("pricing_confirmed") and "official" in kinds:
        hits.append("pricing_confirmed")
    if bonus.get("replicable_low"):
        hits.append("replicable_low")
    if bonus.get("secondary_corroborated") and len(domains_of(sources)) >= 2:
        hits.append("secondary_corroborated")
    if corrections:
        hits.append("corrections_found")
    if len(cand.get("playbook") or []) >= 3:
        hits.append("solo_playbook")
    hits = [h for h in hits if h in BONUS_KEYS]

    conf = ai.get("confidence", 0)
    note = "AI 自动核实（%s，%s，置信度 %.0f%%）" % (
        model_name or AI_MODEL, datetime.now().strftime("%Y-%m-%d %H:%M"), conf * 100)
    if irrelevant_dropped:
        note += "；已剔除 %d 条无关来源（词典/音乐/电商等，不计入来源数）" % irrelevant_dropped
    if ai.get("summary"):
        note += "。" + str(ai["summary"])[:160]

    return {
        "gates": gates,
        # AI 明确说「不是」的那些门槛。拿不准的既不进 gates 也不进这里
        "gates_denied": denied,
        "musts": musts,
        "bonus": hits,
        "caliber": caliber,
        # AI 对口径的判断理由 —— 以前直接丢掉，于是后台只剩「还差 N 项必填」，
        # 看不出它是「没找到证据」还是「找到了反证」。这两件事的处理方式完全不同，
        # 所以必须存下来给人看（界面在口径下拉框下面显示）。
        "caliber_reason": str(ai.get("caliber_reason") or "")[:1200],
        # AI 这一轮对「口径与数字一致」的原始回答（True / False / None）。
        # 勾是粘的（见上面的 musts 合并逻辑），所以 AI 观点翻转只体现在这里：
        # caliber_consistent 留着勾、caliber_consistent_ai=False 就是「它这次不同意」。
        "caliber_consistent_ai": (ai.get("caliber_consistent")
                                  if isinstance(ai.get("caliber_consistent"), bool)
                                  else None),
        "verification": verification,
        "source_kinds": sorted(kinds),
        "sources": sources,
        # 被域名黑名单挡掉的来源条数，供后台/测试观察
        "irrelevant_sources_dropped": irrelevant_dropped,
        "corrections": corrections,
        "note": note,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def extract_json(text):
    """从 LLM 回答里抠出第一个完整 JSON 对象（容忍 ```json 围栏与废话）。"""
    text = re.sub(r"```(?:json)?", "", text or "")
    start = text.find("{")
    if start < 0:
        raise ValueError("回答里没有 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON 没闭合")


# ---------------------------------------------------------------------------
# 网络：抓页面 / 搜索（Bing 优先，国内可达；DDG 兜底）
# ---------------------------------------------------------------------------
def http_get(url, timeout=15, max_bytes=300000):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8", "replace")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data)


def strip_html(page):
    p = _Text()
    try:
        p.feed(page)
    except Exception:
        pass
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return re.sub(r"\n{2,}", "\n", text).strip()


HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"


def search_hn_algolia(q, n=5):
    """HN Algolia 讨论检索（免 key、无 Cloudflare）。

    返回该产品的 HN 讨论页（news.ycombinator.com/item?id=<objectID>），
    外加帖子指向的外部链接（多半是产品官网）。评论区是「真话」来源，
    比标题可信得多；外部链接则能更快定位官网。

    边界：Algolia 只索引 HN 内容，**查不到产品官网以外的通用页面**，
    所以它替代不了 cn.bing —— 这里只当补充证据源，排在所有通用搜索之前；
    无讨论时返回空，不阻塞 bing/ddg 兜底。
    """
    try:
        raw = http_get("%s?query=%s&tags=story&hitsPerPage=%d"
                       % (HN_ALGOLIA_SEARCH, urllib.parse.quote(q), n), timeout=12)
        data = json.loads(raw)
    except Exception:                                     # noqa: BLE001
        return []
    out = []
    for h in (data.get("hits") or []):
        oid = h.get("objectID")
        if oid:
            item = "https://news.ycombinator.com/item?id=%s" % oid
            if item not in out:
                out.append(item)
        ext = (h.get("url") or "").strip()
        if ext.startswith("http") and ext not in out:
            out.append(ext)
    return out


def search_bing(q):
    page = http_get("https://cn.bing.com/search?q=%s&count=15" % urllib.parse.quote(q),
                    timeout=12)
    out = []
    for m in re.finditer(r'<h2[^>]*><a[^>]+href="(http[^"]+)"', page):
        u = html_mod.unescape(m.group(1))
        if "bing.com" in u or "microsoft" in u.lower():
            continue
        out.append(u)
    return out


def search_ddg(q):
    page = http_get("https://html.duckduckgo.com/html/?q=%s" % urllib.parse.quote(q),
                    timeout=12)
    out = []
    for m in re.finditer(r'uddg=([^&"]+)', page):
        u = urllib.parse.unquote(m.group(1))
        if u.startswith("http") and u not in out:
            out.append(u)
    return out


def search(q, per_query=6):
    """返回去重后的 URL 列表。HN Algolia 当补充证据源排最前，两个通用引擎挂了就空着走。

    顺序：HN Algolia（讨论页 + 外链）→ cn.bing → DuckDuckGo。
    HN 最多贡献 3 条，给后面的通用搜索留名额 —— 它查不到官网，
    不能把 bing 挤掉（否则小众产品会完全搜不到）。
    """
    urls = []
    for fn, cap in ((search_hn_algolia, 3), (search_bing, per_query),
                   (search_ddg, per_query)):
        try:
            urls += fn(q)[:cap]
        except Exception:                                     # noqa: BLE001
            pass
        if len(urls) >= per_query:
            break
    seen, out = set(), []
    for u in urls:
        key = u.split("#")[0]
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:per_query]


# 正文少于这个字数就当作「没抓到正文」——多为纯 SPA 的空壳。
MIN_TEXT = 200

# 纯 SPA 的官网，正文全在 JS 里，strip_html 得到接近 0 字 —— 于是一个真实存在的
# 官网在 AI 眼里等于不存在（实测 stan.store：HTTP 200 但只有 2913 字节的 Vue 壳，
# 去标签后 0 字）。meta 与 <title> 是服务端渲染的：不含收入数字，但能说明
# 「这家是做什么的」，比空手强。_Text.SKIP 里有 "head"，所以这些标签本来就进不了
# strip_html 的结果，必须单独抠。
META_WANT = ("og:title", "og:description", "twitter:title", "twitter:description",
             "description", "og:site_name")
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
META_ATTR_RE = re.compile(r'([a-zA-Z][a-zA-Z:._-]*)\s*=\s*"([^"]*)"')


def page_meta(html_text):
    """从 <meta> / <title> 里抠页面摘要（服务端渲染的那部分）。"""
    out = []
    for m in META_TAG_RE.finditer(html_text or ""):
        attrs = {k.lower(): v for k, v in META_ATTR_RE.findall(m.group(0))}
        # 有的站点把 name 写成 ame（stan.store 就是这样），兜一下
        key = (attrs.get("property") or attrs.get("name")
               or attrs.get("ame") or "").lower()
        val = html_mod.unescape((attrs.get("content") or "").strip())
        if key in META_WANT and val and val not in out:
            out.append(val)
    t = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.S | re.I)
    if t:
        val = html_mod.unescape(re.sub(r"\s+", " ", t.group(1))).strip()
        if val and val not in out:
            out.append(val)
    return out[:6]


def _jsonld_blocks(html_text, per_block=1200, max_blocks=3):
    """抠出页面里的 JSON-LD 结构化数据（<script type="application/ld+json">）。

    TrustMRR 的挂牌价就只写在这里（schema.org/Offer 的 price 字段）——
    挂牌横幅本身由客户端渲染，可见正文里没有。超出 per_block 的大块
    （@graph 整谱）只摘报价相关字段，防止截断把 price 挤掉。
    """
    out = []
    for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html_text or "", re.S | re.I):
        raw = html_mod.unescape(m.group(1)).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        s = json.dumps(data, ensure_ascii=False)
        if len(s) > per_block and '"price"' in s:
            pairs = re.findall(
                r'"(price|priceCurrency|availability|name|description|@type)"'
                r'\s*:\s*("(?:[^"\\]|\\.)*"|[\d.]+)', s)
            if pairs:
                s = "{" + ",".join('"%s":%s' % p for p in pairs[:40]) + "}"
        out.append(s[:per_block])
        if len(out) >= max_blocks:
            break
    return out


def fetch_text(url, cap=6000):
    """抓一个页面转纯文本；真的没东西可给时返回 None（流程继续）。

    正文几乎为空（纯 SPA）时回落到 meta 摘要，并**标明这段是 meta** ——
    让 AI 知道它的证据强度弱于正文，而不至于以为官网什么都没有。
    """
    try:
        raw = http_get(url)
    except Exception:
        return None
    text = strip_html(raw)
    if len(text) < MIN_TEXT:
        metas = page_meta(raw)
        if not metas:
            return None
        text = ("（本页正文由 JS 渲染，抓不到；以下为页面的 meta 摘要）\n"
                + "\n".join(metas))
    else:
        # 正文很长时也要带上 meta 摘要 + JSON-LD：有些关键数字**只**写在这两处，
        # 可见正文里反而没有。2026-09-24 search1api 实测：TrustMRR 的挂牌横幅
        # 由客户端渲染，"listed for sale at $50,000" 只存在于 meta description
        # 与 JSON-LD Offer 里；只看可见文本会把真挂牌价当成「误读」纠错掉。
        extra = page_meta(raw) + _jsonld_blocks(raw)
        if extra:
            body_cap = max(MIN_TEXT, cap - 1400)
            text = (text[:body_cap]
                    + "\n（以下为本页 meta 摘要 / JSON-LD 结构化数据，"
                      "可能含正文没有的挂牌价、报价等字段）\n"
                    + "\n".join(extra))
    return text[:cap]


# ---------------------------------------------------------------------------
# LLM（OpenAI 兼容 /chat/completions）
# ---------------------------------------------------------------------------
def llm(messages, temperature=0.2):
    body = json.dumps({
        "model": AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        # 推理模型（如 deepseek-flash）的思考 token 也计入 max_tokens——
        # 实测核一条要烧 4000+（其中思考约 3600），2000/8000 都会被截断，
        # 这里放宽到 16000；不够时 finish_reason=length 会给出明确报错。
        "max_tokens": 16000,
    }).encode("utf-8")
    req = urllib.request.Request(
        AI_BASE + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + AI_KEY})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 只抛 "HTTP Error 400" 没法排障——服务端的 reason（模型名错、key 无效、
        # 参数不合法）都在响应体里，带出来写进任务日志。
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                    # noqa: BLE001
            pass
        raise RuntimeError("LLM 接口 HTTP %s%s" % (
            e.code, ("：" + detail) if detail else "")) from e
    ch = data["choices"][0]
    if ch.get("finish_reason") == "length":
        raise RuntimeError("LLM 回答被 max_tokens 截断（finish_reason=length）"
                           "——模型思考太长，正文没输出完整 JSON")
    return ch["message"]["content"]


SYSTEM_PROMPT = """你是「拆解海外」案例库的核实研究员。给你一条候选案例的资料、
它当前卡在哪，以及几段刚抓回来的网页原文（每段标注了来源 URL）。

铁律：
1. 只依据给的原文判断，原文里没有的不要脑补。
2. 三道门槛是三态：填 "yes"（原文有明确证据支持）、"no"（原文有明确反证，
   如已关站/已下架/明确不是付费生意）、"unknown"（原文没提或不足以判断）。
   拿不准就填 "unknown" —— 别填 false，也别为了让候选通过而凑一个 yes。
   "no" 是有分量的：三道里一道都没成立、却有明确反证时，这条会被判不进库。
3. 来源 URL 只能用原文段里出现的，禁止编造。
4. 收入口径是重点：分清 MRR / ARR / run-rate / 累计收入 / 平台流水(GMV) / 毛利。
   「累计收入被当年化」「平台流水被当收入」是这个库踩过的真实坑。
5. 原文摘录太短、不足以支撑判断的项，一律 unknown 并在 evidence 里说明缺什么。
6. sources 里的 kind 按「这条来源**凭什么可信**」来填，不要按站点名气：
   stripe    = 支付网关侧的验证数据。**TrustMRR 的收入页就属于这一类** ——
               它的数字由 Stripe 等支付商 API 直读，不是编辑写的。
   official  = 公司自己的官网 / 定价页 / 官方公告
   founder   = 创始人在公开渠道的自述（帖子、访谈、Newsletter）
   press     = 媒体报道
   review    = 第三方评测 / 用户评价
   secondary = 二手转述（聚合站、搬运文、论坛转贴）
   这一项判错会直接改变核实等级与质量分，所以拿不准时选**更低**的那一档，
   并在 evidence 里写清为什么。

输出一个 JSON 对象（不要 markdown 围栏）：
{
 "still_alive": "yes|no|unknown", "still_alive_evidence": "str",
 "is_business": "yes|no|unknown", "is_business_evidence": "str",
 "solo_possible": "yes|no|unknown", "solo_possible_evidence": "str",
 "verification": "stripe|official|partial|founder|disputed",
 "caliber": "arr|mrr|run_rate|lifetime|gmv|gross",
 "caliber_reason": "str", "caliber_consistent": bool,
 "number": "写进库的那个数字，带 $ 与单位",
 "sources": [{"label":"str","url":"str","kind":"stripe|official|press|review|founder|secondary"}],
 "corrections": [{"claim":"别人的错误说法","truth":"实际是","source":"url"}],
 "bonus": {"founder_disclosure":bool,"pricing_confirmed":bool,
           "replicable_low":bool,"secondary_corroborated":bool},
 "summary": "一句话概括这个生意",
 "confidence": 0.0
}"""

USER_TMPL = """【候选资料】
%(cand)s

【当前卡点】
%(blockers)s

【网页原文摘录】
%(pages)s

请按系统提示的 JSON 结构输出。"""


# ---------------------------------------------------------------------------
# 后台 API（登录 → 存草稿 → 发布）
# ---------------------------------------------------------------------------
class Admin(object):
    """后台 API 客户端。

    两种用法：给 password 走登录（CLI）；给 cookie 直接带身份（server 自己
    跑流水线时自己铸 token，免得再把明文密码翻出来）。
    """

    def __init__(self, base, password=None, cookie=None):
        self.base = base.rstrip("/")
        if cookie:
            self.cookie = cookie
        else:
            self.cookie = self._login(password)

    def _login(self, password):
        body = json.dumps({"password": password}).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/api/login", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
                setc = r.headers.get("Set-Cookie", "")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", "")
            except Exception:
                pass
            raise AdminError("登录失败（%s）%s" % (e.code, detail))
        except urllib.error.URLError:
            raise AdminError("连不上后台 %s —— 先启动 server.py" % self.base)
        m = re.search(r"case_admin=([^;]+)", setc)
        if not m:
            raise AdminError("后台没有下发登录 cookie")
        return "case_admin=" + m.group(1)

    def _req(self, method, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Cookie": self.cookie})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8")), e.code
            except Exception:
                return {"error": "HTTP %s" % e.code}, e.code

    def get_draft(self, cid):
        d, st = self._req("GET", "/api/candidates/%s/verification" % urllib.parse.quote(cid))
        return (d or {}).get("draft") if st == 200 else None

    def put_draft(self, cid, draft):
        d, st = self._req("PUT", "/api/candidates/%s/verification" % urllib.parse.quote(cid), draft)
        if st != 200:
            raise AdminError("存草稿失败（%s）：%s" % (st, (d or {}).get("error")))
        return (d or {}).get("result") or {}

    def promote(self, cid):
        d, st = self._req("POST", "/api/candidates/%s/promote" % urllib.parse.quote(cid), {})
        return d, st


# ---------------------------------------------------------------------------
# 单条流水线
# ---------------------------------------------------------------------------
# 采集站挂牌页根地址（与 harvest.py 的 TRUSTMRR_SITE 同源，此处单独定义以免
# 为了一个常量去 import 整个采集脚本）。候选 id 即挂牌 slug。
TRUSTMRR_SITE = "https://trustmrr.com"

# TrustMRR 官方给 AI 用的**免 key** 端点（见 https://trustmrr.com/llms.txt）：
#   GET  /startup/{slug}.md     单个挂牌页的干净 Markdown（含挂牌价/倍数/逐日收入）
#   GET  /api/ai/discovery      25 条新入库 + 25 条增长最快，**每条带 website**
#   POST /api/mcp/discovery     MCP；其中 get_startup(slug) 要 OAuth，用不了
# llms.txt 明确要求「别爬渲染后的 HTML 拿全量市场数据」，所以 TrustMRR 侧一律
# 走上面这两个官方通道，不套无头浏览器。
TRUSTMRR_DISCOVERY_API = TRUSTMRR_SITE + "/api/ai/discovery"

_DISCOVERY_CACHE = {"data": None}


def trustmrr_discovery(force=False):
    """拉 TrustMRR discovery 快照，返回 {slug: {name, website, ...}}。

    2026-09-24 实测：200 / 33KB，两个固定分组各 25 条，含 revenue(last30Days/
    mrr/total)、category、paymentProvider、website。**免 key、限次不分页**。

    两个用途：
      1. 给候选补网址 —— 候选池本来 0/28 带 URL，导致 known_urls 恒为空、
         每次退化成 cn.bing 搜索（小众外文站基本搜不到）；
      2. 当新案例发现源用。

    失败一律返回 {}（不抛），抓取流程照常继续。
    """
    if _DISCOVERY_CACHE["data"] is not None and not force:
        return _DISCOVERY_CACHE["data"]

    out = {}
    try:
        raw = http_get(TRUSTMRR_DISCOVERY_API, timeout=20, max_bytes=400000)
        data = json.loads(raw)
    except Exception:
        _DISCOVERY_CACHE["data"] = out
        return out

    for group in ("recentlyAddedStartups", "fastestGrowingStartups"):
        for it in (data.get(group) or []):
            slug = (it.get("slug") or "").strip()
            if slug:
                out[slug] = it
    _DISCOVERY_CACHE["data"] = out
    return out


# ---------------------------------------------------------------- 无头浏览器
#
# 只用来对付 Cloudflare 挡住的站（Indie Hackers / Product Hunt 实测 403）
# 和纯 SPA。TrustMRR 侧不要用它 —— 官方 llms.txt 写了别爬渲染后的 HTML，
# 而且 .md 端点本来就比渲染结果全。
def _find_browser():
    for p in (os.environ.get("CASE_LIB_CHROME"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if p and os.path.exists(p):
            return p
    return None


def render_page(url, timeout=40, budget_ms=8000):
    """用本机无头 Chrome 渲染后返回纯文本；不可用/失败返回 None。

    2026-09-24 实测 `--headless --dump-dom` 能把客户端渲染的挂牌横幅渲出来
    （静态 HTML 里抓不到），代价是每页 5–8 秒，所以只在常规抓取拿不到东西时兜底。
    """
    exe = _find_browser()
    if not exe or not (url or "").startswith("http"):
        return None
    try:
        proc = subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox",
             "--disable-extensions", "--no-first-run",
             "--virtual-time-budget=%d" % budget_ms, "--dump-dom", url],
            capture_output=True, timeout=timeout)
    except Exception:
        return None
    html = (proc.stdout or b"").decode("utf-8", "replace")
    if len(html) < 500:
        return None
    text = strip_html(html)
    if len(text) < MIN_TEXT:
        metas = page_meta(html)
        if not metas:
            return None
        text = ("（本页由无头浏览器渲染后仍几乎无正文；以下为 meta 摘要）\n"
                + "\n".join(metas))
    return text[:6000]


def known_urls(cand):
    """候选自带的已知地址：官网、来源详情页。

    这条很关键 —— 搜索对「小众产品」经常完全无效。实测查 GojiberryAI，
    Bing 返回的是「无结果」兜底模块（一堆高校页面），因为该产品根本没被收录；
    但候选自己就带着 TrustMRR 详情页（3.4k 字正文）和官网，那才是最有价值的原文。

    顺序即优先级：详情页（带数字）在前，官网在后。
    """
    out = []

    def add(u):
        u = (u or "").strip()
        if u.startswith("http") and u not in out:
            out.append(u)

    cid = (cand.get("id") or "").strip()

    # 候选 id 若在 TrustMRR discovery 快照里（免 key，25 新入库 + 25 增长最快各一次请求），
    # 直接拿它自带的官网 —— 候选池本来 0/28 带 URL，这条用来补空。
    info = trustmrr_discovery().get(cid)
    if info:
        add(info.get("website"))

    add(cand.get("source_url"))
    add(cand.get("website"))
    for s in (cand.get("sources") or []):
        if isinstance(s, dict):
            add(s.get("url"))

    # 兜底：候选池 28 条**没有任何一条**带 URL（2026-09-24 实测），
    # 于是 known_urls 恒为空、每次都退化成「纯搜索」——而搜索走 cn.bing.com，
    # 查小众外文站基本查不到（le19emetrou 那轮抓回 5 个百度知道/作业帮页）。
    # 候选 id 本身就来自采集站 slug，挂牌页可以直拼；实测 28 条里大部分有。
    if cid and not out:
        add("%s/startup/%s" % (TRUSTMRR_SITE, cid))

    # TrustMRR 挂牌页一律改成官方 .md：2026-09-24 实测它返回 13KB 干净 Markdown，
    # 比渲染后的 HTML 更全（挂牌价、倍数、逐日收入表都在），且 llms.txt 明确把
    # .md 定为 AI 入口、要求别爬渲染后的 HTML。.md 排在前，先占抓取名额。
    md, rest = [], []
    for u in out:
        m = re.match(r"^(https://trustmrr\.com/startup/[^?#\s]+?)(?:\.md)?/?$", u)
        if m:
            md.append(m.group(1) + ".md")
        rest.append(u)
    seen, merged = set(), []
    for u in md + rest:
        if u not in seen:
            seen.add(u)
            merged.append(u)
    return merged


def collect_pages(cand, max_pages=5, log=None):
    """抓原文：**先抓候选自带的已知地址，不够再用搜索补**。

    返回 (pages, used_urls)。顺序很重要 —— 先去拿已知的好料，避免搜索
    返回的垃圾把名额占满（历史上 eloquent 那次就是搜索把词典页塞满了）。

    这里就滤掉与「核实一个软件生意」无关的域名（词典/音乐/电商…）：
    黑名单以前只在 map_ai_to_draft 阶段生效，那时名额早被垃圾占了
    （实测 stan 那轮 5 个名额里 4 个给了音乐站与 baidu 问答）。
    候选自带的已知地址不过滤 —— 那是录入时的选择，有问题该暴露而不是静默丢弃。
    """
    def say(msg):
        (log or (lambda _m: None))(msg)

    pages, used_urls = [], []
    skipped = 0

    rendered = 0
    for u in known_urls(cand):
        if len(used_urls) >= max_pages:
            break
        # 官方 .md 已经抓到了，就别再去抓它的 HTML 孪生页 —— 内容重复、
        # 白占一个名额（13KB Markdown 本来就是那页 HTML 的干净版）。
        if u + ".md" in used_urls:
            continue
        text = fetch_text(u)
        if text is None or len(text) < MIN_TEXT:
            # 常规抓取拿不到东西（Cloudflare 403 / 纯 SPA）→ 无头 Chrome 再试一次。
            # 只在这里兜底：TrustMRR 侧有官方 .md，不会走到这步。
            text = render_page(u)
            if text:
                rendered += 1
        if text:
            used_urls.append(u)
            pages.append("----- 来源URL: %s -----\n%s" % (u, text))

    if pages:
        say("   已知地址：抓到 %d 页%s"
            % (len(pages), "（其中 %d 页靠无头浏览器渲染）" % rendered if rendered else ""))

    for q in build_queries(cand):
        if len(used_urls) >= max_pages:
            break
        for u in search(q):
            if u in used_urls or len(used_urls) >= max_pages:
                continue
            if is_irrelevant_source(u):
                skipped += 1
                continue
            text = fetch_text(u)
            if text:
                used_urls.append(u)
                pages.append("----- 来源URL: %s -----\n%s" % (u, text))
    if skipped:
        say("   搜索跳过 %d 个无关域名（词典/音乐/电商等，不占名额）" % skipped)
    return pages, used_urls


def verify_one(cand, admin, publish=False, min_score=0, log=None):
    """核一条候选。log 缺省用 print；server 里传一个收集行的小函数。

    min_score：发布所需的最低质量分（60 = 只发精品档）。判定分够但
    质量分不够时，草稿照存、发布跳过 —— 档位这事让规则引擎说了算。
    """
    def say(msg):
        (log or print)(msg)

    cid = cand["id"]
    old = admin.get_draft(cid) or VR.blank()
    cand = dict(cand, _old_musts=(old.get("musts") or []))

    blockers, _ = current_blockers(old)
    gk = [k for kind, k, _ in blockers if kind == "gate"]
    mk = [k for kind, k, _ in blockers if kind == "must"]
    say("== %s  %s" % (cid, cand.get("name") or ""))
    if gk:
        say("   卡点：门槛 %d 项（%s）" % (len(gk), " ".join(gk)))
    if mk:
        say("   卡点：必填缺 %d 项（%s）" % (len(mk), " ".join(mk)))

    # 1) 抓原文：先已知地址，不够再用搜索补
    pages, used_urls = collect_pages(cand, max_pages=5, log=say)
    if not pages:
        say("   没抓到任何原文，无法负责任地填草稿 —— 跳过")
        return {"id": cid, "name": cand.get("name"), "ok": False,
                "why": "没抓到任何原文，无法负责任地填草稿"}

    # 2) LLM 判读
    cand_brief = {k: cand.get(k) for k in
                  ("id", "name", "name_en", "origin", "one_liner", "category",
                   "metrics", "models", "note", "blocking")}
    user = USER_TMPL % {
        "cand": json.dumps(cand_brief, ensure_ascii=False, indent=1),
        "blockers": "\n".join("- %s" % label for _, _, label in blockers) or "（空）",
        "pages": "\n\n".join(pages)[:24000],
    }
    ai = extract_json(llm([{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": user}]))

    # 3) 映射成草稿并存回后台
    say("   检索：抓到 %d 页原文" % len(pages))
    draft = map_ai_to_draft(cand, ai, publish)
    result = admin.put_draft(cid, draft)
    say("   AI：口径=%s 等级=%s 来源=%d 置信度=%.0f%%" % (
        draft["caliber"] or "-", draft["verification"] or "-",
        len(draft["sources"]), (ai.get("confidence") or 0) * 100))
    say("   判定：%s（质量分 %s）" % (result.get("verdict"), result.get("bonus_score")))
    # 「AI 核不动」的项单独列一行 —— 它不挡发布，但人得知道哪些没核到
    _unver = [g["label"] for g in (result.get("unverified_gates") or [])]
    _warns = [w["label"] for w in (result.get("warnings") or [])]
    if _unver:
        say("   门槛未确认：%s（人工在工作台勾上后点发布）" % "；".join(_unver))
    if _warns:
        say("   提醒：%s" % "；".join(_warns))

    # 4) 够格就发布（服务端会再过一遍规则引擎，这里是第二次闸门）
    #
    # 返回结构是后台「这一轮审了哪些」那张结果表的全部原料：
    # 光有 verdict 不够 —— 界面要能一条条说清「它是什么、审成什么样、
    # 还差什么、点哪儿去接着做」，人才接得下去手。
    out = {"id": cid,
           "name": cand.get("name"),
           "ok": True,
           "verdict": result.get("verdict"),
           "tier": result.get("tier"),
           "tier_label": result.get("tier_label"),
           "score": result.get("bonus_score"),
           "caliber": draft.get("caliber") or "",
           "verification": draft.get("verification") or "",
           "source_count": len(draft.get("sources") or []),
           "pages": len(pages),
           "published": False}
    # 判定里没通过的那些项，逐条列出来 —— 这正是人要接着做的事
    out["unverified"] = [g.get("label") for g in (result.get("unverified_gates") or [])]
    out["denied"] = [g.get("label") for g in (result.get("denied_gates") or [])]
    out["warnings"] = [w.get("label") for w in (result.get("warnings") or [])]
    out["remain"] = [m.get("label") if isinstance(m, dict) else str(m)
                     for m in (result.get("missing") or [])]
    if publish and result.get("publishable"):
        if min_score and (result.get("bonus_score") or 0) < min_score:
            out["held"] = True
            say("   → 质量分 %s 低于阈值 %d，只存草稿不发布" % (
                result.get("bonus_score"), min_score))
            return out
        d, st = admin.promote(cid)
        if st in (200, 201):
            out["published"] = True
            out["case_id"] = (d or {}).get("case", {}).get("id")
            say("   → 已发布为案例：%s（%s）" % (
                out["case_id"], result.get("tier_label")))
        else:
            out["publish_error"] = (d or {}).get("error")
            say("   → 发布被拒：%s" % out["publish_error"])
    elif not publish and out["remain"]:
        say("   还差：%s（人工在工作台确认后点发布）" % "；".join(out["remain"]))
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def load_json(name):
    with open(os.path.join(ROOT, "data", name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def plan_data(include_small=False, limit=None, index=None):
    """给后台 GUI 的结构化 plan：每条可选候选的卡点与判定（离线，只读）。

    带上前端要显示的初筛结论（档位 / 分数 / 下一步）—— 后台因此能直接告诉人
    「这条为什么排在前面」，而不是只给一个没有排序依据的清单。
    """
    cands = load_json("candidates")
    index = index if index is not None else triage_index()
    selected = pick_candidates(cands, limit=limit, include_small=include_small,
                               index=index)
    try:
        drafts = load_json("verifications")
    except Exception:
        drafts = {}
    items = []
    for c in selected:
        blockers, r = current_blockers(drafts.get(c["id"]))
        t = TR.score_record(c, "candidate", index)
        items.append({
            "id": c.get("id"),
            "name": c.get("name") or "",
            "headline": (c.get("metrics") or {}).get("headline") or "",
            "gate_keys": [k for kind, k, _ in blockers if kind == "gate"],
            "must_keys": [k for kind, k, _ in blockers if kind == "must"],
            "verdict": r.get("verdict"),
            "publishable": r.get("publishable"),
            "grade": t["grade"],
            "grade_label": t["grade_label"],
            "score": t["score"],
            "action": t["action"],
        })
    return {"total": len(cands), "selectable": len(selected), "items": items}


def print_triage(limit=None, ids=None, include_small=False):
    """离线打印「本次会按什么顺序核、为什么」—— 零成本、不联网、不改动。"""
    index = triage_index()
    cands = load_json("candidates")
    selected = pick_candidates(cands, limit=limit, ids=ids or None,
                               include_small=include_small, index=index)
    scored = [(c, TR.score_record(c, "candidate", index)) for c in cands]
    by_grade = {}
    for _, r in scored:
        by_grade.setdefault(r["grade"], 0)
        by_grade[r["grade"]] += 1

    print("候选池 %d 条 → 本次可选 %d 条（按初筛分排序，零成本、不联网）\n"
          % (len(cands), len(selected)))
    if not selected:
        print("  没有可处理的候选：占位 / 未获取 / 体量太小 / 材料已齐 都已跳过。")
    for c in selected:
        r = TR.score_record(c, "candidate", index)
        print("  %-9s %3d  %-24s %s"
              % (r["grade_label"], r["score"], (c.get("id") or "")[:24],
                 (r["hint"] or r["action"])[:52]))
        for h in r["hits"][:2]:
            print("              · %s" % h["why"])
    print("\n  全池分级：%s"
          % " · ".join("%s %d" % (TR.GRADE_LABEL[g], by_grade.get(g, 0))
                       for g in TR.GRADE_ORDER))
    print("  完整分级（含采集队列，跑 scripts/triage.py 看）")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="AI 自动核实流水线（找卡点→补材料→核实→发布）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="离线列出每条候选的卡点，不联网不改动")
    g.add_argument("--triage", action="store_true",
                   help="离线列出本次的核实顺序与理由（零成本初筛排序）")
    g.add_argument("--all", action="store_true", help="全部可选候选（跳过占位/太小）")
    g.add_argument("--id", action="append", default=[], help="指定候选 id，可多次")
    ap.add_argument("--limit", type=int, default=None, help="最多处理几条（默认 5）")
    ap.add_argument("--include-small", action="store_true", help="连「体量太小」的也处理")
    ap.add_argument("--publish", action="store_true",
                    help="够格的直接发布成案例（human_read 仍不勾：它只能由人勾，"
                         "案例上会因此记成「未经人工核读」）")
    ap.add_argument("--min-score", type=int, default=0,
                    help="发布所需最低质量分（60=只发精品档）")
    ap.add_argument("--password", default=os.environ.get("CASE_LIB_ADMIN_PASSWORD"),
                    help="后台密码（默认读 CASE_LIB_ADMIN_PASSWORD）")
    args = ap.parse_args()

    cands = load_json("candidates")
    index = triage_index()
    selected = pick_candidates(cands, limit=args.limit, ids=args.id or None,
                               include_small=args.include_small, index=index)
    if args.all:
        selected = pick_candidates(cands, limit=None, ids=None,
                                   include_small=args.include_small, index=index)
    if args.triage:
        return print_triage(limit=args.limit, ids=args.id or None,
                            include_small=args.include_small)
    if not selected:
        print("没有可处理的候选（占位条目与「未获取」已跳过；--all --include-small 可放宽）")
        return 0

    drafts = {}
    try:
        drafts = load_json("verifications")
    except Exception:
        pass

    if args.plan:
        print("候选池 %d 条，可选 %d 条（--plan 只看不动）\n" % (len(cands), len(selected)))
        for c in selected:
            blockers, r = current_blockers(drafts.get(c["id"]))
            gk = [k for kind, k, _ in blockers if kind == "gate"]
            mk = [k for kind, k, _ in blockers if kind == "must"]
            print("== %-24s %s" % (c["id"], c.get("name") or ""))
            print("   数字：%s" % ((c.get("metrics") or {}).get("headline") or "未获取")[:60])
            if gk:
                print("   卡点：门槛 %d 项（%s）" % (len(gk), " ".join(gk)))
            if mk:
                print("   卡点：必填缺 %d 项（%s）" % (len(mk), " ".join(mk)))
            if not blockers:
                print("   卡点：（无——草稿已齐，去后台点发布）")
            print("   判定：%s" % r["verdict"])
        return 0

    if not AI_KEY:
        raise SystemExit("[!] 缺 CASE_LIB_AI_KEY 环境变量（LLM 接口密钥）。"
                         "只想看卡点请用 --plan。")
    if not args.password:
        raise SystemExit("[!] 缺后台密码：--password 或 CASE_LIB_ADMIN_PASSWORD。"
                         "密码忘了跑 python scripts/auth.py --reset")

    try:
        admin = Admin(ADMIN_BASE, password=args.password)
    except AdminError as e:
        raise SystemExit("[!] %s" % e)

    print("AI：%s @ %s｜后台：%s｜%s\n" % (
        AI_MODEL, AI_BASE, ADMIN_BASE,
        "核完直接发布" if args.publish else "只存草稿（--publish 才会发布）"))

    done, published, failed, held = 0, 0, 0, 0
    for c in selected:
        try:
            r = verify_one(c, admin, publish=args.publish, min_score=args.min_score)
            done += 1
            if r.get("published"):
                published += 1
            elif r.get("held"):
                held += 1
            elif not r.get("ok", True):
                failed += 1
        except AdminError as e:
            failed += 1
            print("== %s  [失败] %s" % (c["id"], e))
        except Exception as e:
            failed += 1
            print("== %s  [失败] %s" % (c["id"], e))
        print("")

    print("=" * 56)
    print("处理 %d 条｜已发布 %d 条｜够格但分不够 %d 条｜未成 %d 条" % (
        done, published, held, failed))
    if not args.publish:
        print("草稿已存好：后台「候选池」里草稿进度会显示，人工确认后点发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
