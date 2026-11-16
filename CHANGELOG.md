# Changelog

All notable production architecture, infrastructure, database-contract and significant behavior changes are recorded here.

The project follows the Documentation Contract:

> **No architectural change is complete until its documentation impact has been reviewed.**

Dates use the project change date in UTC unless a change explicitly documents another time basis.

---

## 2026-11-16

### Changed — Research Dashboard canonical options data integration (Stage A)

- Replaced the legacy Research Dashboard's independent options run/expiry selectors with the shared `options_filters.py` canonical selector.
- The dashboard now pins one latest eligible SUCCESS snapshot, expiry, raw spot price and the derived metrics for that exact snapshot/expiry. Previous raw-chain data is accepted only from the canonical selector's eligible prior snapshot.
- Legacy filter formulas remain unchanged for Stage B; the existing UI layout and card presentation are retained. Nearest-strike views now use the pinned options spot rather than Binance live price.
- Preserved valid zero Futures funding and basis values by checking explicitly for `None`.
- Prior VAH/POC/VAL now come from the latest validated `amt_snapshots` record with `data_status=VALID`, rather than an independent Binance candle TPO recalculation.
- Added offline tests for snapshot/expiry/spot pinning, derived-row consistency, zero-valued Futures metrics and validated AMT prior levels.
- No database schema, collector, workflow schedule, AMT engine, Futures calculation, CSS or UI layout changes.


### Fixed — Fast Dashboard Supabase environment configuration

- Changed the Options Filters fetch integration to read `SUPABASE_URL` and its key from process environment variables rather than app-specific Streamlit secrets.
- Uses `SUPABASE_ANON_KEY` first, with `SUPABASE_SERVICE_ROLE_KEY` as a fallback when the anon key is not configured; credential values are never logged.
- Missing configuration now returns a clear `UNAVAILABLE / MISSING_SUPABASE_ENV` result and writes only the missing variable names to the server log.
- Added regression checks for environment-based credential lookup, service-role fallback order and fail-closed behavior.
- No filter formulas, CSS, UI layout, panel rendering, AMT or Futures logic changed.


### Fixed — Options Filters database-boundary diagnostics

- Added explicit required-key validation for collection-run, derived-snapshot and raw option-contract records before processing; missing fields now return a structured `UNAVAILABLE / MISSING_REQUIRED_COLUMNS` selection result with source table, exact missing columns and record indexes.
- Added warning-level console diagnostics for missing database record keys and full exception traceback logging in the Fast Dashboard Options Filters fetch boundary, including the exception type and exact exception message.
- Added offline regression coverage for missing required keys in all three database record sources and for record-index reporting.
- No filter formulas, Supabase schema, AMT/Futures logic, dashboard layout, CSS or Options Filters panel rendering changes.



### Fixed — TEST_MODE event classification

- Changed only the canned TEST_MODE outbox event label from `AMT_STATE_CHANGE` to `TEST_ALERT`; the test message, delivery handling and early return remain unchanged.
- Added a focused offline regression test asserting the canned message is still sent, the outbox row is logged as `TEST_ALERT`, and AMT, GEX and OI live evaluators are not called.
- Live AMT/OI evaluation and GEX notification logic are unchanged.

### Fixed — GEX Telegram production schema mismatch and alert isolation

- Production incident: the merged notifier queried `system_signal_journal.created_at`, but the live table has no such column. The actual journal insertion/detection timestamp is `detected_at` (default `now()`); `event_timestamp` is the underlying market-event time.
- Corrected the GEX journal SELECT, cutover filter, ordering, eligibility validation and outbox payload to use `detected_at`. No SQL migration or database schema change was made.
- Added failure isolation around GEX evaluation in `main()`: a GEX query/evaluation exception is logged and treated as an empty GEX result for that run, allowing independent AMT and OI evaluation and dispatch to continue. The failed GEX event is not added to the alert batch or marked SENT and remains eligible for a later retry.
- Updated offline regression tests to assert the actual `detected_at` column in the query, filter, ordering, eligibility and payload, and to verify AMT/OI dispatch continues when GEX evaluation raises.
- Corrected the journal timestamp contract in `DATABASE.md` §7 and the GEX operational description in `README.md`.

