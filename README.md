# BTC AMT & Derivatives Observation Cockpit

A Bitcoin market-structure and derivatives research system. It collects completed-candle AMT data, multi-exchange futures positioning and Delta India options data; derives descriptive research metrics; records structural events; and provides Streamlit observation interfaces.

> **Research and observation only.** This repository does not define an automatically executable trading strategy. AMT states, OI changes, options concentrations, GEX attribution and reaction measurements are descriptive. They do not establish buyer/seller intent, dealer positioning, support/resistance, price targets or trade instructions.

## Current architecture

The live system has four cooperating layers:

1. **Collection and persistence** — AMT, Futures and Options collectors write to Supabase PostgreSQL.
2. **Research calculations** — Options filters, GEX wall attribution, AMT deterioration shadow metrics and post-event reaction analysis operate on stored observations.
3. **Event detection and delivery** — the signal detector writes research events to the system journal; a separate scheduled Telegram process evaluates structural alerts and records delivery state in an outbox.
4. **Observation interfaces** — fast and research Streamlit dashboards read stored data and shared calculation modules.

    GitHub Actions
      |
      +-- BTC Data Collection Coordinator (manual workflow_dispatch)
      |     |
      |     +-- AMT collector
      |     |     +-- amt_snapshots
      |     |     +-- non-blocking shadow_monitor sidecar
      |     |           +-- auction_deterioration_metrics
      |     |
      |     +-- Futures collector -> futures_oi_snapshots
      |     |
      |     +-- Options collector
      |           +-- options_collection_runs
      |           +-- options_oi_snapshots
      |           +-- options_derived_snapshots
      |           +-- GEX attribution -> gex_strike_attribution
      |
      +-- signal_detector.py -> system_signal_journal
      |
      +-- telegram_alerts.py (independent schedule)
      |     +-- evaluates AMT transitions and Futures OI shocks
      |     +-- reads journaled GEX wall breaches
      |     +-- sends Telegram; records telegram_alert_events
      |
      +-- reaction_analyzer.py (daily schedule)
      |     +-- reads system_signal_journal
      |     +-- writes signal_reaction_analysis
      |
      +-- archive/retention workflow (daily schedule)
            +-- archives options_oi_snapshots
            +-- verifies archive coverage before deletion
            +-- VACUUM ANALYZE after retention

    Supabase PostgreSQL
      +-- btc_live_dashboard.py
      +-- btc_live_research_dashboard.py

### Scheduling facts from the repository

- .github/workflows/coordinator.yml has **workflow_dispatch only**. It has no GitHub Actions cron schedule. Any external scheduler that invokes it is outside this repository and cannot be verified from repository contents.
- The Coordinator runs AMT, Futures and Options sequentially, then runs the signal detector only when all three collector jobs succeed. Its concurrency group prevents overlapping coordinator runs.
- .github/workflows/amt_collector.yml runs amt_collector.py, then shadow_monitor.py. The shadow step has continue-on-error: true; shadow-monitor failure must not fail the AMT collection job.
- .github/workflows/telegram_alerts.yml runs at minutes 3 and 33 of every hour, independently of Coordinator completion. It can also be manually dispatched for a test message.
- .github/workflows/reaction-analyzer.yml runs daily at 07:00 UTC (12:30 IST).
- .github/workflows/archive-retention.yml runs daily at 19:10 UTC (00:40 IST). It archives options data older than three days, verifies the archive before deletion, and then runs database size checks and VACUUM (VERBOSE, ANALYZE) on options_oi_snapshots.

Schedules are repository configuration, not proof that a particular run succeeded. Check the corresponding GitHub Actions run and logs.

## Collection and research components

### Auction Market Theory

- amt_core.py fetches Binance BTCUSDT candles and calculates TPO/value measurements.
- research_engine.py implements the state engine used by amt_collector.py.
- amt_collector.py processes completed 30-minute candles, freezes the prior profile, calculates developing value and writes amt_snapshots.
- The collector's current configuration includes ATR period 21, ATR multiplier 0.50, minimum discovery buffer 50, two discovery closes, one re-entry close and balance-crossing settings. The persisted engine_version is v1.0.
- shadow_monitor.py is a separate research-only sidecar. It reads AMT snapshots and upserts one metrics row per profile/candle into auction_deterioration_metrics. It tracks symmetric upside/downside close and wick excursions, ATR/percentage normalization, POC distance and frozen Developing-VAL test episodes. It does not create Telegram alerts or modify AMT state.
- The Streamlit fast dashboard also imports pine_state_machine.py; this is a separate dashboard/state-machine integration and should not be assumed identical to the research_engine.py collector engine.

