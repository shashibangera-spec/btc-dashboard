"""Static regression tests for the Fast Dashboard Options Filters integration."""
from pathlib import Path
import ast
import subprocess

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


def test_terminal_css_is_byte_identical_to_main():
    subprocess.run(["git", "fetch", "origin", "main", "--depth=1"], check=True)
    baseline = subprocess.check_output(
        ["git", "show", "origin/main:btc_live_dashboard.py"], text=True
    )
    assert _style_block(_source()) == _style_block(baseline)


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
    futures = source.index('st.markdown(\'<div class="mini" style="margin-top:8px">FUTURES</div>\'')
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


def _load_dashboard_math_helpers():
    """Load only pure helpers without importing Streamlit's side-effectful app."""
    import ast
    import numpy as np
    import pandas as pd
    import types

    tree = ast.parse(_source())
    names = {"_sum_if_complete", "_select_positive_wall", "_calculate_max_pain"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    module = types.ModuleType("dashboard_math_helpers")
    module.__dict__.update({"np": np, "pd": pd})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(DASHBOARD), "exec"), module.__dict__)
    return module


def test_options_aggregates_preserve_observed_zero_and_reject_missing_values():
    import numpy as np
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    assert helpers._sum_if_complete(pd.Series([0.0, 0.0])) == 0.0
    assert helpers._sum_if_complete(pd.Series([2.0, 0.0])) == 2.0
    assert np.isnan(helpers._sum_if_complete(pd.Series([2.0, np.nan])))
    assert np.isnan(helpers._sum_if_complete(pd.Series([np.nan, np.nan])))
    assert np.isnan(helpers._sum_if_complete(pd.Series([1.0, -1.0])))
    assert np.isnan(helpers._sum_if_complete(pd.Series([1.0, np.inf])))


def test_options_wall_selection_does_not_turn_missing_or_zero_into_a_wall():
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    frame = pd.DataFrame({"Strike": [100, 110, 120], "OI_USD_Call": [None, 0.0, 0.0]})
    assert helpers._select_positive_wall(frame, "OI_USD_Call") is None

    frame.loc[2, "OI_USD_Call"] = 25.0
    selected = helpers._select_positive_wall(frame, "OI_USD_Call")
    assert selected["Strike"] == 120
    assert helpers._select_positive_wall(frame, "MISSING_COLUMN") is None


def test_max_pain_accepts_real_zero_oi_values():
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    chain = pd.DataFrame({
        "Strike": [100.0, 110.0],
        "OI_Contracts_Call": [0.0, 0.0],
        "OI_Contracts_Put": [0.0, 0.0],
    })
    assert helpers._calculate_max_pain(chain, True) == 100.0


def test_max_pain_fails_closed_for_incomplete_or_missing_oi_inputs():
    import numpy as np
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    complete = pd.DataFrame({
        "Strike": [100.0, 110.0],
        "OI_Contracts_Call": [0.0, 2.0],
        "OI_Contracts_Put": [3.0, 0.0],
    })
    assert helpers._calculate_max_pain(complete, True) == 100.0
    assert np.isnan(helpers._calculate_max_pain(complete, False))

    for column in ("OI_Contracts_Call", "OI_Contracts_Put"):
        incomplete = complete.copy()
        incomplete.loc[0, column] = np.nan
        assert np.isnan(helpers._calculate_max_pain(incomplete, True))

    malformed = complete.copy()
    malformed.loc[1, "OI_Contracts_Call"] = -1.0
    assert np.isnan(helpers._calculate_max_pain(malformed, True))

    duplicate_strikes = pd.concat([complete, complete.iloc[[0]]], ignore_index=True)
    assert np.isnan(helpers._calculate_max_pain(duplicate_strikes, True))


def test_dashboard_uses_null_preserving_options_helpers_for_oi_and_max_pain():
    source = _source()
    options_block = source[source.index("lower_bound = latest_price"):source.index("# --- DEFINE UI VARIABLES ---")]
    assert "filtered_options.copy()" in options_block
    assert "filtered_options.fillna(0)" not in options_block
    assert "_sum_if_complete(" in options_block
    assert "_calculate_max_pain(options_df, is_chain_complete)" in options_block
    assert ".fillna(0)" not in options_block