### Changed — GEX Telegram notification source of truth

- Replaced the Telegram notifier's independent GEX calculation from `options_derived_snapshots` and AMT candles with consumption of detector-confirmed `GEX_WALL_BREACH` rows from `system_signal_journal`.
- Removed the notifier-side options freshness check (including the former `0 <= age <= 35 minutes` gate). The journal row represents a detector-confirmed two-consecutive-candle event; the notifier does not recalculate or re-age the options snapshot.
- GEX confirmation price is sourced from `event_details.confirmation_close_price`; breached level is sourced from `event_details.breached_level`. Journal `event_timestamp` is market-event time and `detected_at` controls deployment cutover.
- Cutover eligibility is `detected_at >= GEX_ALERT_CUTOVER_UTC`. The review branch carries `PLACEHOLDER_SET_AT_MERGE`; it must be replaced by the UTC timestamp of the merge-to-main commit in a visible final pre-merge diff.
- New GEX outbox key: `SYSTEM_SIGNAL_GEX_WALL_BREACH_{journal_id}`. Historical keys `GEX_CALL_BREACH_*` and `GEX_PUT_BREACH_*` do not match the new identity and are not automatically reconciled; the cutover deliberately separates old and new delivery identities.
- Incomplete or malformed GEX `event_details` fail closed for that row and do not block AMT/OI evaluation. Failed GEX delivery is retried on the next scheduled Telegram workflow run. Telegram's send-then-database-write boundary remains non-transactional; a process interruption after Telegram accepts a message but before the outbox records SENT can still produce a duplicate on retry.
- AMT and OI remain locally recomputed from their existing source tables. Only GEX is journal-driven in this change; migrating AMT/OI is a future consideration and is out of scope.
- `TEST_MODE` remains the existing canned connectivity test and bypasses all live alert evaluation. Its test outbox row is now labeled `event_type="TEST_ALERT"`, keeping test deliveries out of AMT alert history.
- Added offline pytest coverage in `test_gex_notifier.py` and CI workflow `.github/workflows/gex-notifier-tests.yml`, using the verified live journal row 6 `event_details` as a fixture. No live Telegram or Supabase connection is required for these unit tests.
- No database schema, RLS policy, signal detector, or Telegram workflow environment changes are included.
- Known limitation: the GEX journal query is capped at 200 rows per notifier run. This cap is intentionally unchanged pending an explicit operational decision; backlog/pagination changes are out of scope.


### Added

- **Single production Coordinator workflow**
  - Added `.github/workflows/coordinator.yml` as the single scheduled collection entry point.
  - The Coordinator runs AMT, Futures OI and Options collectors sequentially.
  - The signal detector runs only after all required collector jobs complete successfully.
  - Concurrency protection prevents overlapping collection cycles.

- **Automated Signal Journal**
  - Added `system_signal_journal` as the canonical research record for significant backend events.
  - Added automated detection for:
    - `AMT_STATE_CHANGE`
    - `GEX_WALL_BREACH`
    - `OI_SHOCK`
    - `VALUE_MIGRATION`
  - Added event-level deduplication using `event_type + event_timestamp + event_key`.
  - Current events default to severity `INFO`.

- **Signal Detector**
  - Added `signal_detector.py`.
  - Detector uses completed/as-of validated AMT, Futures and Options context.
  - OI shocks require matching exchange coverage.
  - GEX breaches require two consecutive completed 30-minute closes beyond the same wall identity and a valid re-arm inside the walls.

### Changed

- **Scheduler migration**
  - Replaced the three independent external collection triggers with one production Coordinator trigger.
  - The Coordinator is now the intended single entry point for scheduled backend collection.

- **Telegram workflow trigger**
  - Updated `telegram_alerts.yml` to listen for completion of **BTC Data Collection Coordinator**.
  - Telegram processing only proceeds for a successful Coordinator run.
  - This ensures alert evaluation occurs after the coordinated collection cycle and signal detection.

