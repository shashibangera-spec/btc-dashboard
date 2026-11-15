# Changelog

All notable production architecture, infrastructure, database-contract and significant behavior changes are recorded here.

The project follows the Documentation Contract:

> **No architectural change is complete until its documentation impact has been reviewed.**

Dates use the project change date in UTC unless a change explicitly documents another time basis.

---

## 2026-11-16

### Added

- **Single production Coordinator workflow**
  - Added `.github/workflows/coordinator.yml` as the single scheduled collection entry point.
  - The Coordinator runs AMT, Futures OI and Options collectors sequentially.
  - The signal detector runs only after all required collector jobs complete successfully.
  - Concurrency protection prevents overlapping collection cycles.

- **Automated Signal Journal**
  - Added `system_signal_journal` as the canonical research record for significant backend events.
  - Added automated detection for:
    - `AMT_STATE_CHANGE`
    - `GEX_WALL_BREACH`
    - `OI_SHOCK`
    - `VALUE_MIGRATION`
  - Added event-level deduplication using `event_type + event_timestamp + event_key`.
  - Current events default to severity `INFO`.

- **Signal Detector**
  - Added `signal_detector.py`.
  - Detector uses completed/as-of validated AMT, Futures and Options context.
  - OI shocks require matching exchange coverage.
  - GEX breaches require two consecutive completed 30-minute closes beyond the same wall identity and a valid re-arm inside the walls.

### Changed

- **Scheduler migration**
  - Replaced the three independent external collection triggers with one production Coordinator trigger.
  - The Coordinator is now the intended single entry point for scheduled backend collection.

- **Telegram workflow trigger**
  - Updated `telegram_alerts.yml` to listen for completion of **BTC Data Collection Coordinator**.
  - Telegram processing only proceeds for a successful Coordinator run.
  - This ensures alert evaluation occurs after the coordinated collection cycle and signal detection.

- **System B auto-refresh architecture**
  - Replaced full-page browser reload behavior with Streamlit fragment-based periodic refresh.
  - Session-state-dependent UI tracking can persist across normal fragment refreshes instead of being reset by a page reload.

- **System B labels**
  - Renamed the AMT hero label to **AUCTION (LAST CLOSE)**.
  - Renamed the live-price hero label to **LIVE PRICE LOCATION**.
  - This makes the differing time bases explicit.

### Fixed

- **Trade Journal session isolation**
  - Moved the personal Trade Journal state away from process-global cached state and into `st.session_state`.
  - Non-sensitive shared history remains separate from the personal journal state.

- **System B `show_level_change` ValueError**
  - Hardened numeric conversion so stale/non-numeric values such as strings, `None`, NaN or incompatible objects do not reach numeric formatting.
  - Invalid values now display as `N/A` rather than crashing the dashboard.

- **Signal journal dashboard access**
  - Added/confirmed the anonymous read policy required for Streamlit to read `system_signal_journal` under RLS.

### Documentation

- Established the permanent three-tier documentation hierarchy:
  - `README.md` — current architecture and operations
  - `DATABASE.md` — schema and data contracts
  - `CHANGELOG.md` — significant historical changes

- Adopted the Documentation Contract requiring documentation review before architectural work is considered complete.

---

## Earlier baseline and infrastructure work

The following production capabilities were completed before the formal changelog was introduced and are retained here as baseline context rather than reconstructed as a detailed commit-by-commit history.

### Backend and data infrastructure

- Built the Supabase-backed AMT, Futures and Options collection pipeline.
- Enabled Row Level Security on production research tables.
- Separated Streamlit anon read access from trusted service-role backend operations.
- Added archive/retention infrastructure with manifest and SHA-256 integrity verification.
- Established completed-candle AMT confirmation discipline and timestamp provenance rules.

### Telegram alert infrastructure

- Added backend-based Telegram alert evaluation independent of an open Streamlit browser.
- Added event deduplication/outbox records, delivery status and retry/audit fields.
- Added data freshness and quality gates for options-based alert context.
- Added a reusable test mode for controlled pipeline verification.

### System B

- Evolved the Streamlit dashboard into the current fast observation cockpit.
- Established the AMT → Futures → Options information hierarchy.
- Added the visually optimized custom terminal skin and dark theme baseline.
- Froze major CSS/HTML layout redesigns as a separately managed UI concern.

---

## Changelog policy going forward

Add an entry when a change materially affects:

- production architecture
- GitHub Actions/workflow behavior
- database schema or RLS
- collector behavior
- event taxonomy or alert logic
- security/access model
- data integrity rules
- significant bug fixes
- major System B behavior or UI baselines

Minor formatting-only changes generally do not require a changelog entry unless they alter user interpretation or operational behavior.