### Futures positioning

- futures_positioning.py collects positioning data from Binance, Bybit, OKX and Delta.
- collector.py stores per-exchange and aggregate observations in futures_oi_snapshots.
- OI is reported as notional context. Changes do not identify which side initiated positions or establish directional intent. Contributor coverage and timestamps matter when comparing aggregate observations.

### Options collection and calculations

- options_collector.py collects BTC option contracts from Delta India, records a run in options_collection_runs, stores raw contracts in options_oi_snapshots and writes expiry-specific derived records to options_derived_snapshots.
- Sampled taker buy/sell fields are collected only for a nearest-strike subset; they are not complete spot/perpetual order flow.
- options_filters.py provides the shared canonical snapshot selector and five read-only filter calculations: Nearby OI, IV Skew, 24h Volume/OI, OI Migration and Sampled Taker Activity. Missing observations remain missing; filters do not silently replace unavailable values with zero.
- research_options_adapter.py connects the research dashboard to the shared options calculation backend.
- gex_attribution.py compares previous/current Top-3 strike exposures and writes event-only Shapley attribution rows to gex_strike_attribution. It attributes calculated GEX change among Gamma, OI and Spot factors; the dominant driver is a mathematical contribution, not a participant identity or dealer-position inference.
- iv_surface_research.py contains IV-surface research calculations.

### Event journal, Telegram and reaction analysis

- signal_detector.py evaluates completed AMT candles, aggregate Futures OI and successful Options-derived data. It writes qualifying events to system_signal_journal: AMT_STATE_CHANGE, GEX_WALL_BREACH, OI_SHOCK and VALUE_MIGRATION.
- telegram_alerts.py runs independently. It evaluates AMT transitions and OI shocks, consumes journaled GEX wall-breach events, and uses telegram_alert_events as a delivery outbox with deduplication and retry/audit fields. Telegram delivery is not the same thing as the research event journal.
- The notifier's accepted outbox event types are AMT_STATE_CHANGE, AMT_PROFILE_INITIALIZATION, GEX_WALL_BREACH, OI_SHOCK and TEST_ALERT. TEST_ALERT is for explicit connectivity testing, not a market signal.
- reaction_analyzer.py creates versioned, non-directional reaction measurements for journal events. The current stored method is reaction-v1.1, using Delta BTCUSD perpetual 30-minute candles and an eight-candle/four-hour observation window. These measurements are not trade returns or validated signal performance.

### Dashboards

- btc_live_dashboard.py is the fast Streamlit observation dashboard. It reads AMT and Futures data, integrates the Pine state-machine module, and displays the shared Options Filters outputs.
- btc_live_research_dashboard.py is the detailed research interface. It uses research_options_adapter.py and shared filter calculations, and reads stored AMT/Futures/Options records.
- The repository contains dashboard source code; the presence of a script does not by itself establish its current hosting/deployment status.

## Archive and retention

- archive_options_snapshots.py exports historical raw options rows to the separate btc-data-archive repository and maintains an archive manifest.
- retention_cron.py targets only public.options_oi_snapshots. Its default retention is three days. Deletion requires --execute; the workflow runs it only after archive verification and exact archive-to-database candidate reconciliation.
- The retention workflow does not target gex_strike_attribution, AMT, Futures, derived Options, signal journal, reaction analysis, Telegram outbox or shadow metrics.
- Routine VACUUM (VERBOSE, ANALYZE) follows retention; it does not guarantee that the physical database file or billed storage immediately shrinks.

## Database and migration documentation

See [DATABASE.md](DATABASE.md) for the live public schema, all ten observed tables and columns, keys/indexes, RLS policy observations, migration history and known migration-repository gaps.

**Important migration caveat:** the live database migration history is not fully reproducible from the current migrations/ directory. The live history includes a migration version that has no matching SQL file in the repository, and several foundational tables have no create-table migration in the current folder. Do not treat this repository as a complete blank-database bootstrap without first reconciling that history.

## Development and tests

