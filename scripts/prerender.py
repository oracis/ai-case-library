#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把精写案例预渲染成一个个独立的静态页面，给搜索引擎、社交平台和「阅读原文」用。

为什么需要这一步（build_static.py 不是已经能出站了吗）：
    build_static.py 产出的 index.html 是纯客户端渲染的单页应用（SPA）——
    所有内容都靠 app.js 从 data.js 注入。人用浏览器打开没问题，但
    搜索引擎爬虫、社交平台预览抓取、以及任何直达链接，拿到的都是一个空壳
    加一堆 <script>：它们读不到正文。案例页也就无法被收录、无法被分享预览。

    这一步把每条案例渲染成 case/<id>.html，正文以写死的 HTML 形式内联在
    页面里，不依赖任何 JS 就能读完。同时产出：
        case/index.html   静态总目录（爬虫入口，也是 SPA 的降级导航）
        sitemap.xml       告诉搜索引擎有哪些地址
        robots.txt        放行爬虫并指向 sitemap
        index.html 内注入一段 <noscript> 案例清单（禁用 JS 时的降级入口）

渲染复用的是 static/style.css 里现成的类名（.d-head / .corr / .rep …），
所以和主站视觉完全一致。该样式表会**内联**进每个页面（见下面 base_css 的说明），
页面不引入 app.js，单页约 50 KB —— 换来的收益是任何打开方式都不会掉样式。

元信息（站点名、公众号、社群、仓库地址）从 data/site.json 读，缺失就用默认值
—— 也就是说这个文件不存在也能构建，只是页脚的引流位是空的、sitemap 里不写
绝对地址。填上之后 sitemap / canonical / og:url 才会是完整 URL。

用法：
    python scripts/prerender.py --out dist            # 单独跑（一般不用）
    python scripts/prerender.py --check --out dist    # 只校验已有产物
（正常路径是被 build_static.py 调用，见那边第 [3/5] 步。）
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from text_clean import clean_snapshot_marks          # noqa: E402

CASE_DIR = "case"                 # 案例页所在的子目录
ANTI_DIR = "anti"                 # 反向样本区（「这些别照着做」）的静态页目录
INDEX_FILE = "index.html"         # 静态总目录的落点

# ------------------------------------------------------------------ 文案常量
# 与 static/app.js 顶部的同名常量保持一致；改了那边记得同步这里，
# 否则同一个字段在抽屉里和独立页上会显示成两个词。
V_LABEL = {
    "stripe": "支付网关验证",
    "official": "官方披露",
    "partial": "口径待核",
    "founder": "创始人自报",
    "disputed": "数字有出入",
    "unverified": "未核实",
    # 显示用：案例聚合/导航站。不在 verify_rules.VERIFICATIONS 的五档里，
    # 但 anti_cases.json 的 33 条 verification 全是它（见 build_anti_cases.py）。
    "aggregator": "聚合站转述",
}
# 必须与 static/app.js 的 KIND_LABEL **逐键一致**（test_prerender 会对照），
# 键集合又要覆盖 verify_rules.SOURCE_TIERS。
# 2026-10-15 补齐 revenuecat / secondary：前者是订阅数据商（App 收入估算），
# 后者是中文二手转述 —— 本库踩过的两个数字错误都来自这一层，
# 独立页上必须把它显示成「二手转述」，不能退化成笼统的「来源」两个字。
KIND_LABEL = {
    "stripe": "支付验证", "official": "官方", "press": "报道",
    "review": "核查", "revenuecat": "订阅数据商",
    "founder": "自述", "secondary": "二手转述",
}
REP_LABEL = {
    "tech": "技术门槛", "distribution": "获客门槛",
    "capital": "资金门槛", "timing": "时机依赖",
}
REP_NOTE = "1 分＝最容易，5 分＝最难。四个维度里只要有一个是 5 分，一个人基本做不了。"

# 反向样本区的两个枚举标签。⚠ 必须与 static/app.js 的 ANTI_ROLE / CAL_LABEL
# 逐键一致（test_prerender 会对照）—— 同一字段在抽屉里和静态页上显示成两个词，
# 读者会以为是两个不同的分类。
ANTI_ROLE = {
    "structural": "抄不动 · 结构",
    "license": "抄不动 · 资格",
    "caliber": "别误读 · 数字",
}
CAL_LABEL = {
    "mrr": "真·月收入",
    "arr": "年化收入（ARR）",
    "annual": "年营收",
    "funding": "融资额",
    "valuation": "估值",
    "gmv": "平台流水（GMV）",
    "price": "定价档位",
    "lifetime": "累计收入",
    "unknown": "年 / 月不明",
    "invalid": "口径对不上",
}

CHINA_DIM_LABEL = {
    "demand": "付费意愿", "payment": "支付可达", "compliance": "合规空间",
    "acquisition": "获客迁移", "localization": "改造成本", "competition": "竞争空位",
}
CHINA_DIM_ORDER = ["demand", "payment", "compliance",
                   "acquisition", "localization", "competition"]

SOLO_DIM_LABEL = {
    "build": "造得出来", "delivery": "单人交付", "reach": "够得着客户",
    "capital": "启动轻", "window": "窗口还开着",
}
SOLO_DIM_ORDER = ["build", "delivery", "reach", "capital", "window"]
SOLO_NOTE = "5 分＝最容易一个人做。其中「单人交付」是新增维度：要团队、要资质、要 7×24 值守的，一票否决。"

# 四象限文案。与 server.py 的 QUAD_META / score_solo_fit.py 的 QUADRANTS 一致。
QUAD_META = {
    "go": ("可以开干", "两边都过线：一个人能做，国内也有市场。"),
    "export": ("能做，但别在国内卖", "技术完全在手，卡在国内的需求或支付土壤上。出口做更顺。"),
    "partner": ("有市场，但一个人啃不动", "需求是真的，门槛在资质、大客户销售或团队交付上。"),
    "skip": ("别碰", "两个方向都不过线。"),
}
MEDAL_LABEL = {"gold": "金", "silver": "银", "bronze": "铜"}
MEDAL_ICON = {"gold": "🥇", "silver": "🥈", "bronze": "🥉"}

DEFAULT_SITE = {
    "title": "拆解海外",
    "subtitle": "overseas teardowns",
    "tagline": "拆解海外已跑通的软件生意，核实每一个数字，判断一个人能不能做、能不能搬回国内。",
    "url": "",                    # 有自定义域名后填这里，sitemap / canonical 才会是绝对地址
    "repo": "https://github.com/oracis/ai-case-library",
    # hint 不要以「公众号」开头：模板（app.js 的顶部条与本文件的 505/535 行）
    # 已经在名字前面写了「公众号」，hint 再带一遍就会读成「公众号 X 公众号同步更新…」。
    "wechat": {"name": "万物解释者", "hint": "同步更新拆解长文"},
    "community": {"name": "社群", "url": "", "hint": "每日线索 · 每周深度"},
    "icp": "",                    # 备案号，备案下来后填这里
}

