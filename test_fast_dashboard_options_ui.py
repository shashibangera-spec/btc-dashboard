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


def test_options_filters_render_full_width_after_context_and_before_diagnostics():
    source = _source()
    context = source.index('_ui_section("3 · CONTEXT"')
    panel = source.index("render_options_filter_panel(options_filter_results)")
    diagnostics = source.index('_ui_section("DIAGNOSTICS AND RAW DATA"')
    assert context < panel < diagnostics
    context_block = source[context:panel]
    assert 'st.markdown(\'<div class="mini" style="margin-top:8px">FUTURES</div>\'' in context_block
    assert "render_options_filter_panel(options_filter_results)" not in context_block


def test_options_filters_panel_is_open_and_uses_five_equal_columns():
    source = _source()
    start = source.index("def render_options_filter_panel(payload):")
    end = source.index("\n\ndef calculate_tpo", start)
    panel = source[start:end]
    assert "st.expander(" not in panel
    assert "columns = st.columns(5)" in panel
    assert 'OPTIONS RESEARCH FILTERS · VALIDATED SNAPSHOT' in panel
    assert "Snapshot spot" in panel
    for label in ("Nearby OI", "IV Skew", "24h Volume / OI", "OI Migration", "Sampled Taker Trade Activity"):
        assert label in panel


def test_filter_cards_use_two_tier_typography_and_equal_heights():
    source = _source()
    start = source.index("def render_options_filter_panel(payload):")
    end = source.index("\n\ndef calculate_tpo", start)
    panel = source[start:end]
    assert "height:150px; min-height:150px;" in panel
    assert 'font-size:1.02rem' in panel
    assert 'color:var(--muted); font-size:0.68rem' in panel
    assert 'margin-top:auto; padding-top:7px;' in panel
    assert 'primary = f\'{float(result.get("call_iv_premium_pct")):+.2f}%\'' in panel
    assert 'secondary = "Call IV Premium"' in panel
    assert 'primary = str(result.get("classification", "N/A"))' in panel
    assert 'primary = f\'Balance {float(result.get("sampled_volume_balance")):+,.2f}\'' in panel
    assert 'Buy {float(result.get("sampled_buy_volume")):,.2f} / ' in panel


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
    names = {"_sum_if_complete", "_normalize_listed_option_oi", "_select_positive_wall", "_calculate_max_pain"}
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



def test_structurally_unlisted_option_side_is_zero_but_listed_missing_is_null():
    import numpy as np
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    # Mirrors the 18-Nov-2026 expiry's Call-only outer strikes.
    products = [
        {"symbol": "BTC-18NOV26-60800-C", "contract_type": "call_options", "strike_price": 60800},
        {"symbol": "BTC-18NOV26-64400-C", "contract_type": "call_options", "strike_price": 64400},
        {"symbol": "BTC-18NOV26-64400-P", "contract_type": "put_options", "strike_price": 64400},
        {"symbol": "BTC-18NOV26-68400-C", "contract_type": "call_options", "strike_price": 68400},
    ]
    received = pd.DataFrame([
        {"Symbol": "BTC-18NOV26-60800-C", "OI_Contracts": 5.0},
        {"Symbol": "BTC-18NOV26-64400-C", "OI_Contracts": 11.0},
        {"Symbol": "BTC-18NOV26-64400-P", "OI_Contracts": 7.0},
        {"Symbol": "BTC-18NOV26-68400-C", "OI_Contracts": 13.0},
    ])

    normalized = helpers._normalize_listed_option_oi(products, received, True).set_index("Strike")
    assert normalized.loc[60800.0, "OI_Contracts_Call"] == 5.0
    assert normalized.loc[60800.0, "OI_Contracts_Put"] == 0.0
    assert normalized.loc[68400.0, "OI_Contracts_Call"] == 13.0
    assert normalized.loc[68400.0, "OI_Contracts_Put"] == 0.0
    assert normalized.loc[64400.0, "OI_Contracts_Put"] == 7.0
    assert np.isfinite(helpers._calculate_max_pain(normalized.reset_index(), True))

    # Both Call-only outer strikes are inside a ±10% band around $64,600.
    local = normalized.reset_index()
    local = local[(local["Strike"] >= 64600 * 0.90) & (local["Strike"] <= 64600 * 1.10)]
    calls = helpers._sum_if_complete(local["OI_Contracts_Call"])
    puts = helpers._sum_if_complete(local["OI_Contracts_Put"])
    assert calls == 29.0
    assert puts == 7.0
    assert puts / calls == 7.0 / 29.0

    # A listed Put whose ticker is absent is not structurally unlisted.
    missing_listed_ticker = received[received["Symbol"] != "BTC-18NOV26-64400-P"]
    missing = helpers._normalize_listed_option_oi(products, missing_listed_ticker, True).set_index("Strike")
    assert np.isnan(missing.loc[64400.0, "OI_Contracts_Put"])
    assert np.isnan(helpers._sum_if_complete(pd.Series([missing.loc[64400.0, "OI_Contracts_Put"]])))


