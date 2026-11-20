## 2026-11-21

### Fixed — Auction Deterioration Shadow Monitor VAL metadata and version rebuild

- Preserve the tested VAL level for immediate REJECTED and BROKEN outcomes and retain the frozen level/start timestamp when an active episode reaches a terminal state.
- Added a per-source-candle identity guard so a duplicated/replayed source row cannot increment val_test_count twice in one full-history calculation.
- Added a calculation-version consistency check over the available source-history window. When stored rows use a different calculation version, the monitor upserts a full-history rebuild rather than mixing old and new formula outputs. Formula changes must bump CALCULATION_VERSION; the next run then rebuilds the affected history. Bumped this change to shadow-v1.2 so the first post-merge run rebuilds the mixed v1.0/v1.1 history.
- Added offline regression coverage for immediate terminal metadata, preservation of active-episode metadata, and duplicate-candle count protection.
- No database schema, source collector, production AMT logic, alerts, dashboard UI or trading rules changed.

## 2026-11-20

### Diagnostic — Button-based Alerts navigation

- Replaces the unresponsive MARKET / ALERTS radio selector with two native Streamlit buttons whose callbacks update the existing `dashboard_view` session-state value.
- Keeps the selector outside the MARKET auto-refresh fragment and preserves the existing MARKET and ALERTS render paths.
- This is a diagnostic interaction test; it does not change market calculations, alert detection or delivery, database access, schema, credentials, collectors, or Pine logic.
- PR is intended for review only. Do not merge or deploy until explicitly approved.

## 2026-11-20

### Fixed — Alerts selector interaction

- Replaced the MARKET / ALERTS `st.segmented_control` with Streamlit's native horizontal `st.radio` control while retaining the existing view-routing and fragment boundaries.
- Added regression assertions for the radio selector and its horizontal layout.
- No market calculations, alert detection or delivery, database access, schema, credentials, collectors, or deployment configuration changed.

## 2026-11-20

### Fixed — Alerts view navigation

- Moves the MARKET / ALERTS selector outside the three-minute auto-refresh fragment so navigation receives normal Streamlit reruns.
- Keeps only the MARKET dashboard inside the auto-refresh fragment; the ALERTS view renders independently without using `st.stop()` to switch views.
- Adds a regression test for the navigation/fragment boundary.
- No market calculations, alert detection or delivery, database access, schema, credentials, or production deployment behavior changed.

## 2026-11-20

### Added — Read-only Alerts history tab

- Added a dedicated ALERTS view alongside the existing MARKET cockpit, preserving the existing market dashboard content and styling.
- Displays canonical events from `system_signal_journal`, with history-window, event-type and delivery-status filters, manual refresh, and expandable event evidence.
- Reconciles GEX wall-breach notifications using the journal row identity; reconciles AMT state changes and OI shocks using exact event-type and UTC event-time matches. Ambiguous and missing matches are explicitly labelled rather than guessed.
- Displays the private `telegram_alert_events` outbox separately, including operational-only records such as profile initialization and TEST_ALERT.
- Reads the journal using the anonymous key when available and reads the private outbox only with a server-side `SUPABASE_SERVICE_ROLE_KEY`. No credential is sent to browser code; no RLS, grants, schema, collector, detector, Telegram sender, retry, alert or AMT logic is changed.
- Adds offline regression tests for access separation, matching rules, unavailable/ambiguous states, research-only classification and read-only behavior.
- The view is capped at 300 journal rows and 500 outbox rows per refresh; it does not run a background polling loop. Reopening the app reloads persisted records from Supabase.
- No database or production deployment changes.

## 2026-11-20

### Changed — Daily raw-options archive/retention schedule moved to 18:17 IST

- Moves the scheduled GitHub Actions run from 06:17 IST (00:47 UTC) to 18:17 IST (12:47 UTC), using cron `47 12 * * *`.
- Keeps the rolling three-day (72-hour) retention cutoff, shared archive/retention cutoff, archive-first sequence, integrity checks, exact snapshot-ID reconciliation, and the existing target table unchanged.
- Updates README, database inventory, and observation guide to match the active workflow schedule. Earlier schedule entries below are retained as historical change records.
- No database, schema, collector, archive format, dashboard, alert, Pine or trading-rule changes.


