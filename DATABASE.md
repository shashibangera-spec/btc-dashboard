# Database Reference — Live Supabase Schema

**Project:** btc-live-db (kjbspazuvqrflysgihor)  
**Schema audited:** public  
**Audit date:** 2026-11-20  
**Source of truth for this snapshot:** live Supabase PostgreSQL catalog, cross-checked against the current main branch SQL migrations and Python consumers.

This document describes the database that was actually present during the audit. It is not a promise that row counts or data freshness remain unchanged. All ten observed public tables had Row Level Security (RLS) enabled.

## 1. Live table inventory

| Table | Purpose | Rows at audit |
|---|---|---:|
| amt_snapshots | Completed BTCUSDT 30m AMT candles, prior/developing value and state output | 423 |
| auction_deterioration_metrics | Research-only shadow metrics and frozen-VAL episode tracking | 375 |
| futures_oi_snapshots | Per-exchange and aggregate Futures OI, funding, basis and source status | 1,859 |
| gex_strike_attribution | Event-only strike-level GEX wall change and Shapley attribution records | 147 |
| options_collection_runs | Options collection run status and contract counts | 382 |
| options_derived_snapshots | Per-snapshot/per-expiry calculated Options metrics | 2,709 |
| options_oi_snapshots | Raw Delta India BTC options contract observations | 177,808 |
| signal_reaction_analysis | Versioned, non-directional price reaction measurements linked to journal events | 31 |
| system_signal_journal | Deduplicated research event journal | 33 |
| telegram_alert_events | Telegram notification outbox, deduplication and delivery/retry state | 23 |

Row counts are point-in-time observations from the audit and will change as collectors, analyzers and retention run.

## 2. Actual columns

Types below are the live PostgreSQL types. timestamptz means timestamp with time zone; jsonb means JSONB. Nullable fields remain nullable in the live schema. Defaults and constraints are summarized below; inspect the SQL migration or live catalog before changing them.

### amt_snapshots

Purpose: one row per Binance BTCUSDT 30-minute candle, including frozen prior profile, developing value, AMT states and Project Echo metadata.

Columns:
- id bigint; exchange text; symbol text; timeframe text
- candle_open_time timestamptz; candle_close_time timestamptz; collected_at timestamptz
- open_price numeric; high_price numeric; low_price numeric; close_price numeric; volume numeric
- atr_21 numeric; atr_buffer numeric; profile_date date; profile_id text
- prior_vah numeric; prior_poc numeric; prior_val numeric
- tpo_row_size numeric; value_area_percent numeric; discovery_above numeric; discovery_below numeric
- engine_version text; confirmed_state text; developing_state text; state_output jsonb
- data_status text; error_message text; created_at timestamptz
- project_echo_code text; project_echo_name text; project_echo_version text
- developing_poc numeric; developing_vah numeric; developing_val numeric

Primary key: id. Unique candle key: (exchange, symbol, timeframe, candle_open_time). Additional indexes: candle time, developing state/time, profile date, Project Echo code/time and confirmed state/time.

### auction_deterioration_metrics

Purpose: one row per AMT profile/candle for the non-alerting shadow monitor. Stores symmetric upside/downside close and wick excursions in USD, ATR and percentage terms; POC distance; and frozen Developing-VAL test state.

Columns:
- id bigint; exchange text; symbol text; timeframe text; profile_id text
- candle_open_time timestamptz; candle_close_time timestamptz; confirmed_state text
- close_price numeric; high_price numeric; low_price numeric
- developing_val numeric; developing_poc numeric; atr_21 numeric
- transition_anchor_time timestamptz; transition_anchor_price numeric
- downside_close_excursion_usd numeric; upside_close_excursion_usd numeric
- downside_wick_excursion_usd numeric; upside_wick_excursion_usd numeric
- downside_close_excursion_atr numeric; upside_close_excursion_atr numeric
- downside_wick_excursion_atr numeric; upside_wick_excursion_atr numeric
- downside_close_excursion_pct numeric; upside_close_excursion_pct numeric
- downside_wick_excursion_pct numeric; upside_wick_excursion_pct numeric
- poc_distance_atr numeric
- val_test_active boolean; val_test_started_at timestamptz; val_test_level numeric
- val_test_status text; val_test_count integer; val_rejection_count integer
- calculation_version text; created_at timestamptz

