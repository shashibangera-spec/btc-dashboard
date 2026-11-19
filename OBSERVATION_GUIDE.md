# Observation Guide — 30-Day Research Cheat Sheet

**Purpose:** A quick reference for resuming the BTC observation programme after time away. This is an observation protocol, not a trading playbook.

**Operating mode:** Freeze and observe. The three engines below collect or communicate different kinds of evidence. None is, by itself, a trade signal.

## 1. The three active engines

### Layer 1 — AMT State Machine and Telegram Alerts

**What it observes:** Completed-candle Auction Market Theory structure and confirmed state transitions.

**Where to look:**
- AMT source records: `amt_snapshots`
- Canonical research event journal: `system_signal_journal`
- Telegram delivery/outbox history: `telegram_alert_events`

Telegram is the communication layer, not the AMT state machine itself. It delivers selected structural events, including confirmed AMT state changes, profile initialization, qualifying GEX wall breaches and OI shocks. A missing Telegram message does not necessarily mean no research event or market development occurred; consult the journal and source tables.

**How to interpret:** Read the confirmed AMT state first. Keep confirmed state separate from developing state. Treat Telegram wording as context to inspect, not an instruction to trade.

### Layer 2 — Phase 2 GEX Shapley Attribution

**Primary table:** `gex_strike_attribution`

**What it observes:** Event-only changes in the leading Call and Put GEX strikes. The attribution decomposes calculated GEX changes among Gamma, OI and Spot inputs using Shapley contributions.

Important event types include:
- `WINNER_SWITCH` — a different strike becomes the leading wall.
- `SIGNIFICANT_MAGNITUDE_CHANGE` — the same wall changes materially in GEX magnitude.
- `NEW_EXPOSURE` — a qualifying new Top-3 exposure appears.
- `DISAPPEARED` — a previously tracked Top-3 exposure is no longer present.

**What the driver means:** `dominant_driver = OI` means the OI component made the largest calculated contribution under the attribution method. It does **not** identify who traded, establish dealer positioning, or prove that the wall will act as support or resistance.

### Layer 3 — Auction Deterioration Shadow Monitor

**Primary table:** `auction_deterioration_metrics`

**What it observes:** Completed-candle excursions from the current transition anchor, normalized by ATR and percentage, POC distance, and frozen Developing-VAL test episodes.

The monitor records close excursions separately from wick excursions:
- A downside wick excursion captures how far the low travelled below the anchor.
- A downside close excursion captures the close-based deterioration from the anchor.
- The upside fields provide the symmetric comparison.

**VAL episode fields:**
- `val_test_count` is cumulative within the current monitoring context and increments once when a new qualifying VAL-test episode starts. It does not increment on every candle while the same episode continues.
- `val_test_status` describes the current candle's episode update: `STARTED`, `CONTINUING`, `REJECTED`, `BROKEN`, or a context/data status such as `NONE`, `INCOMPLETE_GAP`, or `RESET_CONTEXT`.
- The VAL used for an episode is frozen at its start; later developing-VAL movement does not move that episode's reference level.
- `val_rejection_count` counts episodes classified as rejected.

**Critical boundary:** This monitor is non-alerting and research-only. It does not modify the AMT state machine, generate Telegram alerts, or make trading decisions.

## 2. Observation rules — do not break the experiment

1. **Do not change thresholds, formulas, state rules, alert conditions or trading rules during the observation window.**
2. **Do not curve-fit to a small number of early examples.** An interesting sequence is a hypothesis to record, not evidence sufficient to change the system.
3. **Do not add shadow-monitor alerts.** The shadow monitor remains non-alerting.
4. **Do not infer intent from OI or GEX attribution.** OI is not a long/short label; a Shapley driver is a mathematical contribution, not trader identity.
5. **Use completed 30-minute observations.** Keep timestamps aligned and distinguish confirmed state from developing state.
6. **Record contrary examples as carefully as supportive examples.** Include cases where the expected follow-through does not occur.
7. **Do not treat temporal association as causation or predictive validity.** The questions below are research questions, not established relationships.