### Changed — Event-driven Telegram alert evaluation

- Converts `.github/workflows/telegram_alerts.yml` into a reusable workflow via `workflow_call`, while retaining manual `workflow_dispatch` for testing and recovery.
- Removes the independent Telegram cron. The Coordinator invokes Telegram evaluation only after AMT, Futures OI, Options OI and signal detection all succeed.
- Preserves the shared non-cancelling `btc-telegram-alerts` concurrency group so reusable and manual evaluations do not overlap.
- Adds offline workflow-contract regression tests and a focused GitHub Actions validation workflow.
- Leaves the external Coordinator dispatcher, collector workflows, `telegram_alerts.py`, `signal_detector.py`, database schema and outbox unchanged. No production alert logic or market rules changed.

## 2026-11-20

### Fixed — Archive/retention cutoff race

- Establishes one timezone-aware UTC cutoff once per scheduled workflow run and passes the exact value to both archive export and retention preflight/deletion.
- Prevents rows collected after the export snapshot from becoming newly eligible merely because the retention worker recalculated the three-day cutoff later.
- Retains exact archive ID coverage checks and fail-closed behavior for genuinely late-arriving historical rows.
- Moves the daily archive/retention schedule to 00:47 UTC (06:17 IST); GitHub scheduled start times remain subject to queue delay.
- Adds regression tests for timezone-aware cutoff parsing and workflow argument wiring. No database schema, retention duration, archive format, collector, dashboard, alert or trading-rule changes.

### Fixed — Raw options retention DELETE statement timeout

- Replaced one large per-day PostgREST DELETE with bounded, deterministic batches of preflighted snapshot IDs (default 100 IDs per request).
- Preserves the exact UTC partition and frozen retention-cutoff predicates on every batch, and verifies the exact deleted count after each request.
- Stops immediately on any missing/mismatched count. A later run re-discovers only remaining eligible IDs and rechecks archive coverage before continuing.
- Adds regression tests for batching, per-batch count mismatch, preflight ID/count mismatch and empty partitions.
- No retention-window, archive format, database schema, collector, dashboard, alert or trading-rule changes.


### Changed — Raw options snapshot retention reduced to three days

- Reduced the archive workflow export cutoff and retention worker default from five days to three days for `public.options_oi_snapshots` only.
- Preserved the existing archive-first sequence and manifest, file-size, SHA-256, gzip/CSV, row-count, timestamp-range and exact snapshot-ID coverage checks before deletion.
- `public.gex_strike_attribution` and all other research tables remain outside this retention process and unchanged.
- Updated the retention default regression test. No database schema, collector, dashboard, alert, Pine or trading-rule changes.

## 2026-11-19

### Added — Phase 2 event-only GEX wall attribution (review branch)

- Adds per-expiry Top-3 Call/Put GEX ranking and compares the union of previous/current Top-3 strikes.
- Uses six-order Shapley attribution across exchange-reported Gamma, OI and spot; winner switches are attributed by the change in the new-versus-old wall margin.
- Persists only winner switches and same-wall GEX magnitude changes at or above the configurable 2% threshold. Unchanged observations are not written.
- Keeps attribution failures non-blocking for the existing options collector. Requires successful, complete same-expiry snapshots and never fabricates zero values for missing Gamma/OI.
- Adds a dedicated RLS-protected event table migration and offline regression tests. Migration is not applied to the live database.
- No archive/TTL cleanup, dashboard, options filter, alert, Pine or trading-rule changes.

## 2026-11-18

### Added — isolated IV-surface research methodology (unmerged PR)