Primary key: id. Unique candle key: (exchange, symbol, timeframe, profile_id, candle_close_time). Status values: NONE, STARTED, CONTINUING, REJECTED, BROKEN, INCOMPLETE_GAP, RESET_CONTEXT. Current migration default calculation version: shadow-v1.0. The first catalog snapshot during this audit showed zero rows; a subsequent live verification during the same documentation pass showed 375 rows. The sidecar has therefore begun persisting metrics.

Indexes: candle_close_time DESC, (profile_id, candle_close_time DESC), (confirmed_state, candle_close_time DESC), plus primary/unique indexes.

### futures_oi_snapshots

Purpose: one row per exchange observation and an AGGREGATE row, storing OI notional, funding, basis and source/coverage status.

Columns: id bigint; created_at timestamptz; exchange text; oi_usd double precision; funding_rate double precision; basis_pct double precision; status text.

Primary key: id.

### gex_strike_attribution

Purpose: event-only attribution for meaningful changes among previous/current Top-3 Call and Put GEX strikes. Gamma, OI and Spot contributions are mathematical decompositions, not participant or dealer-position labels.

Columns:
- id bigint; comparison_id uuid; previous_snapshot_id uuid; current_snapshot_id uuid
- expiry_date date; option_type text; strike_price numeric; wall_event_type text
- previous_wall_strike numeric; current_wall_strike numeric
- previous_top3_rank smallint; current_top3_rank smallint
- previous_collected_at timestamptz; current_collected_at timestamptz
- gamma_previous numeric; gamma_current numeric; oi_previous numeric; oi_current numeric
- spot_previous numeric; spot_current numeric; contract_multiplier numeric
- gex_previous numeric; gex_current numeric; gex_change numeric; gex_change_pct numeric
- gamma_contribution numeric; oi_contribution numeric; spot_contribution numeric
- wall_margin_change numeric; margin_gamma_contribution numeric
- margin_oi_contribution numeric; margin_spot_contribution numeric
- dominant_driver text; attribution_status text; threshold_pct numeric; created_at timestamptz

Primary key: id. Unique event key: (previous_snapshot_id, current_snapshot_id, expiry_date, option_type, strike_price). Event types: WINNER_SWITCH, SIGNIFICANT_MAGNITUDE_CHANGE, NEW_EXPOSURE, DISAPPEARED. Dominant driver values: GAMMA, OI, SPOT, MIXED, UNATTRIBUTED. Attribution status values: OK, NEW_EXPOSURE, DISAPPEARED, INSUFFICIENT_DATA. Default threshold: 2.0 percent.

Indexes: expiry/current time, comparison ID, event type/current time, plus primary/unique indexes.

### options_collection_runs

Purpose: run-level collection status and completeness metadata, keyed by a UUID shared with raw and derived snapshot records.

Columns: snapshot_id uuid; started_at timestamptz; completed_at timestamptz; underlying text; expected_contracts integer; received_contracts integer; inserted_contracts integer; failed_expiries jsonb; status text; error_message text.

Primary key: snapshot_id.

### options_derived_snapshots

Purpose: calculated metrics for one options collection snapshot and expiry.

Columns:
- snapshot_id uuid; expiry_date date; calculated_at timestamptz; source_collected_at timestamptz
- underlying_spot_price numeric; max_pain_strike numeric; atm_strike numeric
- atm_straddle_price numeric; atm_straddle_pct_spot numeric
- call_gex_wall_strike numeric; call_gex_wall_value numeric
- put_gex_wall_strike numeric; put_gex_wall_value numeric
- gex_contract_multiplier numeric; local_oi_pcr numeric
- local_put_oi_contracts numeric; local_call_oi_contracts numeric
- local_oi_band_pct numeric; metric_version text; metric_status jsonb

Composite primary key: (snapshot_id, expiry_date). Foreign key: snapshot_id references options_collection_runs(snapshot_id). Indexes: calculated time and expiry/calculated time.

### options_oi_snapshots

Purpose: raw contract-level Delta India BTC options observations. Taker buy/sell fields are sampled contract fields, not complete BTC market order flow.