def test_listed_null_or_invalid_oi_and_incomplete_universe_fail_closed():
    import numpy as np
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    products = [
        {"symbol": "BTC-17NOV26-100-C", "contract_type": "call_options", "strike_price": 100},
        {"symbol": "BTC-17NOV26-100-P", "contract_type": "put_options", "strike_price": 100},
        {"symbol": "BTC-17NOV26-110-C", "contract_type": "call_options", "strike_price": 110},
    ]
    received = pd.DataFrame([
        {"Symbol": "BTC-17NOV26-100-C", "OI_Contracts": 2.0},
        {"Symbol": "BTC-17NOV26-100-P", "OI_Contracts": np.nan},
        {"Symbol": "BTC-17NOV26-110-C", "OI_Contracts": -1.0},
    ])

    normalized = helpers._normalize_listed_option_oi(products, received, True)
    assert np.isnan(normalized.loc[normalized["Strike"] == 100, "OI_Contracts_Put"].iloc[0])
    assert normalized.loc[normalized["Strike"] == 100, "OI_Contracts_Call"].iloc[0] == 2.0
    assert normalized.loc[normalized["Strike"] == 110, "OI_Contracts_Call"].iloc[0] == -1.0

    incomplete_pages = helpers._normalize_listed_option_oi(products, received, False)
    assert incomplete_pages[["OI_Contracts_Call", "OI_Contracts_Put"]].isna().all().all()


def test_listed_option_oi_normalization_rejects_duplicate_or_unexpected_contracts():
    import numpy as np
    import pandas as pd

    helpers = _load_dashboard_math_helpers()
    products = [
        {"symbol": "BTC-17NOV26-100-C", "contract_type": "call_options", "strike_price": 100},
        {"symbol": "BTC-17NOV26-100-P", "contract_type": "put_options", "strike_price": 100},
    ]
    received = pd.DataFrame([
        {"Symbol": "BTC-17NOV26-100-C", "OI_Contracts": 2.0},
        {"Symbol": "BTC-17NOV26-100-C", "OI_Contracts": 3.0},
        {"Symbol": "BTC-17NOV26-100-P", "OI_Contracts": 4.0},
    ])
    normalized = helpers._normalize_listed_option_oi(products, received, True)
    assert np.isnan(normalized[["OI_Contracts_Call", "OI_Contracts_Put"]]).all().all()

    unexpected = received.iloc[[0, 2]].copy()
    unexpected.loc[unexpected.index[0], "Symbol"] = "UNEXPECTED"
    normalized = helpers._normalize_listed_option_oi(products, unexpected, True)
    assert np.isnan(normalized[["OI_Contracts_Call", "OI_Contracts_Put"]]).all().all()


def test_dashboard_uses_product_universe_to_normalize_only_structural_oi_absence():
    source = _source()
    fetch_block = source[source.index("expected_symbol_set ="):source.index("return df_pivot, nearest_expiry")]
    assert "_normalize_listed_option_oi(filtered_options, df, prod_complete and tick_complete)" in fetch_block
    assert 'how="outer"' in fetch_block
    assert "filtered_options.fillna(0)" not in source
    assert ".fillna(0)" not in source