- Python dependencies: requirements.txt.
- Test files cover AMT initialization, Pine state-machine integration, Options filters, GEX attribution, Futures integrity, Telegram outbox behavior, reaction analysis and shadow-monitor VAL episodes.
- GitHub Actions contains dedicated test workflows for these areas. A workflow's existence is not evidence that its latest run passed; inspect the actual run status.

## Operational configuration

Backend workflows use GitHub Actions secrets such as SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY. Options collection also uses DELTA_BASE_URL (defaulting in code to the Delta India API). Telegram delivery requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. The archive/retention workflow also uses DATABASE_URL and PAT_TOKEN to connect to PostgreSQL and the private archive repository.

Never place service-role keys, Telegram credentials, database passwords or personal access tokens in source files or documentation.

---

# Research layers

## Layer 1 — Auction Market Theory

**Main files:** `amt_collector.py`, `amt_core.py`, `research_engine.py`

The AMT collector stores completed 30-minute BTCUSDT candles, Prior Value references, Developing Value measurements and state outputs in `amt_snapshots`.

### Prior Value versus Developing Value

- **Prior VAH / POC / VAL:** frozen references from the source session used to evaluate the current auction.
- **Developing VAH / POC / VAL:** evolving measurements describing where the current auction is building value.

These are different reference sets.

### State discipline

The database preserves:

- `confirmed_state`
- `developing_state`

Discovery confirmation requires the configured completed-candle confirmation conditions. A candidate/developing state must not be treated as a confirmed state.

In-progress candles must not be used as completed confirmations.

## Layer 2 — Futures positioning

**Main file:** `collector.py`

The collector retrieves Futures OI, funding and basis from configured sources including:

- Binance
- Bybit
- OKX
- Delta

An `AGGREGATE` row represents the valid contributors available for that observation. Coverage must be considered when comparing aggregate observations.

The 30-minute OI comparison uses a strict 25–35-minute baseline validation.

## Layer 3 — Options research

**Main file:** `options_collector.py`

The options pipeline stores:

- raw options contracts in `options_oi_snapshots`
- run status in `options_collection_runs`
- expiry-specific derived metrics in `options_derived_snapshots`

Derived metrics include Max Pain, ATM straddle, gross Call/Put GEX concentrations and local OI PCR.

### Options interpretation guardrails

- Gross GEX is unsigned and does not reveal dealer positioning or hedging direction.
- GEX/OI concentrations are descriptive measurements, not automatic support, resistance or targets.
- Max Pain is a theoretical OI-based calculation, not a forecast.
- ATM straddle is an expiry-specific priced-movement measure, not a guaranteed range.
- Missing or incomplete chain data remains incomplete; missing data is not zero.

---

## 30-day Data Collection — Research Targets

Active feature development is paused during this collection window. The objective is to build a clean, timestamp-aligned sample before considering any directional automation.

### Research vector: price compression while Futures OI expands (absorption candidate)

Research question: **Are there completed 30-minute windows in which BTC trades within a relatively tight price range while comparable aggregate Futures OI increases?**

This is an *absorption candidate screen*, not proof of passive absorption, buyer/seller intent, trapped positions or a future reversal. Increasing OI does not reveal which side initiated the positions.

At the end of the collection period, query the futures_oi_snapshots and amt_snapshots tables as follows:

1. **Build valid OI observations.** Use only non-null AGGREGATE OI observations. Prefer a trustworthy source/collection timestamp from the status payload; use created_at only where its meaning is understood. Extract the contributor set from the status metadata and retain it with each observation.
2. **Require comparable OI endpoints.** Compare start/end observations over fixed windows (initially 2h and 4h; 8h may be a sensitivity check). Require the same non-empty contributor set at both endpoints and a continuous chain of valid intermediate observations using the existing 25–35-minute interval rule. Do not treat a coverage change as OI expansion.
3. **Align to completed AMT candles as-of each OI observation.** Use the latest valid completed 30-minute AMT candle whose candle_close_time is at or before the OI observation time. Never attach a later candle. The earlier Futures audit found a systematic timestamp offset of roughly nine minutes in many observations; preserve and report the offset distribution rather than assuming nearest timestamps are interchangeable.
4. **Measure price compression over the same window.** From the corresponding completed AMT candles, calculate window_high = MAX(high_price), window_low = MIN(low_price) and price_range_pct = (window_high - window_low) / midpoint_price × 100, where midpoint_price is the midpoint of the window high and low. Also retain net close-to-close change and the number of valid candles.
5. **Measure OI expansion.** Calculate oi_change_pct = (end_oi_usd - start_oi_usd) / start_oi_usd × 100. Because the stored field is USD-notional OI, run a sensitivity comparison using oi_usd / aligned_btc_price as a BTC-equivalent notional proxy where appropriate; do not mistake a price-driven USD valuation change for a pure change in contract count.
6. **Screen empirically, not with a hand-picked magic cutoff.** Report the joint distribution of price_range_pct and oi_change_pct. Identify candidate compression windows using the lower quartile of price range and candidate OI expansion using the upper quartile of OI change, then repeat with alternative quantiles and transparent fixed-threshold sensitivity bands. Publish the actual thresholds and sample counts.
7. **Review each candidate in context.** Retain AMT confirmed/developing state, Prior Value location, contributor coverage, data gaps and the underlying candle sequence. Manually inspect candidate episodes before assigning an absorption interpretation.

