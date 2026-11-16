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
