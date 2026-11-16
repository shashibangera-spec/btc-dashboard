"""Research Dashboard adapter for canonical snapshot-pinned options data."""
from datetime import date
from typing import Any
import pandas as pd
from options_filters import _execute, _missing_record_keys, select_canonical_snapshot, calculate_all_filters


def parse_optional_float(value):
    """Convert optional numeric database values without treating zero as missing."""
    return None if value is None else float(value)


def validated_prior_values(shadow_state):
    """Return validated database prior levels, or None when unusable."""
    if not isinstance(shadow_state, dict) or str(shadow_state.get("data_status", "")).upper() != "VALID":
        return None
    keys = ("prior_vah", "prior_poc", "prior_val")
    if any(shadow_state.get(key) is None for key in keys):
        return None
    vah, poc, val = (float(shadow_state[key]) for key in keys)
    return (vah, poc, val) if vah >= poc >= val else None


def load_canonical_research_options(client: Any, *, as_of: date | None = None) -> dict:
    snapshot, selection = select_canonical_snapshot(client, as_of=as_of)
    if snapshot is None:
        return {"status": "UNAVAILABLE", "selection": selection}
    rows = _execute(
        client.table("options_derived_snapshots")
        .select("*")
        .eq("snapshot_id", snapshot.snapshot_id)
        .eq("expiry_date", snapshot.expiry_date)
        .limit(1)
    )
    if not rows:
        return {"status": "UNAVAILABLE", "reason": "MISSING_MATCHING_DERIVED_RECORD",
                "snapshot_id": snapshot.snapshot_id, "expiry_date": snapshot.expiry_date}
    row = rows[0]
    required = ("snapshot_id", "expiry_date", "underlying_spot_price", "metric_status",
                "max_pain_strike", "atm_strike", "atm_straddle_price", "atm_straddle_pct_spot",
                "local_oi_pcr", "call_gex_wall_strike", "call_gex_wall_value",
                "put_gex_wall_strike", "put_gex_wall_value")
    missing = _missing_record_keys([row], required)
    if missing["missing_columns"]:
        return {"status": "UNAVAILABLE", "reason": "MISSING_DERIVED_FIELDS",
                "missing_columns": missing["missing_columns"]}
    if str(row["snapshot_id"]) != snapshot.snapshot_id or str(row["expiry_date"]) != snapshot.expiry_date:
        return {"status": "UNAVAILABLE", "reason": "DERIVED_IDENTITY_MISMATCH"}
    if not isinstance(row["metric_status"], dict) or row["metric_status"].get("chain") != "OK":
        return {"status": "UNAVAILABLE", "reason": "DERIVED_CHAIN_NOT_VALID"}
    spot = pd.to_numeric(pd.Series([row["underlying_spot_price"]]), errors="coerce").iloc[0]
    if pd.isna(spot) or float(spot) <= 0 or abs(float(spot)-snapshot.spot_price) > 1e-8:
        return {"status": "UNAVAILABLE", "reason": "DERIVED_SPOT_MISMATCH"}
    row = dict(row)
    row["status"] = "OK"
    row["run_completed_at"] = snapshot.completed_at
    return {"status": "OK", "snapshot": snapshot, "derived": row, "filters": calculate_all_filters(snapshot), "selection": selection}
