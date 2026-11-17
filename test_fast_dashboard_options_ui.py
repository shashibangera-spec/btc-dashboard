"""Static regression tests for the Fast Dashboard Options Filters integration."""
from pathlib import Path
import ast
import hashlib
import math
import subprocess

import numpy as np
import pandas as pd

from fast_dashboard_data_helpers import optional_float, strict_sum, safe_ratio, select_observed_max_row, calculate_max_pain

DASHBOARD = Path("btc_live_dashboard.py")


def _source():
    return DASHBOARD.read_text(encoding="utf-8")


def _style_block(source):
    marker = "</style>" + chr(10) + chr(34) * 3 + ", unsafe_allow_html=True)"
    start_marker = "st.markdown(" + chr(34) * 3 + chr(10) + "<style>"
    start = source.index(start_marker)
    end = source.index(marker, start)
    return source[start:end + len(marker)]

def test_dashboard_python_compiles():
    ast.parse(_source())


def test_major_redesign_css_matches_approved_baseline():
    # Intentional major UI redesign baseline; update only with Operator approval.
    digest = hashlib.sha256(_style_block(_source()).encode("utf-8")).hexdigest()
    assert digest == "6db7881ece3c9e203f3a6c93dc0a3e552411d2005d717c9f58ab6e210f6e9e7a"


def test_backend_is_the_only_options_filter_data_source():
    source = _source()
    assert "from options_filters import load_and_calculate_options_filters" in source
    assert "return load_and_calculate_options_filters(client)" in source
    assert 'os.environ.get("SUPABASE_URL")' in source
    assert 'os.environ.get("SUPABASE_ANON_KEY")' in source
    assert 'os.environ.get("SUPABASE_SERVICE_ROLE_KEY")' in source
    assert "create_client(supabase_url, supabase_key)" in source
    assert 'st.secrets["SUPABASE_URL"]' not in source


def test_options_filter_fetch_uses_existing_streamlit_cache():
    source = _source()
    assert "@st.cache_data(ttl=180, show_spinner=False)\ndef fetch_options_filter_results():" in source


def test_panel_is_after_futures_and_before_diagnostics():
    source = _source()
    futures = source.index('<span class="mt">Futures context</span>')
    panel = source.index("render_options_filter_panel(options_filter_results)")
    diagnostics = source.index('_ui_section("DIAGNOSTICS AND RAW DATA"')
    assert futures < panel < diagnostics


def test_all_five_backend_outputs_are_rendered_independently():
    source = _source()
    for key in ("nearby_oi", "iv_skew", "volume_oi", "oi_migration", "taker_activity"):
        assert f'("{key}",' in source
    assert 'result = filters.get(key, {"status": "UNAVAILABLE"' in source
    assert 'if status != "OK":' in source


def test_dashboard_ui_does_not_reimplement_filter_calculations():
    source = _source()
    assert "calculate_nearby_oi(" not in source
    assert "calculate_iv_skew(" not in source
    assert "calculate_volume_oi(" not in source
    assert "calculate_oi_migration(" not in source
    assert "calculate_taker_activity(" not in source



def test_supabase_environment_credentials_fail_closed_when_missing():
    source = _source()
    assert 'reason = "MISSING_SUPABASE_ENV:" + ", ".join(missing)' in source
    assert '"status": "UNAVAILABLE"' in source
    assert '"filters": {}' in source


def test_supabase_service_role_key_is_fallback_only():
    source = _source()
    assert (
        'os.environ.get("SUPABASE_ANON_KEY")\n'
        '            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")'
    ) in source


def test_fast_dashboard_uses_descriptive_options_metrics_without_directional_labels():
    source = _source()
    assert 'result.get("call_iv_premium_pct")' in source
    assert 'result.get("sampled_buy_volume")' in source
    assert 'result.get("sampled_sell_volume")' in source
    assert 'result.get("sampled_volume_balance")' in source
    assert 'result.get("classification", "N/A")' not in source[source.index('if key == "iv_skew":'):source.index('if key == "volume_oi":')]
    assert 'result.get("classification", "N/A")' not in source[source.index('if key == "taker_activity":'):source.index('return "N/A", "UNAVAILABLE · UNKNOWN_FILTER"')]


def test_fast_dashboard_does_not_render_legacy_iv_or_taker_classifications():
    source = _source()
    iv_block = source[source.index('if key == "iv_skew":'):source.index('if key == "volume_oi":')]
    taker_block = source[source.index('if key == "taker_activity":'):source.index('return "N/A", "UNAVAILABLE · UNKNOWN_FILTER"')]
    assert "BULLISH_URGENCY" not in iv_block and "BEARISH_URGENCY" not in iv_block
    assert "AGGRESSIVE_BUYING" not in taker_block
    assert "AGGRESSIVE_SELLING" not in taker_block
    assert "CALL_IV_PREMIUM" not in iv_block or "call_iv_premium_pct" in iv_block



def test_observation_branding_and_baseline_labels_are_explicit():
    source = _source()
    assert "BTC AMT &amp; DERIVATIVES OBSERVATION COCKPIT" in source
    assert "AUCTION (LAST CLOSE)" in source
    assert "LIVE PRICE LOCATION" in source
    assert "BTC TRADING COCKPIT" not in source


def test_dashboard_does_not_zero_fill_missing_option_observations():
    source = _source()
    assert ".fillna(0" not in source
    assert 'pcr, pcr_volume, atm_row = np.nan, np.nan, None' in source
    assert "pcr_volume = safe_ratio(total_puts_vol, total_calls_vol)" in source
    assert "true_max_pain = calculate_max_pain(raw_chain_df, is_chain_complete)" in source


def test_optional_float_distinguishes_missing_from_observed_zero():
    assert optional_float(None) is None
    assert optional_float("") is None
    assert optional_float("not-a-number") is None
    assert optional_float("0") == 0.0
    assert optional_float(0) == 0.0


def test_strict_sum_preserves_missing_and_zero():
    assert math.isnan(strict_sum([None, None]))
    assert math.isnan(strict_sum([1.0, None]))
    assert strict_sum([0.0, 0.0]) == 0.0
    assert strict_sum([1.5, 2.5]) == 4.0


def test_safe_ratio_returns_na_for_missing_or_zero_denominator():
    assert math.isnan(safe_ratio(np.nan, 2.0))
    assert math.isnan(safe_ratio(2.0, np.nan))
    assert math.isnan(safe_ratio(2.0, 0.0))
    assert safe_ratio(0.0, 2.0) == 0.0


def test_max_wall_selection_fails_closed_on_missing_values():
    frame = pd.DataFrame({"Strike": [100, 110], "OI": [5.0, np.nan]})
    assert select_observed_max_row(frame, "OI") is None
    frame["OI"] = [0.0, 0.0]
    assert select_observed_max_row(frame, "OI") is None
    frame["OI"] = [5.0, 8.0]
    assert select_observed_max_row(frame, "OI")["Strike"] == 110


def test_max_pain_requires_complete_non_null_chain_and_preserves_zero_oi():
    chain = pd.DataFrame({
        "Strike": [100.0, 100.0, 110.0, 90.0],
        "Type": ["Call", "Put", "Call", "Put"],
        "OI_Contracts": [2.0, 3.0, 1.0, 4.0],
    })
    assert calculate_max_pain(chain, True) == 100.0
    chain.loc[0, "OI_Contracts"] = np.nan
    assert math.isnan(calculate_max_pain(chain, True))
    assert math.isnan(calculate_max_pain(chain, False))