A useful end-of-period query is therefore a **windowed join**, not a single-row correlation: create rolling 2h/4h OI endpoint pairs with stable contributor coverage; create matching 30-minute AMT high/low windows; join them using the strict as-of rule; calculate price-range and OI-change percentages; then rank or filter the joint distribution. Deduplicate overlapping windows into episodes before statistical testing, and report both the raw window count and independent episode count.

**Do not** classify every tight-range/OI-rise window as absorption. The screen only finds observations worth examining. Any claim of absorption or directional edge requires further validation, including non-overlapping episodes, regime conditioning, out-of-sample replication and alternative OI normalization.

---

### Research vector: Prior Value overlap regime (Balance versus Value Migration)

Research question: **Does the relationship between the current Developing Value Area and the frozen Prior Value Area change how Futures OI should be interpreted at a VAL/VAH breach?**

This is a regime-conditioning hypothesis. High overlap is a candidate balance environment and low overlap is a candidate value-migration/trending environment; neither classification by itself proves balance, trend or predictive power.

#### Value Area Overlap % definition

For each valid completed AMT observation, define:

- Prior Value interval: \\(P=[prior\\_val,prior\\_vah]\\)
- Developing Value interval: \\(D=[developing\\_val,developing\\_vah]\\)

The shared width is:

\\[
I=\\max(0,\\min(prior\\_vah,developing\\_vah)-\\max(prior\\_val,developing\\_val))
\\]

Use **Prior-Value Coverage %** as the primary, interpretable overlap measure:

\\[
OVL_P=100\\times\\frac{I}{prior\\_vah-prior\\_val}
\\]

This answers how much of the prior session's value range is covered by current Developing Value. Also retain two diagnostics so width changes do not distort interpretation:

- Developing-Value Coverage %: \\(OVL_D=100\\times I/(developing\\_vah-developing\\_val)\\)
- Symmetric Intersection-over-Union: \\(IoU=100\\times I/(W_P+W_D-I)\\), where \\(W_P=prior\\_vah-prior\\_val\\) and \\(W_D=developing\\_vah-developing\\_val\\).

Require positive, non-null widths and \\(data\\_status=VALID\\). Calculate overlap from the same completed AMT row used for the as-of OI observation; never use a later developing value or an in-progress candle.

For initial research segmentation only, use provisional buckets:

- **High overlap / candidate Balance:** \\(OVL_P\\geq70\\%\\)
- **Intermediate / transitional:** \\(30\\%\\leq OVL_P<70\\%\\)
- **Low overlap / candidate Value Migration:** \\(OVL_P<30\\%\\)

Retain the continuous values and repeat the analysis with alternative cutoffs (for example 60/40 and 80/20). These are operational research bins, not universal AMT constants. Report how often the three overlap measures disagree.

#### Split the OI breach study by environment

**A. Balanced environment — high overlap**

Research question: when price probes below Prior VAL or above Prior VAH while current Developing Value remains highly overlapped with Prior Value, does prior-session or multi-day positioning explain the subsequent response better than the latest 30-minute OI change?

Compare, as separate predictors:

1. 30-minute OI percentage change using the existing valid 25–35-minute interval and stable contributor set.
2. Prior-day OI change, measured between comparable observations approximately one session apart.
3. Multi-day net OI change (initially 3-day and 7-day endpoint changes), requiring comparable contributor coverage across the full measurement span.