- **System B auto-refresh architecture**
  - Replaced full-page browser reload behavior with Streamlit fragment-based periodic refresh.
  - Session-state-dependent UI tracking can persist across normal fragment refreshes instead of being reset by a page reload.

- **System B labels**
  - Renamed the AMT hero label to **AUCTION (LAST CLOSE)**.
  - Renamed the live-price hero label to **LIVE PRICE LOCATION**.
  - This makes the differing time bases explicit.

### Fixed

- **Trade Journal session isolation**
  - Moved the personal Trade Journal state away from process-global cached state and into `st.session_state`.
  - Non-sensitive shared history remains separate from the personal journal state.

- **System B `show_level_change` ValueError**
  - Hardened numeric conversion so stale/non-numeric values such as strings, `None`, NaN or incompatible objects do not reach numeric formatting.
  - Invalid values now display as `N/A` rather than crashing the dashboard.

- **Signal journal dashboard access**
  - Added/confirmed the anonymous read policy required for Streamlit to read `system_signal_journal` under RLS.

### Documentation

- Established the permanent three-tier documentation hierarchy:
  - `README.md` — current architecture and operations
  - `DATABASE.md` — schema and data contracts
  - `CHANGELOG.md` — significant historical changes

- Adopted the Documentation Contract requiring documentation review before architectural work is considered complete.

---

## Earlier baseline and infrastructure work

The following production capabilities were completed before the formal changelog was introduced and are retained here as baseline context rather than reconstructed as a detailed commit-by-commit history.

### Backend and data infrastructure

- Built the Supabase-backed AMT, Futures and Options collection pipeline.
- Enabled Row Level Security on production research tables.
- Separated Streamlit anon read access from trusted service-role backend operations.
- Added archive/retention infrastructure with manifest and SHA-256 integrity verification.
- Established completed-candle AMT confirmation discipline and timestamp provenance rules.

### Telegram alert infrastructure

- Added backend-based Telegram alert evaluation independent of an open Streamlit browser.
- Added event deduplication/outbox records, delivery status and retry/audit fields.
- Added data freshness and quality gates for options-based alert context.
- Added a reusable test mode for controlled pipeline verification.

### System B

- Evolved the Streamlit dashboard into the current fast observation cockpit.
- Established the AMT → Futures → Options information hierarchy.
- Added the visually optimized custom terminal skin and dark theme baseline.
- Froze major CSS/HTML layout redesigns as a separately managed UI concern.

---

## Changelog policy going forward

Add an entry when a change materially affects:

- production architecture
- GitHub Actions/workflow behavior
- database schema or RLS
- collector behavior
- event taxonomy or alert logic
- security/access model
- data integrity rules
- significant bug fixes
- major System B behavior or UI baselines

Minor formatting-only changes generally do not require a changelog entry unless they alter user interpretation or operational behavior.


### Market Reaction Analysis — research outcome layer

- Added `signal_reaction_analysis`, a separate versioned outcome table linked to `system_signal_journal.id` (`BIGINT`).
- Kept `system_signal_journal` immutable; reaction calculations are stored only in the new table.
- Added `reaction_analyzer.py` (`reaction-v1.0`) to measure neutral post-event price movement using Binance Spot BTCUSDT 30-minute OHLC.
- The analyzer excludes the event candle and requires eight contiguous completed candles strictly after the event and fully inside the event-relative four-hour window.
- Added 30m/1h/2h/4h close observations, window high/low, and signed neutral upside/downside excursion in USD and percent.
- Incomplete candle windows are stored as `INCOMPLETE` and excluded from complete samples; transient processing failures are not persisted and remain retryable.
- Added `.github/workflows/reaction-analyzer.yml`, scheduled daily at 07:00 UTC (12:30 PM IST) with manual dispatch.
- RLS: anon read-only; trusted backend service-role management. No client-side writes.
- No changes to Signal Detector, AMT logic, Telegram workflow, dashboard UI or order execution.


### Research methodology guardrail

- Documented that multiple signal types can originate from one completed candle and share the same reaction window. Future edge evaluation must distinguish event-row counts from unique event-time clusters to avoid overstating the independent sample size.


