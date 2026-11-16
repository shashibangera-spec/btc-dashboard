"""Shared pure helpers for testing dashboard data selection without Streamlit."""
import pandas as pd


def nearest_strikes_by_reference(df, reference_price, count=10):
    work = df.copy()
    work["strike_price"] = pd.to_numeric(work["strike_price"], errors="coerce")
    work = work[work["strike_price"].notna()]
    work["distance"] = (work["strike_price"] - float(reference_price)).abs()
    return work.nsmallest(count, "distance").sort_values("strike_price")