## 3. What to examine over the next 30 days

### GEX question: OI-driven wall changes and subsequent price behaviour

**Research question:** When a `WINNER_SWITCH` has `dominant_driver = OI`, does price subsequently test and reject the new wall?

For each qualifying event, record:
- Event timestamp, expiry, option side and strike.
- Previous and new wall strikes; `dominant_driver` and the OI contribution.
- Whether price subsequently approached/tested the new wall.
- The observed response after the test: rejection, acceptance/trading through, no test, or insufficient data.
- The observation window and the price source/timeframe used.

Do not count a wall switch as a rejection unless price actually tests the new level and the response is observable. Include switches that are not followed by a test or rejection. The attribution itself does not predict the outcome.

### Deterioration question: VAL-test accumulation and confirmed downside discovery

**Research question:** Does a rising `val_test_count` tend to precede a confirmed AMT state change to `DISCOVERY_BELOW`?

For each monitoring context, record:
- The sequence of `val_test_count` values and `val_test_status` changes.
- The frozen `val_test_level` and `val_test_started_at` for each episode.
- Downside wick excursion versus downside close excursion, preferably in ATR units.
- Whether the confirmed AMT state subsequently changes to `DISCOVERY_BELOW`, remains unchanged, or moves elsewhere.
- The elapsed time and number of completed candles between the observations.

A high count is not automatically bearish and is not a signal. Repeated tests may be rejected; a test episode may break; or the confirmed state may never change. Compare all outcomes, including non-confirmations. Remember that the count resets with monitoring-context resets, so compare like-for-like contexts rather than treating it as one uninterrupted lifetime total.

## 4. Daily routine

A daily GEX attribution review is configured for **05:30 IST (Asia/Kolkata)**. It reviews new rows in `gex_strike_attribution` and reports event-type/driver summaries and data-quality anomalies.

- Confirm that the scheduled report actually ran and inspect its result. A configured schedule is not proof of successful execution.
- If the report says **“No new events.”**, distinguish a successful empty query from a query, permission or watermark error.
- Review new event rows without changing thresholds or interpreting them as trade instructions.
- The shadow monitor is observed through its database rows; it has no Telegram alert routine.

**Daily report controls:**
- The report uses a durable watermark based on the ordered tuple `(created_at, id)` to identify events not previously reviewed. It deduplicates rows and advances the watermark only after successful processing; if watermark persistence or processing fails, the report must surface the error rather than claim an empty result or complete coverage.
- The review is **blind analysis**: it analyses the stored attribution events and does not look up live/current BTC prices, charts, or subsequent market outcomes. It therefore does not assess whether a wall later held or price reacted.
- Each row is checked for Shapley reconciliation: `gamma_contribution + oi_contribution + spot_contribution` must equal `gex_change` within the configured absolute/relative floating-point tolerance. Null/missing contributions fail the check and are reported as anomalies.
- The configured daily schedule is not proof that a run completed. Verify the actual run result.

## 5. Deferred Items & Technical Debt

### Deferred: Adverse Auction Warning

We explicitly decided **not** to build an “Adverse Auction Warning” Telegram alert yet. The deterioration shadow monitor remains observation-only and non-alerting while the data accumulates. The metrics must first demonstrate useful, repeatable information across a meaningful sample; no warning threshold, Telegram route, or new alert should be added during this observation phase.

This is separate from the existing AMT Structural Alerts, which communicate confirmed state transitions. A confirmed AMT state can remain unchanged while price deteriorates; that communications gap motivated the research, but it is not by itself evidence that a new alert is warranted.

### ByKaranteli API decision

The paid ByKaranteli historical-data route was abandoned. Its historical endpoint was quoted at **$0.10 per call**, and no paid calls or wallet funding were approved. The project relies on its own forward-testing/observation engine instead of purchasing that historical dataset.

