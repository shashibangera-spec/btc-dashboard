# BTC AMT & Derivatives Observation Cockpit

A research and observation system for Bitcoin markets. The project combines Auction Market Theory (AMT), futures positioning and options-market measurements to reconstruct the auction and study how market structure interacts with derivatives context.

> **This is an observation and research system, not a mechanical trading-signal generator.** AMT states, OI changes, options concentrations and derived metrics are descriptive research inputs unless separately validated by evidence.

## System philosophy

The hierarchy is deliberate:

1. **AMT structure and price action are primary.**
2. **Futures positioning is supporting context.**
3. **Options measurements are supporting context.**
4. **Automated alerts and journals record significant events; they are not trade instructions.**

The project preserves completed-candle confirmation discipline, avoids lookahead, and keeps developing information separate from confirmed information.

## Theoretical Framework

The system adopts an evidence-first Auction Market Theory framework:

1. **AMT establishes market structure.** Completed price action, auction location, acceptance/rejection and the development or migration of value describe the auction. Confirmed AMT state is the structural reference; developing state remains provisional until its confirmation conditions are met.
2. **Futures OI is a descriptive positioning metric.** Expansion or contraction describes a change in reported aggregate open-interest notional, subject to source coverage and timestamp quality. It does not identify whether new positions were initiated by buyers or sellers, nor whether positions are opening, closing or offsetting.
3. **Options OI is a descriptive positioning metric.** Strike concentrations, OI migration, IV premium, GEX and related expiry measurements describe the observed options book. They do not independently establish support/resistance, dealer intent, hedging direction or a price target.
4. **Neither Futures OI nor Options OI is a standalone directional signal.** Derivatives measurements may be studied alongside AMT and price action, but they cannot override the auction structure or be promoted to a trading rule without separate, robust empirical validation.

The empirical corrections to IV Skew and Sampled Taker Activity follow this framework: Call IV Premium is continuous and descriptive; sampled options taker volumes are not full-market spot/perpetual order flow. Likewise, OI_SHOCK is a context alert, not a prediction.

---

# Production architecture

## System A — Research, collection and event detection

System A runs continuously in the cloud and is the system of record.

```text
cron-job.org
      |
      v
BTC Data Collection Coordinator
      |
      +--> AMT Collector
      |
      +--> Futures OI Collector
      |
      +--> Options Collector
      |
      v
Signal Detector
      |
      v
Supabase PostgreSQL
      |
      +--> Research history
      +--> system_signal_journal
      |
      v
Telegram Alert Workflow
```

The external scheduler triggers **one Coordinator workflow**. The Coordinator runs the collectors sequentially:

1. AMT collector
2. Futures OI collector
3. Options collector
4. Signal detector

If a required collector job fails, downstream jobs do not run. This prevents the signal detector from evaluating a partially completed collection cycle.

The Coordinator workflow is manually dispatchable through GitHub Actions and is the single production entry point for scheduled collection.

## System B — Fast observation cockpit

System B is the Streamlit dashboard used for rapid market observation.

Its purpose is to answer:

> **What is happening now, and what has recently changed?**

Its information hierarchy remains:

**AMT structure → Futures context → Options context**

System B now uses a visual-first, single-page observation cockpit: a compact market header, explicit last-closed auction and live-price-location labels, a prior-value ladder, AMT structure, and side-by-side Options/Futures context. The redesign is a presentation layer only; it does not change the canonical Pine state-machine integration, AMT calculations, derivatives source contracts, or alert/trading rules. The approved CSS baseline is pinned by `test_fast_dashboard_options_ui.py`.

### Options Filters — Stage 3 Fast Dashboard integration

The five Options Filters research outputs are integrated into System B through a collapsible Streamlit expander positioned after the Futures context and before Diagnostics and Raw Data. The panel presents Nearby OI, IV Skew, 24h Volume / OI, OI Migration and Sampled Taker Trade Activity as separate research measurements.

- The UI consumes the validated results from the shared `options_filters.py` backend; filter formulas and classifications are not reimplemented in the dashboard.
- The Supabase fetch/calculation wrapper uses Streamlit data caching with a **180-second TTL**.
- If the selected snapshot or an individual filter is unavailable, the panel handles that result without substituting an older snapshot or disrupting the rest of the dashboard.
- The panel preserves the information hierarchy: **AMT structure → Futures context → Options context**. The five filters are an additional Options-context layer; they do not alter AMT or Futures calculations, state logic or alert/trading behavior.
- Missing option observations remain unavailable (`N/A`); observed zero values remain numeric zero. Incomplete option-chain inputs fail closed for wall, ratio and Max Pain calculations.