- Added additive `iv-surface-research-v1.0` helpers for 25-delta risk reversal, ATM IV and contract-level quote-quality diagnostics. The existing nearest-five IV skew function, signatures and return values are unchanged.
- Handles Delta Exchange negative Put deltas by validating the signed convention and using absolute Delta for 25Δ interpolation; interpolation requires a bracket and never extrapolates.
- Rejects the exact decimal IV sentinel `0.01`; reports bid/ask IV sentinel flags separately from mark-IV eligibility.
- Reports positive/two-sided/crossed quote checks, relative bid/ask spread, source timestamp age, methodology version, interpolation brackets and rejection reasons. No unapproved wide-spread or stale-age threshold is imposed.
- Adds a separate SELECT-only loader for the extra fields from `options_oi_snapshots`. No UPDATE, DELETE, INSERT, schema change, collector change, UI integration or production alert/trading-rule change.
- Adds offline regression tests for negative Put Delta, interpolation/no-extrapolation, sentinel IV, crossed quotes, ATM IV and read-only loader behavior.
- Layer 3 persistence is deliberately deferred: results are returned in memory; no database or local artifact is written pending review of the unmerged PR.

## 2026-11-17

### Fixed — System B listed-contract OI completeness

- Distinguishes structurally unlisted Call/Put sides from listed contracts whose ticker or OI observation is missing.
- Normalizes a side to zero only when complete product and ticker pagination proves that the contract is absent; listed-but-missing, NULL, negative or non-finite OI remains invalid.
- Preserves whole-chain completeness for Max Pain and local-band completeness for Local OI PCR, without changing either formula or the ±10% band.
- Added offline regression coverage for unlisted outer strikes, missing listed tickers, NULL/invalid OI, incomplete pagination, duplicate and unexpected contracts.
- No CSS, HTML, layout, labels, collectors, database schema, Options Filters backend or AMT/Futures logic changed.

## 2026-11-17

### Fixed — OI Migration snapshot eligibility and strike-level coverage

- Selects the most recent successful, complete snapshot for the same underlying and expiry within the inclusive 25–45-minute comparison window; reports explicit unavailable reasons when no eligible baseline can be selected.
- Replaces whole-chain contract-universe equality with a contract-identity intersection at strike and option-type level.
- Excludes an entire strike when either Call/Put side is not comparable or any matched OI observation is NULL/invalid; missing OI is never converted to zero.
- Requires at least five valid paired strikes and returns `INSUFFICIENT_DATA · FEWER_THAN_5_VALID_PAIRED_STRIKES` below that threshold.
- Adds offline regression coverage for interval boundaries, too-close/too-old baselines, invalid candidates, contract changes, NULL OI, the five-strike minimum and tie-breaking.
- No database schema, collector, workflow, UI, CSS, AMT, Futures, Pine, alert or trading-rule changes.

# Changelog

## 2026-11-17

### Fixed — System B options missing-versus-zero handling

- Removed zero-filling from the Fast Dashboard's local options calculations. Missing OI, volume and GEX inputs remain missing; observed zero values remain valid zero observations.
- Local OI/volume aggregates and PCR values are unavailable when any required observation is missing or invalid, rather than silently treating missing rows as zero.
- Max Pain now requires an explicitly complete chain with unique positive strikes and finite, non-negative Call and Put OI at every strike. Any missing, malformed or incomplete input fails closed to unavailable; genuine zero OI is retained.
- Added offline regression tests for genuine zero observations, missing/invalid inputs, wall selection and Max Pain fail-closed behavior.
- No terminal skin, CSS, HTML, layout, labels, AMT/Futures logic, database, collectors, alerts or trading rules changed.

## 2026-11-17

### Fixed — Telegram outbox event-type constraint synchronization (PR #24)

- Updated the `telegram_alert_events_event_type_check` constraint to accept the five event types emitted or used by the notifier: `AMT_STATE_CHANGE`, `AMT_PROFILE_INITIALIZATION`, `GEX_WALL_BREACH`, `OI_SHOCK` and `TEST_ALERT`.
- Synchronized the Python `ALLOWED_EVENT_TYPES` guard with the database constraint. Unsupported event types are rejected before an outbox database write.
- Added regression coverage for allowlist synchronization, successful PENDING enqueue for each supported type, and rejection of unsupported types without creating an outbox row.
- Applied by migration `20261117_allow_amt_profile_initialization_and_test_alert_event_types.sql`. This changes the allowed values of the existing CHECK constraint; it does not add a table or column.