For each measure, compare absorption-candidate outcomes at the breached edge: price-range compression, failure to extend beyond the breach extreme, and recovery/acceptance back inside Prior Value over predeclared 1h, 4h and 24h horizons. Use matched observations and non-overlapping episodes. Do not assume prior-day or multi-day OI is superior; test it against the 30-minute measure.

**B. Trending / low-overlap environment**

Research question: when Developing Value has migrated away from Prior Value, does the 30-minute OI change measured at the VAL/VAH edge add information about continuation versus failed auction?

At each breach, retain the OI change over the valid 25–35-minute comparison interval, breach side (below VAL / above VAH), penetration depth and overlap metrics. Define outcomes before examining results:

- **Continuation candidate:** subsequent completed candles remain outside the relevant Prior Value boundary and extend the excursion beyond the breach extreme within the selected horizon.
- **Failed-auction candidate:** price re-enters Prior Value and satisfies a predeclared completed-candle acceptance/reclaim condition within the selected horizon.
- **Unresolved:** neither outcome occurs or the required candles/data are missing.

Test 30-minute OI change as a continuous variable first; any expansion/contraction buckets are secondary sensitivity analysis. Do not infer buyer/seller identity from OI.

#### Breach depth and data controls

For a downside breach, define penetration points as \\(prior\\_val-episode\\_low\\); for an upside breach, use \\(episode\\_high-prior\\_vah\\). Normalize depth by Prior Value width and, separately, by the contemporaneous ATR:

\\[
Depth_{VA}=100\\times\\frac{penetration}{prior\\_vah-prior\\_val}
\\]

\\[
Depth_{ATR}=\\frac{penetration}{ATR_{21}}
\\]

Keep penetration continuous initially. If shallow/deep categories are later needed, select cutoffs from the observed distribution or pre-register sensitivity bands rather than choosing them after seeing outcomes.

Across both environments:

- Use the latest valid completed AMT candle at or before each OI observation (strict as-of alignment).
- Require stable non-empty aggregate contributor coverage and valid OI continuity; do not bridge missing intervals or coverage changes.
- Preserve source timestamp offsets, nulls, and missing outcome windows.
- Distinguish USD-notional OI changes from contract-count changes; include the existing BTC-equivalent normalization sensitivity.
- Deduplicate overlapping breach windows into independent episodes and report both raw observations and episode counts.
- Compare the same outcome horizons and controls across regimes; report effect sizes, confidence intervals, sample counts and multiple-testing adjustments.

**Interpretation guardrail:** the balanced/trending split is a research stratification, not a pre-validated AMT law. The claims that intraday OI is less informative in balance and that 30-minute OI is more informative during value migration are hypotheses to test, not assumptions to encode into alerts or trading rules.

---

---

# Data integrity and research guardrails

- **Missing data:** preserve as SQL `NULL`; never silently convert missing measurements to zero.
- **Completed candles:** use completed 30-minute candles for AMT confirmation.
- **No lookahead:** use only information available at or before the event timestamp.
- **No retrospective reclassification:** later information is an outcome, not contemporaneous context.
- **Timestamp provenance:** distinguish market observation time from collector/database insertion time.
- **Futures coverage:** compare aggregate OI only with appropriate contributor coverage.
- **Options provenance:** preserve expiry, snapshot ID, source collection time and metric status.
- **No unsupported causal claims:** simultaneous movement does not by itself establish causation.

## Strict as-of rule

For historical event analysis, Futures and Options observations may be used as contemporaneous context for an AMT event only when their reliable source/observation timestamps are at or before the event time.

Later observations are subsequent developments and must not be backfilled into the earlier explanation.

---

# Documentation contract

## The three-tier documentation hierarchy

### `README.md`

The current system architecture, operating model, workflows and research philosophy.

### `DATABASE.md`

The database contract: table inventory, schemas, relationships, RLS and timestamp/data-quality conventions.

### `CHANGELOG.md`

The historical record of significant architecture, infrastructure and behavior changes.

## Completion rule

> **No architectural change is complete until its documentation impact has been reviewed.**

For every future production change, the implementation workflow is:

```text
1. Discuss and approve logic
        ↓
2. Implement code / SQL / workflow
        ↓
3. Test and verify
        ↓
4. Review documentation impact
        ↓
5. Update README.md / DATABASE.md / CHANGELOG.md as applicable
        ↓
6. Commit documentation
        ↓
7. Mark change complete
```

