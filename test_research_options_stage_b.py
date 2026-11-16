"""Stage B regression tests: canonical filter outputs drive Research Dashboard."""
from pathlib import Path

from research_options_adapter import load_canonical_research_options
from test_research_options_stage_a import Client, make_db


def test_research_adapter_exposes_all_five_canonical_filter_results():
    result = load_canonical_research_options(Client(make_db()))
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
    result = load_canonical_research_options(Client(db))
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
    assert 'aggressor_analysis["taker_buy"]' in source
    assert 'aggressor_analysis["taker_sell"]' in source
    assert 'vol_analysis["call_ratio"]' in source
    assert 'iv_analysis["skew_pct"]' in source
    assert 'walls_res.get("walls", [])' in source
    assert 'else "Stable"' not in source
    assert "filter_report_status" in source
