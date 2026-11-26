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
- `discovery_event_start=true` only on the candle where the internal confirmed side changes from unset to confirmed. Repeated `DISCOVERY_CONFIRMED_*` labels after a temporary re-entry candidate do not count as a new episode.
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
- Boundary retest evidence is reported separately and is not silently converted into acceptance. It requires the preceding completed candle to have closed outside the same unbuffered prior-value boundary, the current candle to test back through that boundary, and the current close to remain outside. A gap invalidates the retest sequence.
- This balance heuristic is not yet historically validated as a reliable balance/rotation classifier. Synthetic tests now verify that two qualifying POC-side crossings can trigger it while price remains inside prior value, and that a missing candle resets the crossing window. It did not trigger in the historical sample.

## Historical data validation — refreshed 26 Nov 2026
Read-only checks against Supabase were repeated using the current archive:
- Raw Binance spot BTCUSDT 30m archive: **1,212 candles** (1 Nov through 26 Nov 2026), covering only **26 UTC dates**. The `session_ohlc_history` table contains session aggregates, not a substitute 30m raw-candle history. The current Supabase raw archive therefore cannot support a longer direct replay yet; broader validation requires continued collection or a separately identified public Binance historical fixture.
- 1–25 Nov: **25 complete sessions**, each with 48 consecutive candles.
- 26 Nov: **12 archived completed candles** at the time of the latest read-only query (00:00–05:30 UTC).
- No missing or irregular 30-minute intervals were found in the raw archive.
- Independent $50 / 70% profile calculation matched stored prior VAH/POC/VAL on **729 of 729 comparable snapshot rows**.
- Developing VAH/POC/VAL matched **682 of 682 comparable populated rows**.
- **47** AMT snapshot rows have null developing-value fields despite `data_status=VALID`.
- A refreshed read-only join of all 732 AMT snapshots from 1 Nov onward found **one unmatched snapshot**: 26 Nov 06:00 UTC. The earlier 05:00 and 05:30 UTC snapshots are now present in the raw OHLC archive. The raw archive currently ends at 05:30 UTC, while the latest AMT snapshot is 06:00 UTC (collected at 06:31:26 UTC). This is a one-candle archive-coverage lag; do not substitute AMT snapshot OHLC into the independent input.

These results establish profile arithmetic parity for the comparable rows only. They do not establish state-machine accuracy, predictive value, profitability, or tick-level Market Profile equivalence. The historical archive contained no missing 30-minute intervals, so gap-reset behaviour is covered by synthetic tests rather than a naturally occurring historical gap.

## Direct Python historical replay — offline fixture
A read-only fixture containing the archived raw Binance BTCUSDT spot 30m OHLC candles was added under `tests/fixtures/`. GitHub Actions executed the actual Python engine against all 1,212 records; no live database connection or database write was used in the test.

- The fixture covers 1–26 Nov 2026 and uses a fixed as-of time of 26 Nov 07:00 UTC so all 1,212 archived candles are completed.
- The Python engine produced **1,164 output bars** across 25 complete prior sessions plus the partial 26 Nov session; no natural data gaps were present.
- It identified **30 distinct discovery-confirmation episodes**: 6 upside and 24 downside. Episode counting uses `discovery_event_start`, not repeated per-candle confirmed-state labels.
- At confirmation, **7/30** episodes had fully separated developing value. Reviewing the remainder of each episode window, **12/30** achieved same-side value separation at some point (7 already separated at confirmation and 5 later); **18/30** did not achieve same-side separation before the next episode or session boundary. This is a descriptive value-development check, not a quality or profitability score.
- There were 17 confirmed re-entry transitions and 53 boundary-retest-labelled candles. The latter is a candle count, not a count of unique retest sequences.
- The balance heuristic did not trigger. The maximum observed was **one developing-POC side crossing in any rolling five-candle window**; the required two crossings never occurred. This does not prove the market was never balanced. It shows the current heuristic was non-operative in this sample and should not be treated as a validated balance classifier.
- The historical episode ledger was emitted from the Python engine in CI and reviewed across all 30 episodes. Within each episode's remaining session window (or until the next confirmed episode), **17/30** reached confirmed re-entry: **5 within four candles**, another **12 after more than four candles**. The remaining **13/30** did not reach confirmed re-entry before that window ended. This is an outcome description, not a false-positive label; a session boundary or new episode ends the review window.
- GitHub Actions Run #73 executed the expanded offline suite: **18 tests passed**, including the full raw-history replay, synthetic balance/gap tests, and the historical retest-sequence count assertions.

These are descriptive historical state counts only. They do not establish false-confirmation rates, predictive value, or profitability. Automated assertions spot-check three rapid re-entry sequences (4 Nov, 17 Nov, 22 Nov) and two sustained discovery sequences (21 Nov downside, 26 Nov upside); the CI ledger provides the full 30-episode review trail.

## State-machine validation still required
- Manually review the 30 discovery episodes against completed candles, especially quick re-entry and opposite-side transitions.
- Review candidate cancellation, opposite-side transitions, re-entry, and session rollover manually against completed candles.
- Review whether the balance heuristic's POC-crossing condition is a useful description of rotation on a larger historical sample; synthetic mechanics tests alone do not validate the market interpretation.
- Measure false confirmations and cases where price discovery is not accompanied by value development.
- Resolve raw OHLC archive lag before claiming current-session replay completeness.

## Safety and release
- Read-only Supabase credential required: `SUPABASE_READ_ONLY_KEY`, or an `SUPABASE_ANON_KEY` with SELECT permission.
- No service-role credential is required or used by this module.
- This branch is research-only. Do not connect it to the production dashboard, collector, alerts, Playbook or trading workflow until state-machine validation and explicit release approval.
