# -*- coding: utf-8 -*-
"""PRD 瘦身：§6/§6.1 日志迁 CHANGELOG.md，PRD 留指针并刷新过时项。"""
import os, re, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
prd = os.path.join(ROOT, "docs", "PRD.md")
txt = open(prd, encoding="utf-8").read()
lines = txt.splitlines()

# 备份（产物留 tmp）
shutil.copyfile(prd, os.path.join(ROOT, "tmp", "PRD_before_slim_20261126.md"))

# --- 1. 切出 §6 + §6.1（含表头与全部条目），边界用章节标题 ---
i6 = next(i for i, l in enumerate(lines) if l.startswith("## 6. 本期更新"))
i8 = next(i for i, l in enumerate(lines) if l.startswith("## 8. 下一步 Roadmap"))
log_block = "\n".join(lines[i6:i8]).rstrip()
# 计数：只数形如 | 数字 | 的条目行（两个表的 # 列）
entries = [l for l in log_block.splitlines() if re.match(r"^\| \d+ \|", l)]
print("迁移日志行 %d，条目 %d 条" % (len(log_block.splitlines()), len(entries)))

# --- 2. 生成 CHANGELOG.md ---
head = """# 变更日志（CHANGELOG）

> 本文件从 `docs/PRD.md` §6 / §6.1 拆出（2026-11-26 瘦身）。
> **PRD 只留现行设计**；逐次发布 / 修复 / 排障记录**一律进本文件**，不再往 PRD 追加。
> 编号 `#N` 全仓通用（流水、MEMORY、各 skill 里写的「PRD #N」即本文件第 N 条）。
> 原表分两段：§6 = #1–11（v2→v3，2026-09-25~10-18）；§6.1 = #1–229（v3.0→v3.9 起的工程加固，2026-10-21 起持续追加）。
> 两段编号各自独立：下文两个「#1」分别是两段的起点，引用时以「§6 #N」/「§6.1 #N」区分。
> 以下为**迁出时的原文**，未删改任何条目。

---

"""
chg = os.path.join(ROOT, "docs", "CHANGELOG.md")
open(chg, "w", encoding="utf-8", newline="").write(head + log_block + "\n")
print("CHANGELOG.md written:", len((head + log_block).splitlines()), "lines")

# --- 3. PRD 用指针替换 §6/§6.1 ---
ptr = """## 6. 变更记录

逐次发布 / 修复 / 排障的**完整记录已迁至 [`docs/CHANGELOG.md`](CHANGELOG.md)**（2026-11-26 瘦身，共 %d 条，编号 `#N` 全仓通用）。
PRD 只维护**现行设计**：新增设计决策就地更新对应章节，并在 CHANGELOG 留一条「为何改」。
⛔ 不要再往本文追加运行日志——那会让设计文档退化成流水账。

""" % len(entries)
new_lines = lines[:i6] + ptr.splitlines() + [""] + lines[i8:]
txt = "\n".join(new_lines) + "\n"

# --- 4. 修 PRD 内对 §6.1 的交叉引用 ---
repls = {
    "见 §6.1 第 56 行。": "见 `docs/CHANGELOG.md` §6.1 #56。",
    "见 §6.1 第 57 行。": "见 `docs/CHANGELOG.md` §6.1 #57。",
    "见 §6.1 #32 / #42。": "见 `docs/CHANGELOG.md` §6.1 #32 / #42。",
    "见 §6.1 #18；": "见 `docs/CHANGELOG.md` §6.1 #18；",
}
for a, b in repls.items():
    n = txt.count(a)
    txt = txt.replace(a, b)
    print("替换交叉引用 %s → %d 处" % (a[:18], n))
assert "§6.1 第" not in txt and "见 §6.1 #" not in txt, "仍有未改的 §6.1 引用"

# --- 5. 刷新文首快照 ---
txt = txt.replace("> 版本：**v4.0**　日期：**2026-11-05**　状态：**现行**（上一版 v3.9 / 2026-11-05）",
                  "> 版本：**v4.1**　日期：**2026-11-26**　状态：**现行**（上一版 v4.0 / 2026-11-05）")
old_snap = """> 本文描述的是**代码当前实际实现的状态**，不是设想。数据截至文首日期（2026-11-04 只读核）：
> **案例 461 条 / 候选 0 条 / 采集队列 278 条 / 归档 1404 条**；
> 发布台账真值 = SQLite `data/ledger.db` 的 `platform_posts` **363 条、0 组 `(case_id, platform)` 重复**
> （`python -c \"sqlite3 ... group by ... having n>1\"` 实测）。"""
new_snap = """> 本文描述的是**代码当前实际实现的状态**，不是设想。数据截至文首日期（2026-11-26 只读核）：
> **案例 599 条 / 候选 3 条 / 采集队列 390 条**（`case_library` 表 / `candidates.json` / `inbox.json` 实测）；
> 发布台账真值 = SQLite `data/ledger.db` 的 `platform_posts` **529 条**（2026-11-26 实测）。
> ⚠ 具体条数会随采集 / 发布滚动，上面是**瘦身当日快照**，不是固定值；刷新时重跑只读计数。"""
assert old_snap in txt, "文首快照锚点没命中"
txt = txt.replace(old_snap, new_snap)

# --- 6. §4.8 标题与平台数 ---
txt = txt.replace("### 4.8 四平台分发（v3 新增，替代 v2 的「公众号管线」单节）",
                  "### 4.8 多平台分发（v3 起为四平台，现为九平台入口；替代 v2 的「公众号管线」单节）")
txt = txt.replace("#### 4.8.1 能力矩阵（2026-10-17/18 实测）",
                  "#### 4.8.1 能力矩阵（2026-10-17/18 实测；后续新增平台与冻结状态见 `docs/CHANGELOG.md` 各轮发布条目）")
txt = txt.replace("| `reconcile_verdict.py` | 四平台共用四态诊断",
                  "| `reconcile_verdict.py` | 多平台共用四态诊断")

# --- 7. §8 Roadmap:勾销已完成、修过时数字 ---
txt = txt.replace("""6. **README 与 PRD 同步**：以本文数据为准刷新 README 条形数字与发布计划模板（2026-10-21 复核：README 仍写 24 条案例）。
7. **消化采集积压**：135 条 `deep` 卡在 inbox，按分数降序批量晋升；顺带确认 `pick_targets` 的「只挑 trustmrr」硬条件是安全阀（实测 deep 135 条全部来自 trustmrr，HN/IH 的 41 条全是 drop/later，当前 0 漏判）。""",
"""6. ~~README 与 PRD 同步~~ **本轮 v4.1 已做**：文首快照刷新到 599 案例 / 529 台账，§4.8 平台数改为九入口；README 条形数字另行同步（本次未动 README）。
7. **消化采集积压**（持续）：2026-11-26 只读核 inbox **390 条**、候选 **3 条**；按分数降序批量晋升，继续确认 `pick_targets` 的「只挑 trustmrr」硬条件是安全阀。""")
txt = txt.replace("1. **小程序上线**：按「备案通过 → 个体户执照 → 主体变更 → 开虚拟支付 → 认证」时序推进；先跑通真机支付闭环。",
                  "1. **小程序上线**（备案已完成：苏ICP备20260791212号-1X）：剩余「个体户执照 → 主体变更 → 开虚拟支付 → 认证」；个人主体开不了虚拟支付，付费墙 `get-cases` 双层白名单 + `canReadFull()` 已就绪，待真机支付闭环。")

open(prd, "w", encoding="utf-8", newline="").write(txt)
print("PRD slimmed:", len(txt.splitlines()), "lines (was", len(lines), ")")