def test_dashboard_uses_null_preserving_options_helpers_for_oi_and_max_pain():
    source = _source()
    options_block = source[source.index("lower_bound = latest_price"):source.index("# --- DEFINE UI VARIABLES ---")]
    assert "filtered_options.copy()" in options_block
    assert "filtered_options.fillna(0)" not in options_block
    assert "_sum_if_complete(" in options_block
    assert "_calculate_max_pain(options_df, is_chain_complete)" in options_block
    assert ".fillna(0)" not in options_block


def test_auction_story_only_renders_state_text_on_a_state_transition():
    source = _source()
    story = source[source.index("# ---------- AUCTION STORY CALLOUT ----------"):source.index("# ---------- WHAT CHANGED — UI ONLY ----------")]
    assert "_has_auction_transition = (" in story
    assert "previous_ui_state is not None and previous_ui_state != current_amt_state" in story
    assert "if _has_auction_transition:" in story
    assert '+ story_html' in story
    assert '_ui_chip("Price vs Prior VAL", prior_val_distance)' in story
    assert '_ui_chip("Price vs Developing VAH", dev_vah_distance)' in story
    assert '_ui_chip("Nearest GEX Wall", nearest_wall)' in story


def test_what_changed_renders_only_valid_changed_developing_levels():
    source = _source()
    start = source.index("# ---------- WHAT CHANGED — UI ONLY ----------")
    end = source.index("# ---------- 1 · PRICE ACTION", start)
    block = source[start:end]
    assert 'if previous_ui_snapshot is not None:' in block
    assert 'current_value is not None and previous_value is not None and current_value != previous_value' in block
    assert 'if changed_levels:' in block
    assert '_ui_section("WHAT CHANGED"' in block
    assert 'column.metric("AUCTION STATE"' not in block
    assert 'column.metric("N/A")' not in block
    assert '"UNCHANGED"' not in block


def test_amt_structure_does_not_repeat_last_close_auction_classification():
    source = _source()
    start = source.index('_ui_section("2 · AMT STRUCTURE"')
    end = source.index("# ---------- FUTURES OI CONTEXT", start)
    block = source[start:end]
    assert 'Fast Auction Classification' not in block
    for label in ("Developing VAH", "Developing POC", "Developing VAL", "VA Expansion"):
        assert f'_ui_card("{label}"' in block
    assert '], "g4")' in block


def test_amt_structure_developing_value_cards_are_ordered_low_to_high():
    source = _source()
    start = source.index('_ui_section("2 · AMT STRUCTURE"')
    end = source.index("# ---------- FUTURES OI CONTEXT", start)
    block = source[start:end]

    val = block.index('_ui_card("Developing VAL", _ui_num(dev_val_ui), tone="down")')
    poc = block.index('_ui_card("Developing POC", _ui_num(dev_poc_ui), tone="warn")')
    vah = block.index('_ui_card("Developing VAH", _ui_num(dev_vah_ui), tone="up")')
    expansion = block.index('_ui_card("VA Expansion", va_expansion_value, _va_sub, _va_tone)')

    assert val < poc < vah < expansion


def test_session_ohlc_history_value_columns_are_ordered_val_poc_vah():
    import ast
    import types
    import pandas as pd

    tree = ast.parse(_source())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_session_journey"
    )
    module = types.ModuleType("session_journey")
    module.__dict__.update({
        "pd": pd,
        "calculate_tpo": lambda frame: (120.0, 80.0, 100.0),
    })
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(DASHBOARD), "exec"), module.__dict__)

    start = pd.Timestamp("2026-11-19 05:30:00", tz="Asia/Kolkata")
    candles = pd.DataFrame([
        {"Date": start, "ShiftedDate": start, "open": 95.0, "high": 110.0, "low": 90.0, "close": 105.0},
        {"Date": start + pd.Timedelta(minutes=30), "ShiftedDate": start, "open": 105.0, "high": 115.0, "low": 100.0, "close": 108.0},
    ])

    result = module.calculate_session_journey(candles, 130.0, 70.0)

    assert list(result.columns)[-3:] == ["VAL", "POC", "VAH"]
    assert result.loc[0, "VAL"] == 80.0
    assert result.loc[0, "POC"] == 100.0
    assert result.loc[0, "VAH"] == 120.0
