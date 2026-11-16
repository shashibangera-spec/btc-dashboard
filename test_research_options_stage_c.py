"""Stage C regressions for null-preserving OI map and pinned raw options views."""
from pathlib import Path
import pandas as pd

from research_options_adapter import pivot_option_metric, aggregate_taker_volumes, nearest_strikes_by_reference


def test_oi_pivot_preserves_missing_and_observed_zero_separately():
    raw = pd.DataFrame([
        {"strike_price": 900, "option_type": "CALL", "oi_contracts": None},
        {"strike_price": 900, "option_type": "PUT", "oi_contracts": 0},
        {"strike_price": 1000, "option_type": "CALL", "oi_contracts": 12},
        {"strike_price": 1000, "option_type": "PUT", "oi_contracts": None},
    ])
    pivot = pivot_option_metric(raw, "oi_contracts", {"CALL": "Call OI", "PUT": "Put OI"})
    row_900 = pivot.loc[pivot.strike_price == 900].iloc[0]
    row_1000 = pivot.loc[pivot.strike_price == 1000].iloc[0]
    assert pd.isna(row_900["Call OI"])
    assert row_900["Put OI"] == 0
    assert row_1000["Call OI"] == 12
    assert pd.isna(row_1000["Put OI"])


def test_taker_aggregation_preserves_missing_sides_and_zero():
    raw = pd.DataFrame([
        {"strike_price": 900, "taker_buy_volume": None, "taker_sell_volume": 0},
        {"strike_price": 900, "taker_buy_volume": None, "taker_sell_volume": None},
    ])
    result = aggregate_taker_volumes(raw).iloc[0]
    assert pd.isna(result["taker_buy_volume"])
    assert result["taker_sell_volume"] == 0


def test_nearest_ten_strikes_are_selected_using_snapshot_spot_not_live_price():
    strikes = pd.DataFrame({"strike_price": [800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300]})
    snapshot_spot, live_price = 1000, 1240
    selected = nearest_strikes_by_reference(strikes, snapshot_spot, 10)
    assert set(selected.strike_price) == {800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200, 1250}
    assert 1300 not in set(selected.strike_price)
    assert set(nearest_strikes_by_reference(strikes, live_price, 10).strike_price) != set(selected.strike_price)


def test_dashboard_map_does_not_fill_missing_oi_and_uses_pinned_snapshot_spot():
    source = Path("btc_live_research_dashboard.py").read_text(encoding="utf-8")
    map_start = source.index('st.subheader("📊 OI Concentration Map")')
    map_end = source.index('with raw_tab:', map_start)
    map_code = source[map_start:map_end]
    assert "get_nearest_strikes(opt_df, options_reference_price, count=10)" in map_code
    assert ".fillna(0)" not in map_code
    assert '"OI State"' in map_code and '"Missing"' in map_code
    raw_start = source.index('with raw_tab:')
    raw_end = source.index('render_live_dashboard()', raw_start)
    raw_code = source[raw_start:raw_end]
    assert 'opt_forensics.get("status") == "OK"' in raw_code
    assert "get_nearest_strikes(opt_df, options_reference_price, count=10)" in raw_code
    assert "options_metrics.get(\"max_pain_strike\")" in raw_code
    assert "options_metrics.get(\"atm_strike\")" in raw_code
    assert "opt_forensics.get(\"expiry_date\")" in source