The free ByKaranteli API may remain available as an **optional secondary cross-check** for live GEX-wall information. It is not a core data dependency, does not replace the project’s primary collection pipeline, and should not be treated as a historical backfill source.

### Database migration/bootstrap drift

A forensic comparison found that the live database has migration history and schema objects that are not fully represented by SQL migration files in the GitHub repository. One concrete example is the applied Project Echo/options-derived-metrics migration (live version `20261112110255_add_project_echo_and_options_derived_metrics`), for which a matching SQL file was not present in the repository at the time of the audit. Several foundational table-creation migrations were also absent.

**Known limitation:** the repository's migration directory is not currently a complete, reproducible blank-database bootstrap. Do not assume a fresh database can be created from the checked-in SQL files alone. Before attempting a clean rebuild or new environment, reconcile and restore the missing schema/migration history and validate it against the live catalog. The documentation records this drift; it does not resolve it.

### Three-day raw options retention

The raw `public.options_oi_snapshots` table has a **3-day retention window**. Older records are archived and verified before deletion by the scheduled retention workflow, reducing ongoing database storage use. This is a raw-snapshot storage policy, not a retention policy for all research outputs.

The following event/metric tables are intended to be retained permanently:
- `public.gex_strike_attribution` — event-level GEX/Shapley attribution history.
- `public.auction_deterioration_metrics` — completed-candle deterioration and VAL-episode observations.

The archive workflow's configured schedule and retention settings should not be confused with proof that a particular purge ran successfully. Check the workflow run and archive verification logs before asserting that a specific batch was archived or deleted.

## 6. Quick reference — where to look

| Question | Primary source |
|---|---|
| What confirmed AMT state was recorded? | `amt_snapshots.confirmed_state` |
| What AMT events were detected? | `system_signal_journal` |
| What Telegram notifications were attempted/delivered? | `telegram_alert_events` |
| Which GEX wall event occurred and what drove the calculated change? | `gex_strike_attribution` |
| How did price excursions and VAL-test episodes develop? | `auction_deterioration_metrics` |

## 7. End-of-window review

At the end of 30 days, review the complete sample before proposing any change. Summarize:
- Number of eligible observations and missing/incomplete records.
- GEX OI-driven winner switches, subsequent wall tests, rejections, acceptances and untested cases.
- VAL-test episode counts, rejections/breaks, and subsequent confirmed AMT state changes.
- Counterexamples, ambiguous cases and limitations in timestamp alignment or data coverage.

**No threshold or rule change is implied by completing this review.** Any proposed change must be a separate, evidence-backed decision and must be approved before implementation.

---

*This guide describes the observation protocol and the repository's research components. For the full architecture and live database inventory, see [README.md](README.md) and [DATABASE.md](DATABASE.md).*

## Document Lifecycle & Decision History

This guide is a permanent, evolving record of the project's research protocol and engineering decisions.

1. **Do not delete this file.** When an observation period ends or a feature moves from research into implementation/production (for example, if the Adverse Auction Warning is eventually approved for production), update this guide to reflect the new state and preserve the prior decision context. Do not delete the guide at the end of an observation period.
2. **Keep a dated decision log.** Whenever a hypothesis is supported, rejected, or remains inconclusive based on the collected data, add a dated entry describing the observation period/sample, what the data showed (including relevant counterexamples and limitations), the resulting decision, and why any associated code or configuration was changed. If no code change is justified, record that outcome too. Link relevant commits, pull requests, tests, or reports where available. Do not describe a hypothesis as proven unless the evidence genuinely warrants that conclusion.
3. **Archive, don't erase.** When a tool, integration, hypothesis, or data source is abandoned (including the ByKaranteli paid historical API route), move its description and decision rationale into a clearly labelled **Deprecated** subsection or section. Preserve why it was retired and any replacement/cross-check arrangement; do not silently remove the historical record.

Add new dated decision entries rather than rewriting history. Correct factual errors transparently, retain superseded decisions with their dates and rationale, and distinguish observed evidence, interpretation, and approved implementation. This keeps the repository's research and development history auditable over time.
