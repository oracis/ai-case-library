# 2026-11-28 清理与提交过程记录

生成时间：2026-11-28 19:50（GMT+8）
仓库：`D:/ai-case-library`（remote `git@github.com:oracis/ai-case-library.git`）
范围：从「全量测试后遗留 4 个红灯」开始，到「批量清 drafts 管线水印」为止。本文只记录**实测命令与响应**，不代替 `docs/CHANGELOG.md` 的条目口径。

## 一、结论摘要

- 已提交并推送：`2c82215` — `data: 补齐 10 个新 case 两轴评分并清洗 1 处黑话（全库 599 条）`。远端 `main` = 本地 HEAD = `2c82215a90c64befc8074cacacf2487c4c52141b`。
- 未提交：6 个原改动文件 + 52 个 `drafts/*.md`；另有 1 个新脚本已 `git add`（因钩子要求修复脚本必须进仓库）。
- 全量测试：**106 个文件 / 0 失败**，真值指纹零污染。
- `scrub_jargon --verify` 判据已重定并通过；11-05 的 461 条旧基线**未迁、未覆盖**。
- CHANGELOG #238–#241 的状态措辞已统一，历史 #142/#201/#235 的过期状态已订正。
- `drafts/` 下 52 个本地草稿的 `> 平台：… ｜ 状态：待推送…` 水印行已批量清掉。**注意：这些是本地草稿，发布时 `clean()` 本就会剥，不是线上问题。**

## 二、逐项过程与证据

### 1. 清掉 4 个历史红灯（19:05–19:20）

- 跑法：`python scripts/run_all_tests.py -v`（临时库隔离 + 真值指纹）。
- 首轮：106 文件 / 102 通过 / 4 失败。失败项：`test_fit_ready`、`test_make_article`、`test_score_value_parity`（10 个新 case 缺 `china_fit`/`solo_fit`，库 589→599）与 `test_jargon`（`videobgremover` 的「候选」黑话）。
- 修法：① 按红线**先** `score_china_fit.py` **后** `score_solo_fit.py` 补分；② `scrub_jargon.py --apply` 把 `videobgremover.corrections[0].claim` 的「候选资料 headline 称」改成「档案头条数字写的是」；③ `test_score_value_parity.py` 只更新事实计数 589→599 / 124→134，判据结构不动。
- 写前备份：`tmp/backup_cases_190713.json`、`tmp/backup_ledger_190713.db`。
- 数据审计：HEAD vs 当前 599→599，零丢键/清空；变化仅分数对象的 `rank`/`scored_at` 重排 + 1 处 corrections 文案；db 与镜像逐 case 一致。
- 复测：4 个单项全绿；`run_all_tests.py` 106 文件 / 0 失败（9m21s），`ledger.db + 61 个 data/*.json` 指纹零污染。

### 2. `scrub_jargon --verify` 判据重定（19:25–19:36）

- 用户问「461 条旧基线要不要一起迁」。裁定：**不迁、不许覆盖** `_backups/jargon-2026-11-05/cases.json`（11-05 清洗前唯一取证快照，未被 git 跟踪）；覆盖后 `--verify` 会变成「当前 vs 当前」自证。
- 只读实证：备份 461 条 / 当前 599 条，前 461 条 id 位置全对齐、无删除；按 id 逐字段 365 处数字漂移均为 11-05 后的正常收入刷新（dropapost `$1,103→$1,137`、stat-ai `$18,647→$15,469`、copykitten `$349→$365`）。旧快照重放清洗规则：214 处改写、**数字漂移 0、洗后残留 0**。旧判据拿当前数据比旧快照，把正常刷新误判为失败，属一次性迁移验收口径过期，非数据债。
- 改法：`scripts/scrub_jargon.py` 新增纯函数 `verify_snapshot(old, cases)`；`--verify` 改为①旧快照重放（改写处必须非空、逐字段数字序列不变、洗后残留 0）②当前库残留 0；历史差异降级为提示；无旧快照时只验当前残留；防自证闸：旧快照已洗过（`replay=0`）判失败。`scripts/test_jargon.py` +5 项判据测试。
- 实测：`python scripts/scrub_jargon.py --verify` → `残留黑话 0 / 旧快照重放改写 214、数字漂移 0、洗后残留 0 / 提示数字刷新 365、新增 138、案例数 461→599 / ✅ 通过`（exit 0）。`test_jargon` 17/17 OK。旧快照字节未动。

