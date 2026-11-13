# Supabase Database Schema — Data Dictionary

**Database:** Supabase PostgreSQL (AWS Mumbai)  
**Tables documented:** 5  
**Scope:** Automated Supabase-backed BTC research backend only.

This document describes the tables and fields used by the current AMT, futures and options collectors and dashboard. It is a data dictionary, not a replacement for inspecting the live PostgreSQL catalog. Confirm exact SQL types, defaults, nullability, indexes, constraints and policies against the live Supabase schema before making database migrations.

## Table inventory

| # | Table | Purpose |
|---:|---|---|
| 1 | `amt_snapshots` | Completed 30-minute AMT candles, prior/developing value and state |
| 2 | `futures_oi_snapshots` | Exchange-level and aggregate OI, funding and basis |
| 3 | `options_oi_snapshots` | Raw options-chain contracts and available trade-tape fields |
| 4 | `options_collection_runs` | Run-level collection status and completeness |
| 5 | `options_derived_snapshots` | Expiry-specific derived options metrics and quality status |

## 1. `amt_snapshots`

Stores completed 30-minute BTCUSDT AMT candles, frozen prior-value references, developing-value measurements and state output.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `exchange` | Source exchange; collector writes `BINANCE` |
| `symbol` | Instrument; collector writes `BTCUSDT` |
| `timeframe` | Candle timeframe; collector writes `30` |
| `candle_open_time` | Candle open timestamp |
| `candle_close_time` | Candle close timestamp |
| `open_price`, `high_price`, `low_price`, `close_price` | Candle OHLC prices |
| `volume` | Candle volume |
| `profile_date` | Source session date used for Prior Value |
| `profile_id` | Identifier for the frozen source profile |
| `prior_vah`, `prior_poc`, `prior_val` | Prior-session value area high, point of control and value area low |
| `developing_vah`, `developing_poc`, `developing_val` | Current developing value area high, point of control and value area low |
| `tpo_row_size` | TPO price-row size used by the collector |
| `value_area_percent` | Value-area percentage used by the collector |
| `atr_21` | 21-period ATR |
| `atr_buffer` | Calculated penetration buffer |
| `discovery_above`, `discovery_below` | Upper and lower discovery thresholds |
| `engine_version` | AMT engine version |
| `confirmed_state` | Confirmed AMT state |
| `developing_state` | Developing/candidate AMT state |
| `state_output` | Full JSONB payload from the AMT engine |
| `data_status` | AMT data-quality status |
| `project_echo_code` | Project Echo state code |
| `project_echo_name` | Project Echo descriptive state name |
| `project_echo_version` | Project Echo version |

### Prior profile date convention

`profile_date` and `profile_id` refer to the source session/profile used to establish Prior VAH/POC/VAL. They are not necessarily the date of the current candle. For example, a current-session candle can intentionally reference the previous session's profile ID. This is by design and must not be interpreted as a candle timestamp defect.

### AMT state interpretation

`confirmed_state` and `developing_state` must be read separately. Discovery confirmation uses two consecutive completed 30-minute closes beyond the configured discovery threshold. The first qualifying close can appear in `developing_state` as a candidate while `confirmed_state` retains the last confirmed state.

The collector upserts candles using the conflict key:

`exchange, symbol, timeframe, candle_open_time`

## 2. `futures_oi_snapshots`

Stores exchange-level and aggregate Futures Open Interest, funding and basis observations.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `created_at` | Collector/database record timestamp |
| `exchange` | Source label: BINANCE, BYBIT, OKX, DELTA or AGGREGATE |
| `oi_usd` | OI expressed in USD |
| `funding_rate` | Funding rate as supplied/normalized by collector |
| `basis_pct` | Futures basis percentage |
| `status` | Status payload describing validity, contributor coverage and/or errors |

### Coverage and baseline rules

- An `AGGREGATE` row represents the exchanges contributing valid measurements to that snapshot. It does not guarantee four-exchange coverage.
- Always retain and display contributor coverage when interpreting aggregate OI.
- A 2/4 aggregate and a 4/4 aggregate are not equivalent coverage observations.
- Missing exchange measurements remain NULL; they must not be treated as zero.
- The 30-minute OI delta is valid only when the comparison interval passes the configured 25–35-minute baseline check. Invalid intervals must be rejected/flagged, not presented as a valid 30-minute change.
- OI expansion does not identify the initiating side. OI contraction alone does not prove liquidation or position direction.