Examples:

| Change | Documentation impact |
|---|---|
| Architecture/workflow change | README + CHANGELOG |
| Database schema/RLS change | DATABASE + CHANGELOG |
| New table | DATABASE + README where architecturally relevant + CHANGELOG |
| Alert/event logic change | README + CHANGELOG |
| Significant bug fix | CHANGELOG |
| Major System B UI redesign | README + CHANGELOG |
| Minor display formatting | Usually none |

A change may legitimately require no documentation update, but that decision should be made explicitly rather than forgotten.

---

# Setup and secrets

Configure credentials in the appropriate secret store:

- **Streamlit Cloud:** `SUPABASE_URL`, `SUPABASE_ANON_KEY`
- **GitHub Actions:** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- **Telegram workflow:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

Never commit secrets or expose service-role credentials to client-side code.


## Market Reaction Analysis

**Files:** `reaction_analyzer.py`, `.github/workflows/reaction-analyzer.yml`

Market Reaction Analysis is a downstream research process. It does not modify the Signal Detector, the original `system_signal_journal`, AMT logic, Telegram alerts or order execution.

- Runs daily at **07:00 UTC / 12:30 PM IST**, and can also be started manually through GitHub Actions.
- Reads journal events whose event timestamp is at least four hours old.
- Uses Delta Exchange India `BTCUSD` perpetual 30-minute OHLC candles strictly after the event timestamp (`/v2/history/candles`, `resolution=30m`).
- Excludes the event candle and requires eight contiguous, completed 30-minute candles fully contained in the event-relative four-hour window.
- Stores results in `signal_reaction_analysis`, linked to the original journal row.
- Records close prices at 30m, 1h, 2h and 4h, window high/low and neutral upside/downside excursions in USD and percent.
- Does not infer long/short direction from event type and does not generate orders.

### Data-integrity rule

An event timestamp must align with the 30-minute candle boundary closely enough for eight complete post-event candles to fit inside the next four hours. If Delta Exchange does not return exactly eight contiguous complete candles inside that strict window, the row is marked `INCOMPLETE`; it is not counted as a completed research observation. An INCOMPLETE row is terminal for this immutable historical window and analysis version; it is excluded from complete samples. Transient Delta API/database failures are logged, are not persisted as terminal rows, and remain retryable.

Excursions are measured relative to the event's journal price. The upside excursion is non-negative; the downside excursion is zero or negative. They are neutral price movements, not a simulated trade return or evidence of a tradable edge. Note that the journal anchor is sourced from the BTCUSDT AMT feed while reaction OHLC is Delta BTCUSD perpetual: contract basis/venue differences are a known measurement limitation and must be disclosed in any statistical review.

The current analysis version is `reaction-v1.1` (Delta Exchange India BTCUSD perpetual candles). The earlier `reaction-v1.0` Binance Spot methodology is retained as a distinct version. A future methodology change must use a new version rather than silently overwrite the meaning of historical observations.

Required GitHub Actions secrets: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. The service-role key is never exposed to System B.


### Statistical sample independence

One completed 30-minute candle can generate more than one journal event type (for example, an AMT state transition and a Developing POC migration). Their reaction windows can therefore be identical or highly overlapping. For edge evaluation, report both event-type observations and unique underlying event-time clusters; do not count same-candle events as independent samples.


## Options filters backend (isolated research module)

**Files:** `options_filters.py`, `test_options_filters.py`

The options research module exposes a canonical, read-only snapshot selector and five independently evaluated filters, shared by System B and the Research Dashboard. Both dashboards render the same pinned-snapshot backend results; neither duplicates filter calculations.

