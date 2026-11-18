from scripts.analyze_iv_amt_layer4 import (
    dte_bucket,
    legacy_nearest_five_skew,
    pearson,
    quote_exposure,
)


def test_legacy_skew_uses_nearest_five_unique_strikes_and_legacy_formula():
    contracts = []
    for strike, call, put in [
        (90, 0.50, 0.60), (95, 0.51, 0.61), (100, 0.52, 0.62),
        (105, 0.53, 0.63), (110, 0.54, 0.64), (120, 9.0, 9.0),
    ]:
        contracts.extend([
            {"strike_price": strike, "option_type": "CALL", "mark_iv": call},
            {"strike_price": strike, "option_type": "PUT", "mark_iv": put},
        ])
    result = legacy_nearest_five_skew(contracts, 100)
    assert result["legacy_skew_pct"] is not None
    assert result["legacy_skew_pct"] < 0


def test_dte_buckets_are_explicit():
    assert dte_bucket(0) == "0DTE"
    assert dte_bucket(2) == "1-2 DTE"
    assert dte_bucket(5) == "Weekly (3-7 DTE)"
    assert dte_bucket(30) == "Monthly (>7 DTE)"


def test_quote_exposure_flags_wide_and_sentinel_independently():
    result = quote_exposure([{
        "bid_price": 80, "ask_price": 120,
        "bid_iv": 0.01, "ask_iv": 0.55,
    }])
    assert result["wide_spread_exposed"] is True
    assert result["sentinel_exposed"] is True


def test_pearson_positive_and_sample_count():
    value, n = pearson([(1, 2), (2, 4), (3, 6)])
    assert n == 3
    assert value == 1.0
