# Phase 3.8 — Research Consumer Unit-Safety Corrections

## Supported research metric provenance

The research filter selector now accepts only `metric_version=options-derived-v2.0-btc-oi` with `gex_contract_multiplier=1.0`. Current snapshots fail closed when either value is missing, malformed, or unsupported. Previous snapshots are eligible only when both provenance fields match the current v2 snapshot. Legacy v1 snapshots are skipped and are never paired with v2 snapshots.

The v2 raw compatibility column `options_oi_snapshots.oi_contracts` contains BTC-denominated OI from Delta `oi`; its historical v1 rows remain contract counts from Delta `oi_contracts`. The column name is not a unit declaration. Research filter results include source metric version and multiplier.

## Nearby OI threshold

The legacy nearby-OI code used a strict `OI > 500` gate on the raw `oi_contracts` compatibility field. Phase 3.5 verified the v1 field as contract count and the Delta BTC option conversion of 0.001 BTC per contract for the audited BTC option universe. Therefore the strict v2 equivalent is `OI > 0.5 BTC`. The original choice of 500 contracts remains an inherited research heuristic; its statistical rationale has not been established. The v2 threshold is explicitly labeled BTC and is not presented as a validated market level.

## Volume/OI withholding

Delta's [official v2 REST API ticker documentation](https://docs.delta.exchange/#get-tickers-for-products) lists `volume` as trading volume but does not define the unit for the options ticker's `volume_24h` field. The Phase 3.5 evidence available for this project verified OI units, not `volume_24h` units. No verified historical payload evidence in this phase establishes a compatible volume unit. Therefore Volume/OI ratios and their classifications are withheld with `VOLUME_UNIT_UNVERIFIED`. Raw `volume_24h` values are not modified. This research metric must remain unavailable until the source unit is established and documented.

## GEX description

The research dashboard describes v2 GEX as using BTC-denominated OI with multiplier 1.0 and standard API gamma. GEX remains a gross unsigned model-derived exposure measure; it is not signed gamma, dealer positioning, or directional exposure.

## Export labels

The fast dashboard's nearest-expiry TSV export labels the source quantity `OI_BTC`, preserving the exact original Delta `oi` source value and `oi_value_usd` as `OI_USD`. Internal calculation fields and the database compatibility column names remain unchanged. The export does not claim that BTC OI is a contract count.

## Historical and legacy protection

- No v1 historical row is rewritten, backfilled, relabeled, or recalculated by Phase 3.8.
- Phase 2 OI migration utilities were written for legacy contract-count semantics. Do not apply them to v2 BTC-denominated compatibility fields without explicit unit validation. Phase 2 calculations are unchanged in this phase.
- The raw archive exports `options_oi_snapshots.oi_contracts` without an explicit metric-version column. Archive consumers must use the retained `raw_payload` (where available) to inspect Delta's original `oi` and `oi_contracts` fields, and/or join the associated snapshot to `options_derived_snapshots` for `metric_version` and `gex_contract_multiplier`. Earlier archived rows without raw payload do not have complete row-level unit provenance. Do not infer units from the compatibility column name.
- No database schema change is introduced.

## Validation boundary

The Phase 3.8 GitHub Actions workflow runs offline tests only. It does not query Supabase or Neon, call live Delta/Binance APIs, execute collectors, backfill history, or validate production deployment state.