The exact internal structure of `status` should be checked against the current collector output; do not assume every exchange writes an identical status payload.

## 3. `options_oi_snapshots`

Stores raw per-contract options-chain observations and selected trade-tape fields. Rows belonging to one collection run share a `snapshot_id`.

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `snapshot_id` | UUID identifying the collection run |
| `collected_at` | Collector timestamp for the stored contract observation |
| `exchange_timestamp` | Exchange/source timestamp where supplied |
| `exchange` | Options venue/source |
| `underlying` | Underlying asset |
| `symbol` | Option contract symbol |
| `expiry_date` | Contract expiry date |
| `option_type` | CALL or PUT |
| `strike_price` | Contract strike |
| `product_id` | Exchange/API product identifier |
| `contract_status` | Contract status supplied by source |
| `oi_contracts` | Open interest in contracts |
| `oi_value_usd` | OI value in USD |
| `volume_24h` | 24-hour contract volume |
| `turnover_usd` | Turnover value in USD |
| `last_price` | Last traded price |
| `mark_price` | Mark price |
| `spot_price` | Underlying spot price associated with the contract snapshot |
| `bid_price`, `ask_price` | Best bid and ask prices |
| `bid_size`, `ask_size` | Best bid and ask sizes |
| `bid_iv`, `ask_iv` | Bid and ask implied volatility |
| `mark_iv` | Mark implied volatility |
| `greeks` | Greeks JSON payload, including gamma where available |
| `taker_buy_volume`, `taker_sell_volume` | Trade-tape taker volumes when collected |
| `data_status` | Per-contract data-quality/status information |
| `raw_payload` | Original API payload where retained |

Not every source field is guaranteed to be populated for every contract. Taker buy/sell volume collection is limited to selected contracts near spot; NULL means unavailable/not collected, not zero volume.

## 4. `options_collection_runs`

Tracks run-level options collection completeness and errors.

| Column | Meaning |
|---|---|
| `snapshot_id` | UUID primary key linking raw and derived rows |
| `started_at` | Collection start timestamp |
| `completed_at` | Collection completion timestamp, when completed |
| `underlying` | Underlying asset collected |
| `expected_contracts` | Expected contract count |
| `received_contracts` | Contracts returned by source |
| `inserted_contracts` | Contracts successfully inserted |
| `failed_expiries` | JSON details for expiries with collection/derivation errors |
| `status` | RUNNING, SUCCESS, PARTIAL or FAILED |
| `error_message` | Run-level error details, when present |

Status meanings:

- `RUNNING`: collection has started and has not yet reached a terminal status.
- `SUCCESS`: expected contracts were inserted and no expiry-level failure was recorded.
- `PARTIAL`: some contracts were inserted, but the run had missing contracts or expiry-level errors.
- `FAILED`: no successful collection completion; inspect the error fields.

A run-level SUCCESS does not mean every individual derived metric is necessarily meaningful in every future implementation; inspect the per-expiry `metric_status` in `options_derived_snapshots` as well.

## 5. `options_derived_snapshots`

Stores calculated options metrics separately for each collection snapshot and expiry. This table preserves expiry-specific provenance and avoids combining metrics from different expiries.

**Collector upsert key:** `snapshot_id, expiry_date`.

| Column | Meaning |
|---|---|
| `snapshot_id` | UUID linking the derived row to `options_collection_runs` and raw contract rows |
| `expiry_date` | Expiry for which metrics were calculated |
| `calculated_at` | Time the derived metrics were calculated |
| `source_collected_at` | Source collection timestamp represented by the contracts used |
| `underlying_spot_price` | Spot price used for calculations |
| `max_pain_strike` | Theoretical max-pain strike |
| `atm_strike` | Selected nearest common call/put strike to spot |
| `atm_straddle_price` | Sum of call and put mark prices at the selected ATM strike |
| `atm_straddle_pct_spot` | ATM straddle expressed as a percentage of spot |
| `call_gex_wall_strike` | Strike with the highest calculated gross Call GEX concentration |
| `call_gex_wall_value` | Gross Call GEX value at that strike |
| `put_gex_wall_strike` | Strike with the highest calculated gross Put GEX concentration |
| `put_gex_wall_value` | Gross Put GEX value at that strike |
| `gex_contract_multiplier` | Contract multiplier used in GEX calculation |
| `local_oi_pcr` | Local put OI divided by local call OI |
| `local_put_oi_contracts` | Put OI contracts inside the configured local band |
| `local_call_oi_contracts` | Call OI contracts inside the configured local band |
| `local_oi_band_pct` | Spot-relative percentage band used for local OI |
| `metric_version` | Derived calculation version |
| `metric_status` | JSONB object containing individual metric quality/status values |

