# Overseas Teardowns · 拆解海外

[![CI](https://github.com/oracis/ai-case-library/actions/workflows/ci.yml/badge.svg)](https://github.com/oracis/ai-case-library/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-3fb950.svg)](LICENSE)
[![Python 3 · stdlib only](https://img.shields.io/badge/Python%203-stdlib%20only-3776ab.svg)](#quick-start)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-8b949e.svg)](#contributing)

[中文说明 →](README.md) · **Data schema → [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md)**

A locally-run, **verification-first case library** of small overseas software
businesses that actually make money. Each entry is searched, sanity-checked and written
up in plain language, organised by **business model** rather than by industry, so you can
build judgement instead of collecting screenshots.

Zero dependencies — Python standard library only, nothing to `pip install`.

> **Language note.** This upstream repository is written in Chinese and the case write-ups
> are Chinese prose. That is not an oversight: the editorial layer (what a number *means*
> for someone building in the Chinese market) is the product, and machine translating it
> would reintroduce exactly the second-hand retelling this library exists to reject. What
> *is* English and machine-readable is the interface — see
> [Data access](#data-access-dont-need-the-ui) and [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md).
> Read in Chinese with a translator if you want the analysis; consume the JSON if you want
> the data.

---

## Why this is not just another case list

Most case lists tell you *how much someone made*. This one tells you three extra things.

**1. Who said the number** — every case carries an evidence grade:

| Grade | Meaning |
|---|---|
| `stripe` | Verified through a payment gateway (TrustMRR, Stripe case studies) |
| `official` | Company press release or financial disclosure |
| `partial` | Figures are real but the definition is unclear (MRR vs. all-time?) |
| `founder` | Founder self-reported, no third-party check |
| `disputed` | Sources contradict each other |
| `unverified` | Not checked yet |

Of the 24 curated cases: **16 verified** (10 `stripe` + 6 `official`), 2 marked
`disputed`, 2 marked `partial`.

**2. What everyone else got wrong** — the most valuable part. **14 corrections** are
recorded so far, for example:

- **Nitra customer count**: Chinese coverage said "7,000+ clinics"; the official disclosure
  is **700+**. Off by an order of magnitude.
- **Chatbase revenue**: "$20M/year" is a misreading. $20M is **cumulative**; current ARR is
  around $10M.
- **ShipFast today**: old coverage says "$50K+/month"; the payment-gateway-connected data
  shows roughly **$2,989** over the last 30 days. It is in maintenance mode.
- **Viktor customer count**: the same batch of coverage mixes "12,000 teams" with
  "2,000 organisations" — two different units.
- **Speel.co**: $65K MRR is real, but month-over-month growth is **0%** — revenue being
  real is not the same as the business still growing.

**3. Whether one person can do it** — four difficulty scores per case
(technical / distribution / capital / timing, where **1 = easiest, 5 = hardest**).
A `5` in any dimension means a single person essentially cannot do it.

---

## The three-tier funnel

```
data/inbox.json        harvest queue   ~190 links   machine-mined, never reviewed
data/candidates.json   candidate pool     37        human-picked, numbers unchecked
data/cases.json        curated cases      24        numbers checked, quotable
```

The sidebar renders these three tiers as a funnel. **A large harvest queue with few
curated cases is the healthy state** — two or three write-ups a day is realistic, a dozen
a week. This library is grown, not built in one pass.

Every tier can be promoted from the UI: harvest queue → "move to candidate pool",
candidate pool → "promote to curated case". Promotion into the curated tier attaches a
`needs_review` flag reminding you the numbers are still unchecked.

---

## Two scoring axes (and why they are separate)

These are **different questions**, and the answers frequently disagree:

- `china_fit` — **can this be ported to China?** Six weighted dimensions scoring the
  Chinese market situation specifically (payment collection without Stripe, regulatory
  distance, domestic free substitutes). Max 34.5 raw, normalised to 100.
- `solo_fit` — **can one person build and run this?** Five weighted dimensions, of which
  `delivery` (no team, no licences, no 24/7 on-call) carries the heaviest weight because it
  is the most common death sentence. Max 29 raw, normalised to 100.
- `composite` — the two axes combined, **bucket-style rather than averaged**:

```
composite = 0.6 × min(solo, china) + 0.4 × mean(solo, china)

90 × 40 → 50          but          60 × 60 → 60
```

The weak axis takes 60% of the weight, because the relationship is multiplicative: doable
but unsellable in China = 0; demand exists but one person cannot deliver = 0.
**Two-passing beats one-spike.**

Current results:

| | 🥇 Gold | 🥈 Silver | 🥉 Bronze |
|---|---|---|---|
| China portability | **AEO Engine** 86.4 | Visualizee.ai 79.1 | Speel.co 75.7 |
| Solo founder fit | **Rezi** 80.3 | Visualizee.ai 80.3 | Lancer.app 76.9 |
| Composite | **Visualizee.ai** 79.3 | Bustem 71.1 | AEO Engine 70.6 |

Note how much the ordering moves, and why that is informative:

- `AEO Engine` leads China portability (86.4) but drops to third on composite — it is
  enterprise-sales driven, so a solo builder can't reach the buyers.
- `Bustem` is 4th on China portability and 2nd on composite — no weak axis at all.
- `Rezi` leads solo fit (80.3, pure self-serve, zero delivery burden) yet only sits mid-table
  overall — it cannot collect money in China.

At a pass mark of 70, the 24 cases split into four quadrants: **2** `go`
(both axes clear), **4** `partner` (demand is real, but the barrier is licensing or
enterprise sales), **9** `export` (buildable, don't sell it in China), **9** `skip`.
A further **8 cases carry a hard-stop flag** (`china_fit.blocker`) — medical data
regulation, outbound-calling law, or a value proposition that depends on a payment
infrastructure China does not have.

Scores are subjective judgements, but every one of them ships with its reasoning —
hover the score on a card, or open the detail drawer. To re-weight the model, edit
`WEIGHTS` / `SCORES` in `scripts/score_china_fit.py` and rerun:

```bash
python scripts/score_china_fit.py     # China portability, writes cases[].china_fit
python scripts/score_solo_fit.py      # Solo fit + composite + quadrants
python scripts/score_solo_fit.py --threshold 75
```

---

## Quick start

```bash
git clone https://github.com/oracis/ai-case-library.git
cd ai-case-library

python server.py        # Windows: double-click start.bat
```

The browser opens <http://127.0.0.1:5052/>. Change the port with
`CASE_LIB_PORT=5099 python server.py`.

A static, read-only build also works with no server at all:

```bash
python scripts/build_static.py      # -> dist/
# then open dist/index.html
```

---

## Data access (don't need the UI)

The build produces two files carrying the same document:

| File | Purpose |
|---|---|
| `dist/data.js` | `window.__CASE_LIB_DATA__ = {...}` — what the page loads |
| `dist/data.json` | Identical payload, for third-party programs |

`dist/data.json` is the **stable machine interface**: versioned (`schema_version`),
fully documented in English, and free of prose where it matters. Scores, enums, metrics,
ranks and weights are all language-neutral; URLs and `name_en` are English. Full field
reference, enum tables, scoring formulas and `jq` recipes:

**→ [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md)**

```bash
python scripts/build_static.py --pretty --no-inbox   # human-diffable, no harvest queue
```

---

## Harvesting

Collectors run daily and write **only** to the harvest queue — never to the curated tier.

```bash
python scripts/harvest.py --source hn          # Hacker News, no key needed
python scripts/harvest.py --source trustmrr    # TrustMRR leaderboard + marketplace
python scripts/harvest.py --source all
python scripts/harvest.py --source hn --dry-run
python scripts/harvest.py --max-inbox 400      # excess is archived, never deleted
```

`--source ph` (Product Hunt) needs a token: `--token <PH_TOKEN>` or `$PH_TOKEN`.

Raw items are filtered against `data/sources.json` `filter_rules` to strip advertising and
press releases, keeping only "someone speaking for something they built". Funding rounds
are dropped too — that is a capital story, not a replicable business.

**Only the harvest tier is automated.** Curation stays manual, always: the whole value is
"search for it yourself before you write it down". Hand that step to a script and the
library degrades into exactly the kind of second-hand aggregator it was built to oppose.

### Runs in the cloud, unattended

`.github/workflows/harvest.yml` runs `scripts/daily_harvest.py` on GitHub's machines every
day at 09:00 Asia/Shanghai:

```
harvest → validate → commit → push → (if OSS credentials exist) build → deploy
```

Your machine stays off. Two deliberate design choices: **validate before pushing** (any
failure leaves the remote untouched), and **inline validation** — commits pushed with
`GITHUB_TOKEN` do not trigger `ci.yml`, so the workflow cannot rely on CI as a safety net.

Locally, the same entry point:

```bash
python scripts/daily_harvest.py --dry-run   # harvest only, no commit
```

Safe by construction: no changes → exits without an empty commit; `data/` untouched files
are never staged; `cases.json` is **never** modified automatically; harvest failure
commits nothing; push failure keeps the local commit and tells you what to run.

---

## Verification

```bash
python scripts/verify.py "Nitra" --domain nitra.com     # print checklist + search entry points
python scripts/verify.py --checklist                    # generic checklist only
```

It lays out the six revenue definitions (ARR / MRR / run-rate / cumulative / gross
transaction value / gross margin), ranks evidence classes by strength (payment gateway >
official release > media > founder's own words > second-hand retelling), and flags the six
most common comparison errors.

**This step cannot be delegated to a tool.** A tool will not become suspicious because a
number "looks reasonable". You will.

---

## Tests

```bash
python selftest.py                    # backend: 34 API tests, auto backup + restore of data/
node uitest.js                        # frontend: 55 render checks using a DOM stub
node uitest.js --static               # static build: read-only mode + control degradation
python scripts/test_build_static.py   # static build: anti-deletion guards + build self-check
python scripts/test_daily_harvest.py  # auto-commit script: git mechanics + failure paths
python scripts/check_ci.py            # validates workflow YAML and its embedded shell
```

`selftest.py`, `uitest.js` and `build_static.py` run in CI on every push. One assertion is
specific to this project: **`data/` must come back byte-identical after the test run**,
which proves the backup/restore actually works — if it ever silently breaks, CI finds out
before you do. A hand-edited JSON file with a missing comma is caught there too.

---

## Deploying (Aliyun OSS)

```bash
python scripts/build_static.py              # -> dist/ (6 files, ~580 KB)
python scripts/deploy_oss.py --check --env-file .env
python scripts/deploy_oss.py --bucket <bucket> --dry-run --env-file .env
```

`deploy_oss.py` implements OSS V1 request signing (HMAC-SHA1) itself — no `oss2`, no
`ossutil`. The online build is a **read-only snapshot**: write operations (promoting
candidates, promoting to curated) are replaced with a "read-only snapshot" label rather
than buttons that do nothing. Clone the repo and run `python server.py` to verify or edit.
Reading progress uses `localStorage` and works fine online.

---

## Contributing

Corrections are the most welcome contribution — if you can show that a figure in this
library is wrong, with a primary source, open an issue and it goes into `corrections[]`
with attribution. New case proposals are also welcome; they should state the evidence
class up front, and a payment-gateway-verifiable number will always beat a founder's blog
post.

The curated tier is intentionally slow. Expect review before anything is promoted.

## License

MIT — commercial use, modification and redistribution are all permitted.
See [LICENSE](LICENSE).
