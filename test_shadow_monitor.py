from datetime import datetime, timedelta, timezone

from shadow_monitor import calculate_shadow_rows


START = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


def candle(index, *, close=100.0, low=99.0, high=101.0, val=98.0,
           poc=100.0, state="IN_VALUE", profile="P_2026-11-19", atr=2.0):
    opened = START + timedelta(minutes=30 * index)
    closed = opened + timedelta(minutes=30)
    return {
        "exchange": "BINANCE",
        "symbol": "BTCUSDT",
        "timeframe": "30",
        "candle_open_time": opened.isoformat(),
        "candle_close_time": closed.isoformat(),
        "profile_id": profile,
        "confirmed_state": state,
        "close_price": close,
        "high_price": high,
        "low_price": low,
        "developing_val": val,
        "developing_poc": poc,
        "atr_21": atr,
        "data_status": "OK",
    }


def test_val_episode_starts_once_and_freezes_initial_developing_val():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
        candle(2, close=100, low=97.5, val=97),  # Developing VAL moved; episode level stays 98.
    ]
    result = calculate_shadow_rows(rows)
    assert result[1]["val_test_status"] == "STARTED"
    assert result[1]["val_test_level"] == 98
    assert result[1]["val_test_started_at"] == (START + timedelta(minutes=60)).isoformat()
    assert result[2]["val_test_status"] == "CONTINUING"
    assert result[2]["val_test_level"] == 98
    assert result[2]["val_test_count"] == 1


def test_val_episode_continues_while_frozen_level_is_retested():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
        candle(2, close=99.5, low=97.5, val=97.5),
        candle(3, close=100, low=98, val=97),
    ]
    result = calculate_shadow_rows(rows)
    assert [r["val_test_status"] for r in result[1:]] == [
        "STARTED", "CONTINUING", "CONTINUING"
    ]
    assert result[3]["val_test_count"] == 1


def test_val_episode_is_rejected_only_after_price_stops_retesting_without_closing_below():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
        candle(2, close=100, low=98.5, val=97.5),
    ]
    result = calculate_shadow_rows(rows)
    assert result[1]["val_test_status"] == "STARTED"
    assert result[2]["val_test_status"] == "REJECTED"
    assert result[2]["val_test_active"] is False
    assert result[2]["val_test_count"] == 1
    assert result[2]["val_rejection_count"] == 1
    assert result[2]["val_test_started_at"] == (START + timedelta(minutes=60)).isoformat()
    assert result[2]["val_test_level"] == 98


def test_val_episode_is_broken_by_completed_close_below_frozen_level():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
        candle(2, close=97.5, low=96, val=97),
    ]
    result = calculate_shadow_rows(rows)
    assert result[1]["val_test_status"] == "STARTED"
    assert result[2]["val_test_status"] == "BROKEN"
    assert result[2]["val_test_active"] is False
    assert result[2]["val_test_count"] == 1
    assert result[2]["val_rejection_count"] == 0
    assert result[2]["val_test_started_at"] == (START + timedelta(minutes=60)).isoformat()
    assert result[2]["val_test_level"] == 98


def test_transition_anchor_resets_only_on_confirmed_state_or_profile_change():
    rows = [
        candle(0, close=100, low=99, high=101),
        candle(1, close=96, low=95, high=100),
        candle(2, close=97, low=94, high=99, state="DISCOVERY_CONFIRMED_BELOW"),
        candle(3, close=98, low=97, high=99, state="DISCOVERY_CONFIRMED_BELOW"),
    ]
    result = calculate_shadow_rows(rows)
    assert result[1]["downside_close_excursion_usd"] == 4
    assert result[2]["transition_anchor_price"] == 97
    assert result[2]["downside_close_excursion_usd"] == 0
    assert result[3]["transition_anchor_price"] == 97
    assert result[3]["downside_wick_excursion_usd"] == 3


def test_gap_ends_active_episode_without_counting_it_as_rejected():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
        candle(3, close=100, low=99, val=98),
    ]
    result = calculate_shadow_rows(rows)
    assert result[1]["val_test_status"] == "STARTED"
    assert result[2]["val_test_status"] == "INCOMPLETE_GAP"
    assert result[2]["val_test_active"] is False
    assert result[2]["val_rejection_count"] == 0


def test_excursions_are_normalized_by_stored_atr_and_poc_distance():
    rows = [
        candle(0, close=100, low=98, high=102, val=95, poc=100, atr=2),
        candle(1, close=96, low=94, high=101, val=95, poc=100, atr=2),
    ]
    result = calculate_shadow_rows(rows)[1]
    assert result["downside_close_excursion_usd"] == 4
    assert result["downside_close_excursion_atr"] == 2
    assert result["downside_wick_excursion_usd"] == 6
    assert result["downside_wick_excursion_atr"] == 3
    assert result["poc_distance_atr"] == 2

def test_immediate_rejected_and_broken_tests_persist_the_tested_val():
    rejected = calculate_shadow_rows([
        candle(0, close=101, low=100, val=98),
        candle(1, close=100, low=97, val=98),
    ])
    assert rejected[1]["val_test_status"] == "REJECTED"
    assert rejected[1]["val_test_level"] == 98
    assert rejected[1]["val_test_started_at"] is None

    broken = calculate_shadow_rows([
        candle(0, close=101, low=100, val=98),
        candle(1, close=97, low=96, val=98),
    ])
    assert broken[1]["val_test_status"] == "BROKEN"
    assert broken[1]["val_test_level"] == 98
    assert broken[1]["val_test_started_at"] is None


def test_duplicate_source_candle_can_increment_val_test_count_only_once():
    first = candle(0, close=101, low=100, val=98)
    test_candle = candle(1, close=100, low=97, val=98)
    result = calculate_shadow_rows([first, test_candle, dict(test_candle)])
    assert result[1]["val_test_status"] == "REJECTED"
    assert result[1]["val_test_count"] == 1
    assert result[2]["val_test_count"] == 1


def test_terminal_episode_keeps_original_val_level_and_started_at():
    rows = [
        candle(0, close=101, low=100, val=98),
        candle(1, close=98, low=97, val=98),
        candle(2, close=100, low=99, val=97),
    ]
    result = calculate_shadow_rows(rows)
    assert result[2]["val_test_status"] == "REJECTED"
    assert result[2]["val_test_level"] == 98
    assert result[2]["val_test_started_at"] == (START + timedelta(minutes=60)).isoformat()
