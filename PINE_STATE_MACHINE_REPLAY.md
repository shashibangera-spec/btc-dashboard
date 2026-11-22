# BTC AMT Observer v7.2.2 — standalone state-machine replay

## Safety boundary

This is an isolated research tool. It does not import or modify the Streamlit dashboard, collector, Supabase schema, alerts, or production state. The standalone engine translates the AMT TPO/profile and auctionState lifecycle in BTC_AMT_Observer_v7.2.2.pine.

The replay compares:
1. The legacy Python state logic from generate_state_journal() in btc_live_dashboard.py on main at commit 2bde6aa4cd900761a1a91132d71368ece678bc2a.
2. The source-derived Pine v7.2.2 port in pine_state_machine.py.

## Important validation distinction

A comparison between the legacy Python engine and this port measures differences between those two Python implementations. It does not prove that the port is identical to TradingView's Pine runtime.

Final runtime-parity validation requires exporting the Pine plot Auction State Code from TradingView for the same BINANCE:BTCUSDT 30m candles and comparing those codes to pine_state_code in the replay output. Until that check is complete, this is a source-derived research candidate, not a verified production replacement.

## Pine defaults represented

- BINANCE:BTCUSDT, 30m completed candles
- Cycle reset: 00:00 UTC / 05:30 IST
- TPO row size: Fixed $50
- Value area: 70%
- POC tie-break: actual max-TPO row nearest profile midpoint
- Development minimum: 3 completed brackets
- Acceptance: 2 consecutive closes outside value, then 2 significant retest bars
- Significance: range > 1.5 × ATR(20)
- Adaptive discovery timeout: round(6 × ATR(20) / 250), clamped to 3–20 bars
- POC persistence: 2 bars; migration threshold: 1 row
- New-area stall: 20 bars
- Failed-discovery confirmation: 2 inside-value bars
- Re-entry persistence: 3 bars

The historical replay treats every candle as completed. Display, OI/PI, EMA diagnostics, poor-extreme annotations, and other independent Pine layers are outside the port's scope.

## Historical input and continuity

The replay window is **12 November 2026 00:00 UTC inclusive through 17 November 2026 00:00 UTC exclusive**.

- Warm-up: 11 November 2026 00:00–23:30 UTC (48 archived candles).
- Target: 12–16 November 2026 (240 completed 30m candles).
- Canonical OHLC source: `public.binance_ohlc_history`, filtered to BINANCE / SPOT / BTCUSDT / 30m.
- Snapshot cross-check: `public.amt_snapshots`, filtered to BINANCE / BTCUSDT / 30m / VALID / `state_output.completed=true`.

The harness reads 288 archived candles (48 warm-up + 240 target) and 240 completed AMT snapshots. Every archive timestamp must exactly match the expected 30m UTC grid. Every target snapshot must have a matching archive candle and its OHLC must match within 0.005. Duplicate timestamps, missing candles, missing snapshots, count changes, or OHLC mismatches stop the replay.

The 11 November 23:30 candle comes from the permanent Binance archive. It is used as the final warm-up bracket, not as a target replay row. The replay does not insert or update any database records and no longer makes a live Binance HTTP request.

The Pine engine receives all 288 candles in chronological order so ATR(20) is warmed before the 12 November reset and the completed 11 November profile becomes the prior-day profile. Only 12–16 November rows are written to the comparison outputs.

## Run

Install repository dependencies and set read-only Supabase credentials.

PowerShell:

```powershell
py -m pip install -r requirements.txt
$env:SUPABASE_URL = "YOUR_SUPABASE_URL"
$env:SUPABASE_ANON_KEY = "YOUR_READ_ONLY_SUPABASE_KEY"
py -m unittest -v test_pine_state_machine.py
py -m pytest -q test_replay_input_validation.py
py replay_pine_state_machine.py --output-dir replay_output
```

The replay is read-only. It reads the archive and snapshot tables only. It does not write to Supabase, query the live Binance API, or change production code or Pine settings.

## Outputs

- state_machine_comparison.csv: every target candle, both state codes/names, OHLC, stored prior value levels, prior/developing profiles, ATR, transitions, and match flag.
- state_machine_divergences.csv: rows where legacy Python and the Pine port disagree.
- state_machine_comparison_summary.json: counts, agreement percentage, state confusion matrix, and caveat about TradingView runtime parity.

Overall agreement is diagnostic only. Rare-state and transition coverage must be reviewed separately; a high aggregate score can be dominated by frequent BUILDING/DEVELOPMENT bars.

## Preserved Pine source asymmetries

The port intentionally preserves the Pine source's exact branch order and asymmetries rather than adding seemingly intuitive exits. In particular, Pine state 15 has no direct close > Prior VAH transition, and state 16 has no direct close < Prior VAL transition. Those states remain active unless a branch present in the source fires. The legacy Python engine has additional opposite-boundary branches in these two states. Any proposed change to the Pine source itself must be a separate, explicit research decision—not silently folded into a parity port.

The legacy comparator also preserves its existing behavior: it has no transitions for states 4, 6, 8, or 12; states 9 and 13 can be entered but have no exit branches in its legacy transition chain.