## 2026-11-17

### Documentation — Prior Value overlap regime added to OI research architecture

- Added a mathematical Value Area overlap framework comparing each completed Developing Value Area with its frozen Prior Value Area. Prior-Value Coverage % is the primary measure; Developing-Value Coverage % and symmetric Intersection-over-Union are retained as diagnostics.
- Added provisional high-overlap (candidate Balance), intermediate and low-overlap (candidate Value Migration) research buckets, with sensitivity testing and no claim that the cutoffs are universal AMT constants.
- Split the Futures OI breach research into two conditional studies: in high-overlap environments, compare 30-minute OI change with prior-day and 3-/7-day OI changes for absorption-candidate outcomes; in low-overlap environments, test 30-minute OI change at VAL/VAH edges against continuation versus failed-auction outcomes.
- Added continuous breach-depth normalization by Prior Value width and ATR, plus strict as-of AMT alignment, stable contributor coverage, non-overlapping episode handling, and explicit outcome definitions.
- Explicitly documents balance/trend and OI-predictor relationships as hypotheses for empirical testing, not established rules or directional signals.
- Documentation-only change. No production code, database schema, collector, workflow, dashboard, alert or CSS behavior changed.


All notable production architecture, infrastructure, database-contract and significant behavior changes are recorded here.

The project follows the Documentation Contract:

> **No architectural change is complete until its documentation impact has been reviewed.**

Dates use the project change date in UTC unless a change explicitly documents another time basis.

---

## 2026-11-17

### Documentation — AMT-first theoretical foundation and 30-day research plan

- Added a Theoretical Framework section establishing AMT and completed price action as the structural foundation; Futures OI and Options OI remain descriptive positioning measurements and neither is a standalone directional signal.
- Added a 30-day research vector to screen for tight completed-candle price ranges concurrent with comparable aggregate Futures OI expansion. The documented method requires stable contributor coverage, valid 25–35-minute observation continuity, strict as-of AMT alignment, price-range/OI-change distributions, USD-notional OI normalization sensitivity and manual review. Results are explicitly absorption candidates, not proof of passive absorption or a directional edge.
- Recorded Aggressive Participation (full-market CVD / Spot CVD) and Liquidations as missing data layers for a possible future research phase. Current sampled options taker activity is not a substitute for either layer.
- Documentation-only change. No production code, collector, database schema, workflow, dashboard or alert behavior changed.

## 2026-11-17

### Changed — Context-only Futures OI_SHOCK updates

- Added as-of AMT Price Location (`ABOVE_VAH`, `BELOW_VAL`, `INSIDE_VALUE`) and confirmed AMT State to the Signal Detector OI_SHOCK event payload, using the latest valid completed AMT candle at or before the OI observation and its Prior VAH/VAL.
- AMT lookup or Prior Value validation failure is recorded as `LOCATION_UNAVAILABLE`; missing confirmed state is `AMT_STATE_UNAVAILABLE`. A valid OI_SHOCK event is not suppressed because AMT context is unavailable.
- Added the same Price Location and AMT State fields to the independently evaluated Telegram OI_SHOCK message and outbox payload. Existing OI thresholds, timestamp handling, 25–35-minute interval checks and contributor matching are unchanged in both paths.
- Added a compact System B Futures OI Context block alongside AMT structure, showing aggregate OI, comparable OI change, as-of AMT location/state, coverage and timestamp. No CSS or existing AMT card/layout changes.
- Added offline regression tests for AMT location, unavailable-context behavior, both OI threshold conventions and interval/contributor safeguards. No database schema migration and no directional trading signal or rule is introduced; no directional OI edge has been validated.

## 2026-11-16

### Changed — Research Dashboard AI report provenance and parity verification (Stage D)

