# Supabase Database Schema — Data Dictionary

**Database:** Supabase PostgreSQL  
**Tables documented:** 8  
**Scope:** Automated Supabase-backed BTC research and observation infrastructure.

This document is the project data dictionary. Before making a migration, confirm exact SQL types, defaults, nullability, indexes, constraints and policies against the live Supabase catalog.

---

# Security model

Row Level Security (RLS) is part of the production database contract.

- **Streamlit/System B:** uses `SUPABASE_ANON_KEY` and only receives explicitly permitted read access.
- **Trusted backend workflows:** use `SUPABASE_SERVICE_ROLE_KEY` for collection, signal detection and alert operations.
- **Service-role credentials must never be exposed in browser or client-side code.**

The two event tables have intentionally different roles:

- `system_signal_journal` is the research record of significant system events.
- `telegram_alert_events` is the operational notification outbox and delivery audit.

---

# Table inventory

| # | Table | Purpose |
|---:|---|---|
| 1 | `amt_snapshots` | Completed 30-minute AMT candles, Prior/Developing Value and states |
| 2 | `futures_oi_snapshots` | Exchange-level and aggregate OI, funding and basis |
| 3 | `options_oi_snapshots` | Raw options-chain contracts and available trade-tape fields |
| 4 | `options_collection_runs` | Run-level options collection status and completeness |
| 5 | `options_derived_snapshots` | Expiry-specific derived options metrics and quality status |
| 6 | `telegram_alert_events` | Telegram notification outbox, deduplication and delivery audit |
| 7 | `system_signal_journal` | Automated research journal of significant system events |
| 8 | `signal_reaction_analysis` | Versioned, non-directional post-event market reaction measurements |

---

# 1. `amt_snapshots`

Stores completed 30-minute BTCUSDT AMT candles, frozen Prior Value references, Developing Value measurements and state output.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `exchange` | Source exchange; collector writes `BINANCE` |
| `symbol` | Instrument; collector writes `BTCUSDT` |
| `timeframe` | Candle timeframe; collector writes `30` |
| `candle_open_time` | Candle open timestamp |
| `candle_close_time` | Completed candle close timestamp |
| `open_price`, `high_price`, `low_price`, `close_price` | Candle OHLC prices |
| `volume` | Candle volume |
| `profile_date` | Source session date used for Prior Value |
| `profile_id` | Identifier for the frozen source profile |
| `prior_vah`, `prior_poc`, `prior_val` | Prior-session Value Area references |
| `developing_vah`, `developing_poc`, `developing_val` | Current Developing Value references |
| `tpo_row_size` | TPO price-row size |
| `value_area_percent` | Value-area percentage |
| `atr_21` | 21-period ATR |
| `atr_buffer` | Calculated penetration buffer |
| `discovery_above`, `discovery_below` | Discovery thresholds |
| `engine_version` | AMT engine version |
| `confirmed_state` | Confirmed AMT state |
| `developing_state` | Developing/candidate state |
| `state_output` | Full AMT engine JSON payload |
| `data_status` | AMT data-quality status |
| `project_echo_code`, `project_echo_name`, `project_echo_version` | Project Echo annotations |

The collector conflict/upsert key is:

`exchange, symbol, timeframe, candle_open_time`

## State interpretation

`confirmed_state` and `developing_state` must be interpreted separately.

A candidate/developing state is not automatically a confirmed state. AMT confirmation uses completed-candle logic; in-progress candles must not be used as completed confirmation.

## Prior profile convention

`profile_date` and `profile_id` identify the source session/profile used to establish Prior Value. They are not necessarily the date of the current candle.

---

# 2. `futures_oi_snapshots`

Stores exchange-level and aggregate Futures Open Interest, funding and basis observations.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `created_at` | Collector/database record timestamp |
| `exchange` | BINANCE, BYBIT, OKX, DELTA or AGGREGATE |
| `oi_usd` | OI expressed in USD |
| `funding_rate` | Funding rate supplied/normalized by collector |
| `basis_pct` | Futures basis percentage |
| `status` | Status payload including validity/coverage information |

## Coverage and baseline rules

- An `AGGREGATE` row represents valid contributors available for that observation.
- Contributor coverage must be considered when comparing aggregate OI.
- Missing measurements remain `NULL`, not zero.
- The valid 30-minute comparison interval is 25–35 minutes.
- OI expansion/contraction does not independently identify which side initiated the exposure.

