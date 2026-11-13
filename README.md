# BTC AMT & Derivatives Observation Cockpit

A research and observation system for Bitcoin markets. It combines Auction Market Theory (AMT), futures positioning and options-market measurements to help reconstruct the auction and study how market structure interacts with derivatives context.

**This project is an observation cockpit, not a mechanical trading-signal generator.** It does not claim that any single AMT state, OI change, options concentration or model-derived metric predicts direction or provides a profitable entry.

## Research philosophy

The hierarchy is deliberate:

1. **AMT structure and price action are primary.** They describe the auction, value, acceptance, rejection and migration.
2. **Futures positioning is supporting context.** Open Interest (OI), funding and basis describe measured positioning conditions, subject to exchange coverage and timestamp quality.
3. **Options measurements are supporting context.** Expiry-specific straddle, Max Pain, local OI and gross GEX concentrations describe options pricing and positioning measurements; they are not automatic targets or support/resistance levels.

The research focus is conditional auction behaviour: value migration persistence, acceptance duration, price movement versus value migration, and whether derivatives context adds information beyond ordinary price and volatility observations.

Mechanical breakout and failed-discovery variations tested historically did not establish robust profitability after fees. Those experiments are not deployed as automatic trading rules. No strategy edge should be inferred from descriptive classifications or conditional frequencies alone.

## Automated backend architecture

The automated backend runs in the cloud:

- **Scheduler:** cron-job.org sends scheduled HTTP triggers.
- **Execution:** GitHub Actions runs the collector workflows. Each workflow retains `workflow_dispatch` for manual execution; scheduled cron triggers are not configured inside the workflow files.
- **Storage:** Supabase PostgreSQL stores AMT, futures and options snapshots.
- **Presentation:** Streamlit Cloud reads the stored data and presents the research cockpit.

The laptop does not need to remain on for the external scheduler and cloud workflows to run. Actual collection still depends on the scheduler, GitHub Actions, upstream exchange/API availability and Supabase connectivity.

### Collector cadence

The external cron-job.org schedule triggers the workflows twice per hour:

| Collector | Workflow | Trigger minutes (UTC) |
|---|---|---|
| AMT | `amt_collector.yml` | :07 and :37 |
| Futures | `collector.yml` | :08 and :38 |
| Options | `options_collector.yml` | :09 and :39 |

The workflows are manually dispatchable through GitHub Actions as well. Keep the external cron schedule and workflow names aligned when making operational changes.

## Research layers

### Layer 1 — Auction Market Theory

**Main files:** `amt_collector.py`, `amt_core.py`, `research_engine.py`

The AMT collector stores completed 30-minute BTCUSDT candles, prior-value references, developing-value measurements and state outputs in `amt_snapshots`.

#### Prior Value versus Developing Value

- **Prior VAH / POC / VAL:** the frozen prior-session value references used to evaluate the current auction.
- **Developing VAH / POC / VAL:** the evolving value-area high, point of control and value-area low calculated from the developing auction.

These are different reference sets. Prior Value provides the reference boundary; Developing Value describes where the current auction is building value. A developing POC near price is not, by itself, evidence of directional acceptance.

The `profile_date` and `profile_id` fields identify the source session used to construct Prior Value. They are not necessarily the current candle's session date; this source-session convention is intentional.

#### State discipline

The database preserves both:

- `confirmed_state`: the confirmed AMT state.
- `developing_state`: the evolving/candidate state.

Discovery confirmation requires two consecutive completed 30-minute closes beyond the applicable discovery threshold. A first qualifying close is a candidate; it does not by itself replace the last confirmed state. In-progress candles must not be treated as completed confirmations.

Project Echo fields provide a versioned descriptive state label alongside the AMT state. They are state annotations, not independent trade instructions.

### Layer 2 — Futures positioning

**Main file:** `collector.py`

The futures collector retrieves Open Interest, funding and basis from the configured exchange sources:

- Binance
- Bybit
- OKX
- Delta