Columns:
- id bigint; snapshot_id uuid; collected_at timestamptz; exchange_timestamp timestamptz
- exchange text; underlying text; expiry_date date; option_type text; strike_price numeric
- product_id bigint; symbol text; contract_status text
- oi_contracts numeric; oi_value_usd numeric; volume_24h numeric; turnover_usd numeric
- mark_price numeric; last_price numeric; spot_price numeric
- bid_price numeric; ask_price numeric; bid_size numeric; ask_size numeric
- bid_iv numeric; ask_iv numeric; mark_iv numeric; greeks jsonb
- data_status text; raw_payload jsonb; taker_buy_volume numeric; taker_sell_volume numeric

Primary key: id. Unique contract key per snapshot: (snapshot_id, product_id). Indexes: snapshot ID, collection time, and contract history (underlying, expiry_date, option_type, strike_price, collected_at DESC).

### signal_reaction_analysis

Purpose: versioned, non-directional post-event price movement measurements linked to the immutable signal journal.

Columns:
- id uuid; signal_id bigint; analysis_version text; anchor_price numeric
- window_start timestamptz; window_end timestamptz
- price_after_30m numeric; price_after_1h numeric; price_after_2h numeric; price_after_4h numeric
- window_high numeric; window_low numeric
- max_up_excursion_usd numeric; max_down_excursion_usd numeric
- max_up_excursion_pct numeric; max_down_excursion_pct numeric
- candles_expected integer; candles_received integer; analysis_status text
- data_source text; error_message text; analyzed_at timestamptz; created_at timestamptz

Primary key: id. Unique key: (signal_id, analysis_version). Foreign key: signal_id references system_signal_journal(id) with ON DELETE RESTRICT. The live rows at audit time used reaction-v1.1; the migration's original default remains reaction-v1.0. Current analyzer code uses Delta BTCUSD perpetual 30-minute candles and an eight-candle/four-hour window. Do not pool methodology versions without reviewing source/contract differences.

### system_signal_journal

Purpose: canonical research event journal populated by signal_detector.py.

Columns:
- id bigint; event_timestamp timestamptz; event_type text; event_key text; severity text
- price numeric; amt_state text; dev_poc numeric; dev_vah numeric; dev_val numeric
- call_wall numeric; put_wall numeric; oi_usd numeric
- event_details jsonb; detected_at timestamptz; detection_version text

Primary key: id. Unique event identity: (event_type, event_timestamp, event_key). Observed live event types: AMT_STATE_CHANGE, GEX_WALL_BREACH, OI_SHOCK, VALUE_MIGRATION.

### telegram_alert_events

Purpose: operational notification outbox for deduplication, delivery status, Telegram message ID, retry count and error tracking. It is not the canonical research event journal.

Columns: event_key text; event_type text; event_time timestamptz; payload jsonb; delivery_status text; telegram_message_id bigint; attempt_count integer; last_error text; created_at timestamptz; sent_at timestamptz; claimed_at timestamptz.

Primary key: event_key. The repository migration constrains event types to AMT_STATE_CHANGE, AMT_PROFILE_INITIALIZATION, GEX_WALL_BREACH, OI_SHOCK and TEST_ALERT. TEST_ALERT is a connectivity test, not a live market event.

## 3. Live RLS and policy observations

- RLS is enabled on all ten public tables.
- Explicit anonymous SELECT policies were observed on amt_snapshots, futures_oi_snapshots, options_collection_runs, options_derived_snapshots, options_oi_snapshots, signal_reaction_analysis and system_signal_journal.
- Explicit service-role management policies were observed on amt_snapshots, futures_oi_snapshots, options_collection_runs, options_derived_snapshots, options_oi_snapshots and signal_reaction_analysis. The service role has privileged backend access and bypasses RLS.
- No rows were returned from pg_policies for auction_deterioration_metrics, gex_strike_attribution or telegram_alert_events. These tables rely on backend service-role access; no anonymous read policy was present in the catalog audit.
- No explicit authenticated policies were observed.
- Do not infer effective access from table grants alone. RLS policies also govern row access. Recheck live grants and policies before any access-control change.

## 4. Index and uniqueness inventory

