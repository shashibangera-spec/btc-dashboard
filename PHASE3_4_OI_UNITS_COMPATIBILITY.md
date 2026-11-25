# Phase 3.4 — BTC OI Compatibility-Field Semantics

**Scope:** Documentation for the corrected Delta BTC options OI units introduced by
`options-derived-v2.0-btc-oi`. This note does not alter schemas, stored records,
historical metric versions, exports, or calculation logic.

## Corrected meaning

Under the corrected implementation, Delta ticker `oi` is already denominated in
BTC. The collector stores that value in the existing database compatibility
column `options_oi_snapshots.oi_contracts`; the dashboard retains the existing
`OI_Contracts` data/export key; and derived local quantities retain the
`local_put_oi_contracts` and `local_call_oi_contracts` column names.

For records written by the corrected implementation, these fields therefore
contain **BTC-denominated open interest**, not a count of option contracts.
The names are retained to avoid a breaking database schema change and to
preserve existing application/export interfaces. The name is historical
compatibility terminology, not a unit declaration.

## Fields requiring downstream care

| Compatibility field | Corrected implementation meaning | Consumer warning |
|---|---|---|
| `options_oi_snapshots.oi_contracts` / `oi_contracts` | BTC-denominated OI from Delta ticker `oi` | Do not treat as number of contracts |
| `local_put_oi_contracts` | Local put BTC OI quantity | Do not label or aggregate as contract count |
| `local_call_oi_contracts` | Local call BTC OI quantity | Do not label or aggregate as contract count |
| Dashboard/export `OI_Contracts` / `OI_Contracts_Call` / `OI_Contracts_Put` | BTC-denominated OI quantity for corrected live-chain data | Retained export key; not a contract count |

`oi_value_usd` remains the exchange-supplied USD-denominated OI value and is
not converted from these BTC quantities. USD OI wall selection continues to use
`oi_value_usd`.

## Historical-version boundary

This documentation does **not** retroactively redefine historical rows.
Older rows may have been collected using the former `oi_contracts` source
field and the legacy `options-derived-v1.0` calculation. Their actual units
and provenance must be established from their stored metric version and raw
source payload before interpretation. Do not pool historical and corrected
values solely because they share a compatibility column name.

The corrected derived metric version is
`options-derived-v2.0-btc-oi`. Downstream database, analytics, CSV, and
spreadsheet consumers should use metric-version/source provenance where
available and should explicitly relabel corrected values as **BTC OI**. A
consumer that requires contract counts must obtain and validate an actual
contract-count source; it must not infer counts from these compatibility fields.

## GEX epsilon review

The existing attribution threshold `GEX_EPSILON = 1e-12` is retained.
Corrected GEX is calculated as gamma × BTC OI × spot² × 0.01. At a
representative spot of $100,000, gamma of 0.00002, and 10 BTC OI, the resulting
GEX is 20,000—more than 10¹⁵ times the epsilon. The threshold's existing
uses
(`_top_n`, zero/near-zero predecessor checks, percentage-change denominator
guards, and `_dominant_driver`) therefore do not suppress exposures of this
representative realistic magnitude. Values at or below epsilon are treated as
effectively zero/unattributed by design. Offline regression tests cover both
the realistic magnitude and the sub-epsilon boundary. No reproducible defect
justifies changing the threshold in this phase.
