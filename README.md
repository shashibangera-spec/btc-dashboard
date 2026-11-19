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