Reliable source/observation time should be preferred over `created_at` when enforcing historical as-of alignment.

---

# 3. `options_oi_snapshots`

Stores raw per-contract options-chain observations. Rows belonging to one collection run share a `snapshot_id`.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `snapshot_id` | UUID identifying the collection run |
| `collected_at` | Collector timestamp |
| `exchange_timestamp` | Exchange/source timestamp where supplied |
| `exchange` | Options venue/source |
| `underlying` | Underlying asset |
| `symbol` | Option contract symbol |
| `expiry_date` | Contract expiry |
| `option_type` | CALL or PUT |
| `strike_price` | Contract strike |
| `product_id` | Source/API product identifier |
| `contract_status` | Contract status supplied by source |
| `oi_contracts` | Open interest in contracts |
| `oi_value_usd` | OI value in USD |
| `volume_24h` | 24-hour contract volume |
| `turnover_usd` | Turnover value |
| `last_price`, `mark_price` | Last and mark prices |
| `spot_price` | Underlying spot associated with the snapshot |
| `bid_price`, `ask_price` | Best bid and ask |
| `bid_size`, `ask_size` | Best bid and ask sizes |
| `bid_iv`, `ask_iv`, `mark_iv` | Implied-volatility fields |
| `greeks` | Greeks JSON payload, including gamma where available |
| `taker_buy_volume`, `taker_sell_volume` | Available trade-tape fields |
| `data_status` | Per-contract data-quality/status information |
| `raw_payload` | Retained original API payload |

Not every source field is populated for every contract. `NULL` means unavailable/not collected, not zero.

---

# 4. `options_collection_runs`

Tracks run-level options collection completeness and errors.

| Column | Meaning |
|---|---|
| `snapshot_id` | UUID primary key linking raw and derived rows |
| `started_at` | Collection start |
| `completed_at` | Collection completion |
| `underlying` | Underlying asset |
| `expected_contracts` | Expected contract count |
| `received_contracts` | Contracts returned by source |
| `inserted_contracts` | Successfully inserted contracts |
| `failed_expiries` | JSON details for expiry-level failures |
| `status` | RUNNING, SUCCESS, PARTIAL or FAILED |
| `error_message` | Run-level error details |

Status meanings:

- `RUNNING`: collection started and has not reached a terminal state.
- `SUCCESS`: collection completed successfully under the collector's completeness rules.
- `PARTIAL`: some data was inserted but completeness/errors failed the full-success criteria.
- `FAILED`: no successful collection completion.

---

# 5. `options_derived_snapshots`

Stores expiry-specific derived options metrics.

**Collector upsert key:** `snapshot_id, expiry_date`

| Column | Meaning |
|---|---|
| `snapshot_id` | UUID linking to the collection run |
| `expiry_date` | Expiry represented by this derived row |
| `calculated_at` | Derived-calculation timestamp |
| `source_collected_at` | Source collection time represented by the contracts used |
| `underlying_spot_price` | Spot price used in calculations |
| `max_pain_strike` | Theoretical Max Pain strike |
| `atm_strike` | Selected nearest common call/put strike |
| `atm_straddle_price` | Combined ATM call/put mark premium |
| `atm_straddle_pct_spot` | ATM straddle as percentage of spot |
| `call_gex_wall_strike`, `call_gex_wall_value` | Gross Call GEX concentration |
| `put_gex_wall_strike`, `put_gex_wall_value` | Gross Put GEX concentration |
| `gex_contract_multiplier` | Multiplier used in the calculation |
| `local_oi_pcr` | Local put OI / local call OI |
| `local_put_oi_contracts`, `local_call_oi_contracts` | Local OI quantities |
| `local_oi_band_pct` | Spot-relative local OI band |
| `metric_version` | Calculation version |
| `metric_status` | Per-metric quality/status JSON |

## Interpretation

- Max Pain is theoretical, not a forecast.
- ATM straddle is expiry-specific and not a guaranteed realized range.
- Gross GEX is unsigned and does not establish dealer positioning or hedging direction.
- GEX walls are concentrations, not automatic support/resistance/targets.
- Derived metrics remain expiry-specific.

---

# 6. `telegram_alert_events`

The Telegram operational outbox. It exists to deduplicate notification events, track delivery and preserve an audit trail.