- Expanded the copy/paste AI report with the exact canonical options snapshot ID, expiry, snapshot-local spot, collection/run completion timestamp, and derived-metric calculation timestamp.
- Explicitly distinguishes the live Binance price and its completed candle close timestamp from the options snapshot-local spot. All source timestamps are labelled UTC; the report generation timestamp is also explicit.
- Added an offline same-fixture regression that runs the Fast Dashboard canonical loader and Research Dashboard adapter against the exact same SUCCESS snapshot and asserts identical output dictionaries for all five filters and identical snapshot ID, expiry, spot and run completion timestamp.
- Added report-content regression checks for provenance fields. Existing AMT and Futures functions, formulas and UI/CSS/layout are outside the Stage D code changes; the PR diff is restricted to report output, regression tests, workflow and documentation.

## 2026-11-16

### Changed — Research Dashboard canonical OI map and raw chain views (Stage C)

- Removed zero-filling from the OI Concentration Map. Missing Call/Put OI remains null and is visually labelled Missing; observed zero OI remains an actual zero-valued observation.
- Centralized nearest-strike selection in the research adapter and use the canonical options snapshot spot for both the OI map and raw options table. No Binance live price is used to choose the ten strikes.
- Reworked raw-chain pivot aggregation to preserve all-null OI, OI USD, volume and taker groups as missing (`sum(min_count=1)`), while preserving actual zero values. Premium and IV remain means with missing values retained.
- The OI map and raw options table render only when the canonical options result is `OK`. Their raw rows, expiry, spot and derived annotations are sourced from the same Stage A pinned snapshot/expiry and matching derived metrics.
- Existing chart/table layout, styling, CSS, HTML, labels and presentation structure are retained.
- Added regression tests for missing-versus-zero OI, missing taker sides, snapshot-spot nearest-ten selection and pinned raw-table provenance.

## 2026-11-16

### Changed — Research Dashboard canonical filter calculations (Stage B)

- Removed the five dashboard-local legacy options filter calculators. The Research Dashboard now consumes `calculate_all_filters()` outputs from `options_filters.py` via the Stage A compatibility adapter.
- Nearby OI, IV Skew, 24h Volume / OI, OI Migration and Sampled Taker Activity cards now read the canonical result fields, preserving the backend's established formulas, thresholds, classifications and snapshot provenance.
- UI card layout and presentation structure remain unchanged. Existing card labels are retained for visual compatibility; the underlying volume/OI metric is the backend's 24h Volume / OI calculation.
- Structured UNAVAILABLE, INSUFFICIENT_DATA, PARTIAL and ERROR states are surfaced with their reasons. Migration is shown as Stable only when the backend explicitly returns a valid STABLE classification; mixed migration is labelled Mixed rather than Stable.
- The copy/paste AI report now includes each canonical filter's status, reason where applicable, classification and valid measurements. Raw-chain Nearby OI evidence markers are sourced from the canonical Nearby OI result.
- Added regression tests for all-five-filter wiring, snapshot provenance, missing prior-snapshot handling, partial taker coverage and removal of legacy analyzer definitions.

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



### Corrected — Descriptive IV Skew and Sampled Taker Activity

- Removed the IV Skew ±5% classification threshold and the `BULLISH_URGENCY`, `BEARISH_URGENCY`, and `NEUTRAL` labels. The backend now exposes the continuous relative Call IV premium as `metric = CALL_IV_PREMIUM` and `call_iv_premium_pct`.
- Removed `AGGRESSIVE_BUYING`, `AGGRESSIVE_SELLING`, and `NEUTRAL_FLOW` from Sampled Taker Activity. The backend now exposes sampled buy volume, sampled sell volume, buy-minus-sell balance, and percentage balance (NULL when total sampled volume is zero).
- Updated System B and the Research Dashboard to display the descriptive metrics without bullish/bearish green/red styling. Existing layout, CSS, snapshot selection, null-preservation, Nearby OI, 24h Volume/OI and OI Migration logic remain unchanged.
- Added backend and dashboard regression coverage for continuous IV values, raw sampled taker metrics, partial-data handling and neutral UI output.
- This is a measurement/presentation correction only. No directional prediction, alert, trade signal, database migration, collector or persistence change is introduced.

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