### Reaction Analyzer data-source migration — `reaction-v1.1`

- Replaced Binance Spot OHLC requests (which returned HTTP 451 from GitHub Actions) with Delta Exchange India `GET /v2/history/candles`.
- Uses `resolution=30m`, `symbol=BTCUSD` (Delta India perpetual market), and Unix-second `start`/`end` parameters.
- Normalizes Delta's `time`, `open`, `high`, `low`, and `close` fields into the analyzer's common candle structure.
- Retains strict event-candle exclusion, eight contiguous completed bars, four-hour deadline, and standard UTC 30-minute boundary validation.
- The four-hour request needs only eight candles, below the endpoint's 2,000-candle response limit; no pagination is needed for this analysis window.
- Bumped the analysis version to `reaction-v1.1` so Delta-based observations are distinct from the prior `reaction-v1.0` Binance Spot methodology.
- Documented the cross-market limitation: journal anchor from the BTCUSDT AMT feed, reaction OHLC from Delta BTCUSD perpetual. Do not pool versions or treat the result as executable P&L without explicit basis/methodology validation.

### Production verification — `reaction-v1.1`

- **Verified:** 2026-11-16 (UTC).
- Manually dispatched the **BTC Market Reaction Analyzer** GitHub Actions workflow; the run completed successfully at `2026-11-16T12:38:16.465Z` with `version=reaction-v1.1`.
- The run applied the event-relative four-hour eligibility cutoff, found 6 eligible journal events, recognized 5 as already analyzed, and processed the single pending event (signal ID 6).
- Signal ID 6 completed with neutral reaction measurements of `+0.8933%` upside and `-0.5856%` downside; the run summary reported 1 complete, 0 incomplete, 0 skipped, and 0 failed-retryable.
- A production Supabase query confirmed five persisted `reaction-v1.1` rows (signal IDs 2–6), all with `analysis_status = COMPLETE` and `error_message = NULL`. The five pre-existing rows were not reprocessed during this run.
- This verifies the live analyzer workflow, idempotent handling of already-analyzed signals, successful processing of the pending signal, and persistence to `signal_reaction_analysis`. No implementation or schema change was made as part of this verification record.



### Added — isolated Options Filters backend (Stage 2)

- Added `options_filters.py` with a read-only Canonical Snapshot Selector, shared raw-chain validation, null-preserving aggregation and five independent calculators (Nearby OI, IV Skew, 24h Volume / OI, OI Migration and Sampled Taker Trade Activity).
- The selector pins the latest `SUCCESS` run, selected future expiry, `snapshot_id` and raw snapshot spot. Invalid selected data fails closed; it does not fall back to a different snapshot.
- OI Migration uses the immediately preceding eligible successful snapshot for the same expiry only when the full contract identity universe matches; elapsed time is reported.
- Calculator failures are isolated. No database migration, new persistence, collector, scheduler, Fast Dashboard UI, AMT code or Futures code is included.
- Added offline tests in `test_options_filters.py` and GitHub Actions workflow `.github/workflows/options-filters-tests.yml`.

### Added — Options Filters Fast Dashboard integration (Stage 3)

- Integrated the five existing Options Filters outputs into System B (`btc_live_dashboard.py`) using the shared `options_filters.py` backend: Nearby OI, IV Skew, 24h Volume / OI, OI Migration and Sampled Taker Trade Activity.
- Added a collapsible Streamlit expander after the Futures panel. The integration preserves the established **AMT → Futures → Options** information hierarchy and leaves AMT/Futures logic and calculations unchanged.
- Added a cached Supabase fetch/calculation wrapper with a **180-second Streamlit cache TTL**.
- The UI renders validated backend outputs only; it does not duplicate filter formulas or thresholds. Unavailable results are handled gracefully, without fallback to a different snapshot.
- Added `test_fast_dashboard_options_ui.py` and `.github/workflows/fast-dashboard-options-ui.yml` to check UI integration boundaries, panel placement, backend-only sourcing, caching and preservation of the existing dashboard styling.
- No database schema migration, new persistence, collector, scheduler, AMT logic or Futures logic was introduced by this UI integration.
