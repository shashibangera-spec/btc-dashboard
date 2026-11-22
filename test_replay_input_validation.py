from datetime import timedelta

import pytest

from pine_state_machine import Candle
from replay_pine_state_machine import (
    BAR, END, START, WARMUP_START, EXPECTED_ARCHIVE_CANDLES,
    EXPECTED_TARGET_CANDLES, verify_and_join,
)


def fixture_data():
    history = []
    ts = WARMUP_START
    while ts < END:
        history.append(Candle(ts, 100.0, 110.0, 90.0, 105.0))
        ts += BAR
    snapshots = []
    for candle in history:
        if candle.open_time < START:
            continue
        snapshots.append({
            "time": candle.open_time,
            "id": f"snapshot-{candle.open_time.isoformat()}",
            "open_price": candle.open,
            "high_price": candle.high,
            "low_price": candle.low,
            "close_price": candle.close,
        })
    return snapshots, history


def test_exact_48_warmup_and_240_target_candles_pass():
    snapshots, history = fixture_data()
    joined, db, warmup_count = verify_and_join(snapshots, history)
    assert len(joined) == EXPECTED_ARCHIVE_CANDLES == 288
    assert len(db) == EXPECTED_TARGET_CANDLES == 240
    assert warmup_count == 48
    assert joined[0].open_time == WARMUP_START
    assert joined[47].open_time == START - BAR
    assert joined[48].open_time == START
    assert joined[-1].open_time == END - BAR


def test_missing_archive_candle_fails_closed():
    snapshots, history = fixture_data()
    del history[47]
    with pytest.raises(RuntimeError, match="Expected 288 archived candles"):
        verify_and_join(snapshots, history)


def test_missing_target_snapshot_fails_closed():
    snapshots, history = fixture_data()
    snapshots.pop(0)
    with pytest.raises(RuntimeError, match="exactly cover"):
        verify_and_join(snapshots, history)


def test_snapshot_ohlc_mismatch_fails_closed():
    snapshots, history = fixture_data()
    snapshots[0]["close_price"] = 106.0
    with pytest.raises(RuntimeError, match="OHLC mismatch"):
        verify_and_join(snapshots, history)


def test_duplicate_archive_timestamp_fails_closed():
    snapshots, history = fixture_data()
    history[100] = history[99]
    with pytest.raises(RuntimeError, match="Duplicate timestamps"):
        verify_and_join(snapshots, history)


def test_archive_gap_fails_closed_even_if_count_is_unchanged():
    snapshots, history = fixture_data()
    history[100] = Candle(history[100].open_time + timedelta(minutes=30),
                          100.0, 110.0, 90.0, 105.0)
    with pytest.raises(RuntimeError, match="exact continuous 30m"):
        verify_and_join(snapshots, history)