# 案例页（case/*.html）的图标声明。路径走 ../icons/ —— 这些页面比主页深一层。
# 图形来自 scripts/gen_logo.py，和主页侧栏、PWA 图标、小程序头像同一套几何；
# 抽成常量是因为下面两个 head 模板都要用，写两遍迟早改一处漏一处。
PAGE_FAVICON = (
    '<link rel="icon" href="../icons/logo.svg" type="image/svg+xml">\n'
    '<link rel="icon" href="../icons/icon-192.png" type="image/png" sizes="192x192">\n'
)

# 案例页里内联的一小段补充样式。主样式表（style.css）是给 SPA 的网格布局写的，
# 独立阅读页只需要一个居中窄栏，所以这里补一点，避免为了 24 个页面去改主样式表。
PAGE_CSS = """\
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text-2);
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.doc{max-width:760px;margin:0 auto;padding:0 20px 80px}
.topbar{position:sticky;top:0;z-index:5;backdrop-filter:blur(8px);
  background:rgba(14,17,22,.86);border-bottom:1px solid var(--border);
  margin:0 -20px 26px;padding:12px 20px;display:flex;align-items:center;gap:12px}
.topbar .home{color:var(--text);text-decoration:none;font-weight:650;font-size:14px}
.topbar .home:hover{color:var(--accent)}
.topbar .sep{color:var(--text-3);font-size:12px}
.topbar .cat{font-size:12px;color:var(--text-3)}
.crumb{font-size:12px;color:var(--text-3);margin-bottom:14px}
.crumb a{color:var(--text-3);text-decoration:none}
.crumb a:hover{color:var(--accent)}
.pager{display:flex;justify-content:space-between;gap:14px;margin-top:34px;
  padding-top:20px;border-top:1px solid var(--border)}
.pager a{flex:1;min-width:0;text-decoration:none;color:var(--text-2);font-size:13px;
  border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:var(--surface)}
.pager a:hover{border-color:var(--accent);color:var(--text)}
.pager a.next{text-align:right}
.pager small{display:block;color:var(--text-3);font-size:11px;margin-bottom:3px}
.pager b{font-weight:600;color:var(--text);display:block;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.promo{margin-top:38px;border:1px solid var(--border);border-radius:10px;
  background:var(--surface);padding:18px 20px}
.promo h3{margin:0 0 4px;font-size:14px;color:var(--text);letter-spacing:.2px}
.promo p{margin:0 0 12px;font-size:12.5px;color:var(--text-3);line-height:1.65}
.promo .row{display:flex;flex-wrap:wrap;gap:10px}
.promo .btn{display:inline-block;font-size:12.5px;text-decoration:none;padding:7px 13px;
  border-radius:7px;border:1px solid var(--border-2);color:var(--text-2);
  background:var(--surface-2,var(--surface))}
.promo .btn:hover{border-color:var(--accent);color:var(--text)}
.promo .btn.primary{border-color:var(--accent);color:var(--accent)}
.promo .btn.ghost{color:var(--text-3);border-style:dashed;background:transparent}
/* 顶部引导条在文档流里（.doc 内），靠负 margin 撑满内容宽度 */
.promo-bar.inpage{margin:0 -20px 22px}
.docfoot{margin-top:28px;font-size:12px;color:var(--text-3);line-height:1.8}
.docfoot a{color:var(--text-3)}
.docfoot a:hover{color:var(--accent)}
/* 静态总目录页 */
.cat-list{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.cat-list li{border:1px solid var(--border);border-radius:9px;background:var(--surface);
  padding:13px 15px}
.cat-list a{color:var(--text);text-decoration:none;font-weight:600;font-size:14.5px}
.cat-list a:hover{color:var(--accent)}
.cat-list .meta{margin-top:5px;font-size:12px;color:var(--text-3);
  display:flex;flex-wrap:wrap;gap:10px}
.cat-list .lin{color:var(--text-2);font-size:13px;margin-top:5px}
.sec-title{font-size:19px;color:var(--text);margin:0 0 6px;letter-spacing:-.2px}
.sec-sub{color:var(--text-3);font-size:13px;margin:0 0 20px}
/* 反向样本区（anti/index.html）：一篇一块，块里逐条列判据 */
.anti-blk{margin:0 0 36px}
.anti-blk .sec-title{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.anti-n{font-family:var(--mono);font-size:12px;color:var(--text-3);font-weight:400}
.anti-judge{font-size:12.5px;color:var(--text-3);margin:0 0 10px}
.anti-lead{font-size:13.5px;color:var(--text-2);margin:0 0 14px;max-width:70ch;line-height:1.72}
.anti-who{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px}
.anti-who b{font-size:14.5px;color:var(--text)}
.anti-role{font-size:11px;padding:1px 8px;border-radius:20px;color:var(--text-3);
  border:1px solid var(--border-2);white-space:nowrap}
.anti-role[data-k="structural"]{color:var(--v-disputed);border-color:color-mix(in srgb,var(--v-disputed) 38%,transparent)}
.anti-role[data-k="license"]{color:var(--v-partial);border-color:color-mix(in srgb,var(--v-partial) 38%,transparent)}
.anti-role[data-k="caliber"]{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 38%,transparent)}
.anti-cal{margin-top:8px;font-size:12.5px;color:var(--text-3)}
.anti-cal b{font-family:var(--mono);font-size:14px;color:var(--text-2)}
.anti-cal .ok{color:var(--money)}
.anti-note{margin:6px 0 0;font-size:12.5px;color:var(--text-3);line-height:1.72}
.anti-why{margin:8px 0 0;padding-left:18px;font-size:12.5px;color:var(--text-2);line-height:1.72}
.anti-angle{margin:8px 0 0;font-size:12.5px;color:var(--text-3);line-height:1.72}
.anti-angle b{color:var(--text-2)}
.anti-done{font-size:11px;color:var(--money)}
@media (max-width:640px){.doc{padding:0 14px 60px}.topbar{margin:0 -14px 20px;padding:11px 14px}}
"""

# ------------------------------------------------------------------ 样式表
# 独立阅读页原本外链 ../style.css。实测这在某些打开方式下会取不到样式表
# （file:// 直开、预览器做了目录隔离、CDN 回源路径不对……），而 style.css 里的
# :root 变量是整个签名的地基，一旦缺失，页面会退化成白底黑字的裸 HTML。
# 所以把主样式表内联进每个内容页：任何打开方式都不会掉样式，内容页也少一个
# 渲染阻塞请求（对 SEO 有利）。dist/style.css 仍然保留，SPA 首页照常外链它。
BASE_CSS_FILE = os.path.join(ROOT, "static", "style.css")
_base_css_cache = None


