# Data Validation Status — BTC Dashboard

**Repository:** `shashibangera-spec/btc-dashboard`  
**Role:** Dashboard and Supabase data layer  
**Registry status:** Draft for review  
**Last inventory:** 2026-11-24  
**Source commit reviewed:** `788498018455b34eb2d111e95ced933e764e9351`  
**Source file:** `btc_live_dashboard.py`  
**Source blob:** `a8dd3b4076e765bd9dfcaa34848f538cbedef7ef`

## Status vocabulary

- **PASS** — exact check completed successfully with linked evidence.
- **CONDITIONAL** — evidence supports the conclusion only within stated qualifications.
- **OPEN** — unresolved finding or work item.
- **FAIL** — a check was run and failed.
- **NOT VERIFIED** — not checked in this validation.
- **SUPERSEDED** — replaced by a later, explicitly linked baseline.

Do not promote a status based only on a design note, README statement, or test specification. Link the actual query/test result and exact source/data identity.

## Accepted baselines and operating context

### BTC-BASE-001 — Supabase schema inventory (2026-11-20)
**Status: CONDITIONAL — historical point-in-time snapshot**

The read-only inventory recorded these public-table row counts:

| Table | Rows |
|---|---:|
| `amt_snapshots` | 423 |
| `auction_deterioration_metrics` | 375 |
| `futures_oi_snapshots` | 1,859 |
| `gex_strike_attribution` | 147 |
| `options_collection_runs` | 382 |
| `options_derived_snapshots` | 2,709 |
| `options_oi_snapshots` | 177,808 |
| `signal_reaction_analysis` | 31 |
| `system_signal_journal` | 33 |
| `telegram_alert_events` | 23 |

RLS was enabled on all ten observed public tables. Catalog objects, policies and indexes were inventoried. The live migration history contained seven versions. The checked-in migrations directory did not contain all foundational table-creation migrations; migration history included `20261112110255_add_project_echo_and_options_derived_metrics` without a matching SQL file. The shadow metrics table contained 375 rows.

These are historical counts and observations, not current live-state claims. No current database query is implied by this registry entry.

### BTC-BASE-002 — Options snapshot retention design
**Status: CONDITIONAL — documented design, not proof of every execution**

The documented retention target is only `options_oi_snapshots`, older than 72 hours (3 days) by UTC age. Execution requires explicit `--execute`; the process is expected to reconcile a manifest/checksum and publish the archive before deleting eligible rows. The documented post-retention maintenance includes size verification and `VACUUM (VERBOSE, ANALYZE)`.

A workflow definition or runbook is not evidence that each scheduled run completed correctly. Record each execution separately with archive identity, checksum, deleted-row count and database evidence.

### BTC-BASE-003 — AMT observation and alert separation
**Status: CONDITIONAL — protocol description**

The observation model distinguishes confirmed state from developing state. Telegram delivery/outbox handling is separate from the canonical `system_signal_journal`; `telegram_alert_events` records delivery events. Preserve that separation when validating signal history. A Telegram delivery is not itself proof that a canonical AMT state transition occurred.

### BTC-BASE-004 — GEX Shapley attribution semantics
**Status: CONDITIONAL — interpretation boundary**

GEX Shapley attribution is a mathematical allocation of modeled exposure contributions. It does not identify trader/dealer identity, establish dealer long/short positioning, or prove support/resistance or directional intent. Keep labels and conclusions non-directional unless independent evidence justifies a stronger statement.

### BTC-BASE-005 — Auction deterioration shadow monitor
**Status: CONDITIONAL — research-only**

The deterioration monitor is a non-alerting research/shadow measure. Its documented episode logic uses a frozen VAL reference and records close/wick excursions. It is not an approved modification to the AMT state machine and should not be represented as a live trading signal.

### BTC-BASE-006 — Observation discipline
**Status: CONDITIONAL — protocol**

- Do not change thresholds during an observation window.
- Record contrary examples, not only confirming examples.
- Use completed 30-minute candles for completed-candle conclusions.
- Do not infer causality or predictive value from temporal association alone.
- Preserve the daily 05:30 IST GEX review convention and the documented `(created_at, id)` watermark when reproducing the relevant review.
- Separate observed facts, calculated metrics and interpretation.

## Open source-audit findings

The following were identified by static source review of `btc_live_dashboard.py` at the source commit above. They are review findings, not proof that a live production incident occurred.

| ID | Finding | Source location |
|---|---|---|
| BTC-OPEN-001 | Missing options volume can be defaulted to zero, obscuring missingness. | [Lines 1279](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1279) |
| BTC-OPEN-002 | Missing IV can become zero; a falsey `mark_iv` may trigger fallback behavior. | [Lines 1287](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1287) |
| BTC-OPEN-003 | `oi_contracts or oi` can replace an explicit zero with a fallback value. | [Line 1278](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1278) |
| BTC-OPEN-004 | The first selected product's `contract_value` is applied across products without demonstrated unit validation. | [Lines 1259–1260](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1259-L1260) and [1421–1423](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1421-L1423) |
| BTC-OPEN-005 | Chain completeness does not require Gamma, volume, IV or quote coverage across the entire expected chain. | [Lines 1299](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1299) |
| BTC-OPEN-006 | GEX walls may be selected from available positive finite Gamma rows despite incomplete expected-chain coverage. | [Lines 321–329](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L321-L329) and [1421–1448](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L1421-L1448) |
| BTC-OPEN-007 | Missing futures ticker funding/mark/spot/OI fields can default to zero. | [Lines 453–468](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L453-L468) |
| BTC-OPEN-008 | Aggregate futures OI uses the latest stored observation without a demonstrated maximum-age freshness gate. | [Lines 471–535](https://github.com/shashibangera-spec/btc-dashboard/blob/788498018455b34eb2d111e95ced933e764e9351/btc_live_dashboard.py#L471-L535) |
| BTC-OPEN-009 | Deployed revision identity was not verified in this inventory. | NOT VERIFIED |
| BTC-OPEN-010 | The 2026-11-20 schema row counts are historical and have not been refreshed here. | NOT VERIFIED |

## Validation register

| Validation ID | Date | Scope | Outcome | Evidence / limitation |
|---|---|---|---|---|
| BTC-DB-20261120 | 2026-11-20 | Read-only Supabase schema, RLS, migration and row-count inventory | CONDITIONAL | Historical inventory; not a current-state query |
| BTC-OBS-20261124 | 2026-11-24 | Repository/source documentation inventory | CONDITIONAL | Static review only; no live Supabase query or deployed-revision check |

## Evidence required for future entries

For every validation, record the exact Git commit and relevant file/blob SHA; exact dataset/time window and timezone; database project/branch/schema/table where applicable; exact SQL or test command; row counts and integrity results; GitHub Actions run URL and job/test names; deployment identity if relevant; and whether the activity was read-only.

## Safety boundary

This registry commit is documentation-only. It does not authorize source-code, dashboard, database, schema, migration, collector, workflow, alert, deployment or production-data changes. Do not dispatch a production database-writing workflow to test documentation or CI.