An `AGGREGATE` row represents the available contributors for that collection, not necessarily all four exchanges. Always interpret aggregate OI alongside its exchange coverage and status. A 2/4 aggregate is not directly equivalent to a 4/4 aggregate.

The 30-minute OI delta is subject to a strict baseline interval validation: snapshots outside the configured 25–35-minute interval are rejected rather than treated as a valid 30-minute change.

OI expansion means measured open contracts/notional increased; OI alone does not identify whether new longs or shorts initiated the exposure. OI contraction does not independently prove long liquidation or short covering. Funding and basis are context, not standalone directional signals.

### Layer 3 — Options research

**Main file:** `options_collector.py`

The options collector stores the raw options chain in `options_oi_snapshots`, records collection-run status in `options_collection_runs`, and calculates expiry-specific derived metrics in `options_derived_snapshots`.

Derived metrics include:

- **Max Pain:** theoretical strike minimizing aggregate intrinsic-value payout under the collected OI assumptions.
- **ATM strike and ATM straddle:** combined call and put mark premium at the selected nearest common strike.
- **ATM straddle % of spot:** normalized straddle measurement.
- **Gross Call GEX concentration wall and value.**
- **Gross Put GEX concentration wall and value.**
- **Local OI PCR:** put OI divided by call OI inside the configured spot-relative band, plus the underlying local OI quantities.
- **Metric quality/status:** per-metric status describing whether the source chain and calculation were valid.

Metrics are calculated separately for each expiry. Do not combine expiries into a single straddle or GEX value without an explicitly defined research method.

#### Options interpretation guardrails

- Gross GEX is unsigned and does not reveal dealer positioning, net gamma or hedging direction.
- A gross GEX or OI concentration is descriptive; it is not automatically support, resistance, a target or a magnet.
- Max Pain is a theoretical OI-based calculation, not a forecast.
- ATM straddle is an expiry-specific priced-movement measure, not a guaranteed range or probability boundary.
- Missing or incomplete chain data must remain visibly incomplete; missing measurements are not zero.
- Taker buy/sell volume is only available where collected. NULL does not mean zero trading activity.

## Data integrity and research guardrails

- **Missing data:** retain as SQL `NULL`; never silently replace missing measurements with zero.
- **Futures coverage:** show contributor coverage with aggregate values. Preserve exchange-level records and status.
- **Baseline validation:** reject invalid 30-minute OI deltas when the snapshot interval is outside 25–35 minutes.
- **Options provenance:** preserve snapshot ID, expiry, source collection time, calculation time and metric status.
- **Completed candles:** use completed 30-minute candles for AMT state confirmation.
- **No lookahead:** define the event timestamp first. Use only information available at or before that timestamp when describing contemporaneous conditions.
- **No retrospective reclassification:** later price, profile or derivatives observations are subsequent outcomes, not inputs available at the earlier event.
- **No unsupported causal claims:** simultaneous movement in price, OI, value and options metrics does not by itself establish which caused another.

### Strict as-of timestamp rule

For historical event analysis, Futures and Options observations may be used as contemporaneous context for an AMT candle only when their source/observation timestamps are **at or before that AMT candle's close time**. A snapshot collected or sourced after the candle close is subsequent information and must be analysed as a later development, not backfilled into the earlier event.

Where source timestamps are missing, ambiguous or not normalized, mark the match as uncertain or unavailable. Do not assume the collector's later timestamp proves that the underlying exchange measurement was available at an earlier time.

## Database

The automated backend currently uses five Supabase tables. See [DATABASE.md](DATABASE.md) for the data dictionary, table relationships, timestamp provenance and status semantics.

## Scope

This README documents the automated Supabase-backed backend and its collectors. The separate standalone manual Live Terminal (System B) and legacy research prototype are intentionally outside this automated-backend documentation scope.

## Setup and secrets

The collectors and Streamlit application use Supabase credentials configured in their respective GitHub Actions and Streamlit Cloud secret stores:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Keep service-role credentials private. Do not commit secrets or expose service-role keys in client-side code.
