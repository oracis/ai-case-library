# `data.json` schema

Field reference for the machine-readable snapshot published by **AI Case Library**
([github.com/oracis/ai-case-library](https://github.com/oracis/ai-case-library)).

This document is the English-language interface contract. It describes **keys, types,
enums and formulas** — not the prose. You can build a client, a chart or a filter on top
of this file without reading a single Chinese sentence.

- **Produced by**: `scripts/build_static.py` (build artifact) and `GET /api/data` (local server)
- **Consumed by**: the static site itself (`data.js`), and any third-party program
- **Source of truth**: the repository `data/*.json` files, hand-curated and committed.
  One exception, since 2026-11-05: the case library lives in a SQLite table
  (`case_library` in the local `data/ledger.db`), and `data/cases.json` is its
  **byte-for-byte export** — the file in git is still what you review and diff,
  and a fresh clone falls back to reading it when no local database exists.
- **License**: MIT — free to use, modify and redistribute, including commercially

## Where to get it

```bash
# from a local clone (recommended: this is the up-to-date machine copy)
python scripts/build_static.py          # writes dist/data.json + dist/data.js

# or from a running local server
python server.py && curl -s http://127.0.0.1:5052/api/data
```

When the project is deployed to object storage, the same document is served as
`data.json` next to `index.html`. `data.js` is byte-identical content wrapped in
`window.__CASE_LIB_DATA__ = {...}` — the page uses that one because `file://` blocks `fetch`.

## Stability and versioning

| Field | Meaning |
|---|---|
| `schema_version` | Semantic version of **this document**. Bumps when a key is renamed, removed or retyped. |
| `generated_at` | When the snapshot was rendered, `YYYY-MM-DD HH:MM:SS`, **Asia/Shanghai**, no timezone suffix. |
| `lang` | BCP-47 tag of the **prose** fields. Currently always `zh-CN`. |

Additive changes (new keys, new enum members) do **not** bump `schema_version`.
Renames do. Consumers should ignore unknown keys rather than fail on them.

### Language boundary

Everything that is *prose* is Chinese: `one_liner`, `verdict`, `what_it_does`,
`why_it_works[]`, `note`, `metric_note`, `corrections[].claim/truth`, and so on.

Everything that is *data* is language-neutral or English: key names, enum values
(`verification`, `quadrant`, `medal`, `sources[].kind`), numeric `metrics`,
all scores, ranks and weights, ISO dates, URLs, and `name_en`.

That split is deliberate. The interpretation layer of this library — what the numbers
*mean* for a builder — is written for a Chinese reader and is not machine-translated.
Scores, enums and metrics are safe to aggregate, sort and chart anywhere.

---

## Top level

```jsonc
{
  "schema_version": "1.0",
  "lang": "zh-CN",
  "generated_at": "2026-09-13 12:27:04",
  "cases":      [ /* Case, 25 items, curated & verified   */ ],
  "candidates": [ /* Candidate, 33 items, human-picked     */ ],
  "inbox":      [ /* InboxItem, ~347 items, machine-mined  */ ],
  "sources":    { /* SourcesDoc, see below                  */ },
  "graveyard":  [ /* GraveyardItem, 22 items, shut down    */ ],
  "anti":       { /* AntiCases: {cases, volumes, built_at} */ },
  "site":       { /* SiteDoc: wechat / community / repo    */ },
  "stats":      { /* Stats, see below                       */ }
}
```

The three arrays are the **three-tier funnel**, and the distinction is the point of the
project:

| Array | Meaning | Who fills it | Numbers verified? |
|---|---|---|---|
| `cases` | Curated cases. Each was searched and checked by hand. | human | yes — see `verification` |
| `candidates` | Looks worth writing; revenue not checked yet. | human, from `inbox` | no |
| `inbox` | Raw harvested links, never reviewed by a human. | script, daily | no |

**Do not treat `inbox` or `candidates` as verified data.** Use them as leads only.

---

## `cases[]`

A curated case. Only `id`, `name`, `status`, `verification` and `one_liner` are
guaranteed present; everything else may be `null`, `""` or `[]`.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable slug, lowercase. Primary key, safe to join on. |
| `name` | string | Display name (may be Chinese-influenced for some entries). |
| `name_en` | string | Latin-script name. Same as `name` for the non-Chinese names. |
| `origin` | string | Free-text HQ, e.g. `美国 · 旧金山`, `新加坡（法国人）`. Not an enum. |
| `one_liner` | string | One-sentence description. |
| `category` | string | Coarse bucket. 7 values in use, free text in principle. |
| `industry` | string | Fine-grained vertical. Free text, ~23 values in use. |
| `status` | string | `curated` for everything in this array. |
| `verification` | enum | Evidence grade. **Read this before quoting a number.** |
| `metrics` | object \| null | Reported financials. See below. |
| `models` | string[] | Business-model tags, e.g. `结果定价`, `订阅制`, `卖给同行`. |
| `replicability` | object \| null | Original 4-dimension difficulty. **1 = easiest, 5 = hardest.** |
| `verdict` | string | The one-line takeaway for a builder. |
| `what_it_does` | string | Product description. |
| `how_it_makes_money` | string | Revenue mechanics. |
| `why_it_works` | string[] | Mechanism, 3–5 bullets. The most reusable part. |
| `playbook` | string[] | Transferable moves, 2–3 bullets. |
| `signals` | string[] | Supporting evidence points. |
| `corrections` | object[] | Corrections to widely-cited wrong figures. See below. |
| `sources` | object[] | Cited evidence. See below. |
| `verified_at` | string | `YYYY-MM-DD` — when the numbers were last checked. |
| `updated_at` | string | `YYYY-MM-DD` — last edit of any kind. |
| `tags` | string[] | Free-text tags. |
| `needs_review` | bool | Optional. Present and `true` when promoted without verification. |
| `caliber` | enum \| "" | Which revenue definition the headline figure uses: `arr` \| `mrr` \| `run_rate` \| `lifetime` \| `annual` \| `last_30d` \| `gmv` \| `gross`. `annual` is a **completed calendar-year sum actually earned** — not ARR (forward-looking), not `lifetime` (multi-year cumulative), not `run_rate` (one month × 12). |
| `quality_score` | number \| null | Bonus score (0–100) at publish time. 60 is the publish threshold. |
| `tier` | enum | `premium` \| `standard` \| `backup` — where the case sits in the library. See below. |
| `tier_reason` | string | Why it got that tier. Always present when `tier` is. |
| `published_from` | string \| null | The candidate `id` this case was promoted from. |
| `human_read` | bool \| null | Whether someone ticked "I read the source myself". **Absent ≠ false** — see below. |
| `human_read_at` | string | Server-stamped `YYYY-MM-DD HH:MM` of the tick. `""` when unticked. Never taken from a request body. |
| `verification_downgraded` | object \| null | Present only when `audit_evidence.py` lowered `verification`. See below. |
| `china_fit` | object \| null | China-portability score. See below. |

#### `verified_at` / `caliber` — candidate → case

Both fields are defined on the **case** above, but a candidate may carry them
early (2026-11-05). When promoted, `server.py` resolves them through
`verify_rules.resolve_case_timepoint(candidate, draft, now)`:

- `verified_at` is **the day the revenue numbers were last checked / fetched**,
  not the promotion day. `make_article.timepoint_row()` prints it as
  `截至 <date>（抓取日快照）`, so stamping the promotion day would silently
  date a stale cached snapshot forward. A candidate-carried valid `YYYY-MM-DD`
  wins; invalid values fall back to the server's `now`; ISO timestamps keep
  their date part only.
- `caliber` resolution order: the human verification draft
  (`verifications.json`) → candidate top level → candidate `metrics.caliber`.
- Candidates without these fields keep the old behaviour (`verified_at = now`).

⚠ Do **not** back-fill `verified_at` into `data/verifications.json` — that
store deliberately has no such field (37/37 entries). And `verified_at` is
unrelated to the three meanings of `verification` described above.

#### `tier`, `tier_reason`

Three shelves, from strongest to weakest evidence:

| `tier` | Meaning | Decided by |
|---|---|---|
| `premium` | 精品池 — first-hand evidence, safe to quote | a source of kind `stripe`/`official`, or `quality_score` ≥ 60 |
| `standard` | 实核池 — independent third-party source, caliber still under review | `verification` is `partial` |
| `backup` | 备选池 — self-reported or contradictory only | everything else |

The policy lives in `verify_rules.default_case_tier()` and is applied by all three
tier-setting paths in the server (promote, tier-patch, manual create) **and** by
`evaluate()` — so the admin's "publish to …" hint can never disagree with where the
case actually lands. `tier_reason` always records which rule decided it.

`standard` was added on 2026-09-20. Before that there were two shelves and every
case holding a third-party source but no first-hand one was lumped into `backup`
together with the self-reported ones, erasing the difference between "has a source"
and "nobody checked".

#### `human_read` / `human_read_at`

A marker, not a gate — it never blocks publishing. Cases promoted **before** the
marker existed simply carry no `human_read` field: absence means *this case predates
the marker*, not *nobody read it*. `false` means the box was left unticked. Only the
server may stamp `human_read_at`, and it takes that moment from the stored draft, so
a forged timestamp in the request body is ignored.

#### `verification_downgraded`

| Field | Notes |
|---|---|
| `from` | The grade the case used to claim. Restoring it is a one-line edit. |
| `to` | What `verification` is now. |
| `reason` | Why — including which source kinds were actually registered. |
| `at` | `YYYY-MM-DD` of the downgrade. |
| `tool` | Always `scripts/audit_evidence.py`. |
| `solo_fit` | object \| null | Solo-founder score. See below. |
| `composite` | object \| null | Two-axis aggregate + quadrant. See below. |

### `verification` enum — highest to lowest evidence grade

| Value | Verdict | Meaning |
|---|---|---|
| `stripe` | strongest | Directly verified through a payment gateway (TrustMRR, Stripe case studies). |
| `official` | strong | Company press release or financial disclosure. |
| `partial` | caution | Figures are real, but the definition is unclear (MRR vs. all-time?). |
| `founder` | weak | Self-reported by the founder, no third-party check. |
| `disputed` | conflict | Sources contradict each other. |
| `unverified` | none | Not checked yet. |

`stats.verified` counts only `stripe` + `official`.

#### The claimed grade must be supported by the sources

A grade is a promise to the reader. Marking a case `stripe` says *we have seen payment
data* — so `stripe`/`official` require an `A`-tier source to be registered on the case.
Everything else steps down the ladder (`partial` needs `B`, `founder` needs `C`):

```
A) stripe/official → stripe    B) press/review → partial
   official → official            C) founder → founder
                                  D) secondary → nothing (needs a human)
```

The rule lives once, as `verify_rules.best_supported_level()` / `evidence_gap()`;
`scripts/audit_evidence.py` walks `cases.json` through it and fixes claims that
evidence does not carry:

```bash
python scripts/audit_evidence.py            # report only
python scripts/audit_evidence.py --apply    # downgrade, with a backup and a record
```

An applied downgrade never deletes the old value — it moves into
`verification_downgraded` (see below), so restoring it is removing one field.

#### Same key, three stores, three meanings — never back-fill one from another

The key `verification` also exists in the *internal* stores. The values look alike
but answer different questions; they are **not** copies of one field:

| Store | Written by | What the value means |
|---|---|---|
| `candidates.json` | `promote_to_candidates.to_candidate()` (hardcodes `stripe`) | Provenance of the number: read from the payment-gateway side (a TrustMRR cached snapshot). **Not** an evidence-grade ruling. |
| `verifications.json` | `PUT /api/candidates/:id/verification`, `ai_verify.py` | Evidence grade (`stripe` / `official` / `partial` / `founder` / `disputed`) derived from the registered `source_kinds`. |
| `cases.json` | `POST /api/candidates/:id/promote` in `server.py` | Published grade: the draft's grade takes precedence over the candidate value, then `evidence_gap()` clamps it to what the sources carry. |

So one case may legitimately read `stripe` in the candidate pool and `partial` in
`verifications.json` — e.g. `bubblav` (Polar payment source, registered sources
`homepage` + `review`). **Do not "fix" this by copying either value into the other:**
neither file is rewritten by the other, and `import_to_db.py` maps them into separate
SQLite tables (`verifications` vs the case tables). The only place they meet is
promotion, which builds a *new* `cases.json` object and deliberately uses the
evidence-grade side for the public case.

### `metrics` object

All keys optional; monetary values are **plain numbers** in USD, no currency symbol.

| Field | Type | Notes |
|---|---|---|
| `headline` | string | Human-readable headline, e.g. `$200M ARR`. Display only. |
| `arr` | number | Annual recurring revenue, USD. |
| `mrr` | number | Monthly recurring revenue, USD. |
| `last_30d_revenue` | number | Trailing 30-day revenue, USD. **Not** MRR — a rolling window, not a recurring commitment. Keep this exact key name: it is the one listed in `data/sources.json`, and `triage.revenue_of()` reads nothing else. TrustMRR reports `Current MRR: 0` for every non-subscription business, so for those entries this is the only revenue figure that exists. |
| `all_time` | number | Cumulative revenue, USD. **Not** ARR — this is the most common conflation in secondary coverage. |
| `customers` | string | Free text — deliberately, because the units differ wildly (`42,000 users` vs `700+ clinics`). |
| `team` | string | Headcount, free text. |
| `funding` | string | Total raised, free text. |
| `valuation` | string | Free text. |
| `growth` | string | Growth trajectory, free text. |
| `price_point` | string | Pricing, free text. |
| `metric_note` | string | Caveats on the figures above, incl. uncertainty ranges. **Read before quoting.** |

### `replicability` object — 1 = easiest, 5 = hardest

| Field | Meaning |
|---|---|
| `tech` | Technical difficulty. |
| `distribution` | Customer-acquisition difficulty. |
| `capital` | Up-front capital required. |
| `timing` | Dependence on a market window. |

A score of `5` in any dimension means a single person essentially cannot do it.
Note the **inverted direction** relative to `solo_fit.dims` and `china_fit.dims`
(both `5` = most favourable). `solo_fit` derives three of its five dimensions from
this object, so the two can never disagree.

### `corrections[]`

The highest-value part of the dataset: figures that circulate widely in secondary
coverage and are wrong.

| Field | Type | Notes |
|---|---|---|
| `claim` | string | The claim as commonly repeated. |
| `truth` | string | What the primary evidence actually says. |
| `source` | string | URL of the evidence, or an attribution note. |

### `sources[]`

| Field | Type | Notes |
|---|---|---|
| `label` | string | Human-readable citation. |
| `url` | string | Link. |
| `kind` | enum | Evidence class. The tier column decides what the case may claim — see below. |

| `kind` | Tier | Meaning |
|---|---|---|
| `stripe` | A | Payment-gateway data (TrustMRR, Stripe case studies). |
| `official` | A | Company press release or financial disclosure. |
| `press` | B | Reporting by an independent outlet. |
| `review` | B | Third-party teardown / fact-check site (SaaSXtra, Steal What Works, NeoDrop). |
| `founder` | C | The founder's own posts. |
| `secondary` | D | Chinese second-hand retellings (WeChat accounts, paid communities). |

`A` (stripe, official) is the only tier that counts as first-hand (`primary: true`
in `verify_rules.SOURCE_TIERS`). A `D`-only case cannot support **any** evidence
grade — it needs a human re-check, not a different label.

---

## Scoring objects

Three independent score objects can appear on a case. All two-axis consumers only
need `composite`; the other two are the inputs.

Every scoring object carries the same envelope: `score` (0–100, 1 decimal),
`raw` (weighted points), `max_raw` (weighted maximum), `rank` (1 = best, across all
scored cases), `medal` (`gold` \| `silver` \| `bronze` \| `null`), `dims`, `weights`,
`scored_at`. `score` is always `raw / max_raw × 100`.

### `china_fit` — can this be ported to China?

Dimensions are 1–5, **5 = easiest to port**. `max_raw = 34.5`.

| Dim | Weight | Question |
|---|---|---|
| `demand` | 1.5 | Will the Chinese target customer actually pay? |
| `payment` | 1.2 | Can revenue be collected smoothly in China? (Stripe is unavailable there.) |
| `compliance` | 1.2 | Distance from regulatory red lines — higher is safer. |
| `acquisition` | 1.0 | Does each overseas acquisition channel have a Chinese equivalent? |
| `localization` | 1.0 | How much has to be rebuilt to fit the market? |
| `competition` | 1.0 | Is there already a dominant free substitute in China? |

Extra fields: `note` (the reasoning, always present), `blocker` (short reason when a
regulatory or structural hard stop applies, else `null`).

```bash
python scripts/score_china_fit.py --dry-run
```

### `solo_fit` — can one person build and run this?

Dimensions are 1–5, **5 = easiest for a solo founder**. `max_raw = 29.0`.

| Dim | Weight | Question |
|---|---|---|
| `delivery` | 1.4 | No team, no licences, no 24/7 on-call? |
| `reach` | 1.3 | Reachable without a sales team? |
| `capital` | 1.1 | Can it open without burning cash first? |
| `build` | 1.0 | Is the stack within one person's range? |
| `window` | 1.0 | Is there still room to enter? |

`build`, `reach`, `capital` and `window` are derived from `replicability` as
`6 − value`; only `delivery` is a separate human judgement. Extra fields:
`delivery_note` (why the `delivery` score is what it is) and `derived_from`
(a string documenting the inversion, for auditability).

```bash
python scripts/score_solo_fit.py --dry-run
```

### `composite` — the two axes combined

`solo_fit` and `china_fit` are **multiplicative in practice**: buildable but unsellable
in China = 0; demand exists but one person can't deliver = 0. So the aggregate is
bucket-shaped rather than a mean, with the weak axis weighted up:

```
composite = 0.6 × min(solo, china) + 0.4 × mean(solo, china)
```

`90 / 40 → 50` while `60 / 60 → 60`. **Two-passing beats one-spike.**

| Field | Notes |
|---|---|
| `score` | The composite value. |
| `solo` | Copy of `solo_fit.score`, for convenience. |
| `china` | Copy of `china_fit.score`, for convenience. |
| `rank` | 1 = best composite. |
| `quadrant` | enum — see below. |
| `quadrant_label` | Chinese display label for the quadrant. |
| `formula` | The formula string, embedded in the data. |
| `threshold` | The pass mark used (default `70.0`; configurable via `--threshold`). |

`quadrant` values:

| Value | Meaning |
|---|---|
| `go` | Both axes pass — a single person can do it and China wants it. |
| `partner` | Real demand, but the barrier is licensing, enterprise sales or team delivery. |
| `export` | Perfectly buildable, but don't sell it in China — demand or payment soil is missing. |
| `skip` | Fails both axes. |

Quadrants are evaluated on **each axis separately** against `threshold`, and the
projection onto two axes gives the four cells. Changing `--threshold` re-derives them.

---

## `candidates[]` and `inbox[]`

Candidate fields: `id`, `name`, `name_en`, `origin`, `one_liner`, `category`,
`verification`, `metrics`, `models`, `note`, `blocking`, `added_at`.
(`verification` is usually `unverified`; `blocking` is a free-text reason it might not
be worth writing.)

Inbox adds `source_url` and `harvest_source` (`hn` \| `trustmrr` \| `producthunt` \|
`indiehackers` \| `arrclub` — depends on which collectors ran with credentials).

Neither array contains scores.

### Candidate pool: single I/O entry (2026-11-06)

`data/candidates.json` 的读写**只走 `scripts/candidate_store.py`**，三个接口：

| 接口 | 语义 |
|---|---|
| `load_candidates(path=None)` | 返回 `list[dict]`（保序）；文件不存在 → `[]`；**解析失败响亮抛错**，半截 JSON 不许当空池 |
| `save_candidates(cands, path=None, fmt=None)` | 整批写回；按原格式原子落盘；返回 `(indent, newline, trailing_newline)`（兼容 `contentpack_ready.cmd_apply`） |
| `upsert_candidate(cand, path=None, replace=False, prepend=False)` | 按 `id` 单条增改，**读-改-写全程持文件锁**；默认合并字段、原位，新增追加末尾；`prepend=True` 置顶（`server` / `verify_case` 的既有展示顺序） |

四条口径：

1. **格式不写死。** 写盘前按现有字节枚举 `indent∈{1,2,4}` × 末尾换行 `{有,无}` 找逐字节匹配的那一种，再按它写回。2026-11-06 实测磁盘是 **LF + indent=2 + 无末尾换行**；仓库里若干旧注释写 CRLF 是历史漂移，**别照注释改代码**，跑 `python scripts/candidate_store.py --check` 看磁盘。
2. **原子性归 `atomicio`**（tmp + fsync + `os.replace`）。收口前 `apply_draft_text` 写死 CRLF、`verify_case` 文本模式写并补尾换行、`sync_headline_numbers` / `audit_delisted` 原地截断写，同一份文件被写成三种形态。
3. **收口的是「读写实现」，不是「路径解析」。** 所有带 `name` 的通用层（`server.load_json/save_json`、`harvest`、`ai_verify`、`triage`、`htm_increment`、`promote_ready`、`audit_delisted`）必须**显式传 `path=`**，否则测试替换 `F.ROOT` / `H.DATA_DIR` 后会读到真台账 —— 2026-11-06 实测把 `test_fetch_tm_md` 打挂并触发了联网抓取。
4. **兼容层**：`contentpack_ready.load_candidates/save_candidates/detect_fmt` 保留为薄封装，`CAND_PATH` 模块级常量**不许删**（`test_atomicio` / `test_publish_ready` 靠替换它把写盘引到临时目录）。

刻意不收口的例外（不是漏网）：`scripts/test_*.py` 与 `e2e_verify_write.py` 的独立验证 / 写脏再恢复固件（用被测层读就失去独立性）；`verify_case.py` 清场时用 `shutil.copy2` **逐字节还原备份**（要原字节，不是重新序列化）；`tmp/` 下一次性历史脚本是复现证据，不回改。

⚠️ 本层管「怎么读怎么写」+「谁准写进来」（PRD #164 写盘准入闸），**不做事实裁定、不改字段口径**。`verification` 同名三义见上文「Same key, three stores, three meanings」。

### HN 线索池：与主候选池物理隔离（2026-11-13，PRD #163）

`data/hn_leads.json` 只装 `harvest_source=hn` 的**创始人自述线索**，与
`data/candidates.json` 的支付侧直读候选分开存。原因是主池下游整套是按
TrustMRR 形状写死的状态机：`promote_to_candidates.py` 只挑
`harvest_source=="trustmrr"` + 非空 `trustmrr_slug`；`ai_verify.py` 见
`metrics.headline="未获取"` 就跳过；`fetch_tm_md.py` 会给 `hn-*` id 拼出
`trustmrr.com/startup/hn-*.md` 的 404。HN 记录混进主池会让
`pipeline_status` 跳到 S3，而唯一能补核实草稿的预核把它们全跳过 ⇒
**永远清不掉的死循环**（#162 误写 / #163 迁出实测）。

| 项 | 口径 |
|---|---|
| 唯一消费口 | `scripts/hn_leads.py`（`--apply --yes` 写；`--migrate-from-candidates --yes` 一次性迁移） |
| 存储实现 | 复用 `candidate_store` 通用层，**每处显式传 `path=LEADS_PATH`**（见上文口径 3）；刻意不另造第二套格式/锁 |
| 格式 | LF + indent=2 + 无尾换行；`python scripts/candidate_store.py --path data/hn_leads.json --check` |
| 记录形状 | 顶层 `list[dict]`；`id=hn-<objectID>`、`verification=unverified`、`source_kind=self_reported`、`metrics={"headline":"未获取"}`、附 `hn_*` 线索字段 |
| 阶段 | `pipeline_status.py` 硬编码只读 `data/candidates.json`、不扫描本文件 ⇒ 线索池**不参与 S0–S7**，主池空了阶段自然回 S7 |
| 转轨 | 人工回源拿到一手收入证据后，按正常采集流程重新进 inbox、由 `promote_to_candidates` 判定；⛔ 不许把 `harvest_source` 改成 `trustmrr` 伪造来源绕过安全阀 |

`--apply` 三道闸：线索池格式自检（文件不存在视作新建放行）、线索池无
**未暂存的工作区改动**（`git status` 第二列 `M` 才拦；`A ` / `??` 没有历史
基线可比，放行）、记录 `hn_role=lead`。写盘一律单条
`upsert_candidate(path=...)`（持锁，不整本覆盖），**主候选池永远只读**。

#### 写盘准入闸：两路不许倒进同一张表（2026-11-13，PRD #164）

#163 只是把数据迁走，**写入通道本身仍然无闸**：任何脚本都能再调一次
`upsert_candidate()` 把 HN 线索写回主池。现在在通用层加准入：

| 目标池 | 判定 | 放行 | 拒绝 |
|---|---|---|---|
| 主候选池 `data/candidates.json` | `infer_pool()` 默认落这里（含临时路径，保守方向） | `harvest_source=trustmrr`；`harvest_source=hn_evidence`（**必须带 `hn_evidence` 证据块**，PRD #166）；或空串（legacy，`verify_case.py` 脚手架） | `harvest_source=hn`、非 `hn_evidence` 的 `hn-` 前缀 id、`metrics.headline=未获取`、白名单外来源、缺证据块的 `hn_evidence` |
| 独立线索池 `data/hn_leads.json` | 文件名命中 `hn_leads.json`，或调用方显式 `pool=POOL_LEADS` | `verification=unverified` + `source_kind=self_reported` + `metrics` 无收入数字键 | trustmrr 候选、冒充已核验、带 `mrr`/`arr`/`revenue` 等数字 |

`validate_candidate()` 是纯函数（不联网 / 不读盘 / 不写盘），返回错误列表；
`upsert_candidate()` 默认 `validate=True`，不合规抛 `ValueError` 且一个字节
都不写。`save_candidates()` 默认不校验（主流水线自己的整批写通道），外部
来源写回主池须显式 `validate=True`；`validate=False` 仅供一次性数据修复，
会往 stderr 留痕。

⛔ 闸只核对**来源标签与字段形状**，不判断数字真假、不改字段口径，也不
替代 `promote_to_candidates.py:638` 的事实安全阀。它挡的是「无意写错池」；
要防故意绕过，仍靠 code review + `check_tmp_leaks`。

### HN 证据通道：从线索池到候选池（2026-11-13，PRD #166）

`data/hn_evidence.json` 是**人工回源登记表**，与自动采集的 `hn_leads.json`
刻意分开：线索重采会整条覆盖，人工批注混进去迟早被冲掉，也没法分辨哪句是
机器抓的、哪句是人填的。

| 项 | 口径 |
|---|---|
| 结构 | `{version, updated_at, items:{hn-<objectID>: {...}}}`；LF + indent=2 + 无尾换行 |
| 每条字段 | `evidence_url`（必填，http(s)）、`source_kind`（`verify_rules.SOURCE_TIER_KEYS`）、`verification`（**推导值，禁止手填**）、`revenue`（可空）、`caliber`、`text_path`、`checked_at`、`checked_by`、`note` |
| 有金额 | `revenue={value,caliber,as_of,currency}`；`caliber` ∈ `CALIBER_KEYS` 且 ≠ `undisclosed`；`as_of` 必填（红线 1：金额必须带口径与时点） |
| 无金额 | `caliber=undisclosed`；`verification` 最高 `founder` —— 官网/官方页只证产品与定价存在，不给「收入已核实」背书 |
| 等级推导 | `verify_rules.best_supported_level([source_kind])`；`homepage` 局部映射到 `founder`；手改值与推导不一致 ⇒ `--check` 判错 |
| 写 inbox | `hn_evidence.py --promote <id> --yes` → `harvest_source=hn_evidence`，内嵌 `hn_evidence` 块；按 id 幂等 |
| 进主池 | `promote_to_candidates.py --ids <id> --write` → `check_one_hn()` 只查证据完整性，**不套 `MIN_MRR`** |
| 等级保护 | `ai_verify` 对 `hn_evidence` 候选只允许维持或降级，不允许无新证据升级 |
| 展示 | `metrics.caliber=undisclosed` / `revenue_status=undisclosed`；`stats.undisclosed` 单独计数，**不计入** `stats.verified` |

⛔ `hn_leads.json` 仍不许直接写主池；转轨必须经过本表登记，不许改
`harvest_source` 伪造 `trustmrr`（#162/#163 的教训不变）。

---

## `anti_cases.json` — 反向样本区（2026-10-26；同日上站）

「可以读、不建议照抄」的案例。**2026-10-26 起出现在 `/api/data` 的 `anti` 键里**
（独立键，刻意**不并进 `cases`**，理由见 `docs/SELECTION_PLAYBOOK.md` §4.1）。
站点表现：`#anti` 视图 + 预渲染的 `anti/index.html`（分组内联，可被收录）。

三张表的边界：

| 文件 | 装什么 | 证据口径 |
|---|---|---|
| `cases.json` | 推荐位，按双轴评分排名 | 一手来源优先 |
| `graveyard.json` | 已停运项目的复盘 | 死亡与亏损要有一手来源 |
| `anti_cases.json` | 活得好但你搬不动的案例 | `aggregator`；原始媒体未逐条打开 |

顶层：`_schema`（= `anti_cases/2`）、`_note`、`_source`、`_built_at`、`_summary`、
`volumes[]`、`cases[]`。由 `scripts/build_anti_cases.py` 的 `JUDGE` 表驱动生成——
**表是主体，JSON 是产物**。

`volumes[]`（每一篇）：`key` / `no`（Ⅰ–Ⅶ）/ `short`（筛选条用的短名）/
`title` / `judge`（这一篇在比什么）/ `lead`（导语）/ `count` / `ids[]` / `role`。
按「门槛类型」分 7 篇：账单 · 人头 · 苦力 · 社区 · 关系 · 资格 · 数字。

每条字段：

- `id` / `name` / `category` / `origin` / `one_liner` / `site`
- `role`：`structural`（护城河在钱/算力/人力/数据/关系）｜`license`（缺资格而不是缺能力）｜
  `caliber`（那个数根本不是收入）
- `roles[]`：命中的全部类别；`role` 记主因
- `volume`：归到哪一篇（= `volumes[].key`）。⚠ `/2` 里**取代**了旧的 `report_group`
  （①②③④ 是原筛选报告的分组，与 `role`、与篇都不一一对应，两份分组并存必然漂移）
- `role_note` / `why_not_copy[]` / `article_angle`
- `page_label{key,value,sub}`：聚合页卡片原样（通常是「月收入」）
- `caliber`：该值的**真实**口径。枚举比 `cases.caliber` 多四个：
  `mrr` / `arr` / `annual` / `funding` / `valuation` / `gmv` / `price` / `lifetime` /
  `unknown` / `invalid`
- `caliber_note`：`caliber != mrr` 时**必填**，说明那个数其实是什么
- `caliber_conflict{old,now,why}`：与 `data/htm_backlog_caliber.json` 不一致时留痕两边，
  **不回改旧表**（旧表是历史判断）
- `verification`：一律 `aggregator`（原始媒体未打开，不许标 official / stripe）
- `source_check{page_read,originals_opened,note}`
- `needs_verify` + `needs_verify_reason`：语义比 graveyard 窄，只指「口径待定」
  （`caliber_undefined` / `caliber_conflict`），不指「整条没核」
- `standalone` + `standalone_article`（可选）：已单独成文的那 3 条（各 `role` 一条试点），
  站点据此挂「已单独成文」徽章；成稿在 `out/articles/<id>.html`
- `series_tag` / `series_footer`：公众号栏目名（「这些别照着做 · 结构/资格/数字篇」），
  **不占** `issue_numbers.json` 的拆解篇号。⚠ 与 `volume` 是两个轴：
  栏目只有三个，篇有七个
- `summary`（可选，≤120 字）：公众号摘要；没有则回退 `one_liner`

`/api/data.anti` = `{cases: [...], volumes: [...], built_at: "YYYY-MM-DD HH:MM"}`；
`stats.anti` / `stats.anti_volumes` 是对应的计数。

不变量（`scripts/test_anti_cases.py` 20 项锁住）：id 不与 `cases.json` 相撞；
`role ∈ roles`；三类都非空；每条都归篇且每篇条数与 `count` 一致；短名 ≤4 字；
非 `mrr` 必有 `caliber_note`；口径分歧必留两边；摘要 ≤120；核实等级不许冒充一手。
构建期另一道锁在 `scripts/build_static.py` 的自检里：id 不混进 `cases`、
页面里缺条目当场报错、篇的条数对不上报错。

---

## `sources` object

Reproduces `data/sources.json`, the harvesting configuration.

| Field | Type | Notes |
|---|---|---|
| `updated_at` | string | `YYYY-MM-DD`. |
| `principle` | string | The editorial rule in one line: primary evidence beats secondary retelling. |
| `sources[]` | object[] | Configured collectors: `id`, `name`, `url`, `kind`, `tier` (`A`/`B`/`B-`), `enabled`, `auth`, `how`, `fields[]`, `why`, `caveat`, `endpoints[]`. |
| `filter_rules` | object | `drop_keywords[]`, `keep_signals[]`, `match_mode` (`word_boundary`), `note`. |

`sources[].kind`: `revenue_db` \| `community` \| `launch_board`.

---

## `stats`

Precomputed aggregates, so a client does not have to recompute them.

| Field | Notes |
|---|---|
| `curated`, `candidates`, `inbox` | Funnel sizes. |
| `tiers` | `{premium, standard, backup: count}` — the three shelves. |
| `premium`, `standard`, `backup` | Flat copies of the same counts, for older consumers. |
| `verified` | Count of `stripe` + `official`. |
| `flagged` | Total correction entries + count of `disputed` cases. |
| `china_scored`, `solo_scored`, `dual_scored` | How many cases carry each score object. |
| `china_blocked` | Cases with a non-null `china_fit.blocker`. |
| `china_top3`, `solo_top3`, `dual_top3` | Trimmed leaderboard rows: `rank`, `medal`, `id`, `name`, `category`, `score`, plus `note` / `blocker` / `dims` (`dual_top3` also carries `solo`, `china`, `quadrant`, `quadrant_label`). |
| `quadrants[]` | One entry per quadrant: `key`, `label`, `desc`, `count`, `cases[]` (trimmed cards, same shape as `dual_top3` minus `solo`/`china`). |
| `categories` | Number of distinct categories. |
| `by_verification` | `{enum: count}`. |
| `by_category` | `{category: count}`. |
| `by_model` | `{model: count}`, sorted descending. |
| `inbox_included` | `false` only for builds produced with `--no-inbox`. |

---

## Worked example

Which cases can a solo developer actually ship for the Chinese market?

```bash
curl -s http://127.0.0.1:5052/api/data \
  | jq -r '.cases[]
           | select(.composite.quadrant == "go")
           | "\(.name)  composite=\(.composite.score)  solo=\(.solo_fit.score)  cn=\(.china_fit.score)"'
```

Find revenue figures that are not backed by a payment gateway, i.e. the ones to
double-check before citing:

```bash
jq -r '[.cases[] | select(.verification != "stripe" and .metrics.arr != null)]
       | .[] | "\(.name)\t\(.metrics.headline)\t\(.verification)"' dist/data.json
```

Every correction entry in the dataset — the part that is genuinely hard to find
anywhere else:

```bash
jq -r '.cases[] | .corrections[] | "\(.claim)\n  → \(.truth)\n  src: \(.source)\n"' dist/data.json
```

---

## Changelog

| `schema_version` | Change |
|---|---|
| 1.0 | First published contract. Adds `schema_version` and `lang` to the payload. |
