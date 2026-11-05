#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「内部流水线黑话」从 `data/cases.json` 里洗掉（2026-11-05）。

用法::

    python -X utf8 scripts/scrub_jargon.py --check    # 只报会改什么，不写盘
    python -X utf8 scripts/scrub_jargon.py --apply    # 备份后落盘
    python -X utf8 scripts/scrub_jargon.py --verify   # 只读验收（残留 0 + 数字未变）

为什么要有这个脚本
------------------
`corrections[].claim/truth`、`metrics.metric_note`、`signals[]`、`founder`、
`sources[].label` 这些字段是**会原样印进发布正文**的（数据卡的「备注」、
钩子里的引文、第二条纠错）。2026-11-03 那批指标刷新的案例里，写稿的一步把
流水线自己的词汇写了进去 —— 读者会读到「候选资料 headline 称 'MRR $1,627'」、
「定价（price_point）为 $10,000」、「早期入库时收入未公开」这类不知所云的句子。
`test_make_article` 的 [10d] 只盯 corrections 的两个字段，所以只报出 5 处，
实际全库 168 处、跨 9 类字段，而且**都在还没成文的案例上**（产物里 0 命中）
—— 现在不改，下一批写稿就会印出去。

四条纪律（都是踩过或差点踩的）
------------------------------
1. **只换说法，不动数字**：`--verify` 逐字段比对数字序列（`\\d+([.,]\\d+)*`），
   多一个少一个都算失败并中止写盘。改写文案最容易犯的错就是把 `$1,341` 抄成 `$1,300`。
2. **不动引号里的原文**：`'MRR $694'`、「MRR $998」是从页面抄下来的，替换会变成
   伪造引文，所以规则只在引号外生效。
3. **不碰枚举/数值字段**：`metrics.caliber` 的值就是 `last_30d` 这种**机器取值**，
   不是文案；第一版把整个 `metrics` 扫了一遍，结果 `caliber="last_30d"` 差点被
   改成中文，直接毁掉判据。可见字段用**白名单**列，不遍历整个 dict。
4. **不做全局空格整理**：第一版带 `是\\s+→是`、` {2,}→" "` 这类规则，把
   「不是 ARR」改成「不是ARR」、把正文里本来正常的英文间距压掉 —— 改了 584 处、
   348 条案例。现在只在「中文与中文之间」收空格，英文与数字两侧原样不动。

幂等：`--apply` 后再跑 `--check` 应是 0 处。
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio                      # noqa: E402

CASES_PATH = os.path.join(ROOT, "data", "cases.json")
BACKUP_DIR = os.path.join(ROOT, "_backups", "jargon-2026-11-05")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# ① 判定：什么东西算「黑话」
# ---------------------------------------------------------------------------
# 只收两类：① 中文流水线词汇（读者不知道「候选」是什么池子、「入库时」指哪一步）；
# ② snake_case 字段标识（页面上根本没有这些词，它是我们自己的 key）。
# 不收 `All time` / `all-time` / `growth` 这类**页面本来就印着**的英文标签。
JARGON = re.compile(
    r"候选|本次核实|抓取原文|入库时"
    r"|price_point|monthly_timeline|metric_note|one_liner|last_30d_revenue|last_30d|all_time"
    r"|\bmetrics\b|\bheadline\b|\bcorrections\b|verify_case\.py"
)

# 引号内的片段不参与替换（那是页面原文，改了就成伪造引文）。
QUOTED = re.compile(r"('[^']{0,80}'|“[^”]{0,80}”|「[^」]{0,80}」|\"[^\"]{0,80}\")")


def _r(*pairs):
    return [(re.compile(p), q) for p, q in pairs]


# 少数「引号里装的其实是字段名」—— 那不是页面原文，是 json key，可以改。
# 这类必须在切引号**之前**处理，否则会被当引文跳过（`rankspot` 就漏了）。
PRE_RULES = _r(
    (r"候选资料中\s*'last_30d_revenue'\s*[:：]\s*", "档案标注的近 30 天收入为 "),
    (r"「metrics\.[a-z_0-9]+」", "「该指标的原始值」"),
    (r"（\s*verify_case\.py\s*）", ""),
    (r"\s*\(?\bverify_case\.py\)?", ""),
    (r"见\s*corrections", "见纠错记录"),
    (r"\bcorrections\b", "纠错记录"),
)