- The selector pins one latest `SUCCESS` collection run, one future expiry with derived chain status `OK`, and raw contracts carrying the same `snapshot_id` and expiry. If the selected run/expiry/chain fails validation, it fails closed rather than falling back to older data.
- Nearest-five selection uses the selected raw options snapshot's own `spot_price`, not a Binance candle close.
- Aggregation preserves all-null groups as missing values. Filter-specific completeness checks distinguish missing observations from observed zero.
- Nearby OI, 24h Volume / OI and OI Migration retain their existing calculations and classifications. This correction changes only IV Skew and Sampled Taker Activity presentation/semantics:
  - **IV Skew:** the five-nearest-strike average Call IV premium relative to average Put IV is exposed continuously as `metric = CALL_IV_PREMIUM` and `call_iv_premium_pct`. The former ±5% threshold and `BULLISH_URGENCY` / `BEARISH_URGENCY` / `NEUTRAL` classes are removed. This is a descriptive volatility comparison, not a directional prediction.
  - **Sampled Taker Activity:** exposes `sampled_buy_volume`, `sampled_sell_volume`, `sampled_volume_balance` (buy minus sell), and `sampled_volume_balance_pct` when total sampled volume is nonzero. The former `AGGRESSIVE_BUYING`, `AGGRESSIVE_SELLING`, and `NEUTRAL_FLOW` classes are removed. This is sampled options-contract activity, not full-market order flow or a directional alert.
  - Both dashboards show these two filters with neutral styling and descriptive labels; layout and CSS are unchanged.
- OI Migration uses the immediately preceding eligible successful snapshot for the same expiry only when its contract identity set matches; elapsed time is included.
- Each calculator has an independent exception boundary. The module performs no writes, adds no tables, and creates no collection or scheduling workflow.
- The taker measurement remains a sampled subset of available trade activity, not complete order flow.

CI runs the offline backend regression suite through `.github/workflows/options-filters-tests.yml`.


### OI Migration — comparable snapshot contract

OI Migration compares the latest valid options snapshot with the most recent successful, complete snapshot for the same underlying and expiry that completed **25–45 minutes earlier** (inclusive). A nearer run is not used as the baseline. If no eligible baseline exists, the filter returns `UNAVAILABLE` with one of `NO_ELIGIBLE_PREVIOUS_SNAPSHOT`, `SNAPSHOTS_TOO_CLOSE`, `SNAPSHOT_GAP_TOO_LARGE` or `NO_VALID_PREVIOUS_SNAPSHOT_IN_WINDOW`.

Migration is calculated only from the strike-level intersection of contract identities present in both snapshots (product ID, falling back to symbol, together with option type and strike). A strike is excluded when either Call/Put side is not represented by comparable contracts or any matched contract at that strike has missing or invalid OI in either snapshot. Missing OI is never converted to zero. At least **five valid paired strikes** must remain; otherwise the result is `INSUFFICIENT_DATA` with reason `FEWER_THAN_5_VALID_PAIRED_STRIKES`. The reported migration remains descriptive and does not identify buyer/seller intent, dealer positioning or a directional trading signal.


### Additive IV-surface research methodology — `iv-surface-research-v1.0`

The isolated research layer in `options_filters.py` adds two measurements without changing the legacy nearest-five `calculate_iv_skew()` function or its return contract:

- **25-delta risk reversal:** `IV_25Δ Call − IV_25Δ Put`, expressed in volatility points. Call Delta must be positive; Delta Exchange Put Delta must be negative. Put matching and interpolation use absolute Delta while retaining the signed value in diagnostics. The target is interpolated only when it is bracketed by valid observations; no extrapolation is performed.
- **ATM IV:** average of Call and Put mark IV at the nearest strike to snapshot spot for which both sides have usable, non-crossed two-sided price quotes and valid mark IV.
- **IV sentinel:** the exact decimal value `0.01` (1%) is rejected as an IV measurement. Bid/ask IV sentinel flags are retained as quote-quality diagnostics; they do not by themselves invalidate an otherwise usable mark-IV observation.
- **Quote diagnostics:** positive bid/ask prices, crossed-market checks, bid/ask/mark IV validity, relative spread, source timestamp and timestamp age are exposed. Spread and quote age are reported without imposing an unapproved wide-spread or stale-age cutoff.
- **Provenance:** outputs include snapshot ID, expiry, snapshot completion time, spot, methodology version, interpolation method/bracket, quality counts and contract-level rejection reasons.
- **Read-only boundary:** the research loader SELECTs the additional quote and Greek fields from `options_oi_snapshots`. It performs no database writes, schema changes, collector changes or dashboard integration. Existing nearest-five calculations and public function signatures/return values remain unchanged.

**Layer 3 persistence decision is intentionally deferred.** The calculator currently returns a structured in-memory result. No research table, local output file, or persistent export is created by this PR. After review, choose a local versioned research artifact or a separately approved research output table. The raw `options_oi_snapshots` table remains read-only throughout this work.
