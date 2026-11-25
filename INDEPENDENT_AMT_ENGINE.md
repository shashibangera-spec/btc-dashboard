# Independent BTC AMT Research Engine — v1.0

## Scope
A read-only research replay that reads raw Binance BTCUSDT spot 30-minute OHLCV candles from Supabase `public.binance_ohlc_history`. It does not read Pine outputs or `amt_snapshots` as calculation inputs, and it does not write to Supabase.

## Session and data rules
- Auction session: 05:30 IST to 05:30 IST (48 x 30-minute candles).
- Only completed candles are accepted.
- Duplicate timestamps and invalid OHLC ranges are rejected.
- A missing 30-minute candle breaks consecutive confirmation counters.
- A prior profile is eligible only when the previous session contains exactly 48 contiguous candles with the expected session boundaries.
- Partial current sessions may be replayed, but no state is produced without a valid prior profile.

## Profile calculation
- Fixed row size: $50.
- Value area: 70% of total TPO count.
- Each 30-minute candle contributes one TPO to every price row intersected by its high-low range.
- POC ties resolve to the row nearest the midpoint of the profile's row-index span.
- Value area expands outward from POC by the larger adjacent TPO count; equal adjacent counts are handled in the Pine-compatible down-first order.
- Developing POC/VAH/VAL are recalculated using candles from the start of the current session through the current completed candle.

**Important limitation:** this is an OHLC-derived TPO approximation, not tick-by-tick or trade-by-trade Market Profile data. A candle wick crossing several rows contributes once to each row even though the candle does not reveal the time spent in each row.

## Price behaviour
- Discovery candidate: one completed close beyond prior VAH/VAL by the buffer.
- Discovery confirmed: two consecutive completed closes beyond the buffered boundary.
- Buffer: max($50, 0.5 x Wilder ATR(21)).
- A non-qualifying close resets the candidate sequence.
- Re-entry candidate: first completed close back inside frozen prior value after confirmed discovery.
- Re-entry confirmed: second consecutive completed close inside prior value.
- Price outside the unbuffered prior value, consecutive outside closes, and boundary retests are exposed separately from discovery state.

## Value development
Value development is a separate classification from price behaviour:
- Fully separated above/below.
- Broad migration up/down (POC, VAH and VAL all move at least one row in the same direction).
- POC migration up/down.
- Partial migration up/down.
- Overlapping value.

Price discovery is not treated as proof that new value has developed.

## Balance evidence
Balance evidence is a separate observation, not a replacement for price state:
- Track changes in the side of developing POC relative to frozen prior POC.
- Two or more side crossings in a rolling five-valid-candle window, while developing value overlaps prior value, sets `balance_evidence=true`.
- Boundary retest evidence is also reported separately; it is not silently converted into acceptance.

## Validation completed so far
- Raw Binance archive checked: 1,189 candles from 1–25 Nov 2026.
- 24 complete sessions and one partial session (37 candles).
- No missing 30-minute intervals in the archive.
- Independent profile calculation matched stored prior VAH/POC/VAL on all 708 comparable AMT snapshot rows.
- Developing VAH/POC/VAL matched all 661 rows where the existing snapshot actually contains values. 47 other snapshots have null developing values despite `data_status=VALID`; the independent replay calculates these from raw candles.
- These are profile arithmetic/data-parity checks, not proof that the state classifications are profitable or ready for trading.

## Safety and release
- Read-only Supabase credential required: `SUPABASE_READ_ONLY_KEY`, or an `SUPABASE_ANON_KEY` with SELECT permission.
- No service-role credential is required or used by this module.
- This branch is research-only. Do not connect it to the production dashboard, collector, alerts, Playbook or trading workflow until state-machine validation and explicit release approval.