### Research Dashboard — Stage A canonical data integration

`btc_live_research_dashboard.py` remains the detailed forensic/research interface. Its options data path now uses `research_options_adapter.py` and the shared `options_filters.py` canonical selector. The dashboard pins one eligible SUCCESS run, snapshot ID, future expiry and raw snapshot spot; derived metrics must match that same snapshot and expiry. Legacy filter formulas are intentionally retained until Migration Stage B, but nearest-strike selection now uses the pinned options spot. The existing card/layout presentation is unchanged.

The Research Dashboard's Prior VAH/POC/VAL display reads the latest `amt_snapshots` row only when `data_status=VALID`; it no longer independently recomputes prior TPO values from Binance candles. Futures funding and basis preserve valid zero values.


### Research Dashboard — Stage B canonical filter calculations

The five Options Research cards now consume the validated filter results produced by `options_filters.py`: Nearby OI, IV Skew, 24h Volume / OI, OI Migration and Sampled Taker Activity. Their legacy dashboard-local calculators have been removed. The cards retain their existing layout, while UNAVAILABLE, INSUFFICIENT_DATA, PARTIAL and ERROR states are displayed with the backend reason where available. The AI copy report and Nearby OI evidence markers use the same canonical filter outputs. No Stage B formula or threshold changes are introduced; this stage changes the calculation source and UI data mapping only.


### Research Dashboard — Stage C OI map and raw options views

The OI Concentration Map and Raw Options Chain now share the Stage A canonical snapshot's pinned expiry and spot. Both views select the ten nearest strikes through the shared adapter selector. The OI map retains null OI values, distinguishes missing values from observed zero, and does not fill missing OI with zero. Raw table aggregation also preserves all-null OI, USD OI, volume and taker groups as missing. Derived annotations are sourced from the matching canonical derived record. Existing chart/table presentation and dashboard layout remain unchanged.


### Research Dashboard — Stage D AI report provenance and final verification

The copy/paste AI report identifies the exact pinned options snapshot ID, expiry, snapshot-local spot, run completion time, raw-contract collection time range, derived source-collection timestamp and derived calculation timestamp. It separately identifies the live Binance market price and its completed candle close timestamp; these values are not interchangeable. All source timestamps are explicitly UTC. An offline parity regression executes the Fast Dashboard loader and Research Dashboard adapter against the same fixture snapshot and requires exact equality for all five filter result dictionaries and their pinned snapshot identity. Stage D does not modify AMT or Futures calculations, formulas, CSS or dashboard layout.

### Division of labor for future System B changes

- **ChatGPT:** backend logic, APIs, GEX/AMT calculations, database infrastructure, Telegram alerts and minor Python/dataframe changes.
- **Claude:** major UI/CSS/HTML redesigns.
- **User:** reviews and merges changes.

Any proposed change that touches the custom CSS or HTML layout should be treated as a UI-skin change and reviewed carefully to avoid breaking the frozen terminal design.

---

# Automated backend components

## Coordinator workflow

**File:** `.github/workflows/coordinator.yml`

The Coordinator is named **BTC Data Collection Coordinator** and uses reusable collector workflows in sequence. It then runs `signal_detector.py` only after all collector jobs complete successfully.

Concurrency prevents overlapping collection cycles from running simultaneously.

## Signal Detector

**File:** `signal_detector.py`

The detector evaluates completed, validated backend data and writes qualifying research events to `system_signal_journal`.

Current event taxonomy:

### 1. `AMT_STATE_CHANGE`

Logged when the **confirmed AMT state** changes between consecutive completed 30-minute candles.

Developing/candidate states do not independently trigger this event.

### 2. `GEX_WALL_BREACH`

Logged when price closes beyond the same nearest Call or Put GEX wall for **two consecutive completed 30-minute candles**.

A new breach cannot be logged until price has first returned inside the walls and re-armed the condition. Wall identity includes side, expiry and level.

### 3. `OI_SHOCK`