### 3. 分组提交与推送（19:35–19:36）

- 用户「先提交数据/cases.json，其他几笔再说」。提交前 `git fetch origin main` → `HEAD...FETCH_HEAD = 0 0`、无分叉。
- 只 `git add -- data/cases.json`（暂存区仅此 1 文件，+3698/-3058），钩子 `[OK]`（仅提醒有未提交改动，不挡闸）。普通 `git commit`（不带 pathspec、不 `--no-verify`）生成：
  - `2c82215` — `data: 补齐 10 个新 case 两轴评分并清洗 1 处黑话（全库 599 条）`
- 用户「推完把这次改动汇总进 CHANGELOG.md」后：`git push origin main` → `4f45744..2c82215`。回读本地 HEAD / `origin/main` / `git ls-remote origin main` 三者同为 `2c82215a90c64befc8074cacacf2487c4c52141b`，`git status -sb` 无 ahead/behind。
- CHANGELOG 新增 #240 汇总；随后按用户要求把 #238/#239 的「改动未提交、未推送。」改为「改动状态以 #240 为准。」，#240 内改「#238/#239 的改动状态均以本条为准」。

### 4. 历史措辞订正与全仓复扫（19:39–19:42）

- 复查无别处引用 #238/#239 的旧措辞；`tmp/` 零命中；`.workbuddy/memory/` 里的同类句子是按时间追加的历史叙述（19:37 条目已覆盖），不回改。
- 用户「那 3 条历史条目也一起订正」。先核落库证据再改：
  - #142 → 代码与文档改动随 `9074b2e` 提交并推送（本仓 `origin/main` 祖先）。
  - #201 → 游戏仓改动已提交 `7866f35`（游戏仓 `origin/main` 祖先、工作区干净；游戏仓本地另有 1 笔未推送提交 `ea06a79`，与本稿无关）。
  - #235 → 11-27 台账已提交并推送 `ad99687`。
- 三条均注明原措辞已过期；顺手去掉 #142 行尾原有空格（否则改动该行会让 `git diff --check` 报 trailing whitespace）。复检 CHANGELOG「改动未提交」「未提交、未推送」零残留。
- 全仓复扫结论：唯一待办是未提交/未推送的改动本身；preflight 正常、`ledger_health` 无待处理项、`case_store --check` 镜像与库一致（3,687,691 bytes）。2 条「非 published 却带 published_at」是历史行（`clawlink`、`magicslides-app`），note 已写明，不动。

### 5. PRD 术语行与自曝写法排查（19:45–19:48）

- 用户说的「README 里那处水印文案」实为 `docs/PRD.md:415`（README.md / README.en.md 对「水印 / 未推送 / 本地稿 / 平台：」零命中）。经确认改为：
  - 原：`> 平台：X ｜ 状态：本地稿，未推送`
  - 新：`> 平台：X ｜ 状态：本地稿` 这类草稿状态标记行，等于向审核自曝批量（CSDN 一.3）
- 再扫公开文档（README×2 / PRD / DATA_SCHEMA / 产品流程图）：仅 PRD:415 一处水印状态标记；CHANGELOG 8 处历史引文不动；`publish_guard.WATERMARK = r"^>\s*平台[:：].{0,120}(本地稿|未推送|状态[:：])"` 必须保留这些字面量才能拦老稿。PRD 的「内部黑话」「口径旁注」、README 的「候选资料」、DATA_SCHEMA 的字段名说明都是规则定义/字段文档，不改。

### 6. 批量清 drafts 水印（19:49–19:50）

