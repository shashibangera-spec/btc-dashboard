"""Strict missing-value helpers for the System B observation dashboard."""
import math
import numpy as np
import pandas as pd

def optional_float(value):
    """Return a finite float preserving 0.0; missing or invalid values become None."""
    if value is None or (isinstance(value, str) and not value.strip()): return None
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return number if math.isfinite(number) else None

def strict_sum(values):
    """Sum only a complete finite series; never coerce NULL to zero."""
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty or numeric.isna().any(): return np.nan
    array = numeric.to_numpy(dtype=float)
    return float(array.sum()) if np.isfinite(array).all() else np.nan

def safe_ratio(numerator, denominator):
    """Return N/A for missing/non-finite inputs or a non-positive denominator."""
    try: n, d = float(numerator), float(denominator)
    except (TypeError, ValueError, OverflowError): return np.nan
    if not math.isfinite(n) or not math.isfinite(d) or d <= 0: return np.nan
    return n / d

def select_observed_max_row(frame, column):
    """Select a positive maximum only when every candidate row is observed."""
    if frame is None or frame.empty or column not in frame.columns: return None
    numeric = pd.to_numeric(frame[column], errors="coerce")
    if numeric.empty or numeric.isna().any(): return None
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all() or float(values.max()) <= 0: return None
    return frame.loc[numeric.idxmax()]

def calculate_max_pain(raw_chain, chain_complete):
    """Compute Max Pain only from a complete chain with observed OI on every contract.

    A missing side at a strike is structural zero only after upstream completeness
    proves every listed contract arrived with OI. Any NULL contract OI fails closed.
    """
    required = {"Strike", "Type", "OI_Contracts"}
    if not chain_complete or raw_chain is None or raw_chain.empty or not required.issubset(raw_chain.columns): return np.nan
    data = raw_chain[["Strike", "Type", "OI_Contracts"]].copy()
    data["Strike"] = pd.to_numeric(data["Strike"], errors="coerce")
    data["OI_Contracts"] = pd.to_numeric(data["OI_Contracts"], errors="coerce")
    if data[["Strike", "Type", "OI_Contracts"]].isna().any().any(): return np.nan
    oi = data["OI_Contracts"].to_numpy(dtype=float)
    if not np.isfinite(oi).all() or (oi < 0).any() or not data["Type"].isin(["Call", "Put"]).all(): return np.nan
    strikes = sorted(float(x) for x in data["Strike"].unique())
    if not strikes: return np.nan
    grouped = data.groupby(["Type", "Strike"], sort=False)["OI_Contracts"].sum()
    calls = {float(k): float(v) for (kind, k), v in grouped.items() if kind == "Call"}
    puts = {float(k): float(v) for (kind, k), v in grouped.items() if kind == "Put"}
    payouts = {}
    for settle in strikes:
        payouts[settle] = sum(calls.get(k, 0.0) * max(settle-k, 0.0) + puts.get(k, 0.0) * max(k-settle, 0.0) for k in strikes)
    return float(min(payouts, key=payouts.get))
