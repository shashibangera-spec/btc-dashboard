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

The current visually optimized System B UI is a frozen baseline. It includes the custom terminal skin, CSS hero header, visual Value Area ladder, side-by-side context charts and terminal-styled expanders.

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

OI remains non-directional context; the event records an abnormal measured change, not whether longs or shorts caused it.

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

The Telegram outbox is operationally separate from the research signal journal:

- **`system_signal_journal`:** records significant system events for research and later outcome analysis.
- **`telegram_alert_events`:** records notification delivery, deduplication and retry/audit state.

This separation is intentional. Not every research event must become a notification, and notification delivery state should not be used as the canonical research history.

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
- Uses Binance Spot `BTCUSDT` 30-minute OHLC candles strictly after the event timestamp.
- Excludes the event candle and requires eight contiguous, completed 30-minute candles fully contained in the event-relative four-hour window.
- Stores results in `signal_reaction_analysis`, linked to the original journal row.
- Records close prices at 30m, 1h, 2h and 4h, window high/low and neutral upside/downside excursions in USD and percent.
- Does not infer long/short direction from event type and does not generate orders.

### Data-integrity rule

An event timestamp must align with the 30-minute candle boundary closely enough for eight complete post-event candles to fit inside the next four hours. If Binance does not return exactly eight contiguous complete candles inside that strict window, the row is marked `INCOMPLETE`; it is not counted as a completed research observation. An INCOMPLETE row is terminal for this immutable historical window and analysis version; it is excluded from complete samples. Transient API/database failures are not persisted and remain retryable. Transient API/database errors are logged and remain retryable.

Excursions are measured relative to the event's journal price. The upside excursion is non-negative; the downside excursion is zero or negative. They are neutral price movements, not a simulated trade return or evidence of a tradable edge.

The analysis version is `reaction-v1.0`. A future methodology change must use a new version rather than silently overwrite the meaning of historical observations.

Required GitHub Actions secrets: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. The service-role key is never exposed to System B.
