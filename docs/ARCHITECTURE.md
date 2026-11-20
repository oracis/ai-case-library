# 模块边界与交接契约

> 一句话：**业务边界早就清楚，乱的是交接方式——四段逻辑都直接读写同一批裸文件
> 和同一个 SQLite 库。所以不拆仓、不造新框架，只把"谁能读写哪个状态、在哪交接、
> 交接要过什么闸"写成可查的表，新增脚本照表走。**
>
> 本文 2026-11-20 成文。数字是当天实测（`grep -l` 口径，含 docstring 提及），
> 会随仓库变化漂移；文末给出复核命令，引用前先跑一遍。
> 专题细节不在这里重复：数据口径看 [DATA_SCHEMA.md](DATA_SCHEMA.md)，
> 代码规矩看 [CODE_STYLE.md](CODE_STYLE.md)，平台差异看 [MULTIPLATFORM.md](MULTIPLATFORM.md)，
> 发布 SOP 看 skill `case-library-publish-runbook`。

---

## 一、四段划分（逻辑分层，不是物理拆仓）

```
① 采集与入库                ② 内容生产与评分              ③ 发布与对账
harvest / daily_harvest     ai_verify / draft_case_text    publish_one / publish_slot
promote_to_candidates       score_* / gen_platform_md      multiplatform/ + 各 *_publish.py
candidate_store             retone / make_article          *_reconcile / pub_reconcile
        │                            │                            │
        │  candidates.json            │  cases.json                │  publish_manifest.json
        └────────────────────────────┴────────────────────────────┘
                                     │
                         ④ 站点层（横切，只读为主）
                 server.py / static / admin / build_static / auto_deploy / miniprogram
```

**为什么不物理拆成三个仓**（2026-11-20 评估结论）：

| 阻碍 | 实证 |
|---|---|
| 单一真值库拆不开 | `platform_posts` 外键指向**老表** `cases(id)`（`db.py:299`），SQLite 改不了外键 ⇒ 老表必须留着当锚点，真值 `case_library` 只能并列新建、不能改名复用（`db.py:211-221`） |
| 路径假设遍布全仓 | 189 个文件写死 `ROOT=dirname(dirname(abspath(__file__)))`，296 个 `sys.path.insert`；搬家即断 |
| 公共基座横跨三段 | `wechat_publish.py` 被 52 个非测试脚本 import（CDP 协议层已于 11-20 抽到 `cdp_core.py`，但公众号专属逻辑仍共用） |
| 镜像/真值一致性 | `cases.json` 是 SQLite 导出镜像；跨仓后没人兜得住"库改了、镜像没改" |
| 本机基础设施单仓假设 | `.githooks/pre-commit`、CI workflow、`atomicio` 进程锁、CDP 的 9222/9223/9224 profile 配对，全绑一个工作目录 |

**已有的缝（借力，别重造）**：`scripts/multiplatform/` 是干净子包（只被 8 个文件 import）；
`candidate_store` / `case_store` 是各自的唯一写入口；`scripts/_oneoff/` + `check_tmp_leaks`
闸建立了脚本纪律；三个 skill 封装了平台差异与发布 SOP。

---

## 二、共享状态矩阵（本文核心）

谁读写哪个状态，**一张表说清**。表中"真值"列是冲突时的权威来源，其它一律是镜像或派生。

| 状态 | 真值 | ① 采集入库 | ② 内容生产 | ③ 发布对账 | ④ 站点层 |
|---|---|---|---|---|---|
| `data/ledger.db` → `case_library` | **SQLite 表本身** | 写（promote 入库） | 写（评分/文案回填） | 只读加载 | 经 API 读写 |
| `data/cases.json` | `case_library` 的导出镜像 | — | 只读为主 | 只读 | 只读 |
| `data/candidates.json` | **文件本身**（LF + indent2 + 无尾换行） | 读写（唯一入口 `candidate_store`） | 只读/经入口回填 | 只读 | 经 API 读写 |
| `data/verifications.json` | 文件本身 | 写（`ai_verify`） | 只读 | — | 经 API 写 |
| `data/publish_manifest.json` | 文件本身 | — | — | 读写（**别用 `Manifest.save()` 改单条**，见下） | 只读 |
| `platform_posts` 表 | SQLite 本身 | — | — | 写（`db.upsert_post`） | 经 API 只读 |

### 三条铁律

1. **真值在 SQLite，`cases.json` 只是镜像。** 读走 `case_store.load_cases()`，写走
   `save_cases()` / `save_case()`；表外字段会静默丢弃（先补 `db.py` + `_CASE_LATE_COLUMNS`）。
   ⚠️ 真值表名是 `case_library`，不是字面的 `cases` 表——`db.CASE_TABLE='case_library'`。
2. **子集视图禁止交给整本写盘函数。** `--case/--filter/--only` 只做对账视图；
   写盘必传全量 `local_all`。曾把 81 条洗成 1 条。提交前必看 `git diff --cached --stat`
   里 `data/*.json` 有无大幅删行。