Logged when aggregate Futures OI changes by **more than 3.0%** over a valid 25–35-minute comparison interval with **exactly matching exchange coverage**.

The event payload also records the latest valid completed AMT candle available as-of the OI observation: `price_location` (`ABOVE_VAH`, `BELOW_VAL`, or `INSIDE_VALUE`) and its `confirmed_state`. If no eligible AMT candle or valid Prior Value levels are available, location is `LOCATION_UNAVAILABLE`; the OI event is still retained. Missing confirmed state is `AMT_STATE_UNAVAILABLE`.

This is a **context alert only**. OI expansion/contraction and AMT location do not establish which side initiated exposure, predict a bounce/drop, or constitute a validated directional trading signal. No directional OI rule has been validated.

### 4. `VALUE_MIGRATION`

Logged when the Developing POC moves by **more than $150** between consecutive completed 30-minute AMT candles.

All current detector events default to severity `INFO`. Severity classification is a future research question rather than an assumed interpretation.

### Event deduplication

The journal uses the event identity:

`event_type + event_timestamp + event_key`

The database unique constraint and detector upsert logic prevent the exact same event from being recorded twice.

---

# Telegram alerts

**File:** `.github/workflows/telegram_alerts.yml`

The Telegram workflow listens for completion of **BTC Data Collection Coordinator**.

It evaluates alerts only after a successful Coordinator run, meaning the collection sequence and signal detector have already completed.

The notifier validates every outbox `event_type` against a Python allowlist before writing. That allowlist must remain synchronized with the database CHECK constraint documented in `DATABASE.md` and the corresponding SQL migration. The current accepted values are `AMT_STATE_CHANGE`, `AMT_PROFILE_INITIALIZATION`, `GEX_WALL_BREACH`, `OI_SHOCK` and `TEST_ALERT`. `AMT_PROFILE_INITIALIZATION` records the first confirmed state of a newly detected AMT profile; `TEST_ALERT` is reserved for the explicit connectivity-test path and is not a live market event. Unsupported values are rejected before an outbox write.

The research journal and Telegram outbox have distinct responsibilities, but the delivery paths are not identical across event types:

- **GEX:** `GEX_WALL_BREACH` alerts read confirmed events from `system_signal_journal`; the notifier does not independently recompute GEX walls from options snapshots. Cutover eligibility uses `detected_at` (journal insertion/detection time), while `event_timestamp` remains the underlying market-event time.
- **AMT:** `AMT_STATE_CHANGE` alerts still recompute locally from `amt_snapshots`.
- **OI:** `OI_SHOCK` alerts still recompute locally from `futures_oi_snapshots`; the message adds as-of AMT Price Location and confirmed AMT State as descriptive context only. Missing AMT context is labelled unavailable and does not suppress an otherwise valid OI event.
- **`telegram_alert_events`:** remains the operational notification outbox for delivery status, deduplication and retry/audit state.

Thus, GEX delivery is journal-driven while AMT and OI remain locally evaluated. Migrating AMT/OI to journal-driven delivery is a future consideration and is not part of the GEX notification correction. The research journal remains the canonical record of detected system events; the outbox records notification delivery.

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

# Database and security

Supabase PostgreSQL is the central system of record.

The current documented inventory includes:

- `amt_snapshots`
- `futures_oi_snapshots`
- `options_oi_snapshots`
- `options_collection_runs`
- `options_derived_snapshots`
- `telegram_alert_events`
- `system_signal_journal`
- `signal_reaction_analysis`

See [DATABASE.md](DATABASE.md) for the data dictionary, RLS model, timestamp provenance, constraints and event schemas.

## Access model

- **Streamlit:** `SUPABASE_ANON_KEY` with read access governed by RLS.
- **GitHub Actions backend:** `SUPABASE_SERVICE_ROLE_KEY` for trusted collection, event detection and alert operations.
- **Service-role credentials and Telegram credentials must never be exposed to Streamlit/browser code or committed to Git.**

---

# Archive and retention

The `archive-retention.yml` workflow manages archive/retention operations.

Historical data eligible for retention cleanup is archived before deletion according to the verified archive process. Archive manifests and integrity metadata, including row counts, file sizes and SHA-256 hashes, form the audit trail.

Do not manually delete source data outside the verified archive-and-integrity procedure.

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
