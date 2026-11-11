# Supabase Database Schema (Data Dictionary)

Database: PostgreSQL (AWS Mumbai)
Tables: 4

## 1. `amt_snapshots`
Stores the 30-minute AMT candles and state transitions.
*   `id` (bigint): Primary Key
*   `exchange` (text): Default 'BINANCE'
*   `candle_open_time` (timestamptz)
*   `open_price`, `high_price`, `low_price`, `close_price`, `volume` (numeric)
*   `atr_21` (numeric): 21-period ATR
*   `atr_buffer` (numeric): The calculated penetration buffer
*   `prior_vah`, `prior_poc`, `prior_val` (numeric): Previous day value area
*   `confirmed_state` (text): e.g., IN_VALUE, DISCOVERY_CONFIRMED_ABOVE
*   `state_output` (jsonb): Full JSON payload from the engine

## 2. `futures_oi_snapshots`
Stores OI, Funding, and Basis for the 4 exchanges.
*   `id` (bigint): Primary Key
*   `created_at` (timestamptz)
*   `exchange` (text): BINANCE, BYBIT, OKX, DELTA, AGGREGATE
*   `oi_usd` (double precision)
*   `funding_rate` (double precision)
*   `basis_pct` (double precision)
*   `status` (text): JSON string containing contributor coverage and errors

## 3. `options_oi_snapshots`
Stores the raw options chain and trade tape.
*   `id` (bigint): Primary Key
*   `snapshot_id` (uuid): Ties all strikes in a single run together
*   `collected_at` (timestamptz)
*   `expiry_date` (date)
*   `option_type` (text): CALL or PUT
*   `strike_price` (numeric)
*   `product_id` (bigint)
*   `oi_contracts`, `oi_value_usd`, `volume_24h` (numeric)
*   `mark_iv` (numeric)
*   `taker_buy_volume`, `taker_sell_volume` (numeric): Captured by Filter 5
*   `raw_payload` (jsonb): The original API response for auditing

## 4. `options_collection_runs`
Tracks the success/failure of the options collector.
*   `snapshot_id` (uuid): Primary Key
*   `started_at`, `completed_at` (timestamptz)
*   `status` (text): RUNNING, SUCCESS, PARTIAL, FAILED
*   `inserted_contracts` (integer)