3. **`Manifest.save()` 会整本重刷。** `load()` 先按 db 覆盖 `state` 与 `at`，为改一条调
   一次就带出全表回退。单条改 = 直接改 JSON + 一条 SQL；刷坏了用
   `scripts/_oneoff/repair_manifest_at_regression.py`。

---

## 三、三处交接契约（显式入口）

交界处的判据不再散落在 CLI 脚本里，各自的入口与闸门如下。**新增跨段调用只能走这些入口。**

| 交界 | 显式入口 | 闸门 | 幂等判据 |
|---|---|---|---|
| ①→② inbox→候选 | `promote_to_candidates.to_candidate()` + `candidate_store.upsert_candidate()` | `validate_candidate()` 写盘准入（PRD #164） | 按 `id`；同 id 合并字段、不重复插 |
| ②→③ 候选→案例 | `handoff_contract.precheck()` + `server.py:POST /api/candidates/:id/promote` + `case_store.save_case()` | 三道闸：存在+未入案例库 / `VR.evaluate` publishable / `VR.content_gate` 正文四硬字段 | 已在 `case_ids` 直接拒；server 用完即清草稿 |
| ③内部 发布→台账 | `publish_one.py`（十一闸）→ `db.upsert_post()` → `pub_reconcile.py` | 发布成功唯一判据 = 已发表列表按标题命中 + 公开页回读正文 | `(case_id, platform)` 幂等，`--force` 只绕 dedupe |

### 契约使用约定

- `handoff_contract.precheck()` 是**纯函数**（不写盘、不联网、不改入参），任何入口可复用。
  `promote_ready.py` 已 re-export（`P.precheck is H.precheck`）。
  ⚠️ 改判据**必须同步 server.py 的 promote 端点**，否则本地预检与服务端判定分叉。
- I/O 层的完整契约（三接口、格式不写死、原子写归 `atomicio`、显式传 `path=`）
  写在 `DATA_SCHEMA.md:414-433`，这里不复制。**别照旧注释写死 CRLF**——实测是 LF。
- `db.upsert_post()` 有"状态只许前进 + 空字段回填"逻辑：传 `remote_id=''` 会被旧值救回来。
  **清空只能走一条 `UPDATE`**，写完单独回读。

---

## 四、新增脚本的防横跨规矩

写新脚本前按这个清单过一遍，防止继续把三段焊在一起：

1. **先问归属**：这段逻辑属于①②③④哪一段？跨段调用有没有上表的显式入口？
2. **不裸读写共享状态**：候选走 `candidate_store`，案例走 `case_store`，发布记录走 `db.upsert_post`。
   唯一例外是刻意独立的测试固件（见下）。
3. **通用层显式传 `path=`**：`server.load_json/save_json`、`harvest`、`ai_verify` 等带 `name`
   的通用层，测试替换 `ROOT`/`DATA_DIR` 后必须靠显式 `path` 引到临时目录——
   曾因此读到真台账、打挂测试并触发联网抓取。
4. **只读函数不许写盘**：`auth.get_secret({})` 曾抹掉 `admin.json` 的 `password_hash`；
   调别人的 `get_*` 先确认无副作用。
5. **不跑全量 `selftest.py` 做验证**：本机超时被 SIGTERM 会把固件写进 SQLite 真值表
   （8 条 `selftest-*`，`created_at=当日`），且 `git checkout` 只清镜像清不掉真值。
   验证只跑聚焦单测。
6. **一次性脚本进 `scripts/_oneoff/`**（ROOT 上跳三层），`tmp/` 只放产物；
   提交时新脚本一起 `git add`，否则 `check_tmp_leaks` 闸拦。
7. **正文禁内部黑话/金额/破折号**：改写归模型、事实裁定归代码；数字必须带时点 + 口径。
   详见 skill 的质量红线与 `docs/DATA_SCHEMA.md`。

---

## 五、刻意不收口的例外（不是漏网）

- `scripts/test_*.py` 与 `e2e_verify_write.py` 直接读写固件——用被测层读就失去独立性。
- `verify_case.py` 清场用 `shutil.copy2` 逐字节还原备份（要原字节，不是重新序列化）。
- `tmp/` 下一次性历史脚本是复现证据，不回改。
- 各平台脚本写死自己的 CDP 端口/profile（9222/9223/9224）——端口与 profile 必须成对给，
  这是平台隔离要求，不是重复造轮子。第 4 步渐进迁移（改用 `cdp_core` 直连）尚未做，
  等基座稳了再分批推。

---

## 六、数字复核命令

```bash
# 各段引用者计数（换数字前先跑）
grep -l "candidates.json" scripts/*.py | grep -v '^scripts/test_' | wc -l
grep -l "cases.json"      scripts/*.py | grep -v '^scripts/test_' | wc -l
grep -lE '^(import wechat_publish|from wechat_publish import)' scripts/*.py \
  | grep -v '^scripts/test_' | wc -l
grep -l "sys.path.insert" scripts/*.py | wc -l

# 真值表名与镜像格式
cd scripts && python -c "import db; print(db.CASE_TABLE)"
python scripts/candidate_store.py --check
```