| Column | Meaning |
|---|---|
| `event_key` | Primary deduplication identifier |
| `event_type` | Alert category |
| `event_time` | Underlying market-event timestamp |
| `payload` | JSON context associated with the alert |
| `delivery_status` | PENDING, SENT or FAILED |
| `telegram_message_id` | Telegram message ID after successful delivery |
| `attempt_count` | Delivery attempts |
| `last_error` | Most recent delivery error |
| `created_at` | Outbox record creation time |
| `sent_at` | Successful delivery timestamp |

### `event_type` CHECK constraint

The `event_type` column is restricted by `telegram_alert_events_event_type_check` to the following exact values:

- `AMT_STATE_CHANGE` — locally evaluated confirmed AMT state transition.
- `AMT_PROFILE_INITIALIZATION` — first confirmed state recorded for a newly detected AMT profile.
- `GEX_WALL_BREACH` — detector-confirmed GEX wall breach read from `system_signal_journal`.
- `OI_SHOCK` — locally evaluated aggregate Futures OI context event.
- `TEST_ALERT` — explicit Telegram connectivity-test event; not a live market event.

The same allowlist is enforced in `telegram_alerts.py` by `ALLOWED_EVENT_TYPES`; unsupported values are rejected before a database write. Keep the Python allowlist and this CHECK constraint synchronized. The migration adding `AMT_PROFILE_INITIALIZATION` and `TEST_ALERT` is `migrations/20261117_allow_amt_profile_initialization_and_test_alert_event_types.sql`.

## Operational semantics

- RLS is enabled.
- Trusted backend workflows use the service-role credential for operational access.
- `event_key` prevents the same logical event from being enqueued repeatedly.
- `PENDING` is durably upserted before a Telegram network request; `SENT` or `FAILED` is written only after the send attempt returns.
- A previously confirmed `SENT` event with a positive Telegram message ID is not resent. If the initial `PENDING` write fails, Telegram delivery is aborted.
- The outbox write and Telegram API cannot share one database transaction. A crash after Telegram accepts a message but before the `SENT` update can leave a `PENDING` row and may still result in a duplicate on later retry; this is a durable outbox lifecycle, not exactly-once delivery.

---

# 7. `system_signal_journal`

The canonical automated research journal for significant system events detected by System A.

This table is intentionally separate from `telegram_alert_events`. A research event is part of the market-observation history; a Telegram row is part of notification delivery state.

## Core schema

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `event_timestamp` | Timestamp of the confirmed underlying market event |
| `event_type` | Event taxonomy identifier |
| `event_key` | Event-specific deduplication identity |
| `severity` | Current severity classification; initially `INFO` |
| `price` | Price snapshot at the event |
| `amt_state` | Confirmed AMT state at the event |
| `dev_poc`, `dev_vah`, `dev_val` | Developing Value snapshot |
| `call_wall`, `put_wall` | Eligible GEX wall context available as-of the event |
| `oi_usd` | Eligible aggregate Futures OI context available as-of the event |
| `event_details` | JSON event-specific evidence/context |
| `detection_version` | Signal-detector version |
| `detected_at` | Journal insertion/detection timestamp; defaults to `now()` when the detector inserts the row |

## GEX_WALL_BREACH event_details contract

For `event_type = 'GEX_WALL_BREACH'`, `event_details` is the detector-produced evidence payload consumed by the Telegram notifier. All twelve keys below are required; the notifier fails closed for a missing, null or malformed value and skips that row without suppressing unrelated AMT/OI notifications.

| JSON key | Required meaning and validation |
|---|---|
| `wall_side` | Non-empty enum: `CALL` or `PUT` |
| `wall_identity` | Non-empty stable wall identity (side, expiry and level) |
| `expiry_date` | Valid date in `YYYY-MM-DD` format |
| `breached_level` | Finite numeric breached wall price; authoritative message wall value |
| `first_close_timestamp` | Parseable timestamp for the first completed close |
| `first_close_price` | Finite numeric first-close price |
| `confirmation_close_timestamp` | Parseable timestamp for the second consecutive completed close |
| `confirmation_close_price` | Finite numeric confirmation close; authoritative message confirmation price |
| `wall_source_snapshot_id` | Non-empty identifier of the options snapshot used by the detector |
| `wall_source_collected_at` | Parseable timestamp of that wall-source observation |
| `consecutive_confirmation` | Must be boolean `true` |
| `rearm_rule` | Non-empty description of the inside-walls re-arm requirement |

