# BTC AMT v7.2.2 — Research State Verifier (Python-port translation)

## Status and boundary

This is a **research-only Pine translation of `pine_state_machine.py`**. It is not a copy of the locked production `BTC_AMT_Observer_v7.2.2.pine` source. It must not be described as production Pine runtime parity or as a verified replacement.

No production Pine, dashboard, database, collector, alert, or trading rule is changed by this verifier.

## Settings

- Chart: `BINANCE:BTCUSDT`, 30-minute.
- Cycle reset: 00:00 UTC (05:30 IST).
- TPO row: fixed $50.
- Value area: 70%.
- ATR: Pine `ta.atr(20)` RMA, used for significance and adaptive discovery timeout.
- State logic and defaults: translated from `pine_state_machine.py`.

## How to use

1. Open TradingView on `BINANCE:BTCUSDT`, 30m.
2. Open Pine Editor and paste `BTC_AMT_Python_Port_Research_Verifier.pine`.
3. Save as a separate indicator and add it to the chart. Do not replace or edit the production Observer.
4. Open the Data Window and inspect **Python-Port State Code** on the historical candles in 12–16 November 2026.
5. Compare those values with `pine_state_code` in the replay artifact's `state_machine_comparison.csv`.

The state code plot is deliberately in the Data Window, not on the price pane. The last-bar table is a convenience only; historical inspection uses the Data Window.

## Important limitations

- TradingView's Pine runtime will execute this separate translation. It validates this Pine translation against the Python port only if the historical outputs are compared candle by candle.
- It does **not** independently validate the original production script because the production Pine source was not available in the repository when this verifier was prepared.
- No TradingView export is required for spot checks, but a free-plan user may need to select candles individually in the Data Window. This is not an efficient way to collect all 240 values.
- The Python port and this verifier should be treated as a research pair. Any discrepancies must be investigated before drawing conclusions.
- The verifier requires sufficient chart history before 12 November for the ATR warm-up and the completed 11 November profile to be available. The script resets its AMT cycle at 00:00 UTC.

## Known parity-sensitive details

The Python port uses Pine-style ATR RMA, midpoint POC tie-break, VA expansion with down-first tie handling, and the exact source-order auction state transition chain encoded in `pine_state_machine.py`. Any future edits must update the Python reference, Pine translation, and tests together.