### Metric interpretation

- Max Pain is calculated from the collected open-interest distribution as the settlement price minimizing aggregate intrinsic-value payout under the calculation assumptions. It is not a forecast or guaranteed price magnet.
- ATM straddle is the combined call/put mark premium at the selected common strike. It is an expiry-specific priced-movement measure, not a guaranteed realized range or probability boundary.
- Gross GEX is OI-weighted and unsigned. It does not establish dealer positioning, net gamma or hedging direction.
- Call/Put GEX wall fields identify the largest calculated concentration within the relevant option side; they are not automatically support, resistance or targets.
- Local OI PCR is a ratio of local put contracts to local call contracts in the configured spot-relative band. It is not a directional signal by itself.
- Derived metrics are expiry-specific. Compare like expiries and account for time-to-expiry when conducting research.

### Metric quality

`metric_status` records calculation status separately for the chain, spot, Max Pain, ATM straddle, Call GEX, Put GEX and local OI PCR. Examples include `OK`, incomplete-chain statuses, missing-data statuses and invalid-input statuses. Consumers should inspect each metric's status rather than infer that all fields are valid because one metric is populated.

Derived metrics are not calculated from an incomplete chain as though missing contracts had zero OI. An incomplete chain is marked accordingly and its derived metrics must be treated as unavailable/incomplete.

## Relationships

- `options_collection_runs.snapshot_id` identifies one options collection run.
- `options_oi_snapshots.snapshot_id` links raw contract rows to that run.
- `options_derived_snapshots.snapshot_id` links expiry-specific derived metrics to the same run.
- One run can contain multiple expiries; the derived table therefore has one row per `snapshot_id, expiry_date`.
- AMT and Futures observations are associated by timestamps for research; they are not joined by assuming that their latest collection times are identical.

## Timestamp provenance and strict as-of rule

Timestamps represent different events and must not be conflated:

- **AMT `candle_open_time` / `candle_close_time`:** the market interval represented by the completed 30-minute candle.
- **Futures `created_at`:** collector/database record time. Any source timestamp embedded in status or payload should be retained and preferred for source-time alignment when reliable.
- **Options `exchange_timestamp`:** source/exchange time where supplied.
- **Options `collected_at`:** collector time for raw contract records.
- **Options derived `source_collected_at`:** source collection time represented by the contracts used in the derived calculation.
- **Options derived `calculated_at`:** time the derived calculation was performed.
- **Options run `started_at` / `completed_at`:** collection-run boundaries.

### Strict as-of rule for historical research

A Futures or Options observation may be treated as contemporaneous context for an AMT candle only if its reliable source/observation timestamp is **at or before that AMT candle's close time**.

If the available observation was sourced or collected after the AMT candle closed, it is a subsequent development. It must not be backfilled as information available at the earlier event time.

When source timestamps are absent, ambiguous, or not normalized sufficiently to establish ordering, mark the match as uncertain/unavailable. Do not silently substitute a later collection timestamp or the latest available row and call it contemporaneous.

For event studies, establish the event timestamp first, match only eligible as-of observations, and measure outcomes strictly after the event. This prevents lookahead bias and retrospective explanation.

## Data quality conventions

- SQL NULL means missing, unavailable or not collected; it is not numeric zero.
- Do not replace missing exchange contributions, option volumes, Greeks or derived metrics with zero.
- Preserve partial collection status and per-metric status.
- Display futures contributor coverage with aggregate OI.
- Keep options metrics separated by expiry.
- Preserve source timestamps and collection-run identifiers when analysing historical observations.

## Scope exclusions

This data dictionary covers the automated Supabase-backed backend only. The standalone manual Live Terminal (System B) and legacy research prototype are separate and intentionally excluded.
