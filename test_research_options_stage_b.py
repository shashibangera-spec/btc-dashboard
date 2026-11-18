"""Stage B regression tests: canonical filter outputs drive Research Dashboard."""
from pathlib import Path
from datetime import date

from research_options_adapter import load_canonical_research_options
from options_filters import load_and_calculate_options_filters
from test_research_options_stage_a import Client, make_db


TEST_AS_OF = date(2026, 11, 16)  # Fixture expiry is 2026-11-17; keep tests clock-independent.


def test_research_adapter_exposes_all_five_canonical_filter_results():
    result = load_canonical_research_options(Client(make_db()), as_of=TEST_AS_OF)
    assert result["status"] == "OK"
    filters = result["filters"]
    assert set(filters) == {
        "nearby_oi", "iv_skew", "volume_oi", "oi_migration", "taker_activity"
    }
    for item in filters.values():
        assert item["snapshot_id"] == result["snapshot"].snapshot_id
        assert item["expiry_date"] == result["snapshot"].expiry_date
        assert item["spot_price"] == result["snapshot"].spot_price


def test_missing_previous_snapshot_is_unavailable_not_stable():
    result = load_canonical_research_options(Client(make_db()))
    migration = result["filters"]["oi_migration"]
    assert migration["status"] == "UNAVAILABLE"
    assert migration["reason"] == "NO_ELIGIBLE_PREVIOUS_SNAPSHOT"
    assert migration.get("classification") is None


def test_partial_taker_result_is_not_presented_as_neutral_flow():
    db = make_db()
    db["options_oi_snapshots"][0]["taker_sell_volume"] = None
    result = load_canonical_research_options(Client(db), as_of=TEST_AS_OF)
    taker = result["filters"]["taker_activity"]
    assert taker["status"] == "PARTIAL"
    assert taker["reason"] == "INCOMPLETE_NEAREST_STRIKE_TAKER_SAMPLE"
    assert taker.get("classification") is None


def test_dashboard_removes_legacy_filter_calculators_and_maps_canonical_fields():
    source = Path("btc_live_research_dashboard.py").read_text(encoding="utf-8")
    for legacy in (
        "def analyze_neighboring_strikes(",
        "def analyze_iv_skew(",
        "def analyze_volume_vs_oi(",
        "def analyze_oi_migration(",
        "def analyze_aggressor_trades(",
    ):
        assert legacy not in source
    assert 'filter_results = opt_forensics.get("filters", {})' in source
    assert 'migration_analysis.get("classification")' in source
    assert 'aggressor_analysis["sampled_buy_volume"]' in source
    assert 'aggressor_analysis["sampled_sell_volume"]' in source
    assert 'aggressor_analysis["sampled_volume_balance"]' in source
    assert 'vol_analysis["call_ratio"]' in source
    assert 'iv_analysis["call_iv_premium_pct"]' in source
    assert 'walls_res.get("walls", [])' in source
    assert 'else "Stable"' not in source
    assert "filter_report_status" in source



def test_fast_and_research_dashboards_produce_identical_five_filter_outputs_for_same_snapshot():
    # The Fast Dashboard calls load_and_calculate_options_filters(); the
    # Research Dashboard adapter calls the same canonical selector and
    # calculate_all_filters() path while also loading matching derived metrics.
    client = Client(make_db())
    fast = load_and_calculate_options_filters(client, as_of=TEST_AS_OF)
    research = load_canonical_research_options(client, as_of=TEST_AS_OF)
    assert fast["status"] == research["status"] == "OK"
    assert fast["selection"]["snapshot_id"] == research["snapshot"].snapshot_id
    assert fast["selection"]["expiry_date"] == research["snapshot"].expiry_date
    assert fast["selection"]["spot_price"] == research["snapshot"].spot_price
    assert fast["selection"]["completed_at"] == research["snapshot"].completed_at
    assert set(fast["filters"]) == {
        "nearby_oi", "iv_skew", "volume_oi", "oi_migration", "taker_activity"
    }
    assert fast["filters"] == research["filters"]
    for key in fast["filters"]:
        result = fast["filters"][key]
        assert result["snapshot_id"] == research["snapshot"].snapshot_id
        assert result["expiry_date"] == research["snapshot"].expiry_date
        assert result["spot_price"] == research["snapshot"].spot_price


def test_ai_report_includes_full_options_and_live_price_provenance_without_layout_changes():
    source = Path("btc_live_research_dashboard.py").read_text(encoding="utf-8")
    required = (
        "Options Snapshot ID:",
        "Options Expiry:",
        "Options Snapshot-Local Spot Price:",
        "Options Snapshot Run Completed At (UTC):",
        "Options Raw Contract Collection Time Range (UTC):",
        "Options Derived Source Collection Timestamp (UTC):",
        "Options Derived Metrics Calculated At (UTC):",
        "Live Binance Price (AMT market reference):",
        "Live Binance Candle Close Timestamp (UTC):",
    )
    for field in required:
        assert field in source
    assert "opt_forensics.get('raw_collected_at_min'" in source
    assert "report_options.get('source_collected_at')" in source
    assert "report_options.get('calculated_at')" in source
    assert 'st.expander("📋 Copy/Paste Text Report for AI"' in source
    assert 'st.code(report_text, language="text")' in source


def test_research_cards_use_neutral_descriptive_iv_and_taker_labels():
    source = Path("btc_live_research_dashboard.py").read_text(encoding="utf-8")
    for legacy in (
        "BULLISH_URGENCY", "BEARISH_URGENCY",
        "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "NEUTRAL_FLOW",
        "Buy dominant", "Sell dominant",
    ):
        assert legacy not in source
    assert 'st.markdown("**2. Call IV Premium**")' in source
    assert 'st.markdown("**5. Sampled Taker Volumes**")' in source
    assert 'st.metric("Sampled Buy Volume"' in source
    assert 'st.metric("Sampled Sell Volume"' in source
    assert "skew_color" not in source and "taker_color" not in source