**Source of truth:** the Telegram confirmation price is `event_details.confirmation_close_price`, not the journal's top-level `price`. The displayed breached wall is `event_details.breached_level`, not the top-level `call_wall` or `put_wall`. **Timestamp provenance:** `event_timestamp` is the timestamp of the underlying market event (the confirmed market/candle event) and is used as the event time shown in the alert. `detected_at` is the journal insertion/detection timestamp, populated by the database default `now()` when the detector writes the row; it records when the system detected and persisted the event. These timestamps serve different purposes and must not be substituted for one another. The GEX deployment cutover uses `detected_at`, not `event_timestamp`.

The journal row `id` supplies the GEX outbox identity suffix; `event_timestamp` supplies market event time; `detected_at` controls deployment cutover eligibility.

**GEX notifier cutover:** only journal rows with `detected_at >= GEX_ALERT_CUTOVER_UTC` are eligible. This means events inserted into the journal after deployment are eligible even if their underlying `event_timestamp` is slightly earlier. The placeholder in the review branch must be replaced with the UTC timestamp of the merge-to-main commit before merge. The notifier does not apply the old options-snapshot age/freshness window: the signal detector has already validated and recorded the two-candle confirmation.

**Outbox identity:** `SYSTEM_SIGNAL_GEX_WALL_BREACH_{journal_id}`. A confirmed `SENT` row with a valid positive Telegram message ID suppresses subsequent delivery attempts. `FAILED` remains retryable on the next scheduled Telegram workflow run. AMT/OI keep their existing locally generated keys and evaluation paths.

## Event taxonomy

### `AMT_STATE_CHANGE`

Logged when consecutive completed AMT candles have different `confirmed_state` values.

### `GEX_WALL_BREACH`

Logged after two consecutive completed 30-minute closes beyond the same wall identity. A valid inside-walls close is required before re-arming a later breach cycle.

### `OI_SHOCK`

Logged when aggregate OI changes by more than 3.0% across a valid 25–35-minute interval with identical exchange coverage. The detector event-specific `event_details` payload additionally records:

| JSON key | Meaning |
|---|---|
| `price_location` | `ABOVE_VAH`, `BELOW_VAL`, `INSIDE_VALUE`, or `LOCATION_UNAVAILABLE`; calculated from the latest valid completed AMT candle at or before the OI observation, using that candle close and its Prior VAH/VAL |
| `amt_state` | The same candle's `confirmed_state`, or `AMT_STATE_UNAVAILABLE` when no valid state is available |
| `amt_candle_close_time` | Close timestamp of the AMT candle used, or `NULL` when no candle was available |
| `prior_vah`, `prior_val` | Prior Value boundaries used for location classification, or `NULL` when unavailable |

An unavailable AMT lookup or invalid/missing Prior Value boundary does not suppress an otherwise valid OI shock. `LOCATION_UNAVAILABLE` is explicit; the system does not infer a location from developing values or a later candle. These fields are descriptive context, not directional signals. No directional OI trading rule has been validated. The Telegram OI evaluator independently adds the same two display fields to its outbox payload and message; its existing threshold, timestamp, interval and contributor rules remain unchanged.

### `VALUE_MIGRATION`

Logged when Developing POC moves by more than $150 between consecutive completed AMT candles.

## Deduplication contract

The journal enforces uniqueness on:

`event_type, event_timestamp, event_key`

The detector uses an upsert with duplicate protection against this same identity.

## RLS access model

- **RLS is enabled.**
- **Anon:** permitted read access for the research dashboard/sidebar.
- **Authenticated:** no broader access is implied by the journal design unless explicitly granted by a policy.
- **Service role:** trusted backend writes through the GitHub Actions signal detector.

Do not expose service-role credentials to Streamlit or browser clients.

## Research purpose

The journal is designed for later event studies, including potential Market Reaction Analysis such as:

- price after 30 minutes
- price after 1 hour
- price after 4 hours
- maximum favorable excursion (MFE)
- maximum adverse excursion (MAE)

Those outcomes are stored in the separate `signal_reaction_analysis` table. They are deliberately not written back into this immutable event journal.

---

# Relationships