def base_css():
    """读一次 static/style.css 并缓存。读不到就退回空串（页面仍可读，只是没主题）。"""
    global _base_css_cache
    if _base_css_cache is None:
        try:
            with open(BASE_CSS_FILE, encoding="utf-8") as f:
                _base_css_cache = f.read()
        except OSError as e:                                   # noqa: BLE001
            print("    [!] 读不到 %s：%s（页面将没有主题样式）" % (BASE_CSS_FILE, e))
            _base_css_cache = ""
    return _base_css_cache


def page_css():
    """主样式表 + 阅读页补充样式（补充样式在后，窄栏布局覆盖 SPA 的网格）。"""
    return base_css() + "\n" + PAGE_CSS + "\n"


# ------------------------------------------------------------------ 工具函数
def esc(s):
    """HTML 转义。所有从 JSON 来的文本都必须过这一层。"""
    return html.escape(str(s), quote=True)


def esc_attr(s):
    return html.escape(str(s), quote=True)


def human_money(n):
    """美元数字转成人读的形式：33000000 → $33M。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            v = n / div
            return "$%s%s" % (("%.1f" % v).rstrip("0").rstrip("."), unit)
    return "$%d" % n


def txt_len(s):
    return len(re.sub(r"\s+", "", s or ""))


def short(s, n):
    """压掉空白并截断到 n 个字符。用于 <title>——搜索引擎结果页只显示前 30 来个汉字。"""
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    return s[:n - 1].rstrip("，,、。；; ") + "…"


def load_site(root):
    """读 data/site.json，缺字段的用默认值补齐（不影响构建）。"""
    site = json.loads(json.dumps(DEFAULT_SITE))          # 深拷贝
    path = os.path.join(root, "data", "site.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in (data or {}).items():
                if isinstance(v, dict) and isinstance(site.get(k), dict):
                    site[k].update(v)
                else:
                    site[k] = v
        except Exception as e:                                # noqa: BLE001
            print("    [!] data/site.json 读不了（%s），用默认站点信息" % e)
    return site


# ------------------------------------------------------------------ 详情片段
def frag_badges(c):
    out = ['<span class="badge" data-v="%s">%s</span>'
           % (esc_attr(c.get("verification") or "unverified"),
              esc(V_LABEL.get(c.get("verification"), "未核实")))]
    for m in (c.get("models") or []):
        out.append('<span class="tg">%s</span>' % esc(m))
    n = len(c.get("corrections") or [])
    if n:
        out.append('<span class="flag">发现 %d 处数字出入</span>' % n)
    return "".join(out)


def frag_metrics(c):
    """关键数字。字段顺序与 app.js 的 detailHTML() 一致。"""
    m = c.get("metrics") or {}
    rows = []

    def push(k, v, mono=False):
        if v not in (None, ""):
            rows.append((k, v, mono))

    push("主指标", m.get("headline"), True)
    if m.get("arr"):
        push("ARR", human_money(m["arr"]), True)
    if m.get("mrr"):
        push("MRR", human_money(m["mrr"]), True)
    if m.get("all_time"):
        push("累计收入", human_money(m["all_time"]), True)
    push("客户/规模", m.get("customers"))
    push("团队", m.get("team"))
    push("站长/创始人", c.get("founder"))
    push("融资", m.get("funding"))
    push("估值", m.get("valuation"))
    push("增长", m.get("growth"))
    push("价格", m.get("price_point"))
    push("地区", c.get("origin"))

    if not rows:
        return ""
    body = "".join('<div class="k">%s</div><div class="v%s">%s</div>'
                   % (esc(k), " mono" if mono else "", esc(v))
                   for k, v, mono in rows)
    note = ""
    if m.get("metric_note"):
        note = ('<div class="metric-warn"><b>口径说明：</b>%s</div>'
                % esc(m["metric_note"]))
    return '<div class="metrics">%s</div>%s' % (body, note)


def frag_list(items, cls):
    items = [x for x in (items or []) if x]
    if not items:
        return ""
    return '<ul class="d-list %s">%s</ul>' % (
        cls, "".join("<li>%s</li>" % esc(x) for x in items))


def frag_corrections(c):
    cos = c.get("corrections") or []
    if not cos:
        return ""
    blocks = []
    for co in cos:
        src = ""
        if co.get("source"):
            src = ('<div class="corr-src"><a href="%s" target="_blank" '
                   'rel="noopener nofollow">%s</a></div>'
                   % (esc_attr(co["source"]), esc(co["source"])))
        blocks.append('<div class="corr"><div class="corr-claim">%s</div>'
                      '<div class="corr-truth">%s</div>%s</div>'
                      % (esc(co.get("claim") or ""), esc(co.get("truth") or ""), src))
    return "".join(blocks)


def frag_rep_bars(dims, order, labels, extra_cls=""):
    """五格进度条。data-lv 供 style.css 上色，和主站同一套。"""
    cells = []
    for k in order:
        v = (dims or {}).get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        v = int(v)
        cells.append('<div class="rep-cell" data-lv="%d">'
                     '<div class="rep-k">%s</div><div class="rep-v">%d/5</div>'
                     '<div class="rep-bar">%s</div></div>'
                     % (v, esc(labels.get(k, k)), v, "<i></i>" * 5))
    if not cells:
        return ""
    return '<div class="rep %s">%s</div>' % (extra_cls, "".join(cells))


def frag_hero(score, medal, rank, tier):
    """大分数块。tier 决定配色（hi / mid / lo），与主站同名类。"""
    m = ""
    if medal:
        m = ('<span class="d-medal m-%s">%s 第 %s 名 · %s牌</span>'
             % (esc_attr(medal), MEDAL_ICON.get(medal, ""), esc(rank),
                esc(MEDAL_LABEL.get(medal, ""))))
    else:
        m = '<span class="d-medal plain">第 %s 名</span>' % esc(rank)
    return ('<div class="china-hero t-%s"><div class="ch-score"><b>%s</b>'
            '<small>/ 100</small></div>%s</div>' % (tier, esc(score), m))


def tier_of(score):
    """分档配色，阈值与 app.js 的 chinaTier / soloTier 一致。"""
    if not isinstance(score, (int, float)):
        return "na"
    if score >= 70:
        return "hi"
    if score >= 55:
        return "mid"
    return "lo"


def render_body(c):
    """案例正文。区块顺序与 app.js 的 detailHTML() 对齐，逐段服务端渲染。"""
    out = []
    m = c.get("metrics") or {}

    # 头部
    en = c.get("name_en") or ""
    en_html = ('<small>%s</small>' % esc(en)) if en and en != c.get("name") else ""
    out.append('<header class="d-head"><div class="d-title">%s%s</div>'
               '<div class="d-liner">%s</div>'
               '<div class="d-badges">%s</div></header>'
               % (esc(c.get("name") or c.get("id")), en_html,
                  esc(c.get("one_liner") or ""), frag_badges(c)))

    if c.get("verdict"):
        out.append('<div class="d-h">一句话判断</div>'
                   '<div class="d-verdict">%s</div>' % esc(c["verdict"]))

    if frag_metrics(c):
        out.append('<div class="d-h">关键数字</div>' + frag_metrics(c))

    if c.get("what_it_does"):
        out.append('<div class="d-h">它到底做什么</div>'
                   '<p class="d-p">%s</p>' % esc(c["what_it_does"]))
    if c.get("how_it_makes_money"):
        out.append('<div class="d-h">钱从哪来</div>'
                   '<p class="d-p">%s</p>' % esc(c["how_it_makes_money"]))

    # 骨架第 3、4 段（2026-10-14 补，对齐固定 7 段叙事）：
    # 流量来源 + 站长是谁。与 app.js 的 detailHTML() 保持同一顺序与判空逻辑 ——
    # 存量老案例没这两个字段，整段不出现，不留空壳小标题。
    if frag_list(c.get("traffic_sources"), "src"):
        out.append('<div class="d-h">流量从哪来</div>'
                   + frag_list(c.get("traffic_sources"), "src"))
    if c.get("founder"):
        out.append('<div class="d-h">站长是谁</div>'
                   '<p class="d-p">%s</p>' % esc(c["founder"]))

    if frag_list(c.get("why_it_works"), "why"):
        out.append('<div class="d-h">为什么这事能成</div>'
                   + frag_list(c.get("why_it_works"), "why"))
    if frag_list(c.get("signals"), "sig"):
        out.append('<div class="d-h">支撑证据</div>'
                   + frag_list(c.get("signals"), "sig"))
    if frag_list(c.get("playbook"), "play"):
        out.append('<div class="d-h">可以搬走什么</div>'
                   + frag_list(c.get("playbook"), "play"))

    if frag_corrections(c):
        out.append('<div class="d-h bad">核实修正 · 别抄错</div>' + frag_corrections(c))

    # 可复刻度（原始四维：分数越高越难）
    rep = c.get("replicability") or {}
    bars = frag_rep_bars(rep, list(REP_LABEL.keys()), REP_LABEL)
    if bars:
        total = sum(v for v in rep.values() if isinstance(v, int))
        out.append('<div class="d-h">一个人能不能做</div>' + bars
                   + '<div class="rep-note">%s 本条合计 %d/20。</div>' % (esc(REP_NOTE), total))

    # 个人可做性
    sf = c.get("solo_fit") or {}
    if isinstance(sf.get("score"), (int, float)):
        out.append('<div class="d-h">个人可做性 · 方向统一后的总分</div>'
                   + frag_hero(sf["score"], sf.get("medal"), sf.get("rank"),
                               tier_of(sf["score"])))
        out.append(frag_rep_bars(sf.get("dims"), SOLO_DIM_ORDER, SOLO_DIM_LABEL, "rep-5"))
        if sf.get("delivery_note"):
            out.append('<div class="rep-note">单人交付 %s/5：%s</div>'
                       % (esc((sf.get("dims") or {}).get("delivery", "—")),
                          esc(sf["delivery_note"])))
        out.append('<div class="rep-note">%s</div>' % esc(SOLO_NOTE))

    # 国内移植可行性
    cf = c.get("china_fit") or {}
    if isinstance(cf.get("score"), (int, float)):
        out.append('<div class="d-h">能不能搬回国内做</div>'
                   + frag_hero(cf["score"], cf.get("medal"), cf.get("rank"),
                               tier_of(cf["score"])))
        out.append(frag_rep_bars(cf.get("dims"), CHINA_DIM_ORDER, CHINA_DIM_LABEL))
        if cf.get("note"):
            out.append('<div class="rep-note">%s</div>' % esc(cf["note"]))
        if cf.get("blocker"):
            out.append('<div class="china-blocker">硬伤：%s —— 这一项没解决，'
                       '分数再高也别立项。</div>' % esc(cf["blocker"]))

    # 综合分
    df = c.get("composite") or {}
    if isinstance(df.get("score"), (int, float)):
        label, desc = QUAD_META.get(df.get("quadrant"), ("未分象限", ""))
        out.append('<div class="d-h">综合分 · 两个维度合起来看</div>'
                   + frag_hero(df["score"], df.get("medal"), df.get("rank"),
                               tier_of(df["score"])))
        out.append('<div class="quad-badge q-%s"><b>%s</b><span>%s</span></div>'
                   % (esc_attr(df.get("quadrant") or "na"), esc(label), esc(desc)))
        out.append('<div class="rep-note">综合分 = 0.6 × 短板 ＋ 0.4 × 均值'
                   '（及格线 %s）。短板占 6 成，所以「能做但没市场」和'
                   '「有市场但做不了」都会被压下去。</div>'
                   % esc(df.get("threshold") or 70))

    # 来源
    srcs = c.get("sources") or []
    if srcs:
        items = []
        for s in srcs:
            items.append('<div class="src-item"><span class="src-kind" data-k="%s">%s</span>'
                         '<a href="%s" target="_blank" rel="noopener nofollow">%s</a></div>'
                         % (esc_attr(s.get("kind") or ""),
                            esc(KIND_LABEL.get(s.get("kind"), "来源")),
                            esc_attr(s.get("url") or ""),
                            esc(s.get("label") or s.get("url") or "")))
        out.append('<div class="d-h">来源</div>' + "".join(items))

    # 记录
    rows = [
        ("核实日期", c.get("verified_at") or "—"),
        ("最近更新", c.get("updated_at") or "—"),
        ("归类", "%s%s" % (c.get("category") or "—",
                           (" / " + c["industry"]) if c.get("industry") else "")),
        ("标签", " · ".join(c.get("tags") or []) or "—"),
    ]
    out.append('<div class="d-h">记录</div><div class="metrics">%s</div>'
               % "".join('<div class="k">%s</div><div class="v">%s</div>'
                         % (esc(k), esc(v)) for k, v in rows))

    return "".join(out)


def render_pager(prev_c, next_c):
    """上一名 / 下一名。按综合分名次走，让读者能顺着榜单读下去。"""
    if not prev_c and not next_c:
        return ""

    def cell(c, cls, label):
        if not c:
            return ""
        return ('<a class="%s" href="./%s.html"><small>%s</small><b>%s</b></a>'
                % (cls, esc_attr(c["id"]), label, esc(c.get("name") or c["id"])))

    left = cell(prev_c, "prev", "上一个 · 综合分更高")
    right = cell(next_c, "next", "下一个 · 综合分更低")
    if not left or not right:
        # 榜首/榜尾只有一侧，撑一下让仅有的那侧别贴边
        filler = '<span style="flex:1"></span>'
        left = left or filler
        right = right or filler
    return '<nav class="pager">%s%s</nav>' % (left, right)


def render_promo_bar(site):
    """页面顶部的引导条（放在正文之前，读者一进来就看得到）。

    对独立页来说这不是装饰：从搜索引擎点进来的多半是陌生人，读完就走。
    这一行要回答的是「这站是谁在做、值不值得再看一篇」。文案与 SPA 首页
    那条保持一致，类名也复用主样式表里的 .promo-bar / .pb-*。
    """
    wx = site.get("wechat") or {}
    if not wx.get("name"):
        return ""                      # 没配公众号就不占位
    return ('<div class="promo-bar inpage"><div class="pb-in">'
            '<span class="pb-dot"></span>'
            '<span class="pb-txt">深度拆解发在公众号 <b>%s</b>%s</span>'
            '<span class="pb-act">%s</span>'
            '</div></div>'
            % (esc(wx["name"]),
               ("<em>%s</em>" % esc(wx["hint"])) if wx.get("hint") else "",
               esc(wx.get("action") or "微信搜索关注")))


def render_promo(site):
    """页脚引流位。公众号/社群的名字和链接来自 data/site.json，没配就不显示。"""
    wx = site.get("wechat") or {}
    cm = site.get("community") or {}
    repo = site.get("repo") or ""
    if not (wx.get("name") or cm.get("name") or repo):
        return ""

    rows = []
    if cm.get("url"):
        rows.append('<a class="btn primary" href="%s" target="_blank" '
                    'rel="noopener">%s</a>' % (esc_attr(cm["url"]), esc(cm.get("name"))))
    elif cm.get("name"):
        # 还没开通就不做成按钮：给一个点不动的东西，比不给更伤信任
        rows.append('<span class="btn ghost">%s</span>'
                    % esc(cm.get("pending_note") or "即将开通"))
    if repo:
        rows.append('<a class="btn" href="%s" target="_blank" rel="noopener">'
                    'GitHub 源码与数据</a>' % esc_attr(repo))

    hints = []
    if wx.get("name"):
        hints.append("公众号「%s」—— %s" % (esc(wx["name"]), esc(wx.get("hint") or "")))
    if cm.get("name"):
        hints.append("%s：%s" % (esc(cm["name"]), esc(cm.get("hint") or "")))

    return ('<aside class="promo"><h3>这个库会一直更新</h3>'
            '<p>%s</p><div class="row">%s</div></aside>'
            % ("<br>".join(hints), "".join(rows)))


def render_case_page(c, ctx):
    """单条案例的完整页面。正文全在 raw HTML 里，不依赖 JS。"""
    site = ctx["site"]
    name = c.get("name") or c.get("id")
    liner = c.get("one_liner") or ""
    # <title> 控制在 30 个汉字上下；og:title 带上站名，分享卡片才认得出是谁发的
    title = "%s — %s" % (short(name, 30), short(liner, 22)) if liner else short(name, 40)
    full_title = "%s · %s" % (title, site["title"])
    # 最后再压一刀：名字特别长的案例（wpconvert-ai-convert-ai-sites-to-wordpress
    # 实测 76 字符）会把 title 顶爆。超长不是美观问题 —— 搜索结果页会把它截断，
    # 等于白写。宁可截掉站名尾巴，也要保证整条不超 62。
    if len(full_title) > 62:
        full_title = short(full_title, 62)

    # description 优先用一句话判断——那段话是核过数字之后的结论，比 one_liner 有信息量
    desc_src = (c.get("verdict") or liner or c.get("what_it_does") or "")
    desc = re.sub(r"\s+", " ", desc_src).strip()
    if len(desc) > 118:
        desc = desc[:117].rstrip("，,、 ") + "…"

    base = (site.get("url") or "").rstrip("/")
    url = "%s/%s/%s.html" % (base, CASE_DIR, c["id"]) if base else ""
    canonical = ('<link rel="canonical" href="%s">' % esc_attr(url)) if url else ""
    og_url = ('<meta property="og:url" content="%s">' % esc_attr(url)) if url else ""

    # 结构化数据。只放确定的信息（标题、时间、来源），不编造作者和评分。
    ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": desc,
        "inLanguage": "zh-CN",
        "isPartOf": {"@type": "WebSite", "name": site["title"]},
        "publisher": {"@type": "Organization", "name": site["title"]},
    }
    if url:
        ld["mainEntityOfPage"] = url
    if c.get("verified_at"):
        ld["datePublished"] = c["verified_at"]
    if c.get("updated_at"):
        ld["dateModified"] = c["updated_at"]
    if c.get("tags"):
        ld["keywords"] = "、".join(c["tags"])
    ld_json = json.dumps(ld, ensure_ascii=False, separators=(",", ":")) \
        .replace("</", "<\\/")

    crumb = ('<div class="crumb"><a href="../index.html">%s</a> › '
             '<a href="./index.html">全部案例</a> › %s</div>'
             % (esc(site["title"]), esc(name)))

    top = ('<div class="topbar"><a class="home" href="../index.html">%s</a>'
           '<span class="sep">/</span><span class="cat">%s%s</span></div>'
           % (esc(site["title"]), esc(c.get("category") or "案例"),
              (" · " + esc(c["industry"])) if c.get("industry") else ""))

    foot_bits = ['<p>本页所有数字与结论来自公开来源，核实日期见上。'
                 '发现错误欢迎到仓库提 issue 指正。</p>']
    if site.get("icp"):
        foot_bits.append("<p>%s</p>" % esc(site["icp"]))
    # 兜底：万一 site.json 没配仓库，也别让读者找不到回来的路
    if not site.get("repo"):
        foot_bits.append('<p><a href="../index.html">回到案例库</a></p>')

    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "<title>%s</title>\n"
            '<meta name="description" content="%s">\n'
            "%s"
            "%s\n"
            '<meta property="og:title" content="%s">\n'
            '<meta property="og:site_name" content="%s">\n'
            '<meta property="og:description" content="%s">\n'
            '<meta property="og:type" content="article">\n'
            "%s\n"
            "<style>%s</style>\n"
            '<script type="application/ld+json">%s</script>\n'
            "</head>\n<body>\n"
            '<main class="doc">%s%s%s'
            '<article>%s</article>'
            "%s%s"
            '<footer class="docfoot">%s</footer>'
            "</main>\n</body>\n</html>\n"
            % (esc(full_title), esc_attr(desc), PAGE_FAVICON, canonical,
               esc_attr(full_title), esc_attr(site["title"]), esc_attr(desc),
               og_url, page_css(), ld_json, render_promo_bar(site), top, crumb,
               render_body(c), render_pager(ctx.get("prev"), ctx.get("next")),
               render_promo(site), "".join(foot_bits)))


def render_index_page(cases, ctx, rank_key="composite"):
    """case/index.html —— 静态总目录。给爬虫的内链入口，也是 SPA 的降级导航。"""
    site = ctx["site"]
    title = "全部案例 · %s" % site["title"]
    desc = "%s 共 %d 条已核实的海外 AI 软件生意案例，每条都标了数字可信度、" \
           "国内移植可行性和个人可做性。" % (site["tagline"], len(cases))

    def rank_of(c):
        s = c.get(rank_key) or {}
        return s.get("rank") if isinstance(s.get("rank"), int) else 999

    items = []
    for c in sorted(cases, key=rank_of):
        cf, sf, df = c.get("china_fit") or {}, c.get("solo_fit") or {}, c.get("composite") or {}
        label, _ = QUAD_META.get(df.get("quadrant"), ("—", ""))
        meta = []
        if isinstance(cf.get("score"), (int, float)):
            meta.append("国内移植 %s" % cf["score"])
        if isinstance(sf.get("score"), (int, float)):
            meta.append("一个人做 %s" % sf["score"])
        if df.get("quadrant"):
            meta.append("象限：%s" % label)
        if c.get("verification"):
            meta.append(V_LABEL.get(c["verification"], "未核实"))
        items.append('<li><a href="./%s.html">%s</a>'
                     '<div class="lin">%s</div><div class="meta">%s</div></li>'
                     % (esc_attr(c["id"]), esc(c.get("name") or c["id"]),
                        esc(c.get("one_liner") or ""), esc(" · ".join(meta))))

    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "<title>%s</title>\n"
            '<meta name="description" content="%s">\n'
            "%s"
            "<style>%s</style>\n"
            "</head>\n<body>\n"
            '<main class="doc">%s'
            '<div class="topbar"><a class="home" href="../index.html">%s</a>'
            '<span class="sep">/</span><span class="cat">全部案例</span></div>'
            '<h1 class="sec-title">全部案例 · %d 条</h1>'
            '<p class="sec-sub">%s</p>'
            '<ul class="cat-list">%s</ul>'
            "%s"
            '<footer class="docfoot"><p><a href="../index.html">回到案例库</a>'
            "</p></footer>"
            "</main>\n</body>\n</html>\n"
            % (esc(title), esc_attr(desc), PAGE_FAVICON, page_css(),
               render_promo_bar(site),
               esc(site["title"]),
               len(cases), esc(site["tagline"]), "".join(items),
               render_promo(site)))


def render_anti_page(anti, ctx):
    """anti/index.html —— 反向样本区（「这些别照着做」）的静态页。

    为什么 33 条只出一页，而不是像案例那样每条一页：
      1. 那条链路（render_case_page）是按 cases 的字段渲染的，anti 没有
         verdict / metrics / composite，套上去会产出 33 个半空的页面；
      2. verify_deploy 断言「case/index.html 列出的链接数 = curated 案例数」，
         往 case/ 里塞 33 个 anti 页会让这条判据失效 —— 而它正是部署后
         确认「没少案例」的唯一依据。
    所以独立目录 + 独立一页：可被收录、可被分享，又不碰案例页的任何判据。
    """
    site = ctx["site"]
    cases = anti.get("cases") or []
    vols = anti.get("volumes") or []
    base = (site.get("url") or "").rstrip("/")
    url = "%s/%s/index.html" % (base, ANTI_DIR) if base else ""
    title = short("这些别照着做 · %d 条抄不动的案例 · %s" % (len(cases), site["title"]), 60)
    desc = ("%d 条可以读、但不建议照抄的海外案例，按门槛类型分成 %d 篇："
            "账单、人头、苦力、社区、履历、执照，以及「那个数字根本不是收入」。"
            % (len(cases), len(vols)))
    canonical = ('<link rel="canonical" href="%s">' % esc_attr(url)) if url else ""
    og_url = ('<meta property="og:url" content="%s">' % esc_attr(url)) if url else ""

    def one(a):
        lab = a.get("page_label") or {}
        cal = a.get("caliber") or "unknown"
        # ⚠ 33 条里只有 1 条标签没写错。这一行不是装饰，是这一区的主论点，
        #   所以逐条写出来，而不是在页首笼统说一句「口径可能不准」。
        if cal == "mrr":
            cal_line = ('<div class="anti-cal">页面写「%s」<b>%s</b>'
                        ' → 实际口径：<span class="ok">%s ✔ 33 条里唯一没写错的</span></div>'
                        % (esc(lab.get("key") or "月收入"), esc(lab.get("value") or ""),
                           esc(CAL_LABEL.get(cal, cal))))
        else:
            cal_line = ('<div class="anti-cal">页面写「%s」<b>%s</b> → 实际口径：%s</div>'
                        % (esc(lab.get("key") or "月收入"), esc(lab.get("value") or ""),
                           esc(CAL_LABEL.get(cal, cal))))
        bits = ['<li>',
                '<div class="anti-who"><b>%s</b>' % esc(a.get("name") or a.get("id")),
                '<span class="anti-role" data-k="%s">%s</span>'
                % (esc_attr(a.get("role") or ""),
                   esc(ANTI_ROLE.get(a.get("role"), a.get("role") or "")))]
        if a.get("standalone"):
            bits.append('<span class="anti-done">已单独成文</span>')
        bits.append('</div>')
        if a.get("role_note"):
            bits.append('<div class="lin">%s</div>' % esc(a["role_note"]))
        meta = [x for x in (a.get("origin"), a.get("category")) if x]
        if a.get("verification"):
            meta.append(V_LABEL.get(a["verification"], a["verification"]))
        if meta:
            bits.append('<div class="meta">%s</div>' % esc(" · ".join(meta)))
        bits.append(cal_line)
        if a.get("caliber_note"):
            bits.append('<p class="anti-note">口径说明：%s</p>' % esc(a["caliber_note"]))
        if a.get("why_not_copy"):
            bits.append('<ul class="anti-why">%s</ul>'
                        % "".join("<li>%s</li>" % esc(x) for x in a["why_not_copy"]))
        if a.get("article_angle"):
            bits.append('<p class="anti-angle"><b>落点：</b>%s</p>' % esc(a["article_angle"]))
        src = (a.get("sources") or [])
        if src:
            bits.append('<div class="meta">来源：%s</div>' % " · ".join(
                ('<a href="%s">%s</a>' % (esc_attr(s["url"]), esc(s.get("label") or s["url"])))
                if s.get("url") else esc(s.get("label") or "")
                for s in src))
        bits.append('</li>')
        return "".join(bits)

    blocks = []
    for v in vols:
        items = [a for a in cases if a.get("volume") == v.get("key")]
        if not items:
            continue
        blocks.append(
            '<section class="anti-blk">'
            '<h2 class="sec-title">%s %s <span class="anti-n">%d 条</span></h2>'
            '<p class="anti-judge">判据 · %s</p>'
            '<p class="anti-lead">%s</p>'
            '<ul class="cat-list">%s</ul>'
            '</section>'
            % (esc(v.get("no") or ""), esc(v.get("title") or ""), len(items),
               esc(v.get("judge") or ""), esc(v.get("lead") or ""),
               "".join(one(a) for a in items)))

    if not blocks:      # 旧数据没有分篇信息：整段平铺，不假装有分组
        blocks.append('<ul class="cat-list">%s</ul>' % "".join(one(a) for a in cases))

    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "<title>%s</title>\n"
            '<meta name="description" content="%s">\n'
            "%s%s\n"
            '<meta property="og:title" content="%s">\n'
            '<meta property="og:site_name" content="%s">\n'
            '<meta property="og:description" content="%s">\n'
            '<meta property="og:type" content="article">\n'
            "%s\n"
            "<style>%s</style>\n"
            "</head>\n<body>\n"
            '<main class="doc">%s'
            '<div class="topbar"><a class="home" href="../index.html">%s</a>'
            '<span class="sep">/</span><span class="cat">这些别照着做</span></div>'
            '<h1 class="sec-title">这些别照着做 · %d 条</h1>'
            '<p class="sec-sub">「不建议做」不等于「不值得看」。这一区收的是<b>可以读、'
            '但抄不动</b>的案例：产品形态大多能抄，抄不动的是它底下的东西 —— '
            '账单、人头、苦力、社区、履历、执照，或者干脆是那个数字被读错了。'
            '⚠ 它们<b>大半不是失败案例</b>（<code>lovable</code> 估值 ¥443 亿），别当墓碑读；'
            '卡片上的「月收入」是聚合站原话，真实口径逐条标在下面 —— '
            '<b>%d 条里只有 %d 条标签没写错</b>。</p>'
            "%s"
            "%s"
            '<footer class="docfoot"><p><a href="../index.html">回到案例库</a> · '
            '<a href="../%s/index.html">全部案例目录</a></p></footer>'
            "</main>\n</body>\n</html>\n"
            % (esc(title), esc_attr(desc), PAGE_FAVICON, canonical,
               esc_attr(title), esc_attr(site["title"]), esc_attr(desc), og_url,
               page_css(), render_promo_bar(site),
               esc(site["title"]), len(cases),
               len(cases), sum(1 for a in cases if a.get("caliber") == "mrr"),
               "".join(blocks), render_promo(site), CASE_DIR))


def render_sitemap(cases, site, extra=None):
    """sitemap.xml。没有 site.url 就生成不了——sitemap 协议要求绝对地址。"""
    base = (site.get("url") or "").rstrip("/")
    if not base:
        return ""
    today = date.today().isoformat()

    def newest(c):
        """取「最近更新」当 lastmod；没有就用核实日期，都没有才退回今天。"""
        return c.get("updated_at") or c.get("verified_at") or today

    urls = ['<url><loc>%s/index.html</loc><lastmod>%s</lastmod>'
            '<priority>1.0</priority></url>' % (esc(base), today),
            '<url><loc>%s/%s/index.html</loc><lastmod>%s</lastmod>'
            '<priority>0.8</priority></url>' % (esc(base), CASE_DIR, today)]
    for c in cases:
        urls.append('<url><loc>%s/%s/%s.html</loc><lastmod>%s</lastmod>'
                    '<priority>0.7</priority></url>'
                    % (esc(base), CASE_DIR, c["id"], esc(newest(c))))
    for u in (extra or []):
        urls.append('<url><loc>%s/%s</loc><lastmod>%s</lastmod></url>'
                    % (esc(base), esc(u), today))

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


def render_robots(site, has_sitemap):
    """robots.txt。放开爬虫，并（在有域名时）指向 sitemap。"""
    lines = ["User-agent: *", "Allow: /", ""]
    if has_sitemap:
        lines.append("Sitemap: %s/sitemap.xml" % (site.get("url") or "").rstrip("/"))
        lines.append("")
    return "\n".join(lines)


def noscript_block(cases, site, anti=None):
    """给 SPA 首页注入的降级导航。

    首页是纯客户端渲染的，禁用 JS 或被爬虫抓取时是个空壳；这段 <noscript>
    至少保证「点得进每一条案例」这条路是通的。用 noscript 而不是把内容藏起来，
    是因为隐藏正文属于作弊，noscript 是浏览器标准认可的降级手段。

    anti：反向样本区（33 条）也在这里给一个入口 —— 它是**对外内容**，
    没有 JS 的读者不该因为禁用脚本就完全看不到这一区。
    """
    items = "".join('<li><a href="./%s/%s.html">%s</a> — %s</li>'
                    % (CASE_DIR, esc_attr(c["id"]), esc(c.get("name") or c["id"]),
                       esc(c.get("one_liner") or ""))
                    for c in cases)
    anti_line = ""
    if anti:
        anti_line = ('<p><a href="./%s/index.html">这些别照着做 · %d 条反向样本</a>'
                     '（可以读、但抄不动的案例，按门槛类型分 %d 篇）</p>'
                     % (ANTI_DIR, len(anti.get("cases") or []),
                        len(anti.get("volumes") or [])))
    return ('<noscript><div style="max-width:760px;margin:0 auto;padding:20px;'
            'font:14px/1.7 -apple-system,\'Segoe UI\',\'Microsoft YaHei\',sans-serif">'
            '<h2>%s · 全部 %d 条案例</h2><p>%s</p>'
            '<ul>%s</ul>'
            '%s'
            '<p><a href="./%s/index.html">全部案例目录</a></p>'
            "</div></noscript>\n" % (esc(site["title"]), len(cases),
                                     esc(site["tagline"]), items, anti_line, CASE_DIR))


# ------------------------------------------------------------------ 构建入口
def build_all(cases, out_dir, site, quiet=False, anti=None):
    """生成全部预渲染产物。返回统计 dict，供 build_static.py 汇总。

    anti：反向样本区数据（server.build_payload 的 `anti` 键）。给了才出
    anti/index.html —— 老数据没有这一区时不该凭空造一个空页面出来。
    """
    stats = {"pages": 0, "bytes": 0, "index": "", "sitemap": False, "robots": False,
             "anti_page": False, "anti_bytes": 0}
    if not cases:
        return stats

    case_out = os.path.join(out_dir, CASE_DIR)
    os.makedirs(case_out, exist_ok=True)

    # 导航顺序：按综合分名次，让「上一个/下一个」在榜单上是连续的
    def rank_of(c):
        s = c.get("composite") or {}
        return s.get("rank") if isinstance(s.get("rank"), int) else 999

    ordered = sorted(cases, key=rank_of)
    ctx_base = {"site": site}

    for i, c in enumerate(ordered):
        ctx = dict(ctx_base)
        ctx["prev"] = ordered[i - 1] if i > 0 else None
        ctx["next"] = ordered[i + 1] if i + 1 < len(ordered) else None
        page = render_case_page(c, ctx)
        path = os.path.join(case_out, "%s.html" % c["id"])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(page)
        stats["pages"] += 1
        stats["bytes"] += len(page.encode("utf-8"))

    idx = render_index_page(cases, ctx_base)
    with open(os.path.join(case_out, INDEX_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(idx)
    stats["index"] = "/".join([CASE_DIR, INDEX_FILE])
    stats["bytes"] += len(idx.encode("utf-8"))

    # 反向样本区：单独一页，放 anti/ 而不是 case/（理由见 render_anti_page）
    anti_ids = []
    if anti and (anti.get("cases") or []):
        anti_ids = [a.get("id") for a in anti["cases"]]
        anti_out = os.path.join(out_dir, ANTI_DIR)
        os.makedirs(anti_out, exist_ok=True)
        anti_html = render_anti_page(anti, ctx_base)
        with open(os.path.join(anti_out, INDEX_FILE), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(anti_html)
        stats["anti_page"] = True
        stats["anti_bytes"] = len(anti_html.encode("utf-8"))

    sm = render_sitemap(cases, site,
                        extra=["%s/%s" % (ANTI_DIR, INDEX_FILE)] if stats["anti_page"] else None)
    if sm:
        with open(os.path.join(out_dir, "sitemap.xml"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(sm)
        stats["sitemap"] = True
    rb = render_robots(site, stats["sitemap"])
    with open(os.path.join(out_dir, "robots.txt"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(rb)
    stats["robots"] = True

    if not quiet:
        print("        案例页 %d 个 · 总目录 1 个 · %s · %s"
              % (stats["pages"], "sitemap 已生成" if stats["sitemap"]
                 else "无站点域名，跳过 sitemap",
                 "robots.txt 已生成" if stats["robots"] else "robots 跳过"))
        if stats["anti_page"]:
            print("        反向样本页 1 个（%s/index.html，%d 条）"
                  % (ANTI_DIR, len(anti_ids)))
    return stats


def check_pages(out_dir, cases, verbose=True):
    """产物自检。返回问题列表（空列表＝通过）。"""
    problems = []
    case_out = os.path.join(out_dir, CASE_DIR)

    if not os.path.isdir(case_out):
        return ["%s 目录不存在" % case_out]

    page_files = [f for f in os.listdir(case_out) if f.endswith(".html")]
    if len(page_files) != len(cases) + 1:      # +1 是总目录
        problems.append("case/ 下有 %d 个页面，期望 %d 个（%d 条案例 + 1 个目录）"
                        % (len(page_files), len(cases) + 1, len(cases)))

    # 主样式表读不到时页面不会报错，只会「能读但没主题」——必须显式拦下
    css = page_css()
    if len(base_css()) < 10000:
        problems.append("static/style.css 读不到或异常短（%d 字节），页面会没有主题样式"
                        % len(base_css()))

    checked = 0
    for c in cases:
        p = os.path.join(case_out, "%s.html" % c["id"])
        if not os.path.isfile(p):
            problems.append("缺页面 case/%s.html" % c["id"])
            continue
        with open(p, encoding="utf-8") as f:
            h = f.read()
        checked += 1
        # 最关键的一条：正文必须在 raw HTML 里，不能靠 JS 注入
        body_txt = re.sub(r"<[^>]+>", " ", h)
        if txt_len(body_txt) < 600:
            problems.append("case/%s.html 正文太短（%d 字），可能没渲染出来"
                            % (c["id"], txt_len(body_txt)))
        if c.get("one_liner") and esc(c["one_liner"]) not in h:
            problems.append("case/%s.html 里找不到 one_liner" % c["id"])
        if "<title></title>" in h or "<title> · " in h:
            problems.append("case/%s.html 标题是空的" % c["id"])
        if '<meta name="description" content="">' in h:
            problems.append("case/%s.html 描述是空的" % c["id"])
        # 独立页不引 app.js、不链外样式。
        # ⚠⚠⚠ 必须是**标签级**检查，不能拿子串糊：
        # 2026-10-14 实测 —— static/style.css 的 PWA 注释里提了一句
        # 「长时间停在一个旧 app.js 上」，整份样式表内联进案例页后，
        # `"app.js" in h` 命中，一口气误报 12 条「引入了 app.js」，
        # 而那些页面里一个 <script> 都没有，纯假阳性。
        # 同理 style.css 也不能用子串（注释、正文里都可能出现这个词）。
        if re.search(r"<script[^>]+src=[\"'][^\"']*app\.js", h):
            problems.append("case/%s.html 引入了 app.js（独立页不该依赖 JS）" % c["id"])
        # 样式必须内联。外链的 style.css 在 file:// 直开、预览器目录隔离、
        # CDN 缓存不一致等情况下会取不到，而 :root 变量一缺整页就退化成白底黑字。
        if re.search(r"<link[^>]+href=[\"'][^\"']*style\.css", h) or 'rel="stylesheet"' in h:
            problems.append("case/%s.html 引用了外部样式表（应内联）" % c["id"])
        elif css not in h:
            problems.append("case/%s.html 没有完整内联样式表" % c["id"])
        # 绝对路径在子目录 / 子路径部署时会 404
        if re.search(r'(href|src)="/(?!/)', h):
            problems.append("case/%s.html 里有根路径引用" % c["id"])

    # 总目录页同理：它也是独立打开的入口
    idx_p = os.path.join(case_out, INDEX_FILE)
    if os.path.isfile(idx_p):
        with open(idx_p, encoding="utf-8") as f:
            ih = f.read()
        if 'rel="stylesheet"' in ih or "style.css" in ih:
            problems.append("case/%s 引用了外部样式表（应内联）" % INDEX_FILE)
        elif css not in ih:
            problems.append("case/%s 没有完整内联样式表" % INDEX_FILE)

    if verbose:
        print("        抽查 %d 个页面，%s" % (checked, "全部通过" if not problems
                                              else "发现 %d 个问题" % len(problems)))
    return problems


def main():
    ap = argparse.ArgumentParser(description="预渲染案例独立页")
    ap.add_argument("--out", default="dist", help="输出目录（默认 dist）")
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "cases.json"))
    ap.add_argument("--site-url", default="", help="站点域名，用于 sitemap / canonical")
    ap.add_argument("--check", action="store_true", help="只校验已有产物，不重新生成")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        cases = json.load(f)

    # 「（Stripe 验证，2026-09-17 快照）」这类标注只给作者看，不该进案例页。
    # 数据里已清过，这里是第二道闸门（make_article.py 里有同样的调用）。
    clean_snapshot_marks(cases)

    if args.check:
        out = os.path.join(ROOT, args.out)
        problems = check_pages(out, cases)
        for p in problems:
            print("[FAIL] %s" % p)
        return 1 if problems else 0

    site = load_site(ROOT)
    if args.site_url:
        site["url"] = args.site_url.rstrip("/")
    out = os.path.join(ROOT, args.out)
    os.makedirs(out, exist_ok=True)
    build_all(cases, out, site)
    problems = check_pages(out, cases)
    for p in problems:
        print("[FAIL] %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
