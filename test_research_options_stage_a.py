"""Stage A regression tests for Research Dashboard canonical options adapter."""
from datetime import date
import pandas as pd
from research_options_adapter import load_canonical_research_options, parse_optional_float, validated_prior_values


class Response:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, []
    def select(self, *_): return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_): return self
    def range(self, start, end): self.range_slice = (start, end); return self
    def execute(self):
        rows = list(self.db.get(self.table, []))
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        if hasattr(self, "range_slice"):
            start, end = self.range_slice
            rows = rows[start:end + 1]
        return Response(rows)


class Client:
    def __init__(self, db): self.db = db
    def table(self, name): return Query(self.db, name)


def make_db():
    contracts = []
    for strike in (900, 1000, 1100):
        for side in ("CALL", "PUT"):
            contracts.append({
                "snapshot_id": "snap-1", "expiry_date": "2026-11-17",
                "symbol": f"{side}-{strike}", "product_id": f"{side}-{strike}",
                "option_type": side, "strike_price": strike, "oi_contracts": 10,
                "oi_value_usd": 10000, "volume_24h": 2, "turnover_usd": 200,
                "mark_iv": .5, "taker_buy_volume": 1, "taker_sell_volume": 1,
                "spot_price": 1000, "collected_at": "2026-11-16T12:00:00Z",
                "exchange_timestamp": None, "data_status": "OK",
            })
    return {
        "options_collection_runs": [{
            "snapshot_id": "snap-1", "status": "SUCCESS",
            "started_at": "2026-11-16T12:00:00Z", "completed_at": "2026-11-16T12:30:00Z",
            "expected_contracts": 6, "received_contracts": 6, "inserted_contracts": 6,
        }],
        "options_derived_snapshots": [{
            "snapshot_id": "snap-1", "expiry_date": "2026-11-17",
            "calculated_at": "2026-11-16T12:30:00Z", "source_collected_at": "2026-11-16T12:00:00Z",
            "underlying_spot_price": 1000, "metric_status": {"chain": "OK"},
            "max_pain_strike": 1000, "atm_strike": 1000, "atm_straddle_price": 20,
            "atm_straddle_pct_spot": 2, "local_oi_pcr": 1,
            "call_gex_wall_strike": 1100, "call_gex_wall_value": 3,
            "put_gex_wall_strike": 900, "put_gex_wall_value": 4,
        }],
        "options_oi_snapshots": contracts,
    }


def test_adapter_pins_raw_snapshot_expiry_spot_and_matching_derived_row():
    result = load_canonical_research_options(Client(make_db()), as_of=date(2026, 11, 16))
    assert result["status"] == "OK"
    snap = result["snapshot"]
    assert (snap.snapshot_id, snap.expiry_date, snap.spot_price) == ("snap-1", "2026-11-17", 1000)
    assert set(snap.raw.snapshot_id) == {"snap-1"}
    assert set(snap.raw.expiry_date) == {"2026-11-17"}
    assert result["derived"]["max_pain_strike"] == 1000
    assert result["derived"]["status"] == "OK"


def test_adapter_rejects_derived_spot_that_disagrees_with_raw_snapshot():
    db = make_db()
    db["options_derived_snapshots"][0]["underlying_spot_price"] = 999
    result = load_canonical_research_options(Client(db), as_of=date(2026, 11, 16))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "DERIVED_SPOT_MISMATCH"


def test_adapter_rejects_derived_expiry_or_chain_mismatch():
    db = make_db()
    db["options_derived_snapshots"][0]["metric_status"] = {"chain": "PARTIAL"}
    result = load_canonical_research_options(Client(db), as_of=date(2026, 11, 16))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "DERIVED_CHAIN_NOT_VALID"


def test_futures_optional_numeric_parser_preserves_zero():
    assert parse_optional_float(0.0) == 0.0
    assert parse_optional_float("0") == 0.0
    assert parse_optional_float(None) is None


def test_amt_prior_levels_use_only_validated_database_values():
    valid = {"data_status": "VALID", "prior_vah": 1050, "prior_poc": 1000, "prior_val": 950}
    assert validated_prior_values(valid) == (1050.0, 1000.0, 950.0)
    assert validated_prior_values({**valid, "data_status": "ERROR"}) is None
    assert validated_prior_values({**valid, "prior_poc": None}) is None
    assert validated_prior_values({**valid, "prior_poc": 1100}) is None


def test_research_dashboard_stage_a_keeps_ui_structure_and_uses_canonical_spot():
    from pathlib import Path
    source = Path("btc_live_research_dashboard.py").read_text(encoding="utf-8")
    assert "calculate_tpo" not in source
    assert "load_canonical_research_options" in source
    assert "options_reference_price" in source
    assert 'if row.get("funding_rate") else None' not in source
    assert 'if row.get("basis_pct") else None' not in source