- `options_collection_runs.snapshot_id` identifies one options collection run.
- `options_oi_snapshots.snapshot_id` links raw contracts to that run.
- `options_derived_snapshots.snapshot_id` links expiry-specific derived metrics to the same run.
- One options run can contain multiple expiries.
- AMT, Futures and Options observations are associated for research by timestamp/provenance rather than by assuming collection timestamps are identical.
- `system_signal_journal` stores an event snapshot and event-specific details; it is not a foreign-key replacement for the underlying historical source tables.
- `telegram_alert_events` records notification lifecycle independently from the research journal.

---

# Timestamp provenance and strict as-of rule

Different timestamps represent different events:

- **AMT `candle_open_time` / `candle_close_time`:** the represented completed market interval.
- **Futures `created_at`:** collector/database insertion time; reliable source observation time should be preferred when available.
- **Options `exchange_timestamp`:** source time where supplied.
- **Options `collected_at`:** raw collector observation time.
- **Options derived `source_collected_at`:** source collection time represented by the contracts.
- **Options derived `calculated_at`:** calculation time.
- **Options run timestamps:** collection boundaries.
- **Signal `event_timestamp`:** timestamp of the confirmed underlying event, not merely detector execution time.

## Strict historical as-of rule

A Futures or Options observation may be treated as contemporaneous context for an AMT event only when its reliable observation timestamp is at or before that event time.

Later observations are subsequent information and must not be backfilled into the earlier event explanation.

---

# Data-quality conventions

- SQL `NULL` means missing/unavailable/not collected; it is not numeric zero.
- Do not replace missing exchange contributions, options volumes, Greeks or derived metrics with zero.
- Preserve partial collection status and per-metric status.
- Preserve Futures contributor coverage with aggregate OI.
- Keep Options metrics separated by expiry.
- Preserve source timestamps and collection identifiers for historical analysis.
- Signal detection should fail closed when required freshness, coverage or validity conditions are not met.

---

# Scope exclusions

This document covers the automated Supabase-backed backend and its event infrastructure. System B's visual presentation layer is documented at the architecture level in `README.md`; CSS/HTML implementation details are not a database contract.


---

# 8. `signal_reaction_analysis`

Stores versioned, non-directional market reaction measurements linked to the immutable `system_signal_journal`.

**Foreign key:** `signal_id BIGINT REFERENCES system_signal_journal(id) ON DELETE RESTRICT`  
**Idempotency key:** `signal_id, analysis_version`

| Column | Meaning |
|---|---|
| `id` | UUID primary key |
| `signal_id` | BIGINT foreign key to the original signal |
| `analysis_version` | Calculation version, current `reaction-v1.1`; prior `reaction-v1.0` identifies the Binance Spot methodology |
| `anchor_price` | Original signal journal price |
| `window_start`, `window_end` | First eligible candle open and event-relative four-hour deadline |
| `price_after_30m`, `price_after_1h`, `price_after_2h`, `price_after_4h` | Completed candle close at each horizon |
| `window_high`, `window_low` | Highest high and lowest low across the eight-candle observation |
| `max_up_excursion_usd`, `max_down_excursion_usd` | Neutral upside and downside movement from anchor in USD |
| `max_up_excursion_pct`, `max_down_excursion_pct` | Same movements as percentages of anchor |
| `candles_expected` | Required candle count (8) |
| `candles_received` | Valid contiguous candle count returned |
| `analysis_status` | `COMPLETE`, `INCOMPLETE` or `ERROR` |
| `data_source` | Market data source identifier |
| `error_message` | Data-quality or processing explanation |
| `analyzed_at`, `created_at` | Analysis and row creation timestamps |

## Calculation and eligibility contract

