# Phase 3.4 — BTC OI Compatibility-Field Semantics (Updated after Phase 3.5)

**Scope:** Document the version-dependent units of the existing Delta BTC options OI fields and the historical evidence established by the read-only Phase 3.5 Supabase audit. This note does not alter schemas, stored records, historical metric versions, exports, or calculation methodology.

## Delta source-field semantics

The Phase 3.5 raw-payload audit confirmed the following Delta ticker semantics:

- `oi` is BTC-denominated open interest.
- `oi_contracts` is the number of option contracts.
- For the audited BTC option records, `oi = oi_contracts × 0.001`.
- `oi_value_usd` is already USD-denominated and remains unchanged.

The corrected implementation must use `oi` exclusively for BTC OI. If `oi` is missing or invalid, BTC OI is unavailable; `oi_contracts` must never be used as a fallback. Zero is a valid OI observation. Negative, boolean, malformed, NaN, and infinite values are invalid.

## Version-dependent compatibility-field meaning

Existing field names are retained for schema and interface compatibility. Their units depend on the metric version:

| Compatibility field | Legacy v1 meaning | Corrected v2 meaning |
|---|---|---|
| `options_oi_snapshots.oi_contracts` | Contract count from Delta `oi_contracts` | BTC-denominated OI from Delta `oi` |
| `local_put_oi_contracts` | Local put contract-count aggregate | Local put BTC OI aggregate |
| `local_call_oi_contracts` | Local call contract-count aggregate | Local call BTC OI aggregate |
| Dashboard/export `OI_Contracts`, `OI_Contracts_Call`, `OI_Contracts_Put` | Legacy contract-count values where sourced from v1 | BTC OI values for corrected v2/live-chain data |

These names are compatibility labels, not reliable unit declarations. **Consumers must use metric-version provenance** when interpreting these fields. Do not combine values across versions without explicitly normalizing their units.

The Phase 3.5 audit directly matched all 136,905 available raw OI rows: the stored legacy `oi_contracts` value matched raw `oi_contracts`, and multiplying it by 0.001 matched raw `oi`. The 1,400 derived expiry rows with corresponding raw rows also matched contract-count aggregates for both local put and call OI.

The audit found 4,749 v1 derived expiry rows and no v2 rows at the time of inspection. Raw OI coverage was available for only 1,400 derived expiry rows. Earlier historical rows without matching raw payloads therefore have incomplete row-level raw-source verification. Their v1 provenance and legacy implementation provide context, but do not constitute direct raw verification for each row.

**Do not retrospectively relabel v1 stored OI or local OI aggregates as BTC OI.** Historical v1 values remain contract-count quantities under the observed legacy implementation. Where row-level raw payload is unavailable, retain the provenance qualification rather than claiming direct source verification.

## GEX methodology and version equivalence

The GEX methodology remains an unsigned, model-derived gross exposure measure:

`gamma × OI_in_BTC × spot² × 0.01`

The two version-specific implementations are:

- Legacy v1: `gamma × OI_contracts × 0.001 × spot² × 0.01`
- Corrected v2: `gamma × OI_BTC × 1.0 × spot² × 0.01`

Because the audited Delta relationship is `OI_BTC = OI_contracts × 0.001`, these formulas are mathematically equivalent when each version uses its corresponding OI input and multiplier correctly. A change from multiplier 0.001 to 1.0 is not, by itself, evidence of a 1,000-fold change in GEX.

The corrected metric version is `options-derived-v2.0-btc-oi`. Its `gex_contract_multiplier = 1.0` is a neutral compatibility value because the source OI is already BTC-denominated.

`oi_value_usd` remains the exchange-supplied USD-denominated OI value and is not converted from either OI quantity. USD OI wall selection continues to use `oi_value_usd`.

This measure does not infer dealer positioning, net gamma, hedging direction, support, resistance, or a price target.

## Downstream compatibility requirements

Database queries, analytics, CSV exports, spreadsheets, and other consumers must:

1. Read the associated `metric_version` before interpreting any compatibility OI field.
2. Treat v1 `oi_contracts` and local OI aggregates as contract counts.
3. Treat corrected v2 compatibility fields as BTC-denominated OI.
4. Never fall back from missing/invalid Delta `oi` to Delta `oi_contracts` in corrected v2.
5. Preserve `oi_value_usd` as its independent USD-denominated source field.
6. Avoid pooling v1 and v2 quantities without an explicit, validated unit conversion.
7. Keep historical rows unchanged; no retrospective relabelling or recalculation is implied by this documentation.

## GEX epsilon review

The existing attribution threshold `GEX_EPSILON = 1e-12` is retained. Corrected GEX is calculated as gamma × BTC OI × spot² × 0.01. At a representative spot of $100,000, gamma of 0.00002, and 10 BTC OI, the resulting GEX is 20,000—more than 10¹⁵ times the epsilon. The threshold's existing uses (`_top_n`, zero/near-zero predecessor checks, percentage-change denominator guards, and `_dominant_driver`) therefore do not suppress exposures of this representative realistic magnitude. Values at or below epsilon are treated as effectively zero/unattributed by design. Offline regression tests cover both the realistic magnitude and the sub-epsilon boundary. No reproducible defect justifies changing the threshold in this phase.