| Table | Index / key |
|---|---|
| amt_snapshots | Unique (exchange, symbol, timeframe, candle_open_time); indexes on candle_open_time DESC, (developing_state, candle_open_time DESC), profile_date DESC, (project_echo_code, candle_open_time DESC), (confirmed_state, candle_open_time DESC) |
| auction_deterioration_metrics | Unique (exchange, symbol, timeframe, profile_id, candle_close_time); indexes on candle_close_time DESC, (profile_id, candle_close_time DESC), (confirmed_state, candle_close_time DESC) |
| futures_oi_snapshots | Primary-key index on id |
| gex_strike_attribution | Unique (previous_snapshot_id, current_snapshot_id, expiry_date, option_type, strike_price); indexes on comparison_id, (expiry_date, current_collected_at DESC), (wall_event_type, current_collected_at DESC) |
| options_collection_runs | Primary-key index on snapshot_id |
| options_derived_snapshots | Primary key (snapshot_id, expiry_date); indexes on calculated_at DESC and (expiry_date, calculated_at DESC) |
| options_oi_snapshots | Unique (snapshot_id, product_id); indexes on snapshot_id, collected_at DESC, and contract history (underlying, expiry_date, option_type, strike_price, collected_at DESC) |
| signal_reaction_analysis | Unique (signal_id, analysis_version); indexes on signal_id and analysis_status |
| system_signal_journal | Unique (event_type, event_timestamp, event_key); indexes on event_timestamp DESC and (event_type, event_timestamp DESC) |
| telegram_alert_events | Primary key event_key; partial pending/failed index on created_at; index on (event_type, event_time DESC) |

## 5. Migrations and drift

### SQL files currently present in migrations/

1. 20261116_create_signal_reaction_analysis.sql
2. 20261116_harden_signal_reaction_analysis_grants.sql
3. 20261116_restrict_signal_reaction_service_role_privileges.sql
4. 20261117_allow_amt_profile_initialization_and_test_alert_event_types.sql
5. 20261119_create_gex_strike_attribution.sql
6. 20261120_create_auction_deterioration_metrics.sql

### Live Supabase migration history

The live migration history contains seven versions:

- 20261112110255_add_project_echo_and_options_derived_metrics
- 20261116073602_create_signal_reaction_analysis
- 20261116074155_harden_signal_reaction_analysis_grants
- 20261116074647_restrict_signal_reaction_service_role_privileges
- 20261117070622_allow_amt_profile_initialization_and_test_alert_event_types
- 20261119155241_create_gex_strike_attribution
- 20261120191658_create_auction_deterioration_metrics

**Migration drift / reproducibility warning:** the live history includes 20261112110255_add_project_echo_and_options_derived_metrics, but no matching SQL file exists in the current repository migrations/ directory. The current folder also does not contain initial CREATE TABLE migrations for several foundational live tables, including AMT, Futures, raw Options, Options run/derived, signal journal and Telegram outbox. Therefore the checked-in migrations are not a complete clean-database bootstrap. Reconcile and restore missing migration sources before relying on a fresh-project replay.

The live catalog, not this document or a migration filename alone, is authoritative for the current production schema. Re-run the schema audit after manual database changes.

## 6. Retention and archive boundary

- The retention target is only public.options_oi_snapshots.
- The workflow passes --older-than-days 3 to archive_options_snapshots.py and freezes one shared UTC cutoff at run start: current UTC time minus 72 hours.
- The scheduled GitHub Actions run is configured for 18:17 IST daily (12:47 UTC; cron `47 12 * * *`). Actual starts can be delayed by GitHub Actions scheduling/queue behavior.
- retention_cron.py defaults to three days (overridable through RETENTION_DAYS / --retention-days) and requires --execute for deletion.
- Before deleting, the script validates the archive manifest, checks archive integrity and checksum, and reconciles archived IDs against the exact database deletion candidates. A failed preflight aborts deletion for the run.
- The GitHub workflow publishes the archive to the separate private btc-data-archive repository before running retention.
- The workflow then measures database size and runs VACUUM (VERBOSE, ANALYZE) on options_oi_snapshots.
- No retention rule in the inspected workflow targets the other nine tables.

## 7. Audit notes

- The live shadow table was observed at 375 rows on the final verification query during this audit, confirming that it has begun persisting metrics. Row counts are volatile and should be rechecked before relying on them.
- The current live schema is more complete than the checked-in migration folder. This is documented drift, not silently resolved by this documentation update.
- This file is a descriptive inventory. It does not change schema, RLS, grants, indexes, retention, collectors or calculation logic.