- 只读盘点：**52 文件 / 52 行**，分布在 `drafts/{bilibili,juejin,toutiao,wechat,xhs}` 各 10 + `drafts/longtail` 2；全部 git 跟踪、全 LF、水印固定第 3 行且第 4 行为空行。
- 新增 `scripts/_oneoff/clean_drafts_watermark_20261128.py`（112 行，默认干跑 / `--apply` 才写）：
  - 只认行首 `> 平台：` 且每文件恰好一条，下一行必须为空行；
  - 写前按相对路径备份 52 个原文件到 `tmp/drafts_watermark_backup_20261128/20261128_194756/`；
  - 原子写 + 逐文件回读校验 h1 / 正文 / 行数（恰好少 2 行），任何失败整组回滚。
- 踩坑：bytes 正则不能写中文字面量（`bytes can only contain ASCII literal characters`），改成 str 正则对 `decode("utf-8", "replace")` 匹配。
- 实测：干跑命中 52 → `[APPLY] 已清 52 个文件；残留 0` → 二次干跑命中 0。
- 校验：`git diff --numstat -- drafts` 52 行全是 `0/2`（零新增）；与备份逐文件核对 h1 与正文逐行一致、52/52 零差异、无 CRLF。
- 回归：`test_publish_guard + test_gen_case_drafts` 78 项 OK；`test_md2wechat.py` 80 项全通过。
- 钩子：新脚本未跟踪时 `[FAIL]` ④（脚本必须进仓库），已 `git add` 该脚本后 `[OK]`；`git diff --check` 退出码 0。
- CHANGELOG 新增 #241，记录 PRD 术语行与批量清理，并声明状态以本条为准。

## 三、当前仓库状态（19:56 更新后）

| 项 | 状态 |
|---|---|
| 远端 `main` | `8244cd933aaa8d19e9bf7214d1f1f55ca6d9876d` |
| 本地 HEAD | 同上，无 ahead/behind |
| 已提交并推送 | `8244cd9` — 本次收尾 60 文件（含本文与清理脚本）；`2c82215` — `data/cases.json` 补分/清洗 |
| 已暂存未提交 | 无 |
| 未暂存 | `docs/CHANGELOG.md`（#241 补记实际 SHA）、本记录文件 `docs/RECORD-2026-11-28-cleanup.md`（本表更新本身） |
| 未暂存统计 | 提交前为 60 files changed, 363 insertions(+), 149 deletions(-)（其中 drafts 52 files / 0 added / 104 deleted）；当前仅上述 2 个文档改动 |
| 钩子 / diff | `[OK]` / `git diff --check` 0 |

补记：`git add -A` → 暂存 60 文件 → `git commit` 生成 `8244cd9` → `git push origin main` 为 `2c82215..8244cd9`；本地 HEAD / `origin/main` / `git ls-remote origin main` 三者同为 `8244cd933aaa8d19e9bf7214d1f1f55ca6d9876d`。

## 四、备份与产物路径

- `tmp/backup_cases_190713.json`、`tmp/backup_ledger_190713.db` — 补分/清洗前真值。
- `_backups/jargon-2026-11-05/cases.json` — 11-05 清洗前基线，461 条，**未动、不可覆盖**。
- `tmp/drafts_watermark_backup_20261128/20261128_194756/` — 52 个草稿原文件备份。
- `scripts/_oneoff/clean_drafts_watermark_20261128.py` — 本次批量清理脚本，已随 `8244cd9` 提交并推送。
- `docs/CHANGELOG.md` #238–#241 — 各轮实测记录与状态口径。
- `.workbuddy/memory/2026-11-28.md` — 当日流水（按时间追加）。

## 五、待办

1. 提交并推送本表与 CHANGELOG #241 的 SHA 补记（当前仅这 2 个文档改动，未提交、未推送）。
2. 历史遗留未动：游戏仓本地领先 1 笔 `ea06a79`；`drafts/` 之外可能还有旧水印产物，未在本次授权范围。