- Anchor price is `system_signal_journal.price`; the journal itself is never updated.
- Current source is Delta Exchange India `BTCUSD` perpetual, interval `30m`, via `/v2/history/candles` with `resolution=30m` and Unix-second `start`/`end` parameters.
- Delta response candle fields are normalized to the analyzer's `open_time_ms`, `open`, `high`, `low`, `close`, and `close_time_ms` representation.
- Delta `time` is treated as the candle open time; eight bars are requested in one four-hour range (well below the documented 2,000-candle response cap, so pagination is unnecessary).
- The journal anchor comes from the BTCUSDT AMT feed while the current reaction bars are BTCUSD perpetual. Venue/contract basis differences are a measurement limitation and must be disclosed in downstream statistical evaluation.
- `reaction-v1.0` refers to the original Binance Spot BTCUSDT method; `reaction-v1.1` is the Delta BTCUSD perpetual method. Do not pool versions without an explicit methodology/basis review.
- Eligible candles must open strictly after `event_timestamp`, be fully closed, and close no later than `event_timestamp + 4 hours`.
- Exactly eight contiguous candles are required. The event candle is excluded.
- Horizon prices use the closes of candles 1, 2, 4 and 8; a complete eight-candle window therefore requires the event to align with the 30-minute close boundary.
- Upside excursion is `max(0, window_high - anchor_price)`.
- Downside excursion is `min(0, window_low - anchor_price)`.
- Percent excursions divide those signed USD movements by anchor price and multiply by 100.
- These are neutral movements, not directional MFE/MAE, realized P&L or executable trade returns.
- Incomplete windows are recorded as terminal `INCOMPLETE` results for that immutable historical event/window and analysis version. They are excluded from completed observation samples. Transient Delta API/database failures are not persisted as terminal rows and remain retryable.
- A new calculation methodology must use a new `analysis_version`.

## Access model

- RLS is enabled.
- `anon` has SELECT access for read-only research display.
- `service_role` has backend management access; no browser/client write access is granted.
- The analyzer uses `SUPABASE_SERVICE_ROLE_KEY` only in GitHub Actions.
- The original signal journal remains the immutable event record; this table stores subsequent outcomes separately.


## Reaction sample independence

A single completed AMT candle can create multiple journal records with the same `event_timestamp` and anchor price (for example, `AMT_STATE_CHANGE` and `VALUE_MIGRATION`). Their reaction rows may therefore represent the same market path. Statistical evaluation must report event-type counts separately from unique event-time clusters and must not treat same-candle events as independent observations.


---

# Options research filter backend contract

**Implementation:** `options_filters.py` (read-only, on-demand; no database migration).

## Canonical selection

- Select the latest `options_collection_runs` row with `status = SUCCESS`, ordered by `completed_at DESC`.
- Pin its `snapshot_id`; select the nearest future expiry whose matching `options_derived_snapshots` row reports `metric_status.chain = OK`.
- Read raw contracts only for that exact `snapshot_id` and `expiry_date`. Validate snapshot/expiry identity, unique contract identities, both option sides, finite strikes and a consistent positive raw `spot_price`.
- A failed latest-run/expiry/raw-chain validation fails closed. Do not silently substitute another run or expiry.
- The nearest-five strike calculations use the pinned raw snapshot's `spot_price`.

## Missingness and filter eligibility

- Pandas aggregation must preserve all-null groups as `NULL/NaN`; missing observations are not observed zero.
- Each filter checks the required fields it consumes. One filter's missing data or exception must not suppress the other four.
- Nearby OI, IV Skew and 24h Volume / OI use the five nearest strikes. OI Migration uses the complete selected expiry chain. Taker Activity is a sampled nearest-strike measurement and must not be described as complete market order flow.
- Volume/OI uses `volume_24h` and `oi_contracts`; it does not use `turnover_usd`.

## OI migration baseline

- Compare only with the immediately preceding eligible `SUCCESS` run for the same expiry.
- Require a valid raw chain and exactly matching contract identity set; missing OI coverage is not classified as stable.
- Results expose the previous snapshot ID, previous completion time and elapsed seconds.
- No new persistence, collector, scheduled workflow, or schema object is introduced.

## IV Skew and Sampled Taker Activity result contract

These are calculated from the same pinned snapshot and nearest-five strike sample used by the existing backend. No database schema change or new persistence is introduced.

- **IV Skew:** `metric = CALL_IV_PREMIUM`; `call_iv_premium_pct = ((average_call_iv - average_put_iv) / average_put_iv) * 100`. It remains continuous; there is no ±5% cutoff and no bullish/bearish/neutral classification. Both option sides must have usable IV observations and average Put IV must be nonzero.
- **Sampled Taker Activity:** `sampled_buy_volume` and `sampled_sell_volume` are the sums of the raw sampled contract fields over the selected nearest-five strikes. `sampled_volume_balance = sampled_buy_volume - sampled_sell_volume`. `sampled_volume_balance_pct` is balance divided by total sampled volume, expressed as a percentage, and is `NULL` when total volume is zero.
- Missing buy/sell observations continue to produce `PARTIAL`; missingness is not replaced with zero and no directional class is emitted.
- These fields describe the sampled options contracts only. They are not a measure of complete BTC spot/perpetual order flow and do not generate directional alerts.