# 只有这些 metrics 键是「写给人看的话」；其余是数值/枚举/结构化数据，绝不进清洗。
PROSE_METRIC_KEYS = ("headline", "metric_note", "growth", "provenance", "caliber_reason")


def _split_quotes(text):
    """→ [(is_quote, chunk)]，供「只改引号外」使用。"""
    out, pos = [], 0
    for m in QUOTED.finditer(text):
        if m.start() > pos:
            out.append((False, text[pos:m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append((False, text[pos:]))
    return out


def _r(*pairs):
    return [(re.compile(p), q) for p, q in pairs]


# ---------------------------------------------------------------------------
# ② 规则表（顺序敏感：带上下文的短语先替换，裸词兜底放最后）
# ---------------------------------------------------------------------------
RULES = _r(
    # ---- 括注：字段名对读者没意义，整段收成「据 TrustMRR 档案」 --------------
    (r"[（(]\s*候选资料\s*headline\s*与\s*last_30d_revenue\s*字段\s*[）)]",
     "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*price_point\s*字段\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*headline\s*/\s*last_30d_revenue\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*monthly_timeline\s*/\s*all_time\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*metrics\.all_time\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选档案\s*headline\s*与\s*metrics\.mrr\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*headline\s*与\s*metric_note\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*metric\s*与\s*headline\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选档案\s*metrics\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选档案\s*headline\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*headline\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选资料\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选档案\s*[）)]", "（据 TrustMRR 档案）"),
    (r"[（(]\s*候选缓存快照\s*[）)]", "（早先的缓存快照）"),
    (r"[（(]\s*候选快照口径\s*[）)]", "（快照口径）"),
    (r"[（(]\s*候选[^）)]{0,6}快照\s*[）)]", "（早先的快照）"),
    (r"[（(]\s*候选 one_liner\s*/\s*TrustMRR 描述\s*[）)]", "（该站简介与 TrustMRR 描述如此）"),
    (r"[（(]\s*候选 one_liner\s*[）)]", "（该站简介如此）"),
    (r"[（(]\s*price_point\s*[）)]", ""),
    (r"[（(]\s*候选资料\s*price_point\s*记为", "（档案里的定价记为"),
    (r"[（(]\s*候选资料中\s*metric_note\s*称", "（档案备注称"),
    (r"[（(]\s*候选月度序列", "（档案里的月度流水"),
    (r"[（(]\s*候选月表", "（档案里的月度流水"),
    # ---- 带上下文的短语（先于裸词） --------------------------------------
    (r"原候选数字", "原先的数字"),
    (r"候选资料内部数字自相矛盾", "该站公开数字自相矛盾"),
    (r"候选资料中\s*metric_note\s*称", "档案备注称"),
    (r"候选资料中\s*headline\s*称", "档案头条数字写的是"),
    (r"候选资料中\s*growth\s*称", "档案标注的增速是"),
    (r"候选资料中\s*all_time\s*为", "档案里的累计收入是"),
    (r"候选资料中\s*mrr\s*为", "档案里的 MRR 是"),
    (r"候选资料中\s*'last_30d_revenue'", "档案里标注的近 30 天收入"),
    (r"候选资料\s*headline\s*称", "档案头条数字写的是"),
    (r"候选资料\s*monthly_timeline", "档案里的月度流水"),
    (r"候选资料\s*models\s*标为", "档案把分类标为"),
    (r"候选资料\s*metric_note", "档案备注"),
    (r"候选档案\s*monthly_timeline", "档案里的月度流水"),
    (r"候选档案\s*price_point\s*[:：]", "档案里的定价是"),
    (r"候选档案\s*标注", "档案标注"),
    (r"候选档案\s*所依据的", "档案所依据的"),
    (r"候选档案\s*所带", "档案所带"),
    (r"候选档案头条数字", "档案头条数字"),
    (r"候选档案\s*(\d{4}-\d{2}-\d{2})", r"档案（\1）"),
    (r"候选档案\s*记录", "档案记录"),
    (r"候选指标里", "档案指标里"),
    (r"候选 metrics 里", "档案指标里"),
    (r"候选 metrics", "档案指标"),
    # ---- 裸主语词兜底 ----------------------------------------------------
    (r"候选资料里", "档案里"),
    (r"候选资料", "该站档案"),
    (r"候选档案", "档案"),
    (r"候选数字", "这组数字"),
    (r"候选数据", "这组数据"),
    (r"候选里的", "上面的"),
    (r"候选中的", "上面的"),
    (r"候选所引数字", "上面这组数字"),
    (r"候选记录", "档案记录"),
    (r"候选月度序列", "档案里的月度流水"),
    (r"候选月表", "档案里的月度流水"),
    (r"候选原记的", "早先记录的"),
    (r"候选原记", "早先记录"),
    (r"候选未记录", "档案里没有记录"),
    (r"候选案例", "本案例"),
    (r"这批候选里", "这一批里"),
    (r"候选", "档案"),
    # ---- snake_case 字段标识 → 读者能懂的说法 ----------------------------
    (r"\blast_30d_revenue\b", "近 30 天收入"),
    (r"\blast_30d\b", "近 30 天收入"),
    (r"\ball_time\b", "累计收入"),
    (r"price_point\s*[:：]\s*", "定价是 "),
    (r"\bprice_point\b", "定价"),
    (r"\bmonthly_timeline\b", "月度流水"),
    (r"\bmetric_note\b", "档案备注"),
    (r"\bheadline\b", "头条数字"),
    (r"\bone_liner\b", "一句话简介"),
    (r"metrics\.mrr", "MRR"),
    (r"\bmetrics\b", "档案指标"),
    # ---- 其余中文流水线词 -------------------------------------------------
    (r"早期入库时", "早期收录时"),
    (r"入库时", "收录时"),
    (r"抓取原文", "页面原文"),
    (r"本次核实", "本次核对"),
    # ---- 替换产生的语病 ---------------------------------------------------
    (r"档案里档案", "档案"),
    (r"累计收入\s*累计收入", "累计收入"),
    (r"档案头条数字\s*称", "档案头条数字写的是"),
    (r"这组这组", "这组"),
    (r"（\s*）", ""),
    (r"。\s*。", "。"),
)

# 替换后清理「我自己插入的词」两侧的空格：`的 定价 是` → `的定价是`。
# ⚠ 绝不能做成全局「中文之间去空格」—— `$99/月 和 Custom 版` 里的那个空格是
#   排版需要，全局压掉会变成「月 和」⇒「月和」（真踩过，见 cash-cow-inc）。
# 所以只对下面这些**我换上去的词**做左右收口，别的空格一律不碰。
TRIM_WORDS = (
    "据 TrustMRR 档案", "该站档案", "档案", "定价", "头条数字", "累计收入",
    "近 30 天收入", "月度流水", "档案备注", "一句话简介", "档案指标",
    "这组数字", "这组数据", "上面的", "上面这组数字", "早先的", "早先记录",
    "收录时", "页面原文", "本案例",
)


def _trim_around(text):
    for w in sorted(TRIM_WORDS, key=len, reverse=True):
        text = re.sub(r"(?<=%s)[ \t]+(?=[\u4e00-\u9fff])" % w, "", text)
        text = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=%s)" % w, "", text)
    return text


def scrub_text(s):
    """洗一段文字。**数据与产物两个入口共用它**（判据一处，别各写一遍）。

    先掩掉 PROTECTED（候选人 / 候选集 / 候选表 …），洗完还原 —— 否则
    `候选人`→`档案人`、`候选集`→`档案集` 这种错句会从两个入口一起冒出来。
    """
    if not isinstance(s, str) or not s:
        return s
    s = _mask(s)
    for pat, rep in PRE_RULES:            # 引号里装的是字段名，先于切分处理
        s = pat.sub(rep, s)
    chunks = []
    for is_quote, chunk in _split_quotes(s):
        if is_quote:
            chunks.append(chunk)
            continue
        for pat, rep in RULES:
            chunk = pat.sub(rep, chunk)
        chunks.append(_trim_around(chunk))
    return _unmask("".join(chunks))


# ---------------------------------------------------------------------------
# ③ 遍历：只碰「会印出去」的字段（白名单，不遍历整个 dict）
# ---------------------------------------------------------------------------
def iter_visible(case):
    """产出 (定位串, holder, key)。改 id/name/slug 会断台账认亲，绝不在此列。"""
    for i, corr in enumerate(case.get("corrections") or []):
        for f in ("claim", "truth", "source"):
            v = corr.get(f)
            if isinstance(v, str) and v.strip():
                yield ("corrections[%d].%s" % (i, f)), corr, f
    met = case.get("metrics") or {}
    for k in PROSE_METRIC_KEYS:
        if isinstance(met.get(k), str) and met.get(k).strip():
            yield ("metrics.%s" % k), met, k
    for i, s in enumerate(case.get("signals") or []):
        if isinstance(s, str) and s.strip():
            yield ("signals[%d]" % i), case["signals"], i
    for f in ("founder", "how_it_makes_money"):
        if isinstance(case.get(f), str) and case[f].strip():
            yield f, case, f
    for i, src in enumerate(case.get("sources") or []):
        if isinstance(src.get("label"), str) and src["label"].strip():
            yield ("sources[%d].label" % i), src, "label"


def load():
    import case_store as CS
    return CS.load_cases()


def scan(cases):
    """→ [(case_id, loc, before, after)]，只列真有变化的。"""
    out = []
    for c in cases:
        for loc, holder, key in iter_visible(c):
            before = holder[key]
            after = scrub_text(before)
            if after != before:
                out.append((c.get("id"), loc, before, after))
    return out


def _digits(s):
    """只取「绝不能变」的数字：金额、百分比、日期、带小数的值。

    ⚠ 第一版用 `\\d+([.,]\\d+)*` 取全部数字，结果**字段名里的数字也算**：
    `last_30d_revenue` 换成「近 30 天收入」后少了一个 `30`，整个 apply 被误判成
    「改动了数字」而中止。要守的不是「字符串里每个数字」，而是「读者会去核对的
    那些数字」—— 金额、比例、日期、有效订阅数这类带单位的数。"""
    out = []
    out += re.findall(r"[$¥€£]\s?[\d,.]+", s)
    out += re.findall(r"[\d,.]+%", s)
    out += re.findall(r"20\d{2}-\d{2}-\d{2}", s)
    out += re.findall(r"[\d,]+\.\d+", s)
    return out


def residual_after_scrub(cases):
    """规则跑完仍会命中的位置 —— `--check` 用来看「规则覆盖不到哪」。

    与 `residual()` 的分工：这个是**预演**（洗一遍看还剩什么），那个是**验收**
    （直接读盘上的原文）。混用就会出假绿。"""
    out = []
    for c in cases:
        for loc, holder, key in iter_visible(c):
            txt = scrub_text(holder[key])
            m = JARGON.search(txt)
            if m:
                out.append((c.get("id"), loc, m.group(0), txt))
    return out


def residual(cases):
    """**盘上原文**里的残留（`--verify` 用）。

    🚨 判据必须打在落盘的数据上：第一版写成「先 scrub 再判」，于是数据一个字节
    都没写进去也报 `残留 0 处` —— 假绿。验收永远只看盘上的东西。
    """
    out = []
    for c in cases:
        for loc, holder, key in iter_visible(c):
            txt = holder[key] or ""
            m = JARGON.search(txt)
            if m:
                out.append((c.get("id"), loc, m.group(0), txt))
    return out


# ---------------------------------------------------------------------------
# ④ CLI
# ---------------------------------------------------------------------------
def cmd_check(limit=None):
    cases = load()
    rows = scan(cases)
    print("待清洗 %d 处 ｜ 涉及案例 %d 条" % (len(rows), len({r[0] for r in rows})))
    for cid, loc, before, after in rows[:limit or len(rows)]:
        print("\n· %s %s" % (cid, loc))
        print("  旧：%s" % before)
        print("  新：%s" % after)
    left = residual_after_scrub(cases)
    print("\n规则跑完仍残留：%d 处" % len(left))
    for cid, loc, word, txt in left[:20]:
        print("   ✗ %s %s → %r" % (cid, loc, word))
    return 0 if not rows else 1


def cmd_apply():
    cases = load()
    rows = scan(cases)
    if not rows:
        print("无需改动（幂等）")
        return 0
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, "cases.json")
    if not os.path.isfile(dst):
        with io.open(CASES_PATH, "rb") as f:
            atomicio.write_bytes(dst, f.read())
        print("已备份原件 → %s" % os.path.relpath(dst, ROOT))
    else:
        print("备份已存在，保留最初原件 → %s" % os.path.relpath(dst, ROOT))

    # 逐字段落新值；同时硬校验「数字序列不变」，有一处不符就整体中止
    bad, n = [], 0
    for c in cases:
        for loc, holder, key in iter_visible(c):
            before = holder[key]
            after = scrub_text(before)
            if after == before:
                continue
            if _digits(before) != _digits(after):
                bad.append((c.get("id"), loc, before, after))
                continue
            holder[key] = after
            n += 1
    if bad:
        print("✗ 有 %d 处改写会动到数字，已整体中止（不写盘）：" % len(bad))
        for cid, loc, b, a in bad[:10]:
            print("   %s %s\n     旧：%s\n     新：%s" % (cid, loc, b, a))
        return 2
    import case_store as CS
    CS.save_cases(cases)              # 写 case_library + 重导 cases.json 镜像
    print("已写入 %d 处（只改措辞，数字逐字段核过）" % n)
    return 0


def cmd_verify():
    cases = load()
    left = residual(cases)
    print("残留黑话：%d 处" % len(left))
    for cid, loc, word, txt in left[:20]:
        print("   ✗ %s %s → %r" % (cid, loc, word))
    ok = not left
    dst = os.path.join(BACKUP_DIR, "cases.json")
    if os.path.isfile(dst):
        with io.open(dst, encoding="utf-8") as f:
            old = json.load(f)
        drift, mismatch = [], []
        if len(old) != len(cases):
            mismatch.append("案例数 %d → %d" % (len(old), len(cases)))
        for oc, nc in zip(old, cases):
            a, b = list(iter_visible(oc)), list(iter_visible(nc))
            if [x[0] for x in a] != [x[0] for x in b]:
                mismatch.append("%s 字段集合变了" % oc.get("id"))
                continue
            for (loc, oh, ok_), (_, nh, nk) in zip(a, b):
                if _digits(oh[ok_]) != _digits(nh[nk]):
                    drift.append((oc.get("id"), loc))
        print("数字漂移：%d 处 ｜ 结构差异：%d 处" % (len(drift), len(mismatch)))
        for x in drift[:10]:
            print("   ✗ %s %s" % x)
        for x in mismatch[:10]:
            print("   ✗ %s" % x)
        ok = ok and not drift and not mismatch
    else:
        print("没有备份（%s），跳过数字比对" % os.path.relpath(dst, ROOT))
    print("✅ 通过" if ok else "✗ 未通过")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# ⑤ 产物清洗（out/articles/*.html、drafts/**.md）
# ---------------------------------------------------------------------------
# 这些文件是**清洗数据之前**生成的，里面留着同一批句子（「早期入库时收入未公开」
# 「候选原记的 $3.9K」「见 corrections」）。数据改干净了但它们不会自己变，下次
# 谁把这些稿推上线，读者读到的还是旧措辞。
#
# ⚠ 必须保护「正当术语」：`候选表` / `候选池` / `候选名单` 在反向样本方法论文章里
#   是**定义过的概念**（anti-vii-caliber 讲「候选表加了一列必填的 caliber」）；
#   `候选集` 是检索/排序里的通用词（腾讯云 mort 稿讲「先粗筛到一个较小的候选集，
#   再对候选集跑模型」）—— 那是技术内容，不是我们的流水线词汇。改成「档案集」
#   会把一篇工程稿改成错句。所以先占位保护，洗完再还原。
PROTECTED = ("候选表", "候选池", "候选名单", "候选案例库", "候选集", "候选集里",
             "候选排序", "候选人", "候选者")
_SAFE = "\ue000%d\ue001"          # 私用区字符做占位，正文里不会出现

ARTIFACT_GLOBS = ("out/articles/*.html", "drafts/*/*.md", "drafts/*/*/*.md")
ARTIFACT_BACKUP = os.path.join(ROOT, "_backups", "jargon-artifacts-2026-11-05")


def _artifact_files():
    import glob
    out = []
    for g in ARTIFACT_GLOBS:
        out += glob.glob(os.path.join(ROOT, g))
    return sorted(set(out))


def _mask(text):
    out = text
    for i, term in enumerate(PROTECTED):
        out = out.replace(term, _SAFE % i)
    return out


def _unmask(text):
    out = text
    for i, term in enumerate(PROTECTED):
        out = out.replace(_SAFE % i, term)
    return out


def _detect_hits(text):
    """判「还有没有黑话」，**与改的范围对齐**：引号内不改 ⇒ 也不算残留。

    ⚠ `候选人` 是招聘场景的正当词（jobright 稿：「『候选人质量』能直接对应到
    招聘成本」）。裸 `候选→档案` 会把它改成「档案人」—— 判据和修法两头都得先
    掩掉 PROTECTED，否则一改就是错句。
    """
    hits = []
    for is_quote, chunk in _split_quotes(_mask(text)):
        if is_quote:
            continue
        hits += [m.group(0) for m in JARGON.finditer(chunk)]
    return hits


def scrub_artifact(text):
    """洗一段产物文本；返回 (新文本, 真实命中词)。"""
    hits = _detect_hits(text)
    if not hits:
        return text, []
    return scrub_text(text), hits


def _tag_balance(text):
    return (text.count("<"), text.count(">"))


def cmd_artifacts(apply=False, verify=False):
    rows = []
    for p in _artifact_files():
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        with io.open(p, encoding="utf-8", errors="replace", newline="") as f:
            old = f.read()
        new, hits = scrub_artifact(old)
        if new == old:
            continue
        rows.append((rel, p, old, new, hits))

    if verify:
        # ⚠ 必须扫**全部**产物，不能只看「这轮改过的」：被保护术语的文件
        #   根本没进 rows，用 rows 判就是假绿（第一版正是这个错）。
        left = []
        for p in _artifact_files():
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            with io.open(p, encoding="utf-8", errors="replace", newline="") as f:
                txt = f.read()
            hits = _detect_hits(txt)
            if hits:
                left.append((rel, hits))
        print("产物仍命中：%d 个文件" % len(left))
        for rel, hits in left[:20]:
            print("   ✗ %s %s" % (rel, sorted(set(hits))))
        print("✅ 通过" if not left else "✗ 未通过")
        return 0 if not left else 1

    print("待清洗产物 %d 个文件 ｜ 命中 %d 处"
          % (len(rows), sum(len(h) for _, _, _, _, h in rows)))
    for rel, p, old, new, hits in rows:
        print("   · %-52s %s" % (rel, sorted(set(hits))))
    if not apply:
        return 0 if not rows else 1

    # 逐文件校验后落盘：标题不许变、标签配平、金额/比例/日期不变
    bad, n = [], 0
    for rel, p, old, new, hits in rows:
        probs = []
        h_old = re.search(r"<h1[^>]*>.*?</h1>", old, re.S)
        h_new = re.search(r"<h1[^>]*>.*?</h1>", new, re.S)
        if h_old and (not h_new or h_old.group(0) != h_new.group(0)):
            probs.append("<h1> 变了")
        if _tag_balance(old) != _tag_balance(new):
            probs.append("标签数变了 %s→%s" % (_tag_balance(old), _tag_balance(new)))
        if _digits(old) != _digits(new):
            probs.append("金额/日期变了")
        if probs:
            bad.append((rel, probs))
            continue
        os.makedirs(os.path.dirname(os.path.join(ARTIFACT_BACKUP, rel)), exist_ok=True)
        dst = os.path.join(ARTIFACT_BACKUP, rel.replace("/", "__"))
        if not os.path.isfile(dst):
            atomicio.write_text(dst, old)
        atomicio.write_text(p, new, newline="")
        n += 1
    if bad:
        print("✗ 有 %d 个文件没过校验（已跳过，其余照常）：" % len(bad))
        for rel, probs in bad:
            print("   %s %s" % (rel, probs))
    print("已写入 %d 个文件，原件在 %s" % (n, os.path.relpath(ARTIFACT_BACKUP, ROOT)))
    return 0 if not bad else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--artifacts", action="store_true",
                    help="清洗产物（out/articles/*.html、drafts/**.md）而不是数据")
    ap.add_argument("--limit", type=int, default=None, help="--check 时只打印前 N 处")
    a = ap.parse_args()
    if a.artifacts:
        return cmd_artifacts(apply=a.apply, verify=a.verify)
    if a.apply:
        return cmd_apply()
    if a.verify:
        return cmd_verify()
    return cmd_check(a.limit)


if __name__ == "__main__":
    sys.exit(main())
