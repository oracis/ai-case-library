# `data.json` schema

Field reference for the machine-readable snapshot published by **AI Case Library**
([github.com/oracis/ai-case-library](https://github.com/oracis/ai-case-library)).

This document is the English-language interface contract. It describes **keys, types,
enums and formulas** — not the prose. You can build a client, a chart or a filter on top
of this file without reading a single Chinese sentence.

- **Produced by**: `scripts/build_static.py` (build artifact) and `GET /api/data` (local server)
- **Consumed by**: the static site itself (`data.js`), and any third-party program
- **Source of truth**: the repository `data/*.json` files, hand-curated and committed
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
| `caliber` | enum \| "" | Which revenue definition the headline figure uses: `arr` \| `mrr` \| `run_rate` \| `lifetime` \| `gmv` \| `gross`. |
| `quality_score` | number \| null | Bonus score (0–100) at publish time. 60 is the publish threshold. |
| `tier` | enum | `premium` \| `standard` \| `backup` — where the case sits in the library. See below. |
| `tier_reason` | string | Why it got that tier. Always present when `tier` is. |
| `published_from` | string \| null | The candidate `id` this case was promoted from. |
| `human_read` | bool \| null | Whether someone ticked "I read the source myself". **Absent ≠ false** — see below. |
| `human_read_at` | string | Server-stamped `YYYY-MM-DD HH:MM` of the tick. `""` when unticked. Never taken from a request body. |
| `verification_downgraded` | object \| null | Present only when `audit_evidence.py` lowered `verification`. See below. |
| `china_fit` | object \| null | China-portability score. See below. |

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
