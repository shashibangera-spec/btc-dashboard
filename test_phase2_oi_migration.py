"""Offline synthetic coverage for Phase 2 OI migration."""
import importlib, sys, types
from datetime import date
import pandas as pd
import pytest
from phase2_oi_migration import calculate_oi_migration

EXPIRIES=("2026-12-01","2026-12-08")
def chain(snapshot):
    rows=[]
    for expiry in EXPIRIES:
        for strike,call,put in ((90,100,80),(100,200,160),(110,50,40),(120,25,20),(130,10,8)):
            for side,oi in (("CALL",call),("PUT",put)):
                rows.append({"snapshot_id":snapshot,"expiry_date":expiry,"product_id":f"{expiry}-{side}-{strike}",
                    "option_type":side,"strike_price":strike,"oi_contracts":oi+(10 if snapshot=="current" else 0)})
    return rows

def calc(cur=None,prev=None,**kw):
    return calculate_oi_migration(cur if cur is not None else chain("current"),prev if prev is not None else chain("previous"),
        current_snapshot_id="c1",previous_snapshot_id="p1",current_completed_at="2026-11-25T12:30:00Z",
        previous_completed_at="2026-11-25T12:00:00Z",selected_expiry=EXPIRIES[0],eligible_expiries=EXPIRIES,**kw)

def test_calculates_pair_provenance_and_elapsed_seconds():
    r=calc(); assert r["status"]=="OK" and r["provenance"]["elapsed_seconds"]==1800
    assert (r["provenance"]["current_snapshot_id"],r["provenance"]["previous_snapshot_id"]) == ("c1","p1")

def test_nearest_expiry_and_expiry_specific_results_remain_distinct():
    r=calc(); assert r["nearest_expiry"]["expiry_date"]==EXPIRIES[0] and set(r["expiry_specific"])==set(EXPIRIES)

def test_cross_expiry_same_strike_aggregation_retains_expiry_breakdown():
    x=calc()["cross_expiry_same_numerical_strike"]
    assert x["aggregation_basis"]=="COMMON_VALID_MATCHED_PRODUCT_IDS_ACROSS_ELIGIBLE_EXPIRIES"
    assert x["expiry_breakdown_retained"] and x["matched_contract_count"]==20

def test_null_oi_is_excluded_not_zero_filled():
    cur=chain("current"); cur[0]["oi_contracts"]=None
    x=calc(cur)["nearest_expiry"]["coverage"]
    assert x["invalid_matched_product_ids"] and x["valid_matched_product_ids"]==9

def test_duplicate_product_ids_withhold_comparison():
    cur=chain("current"); cur.append(dict(cur[0])); r=calc(cur)
    assert r["status"]=="WITHHELD" and r["endpoint_validation"][0]["duplicate_product_ids"]

def test_duplicate_contract_identity_withholds_comparison():
    cur=chain("current"); cur.append(dict(cur[0],product_id="different")); r=calc(cur)
    assert r["status"]=="WITHHELD" and r["endpoint_validation"][0]["duplicate_contract_identities"]

def test_unmatched_products_are_reported_and_excluded():
    cur=chain("current"); cur=cur[1:]; x=calc(cur)["nearest_expiry"]["coverage"]
    assert x["unmatched_previous_product_ids"] and x["valid_matched_product_ids"]==9

def test_hhi_top5_top10_reported_for_call_put_and_combined():
    metrics=calc()["nearest_expiry"]["current_concentration"]
    for side in ("CALL","PUT","COMBINED"):
        assert metrics[side]["hhi"] is not None and metrics[side]["top5_share_pct"] is not None and metrics[side]["top10_share_pct"] is not None

def test_regions_are_not_invented_without_explicit_fixed_boundaries():
    assert calc()["strike_region_concentration"]["nearest_expiry"]["status"]=="NOT_CALCULATED"

def test_explicit_fixed_regions_report_previous_current_delta_and_share_change():
    regions=calc(region_boundaries=[("LOW",0,100),("HIGH",100,1000)])["strike_region_concentration"]["nearest_expiry"]
    assert regions["status"]=="OK"
    assert regions["by_option_side"]["CALL"]["regions"]["LOW"]["oi_change"]==pytest.approx(10)
    assert regions["by_option_side"]["CALL"]["regions"]["LOW"]["share_change_percentage_points"] is not None

def test_overlapping_region_boundaries_are_rejected():
    with pytest.raises(ValueError): calc(region_boundaries=[("A",0,100),("B",90,200)])

def test_matched_cohort_arithmetic_reconciles_but_exchange_completeness_is_unverified():
    r=calc(); assert r["validity"]["arithmetic_reconciliation"]=="PASS"
    assert r["cross_expiry_arithmetic_reconciliation"]["difference"]==pytest.approx(0)
    assert r["validity"]["exchange_wide_completeness"]=="UNVERIFIED"

class Response:
    def __init__(self,data): self.data=data
class Query:
    def __init__(self,db,table): self.db,self.table,self.filters,self.sort=db,table,[],None
    def select(self,*_): return self
    def eq(self,k,v): self.filters.append((k,v)); return self
    def order(self,k,desc=False): self.sort=(k,desc); return self
    def limit(self,n): self.maximum=n; return self
    def range(self,a,b): self.bounds=(a,b); return self
    def execute(self):
        rows=list(self.db.get(self.table,[]))
        for k,v in self.filters: rows=[r for r in rows if r.get(k)==v]
        if self.sort: rows.sort(key=lambda r:r.get(self.sort[0]) or "",reverse=self.sort[1])
        if hasattr(self,"maximum"): rows=rows[:self.maximum]
        if hasattr(self,"bounds"): rows=rows[self.bounds[0]:self.bounds[1]+1]
        return Response(rows)
class Client:
    def __init__(self,db): self.db=db
    def table(self,name): return Query(self.db,name)

def adapter_db(invalid_previous=True,tied_previous=False):
    db={"options_collection_runs":[],"options_derived_snapshots":[],"options_oi_snapshots":[]}
    def add(sid,stamp,bad=False):
        rows=[]
        for expiry in EXPIRIES:
            for strike in (90,100,110):
                for side in ("CALL","PUT"):
                    rows.append({"snapshot_id":sid,"expiry_date":expiry,"symbol":f"{expiry}-{side}-{strike}",
                        "product_id":f"{expiry}-{side}-{strike}","option_type":side,"strike_price":strike,"oi_contracts":10,
                        "oi_value_usd":100,"volume_24h":1,"turnover_usd":10,"mark_iv":.5,"taker_buy_volume":1,
                        "taker_sell_volume":1,"spot_price":0 if bad else 100,"collected_at":stamp,
                        "exchange_timestamp":stamp,"data_status":"OK"})
        db["options_collection_runs"].append({"snapshot_id":sid,"status":"SUCCESS","started_at":stamp,"completed_at":stamp,
            "underlying":"BTCUSD","expected_contracts":len(rows),"received_contracts":len(rows),"inserted_contracts":len(rows)})
        for expiry in EXPIRIES: db["options_derived_snapshots"].append({"snapshot_id":sid,"expiry_date":expiry,
            "calculated_at":stamp,"source_collected_at":stamp,"underlying_spot_price":100,"metric_status":{"chain":"OK"}})
        db["options_oi_snapshots"].extend(rows)
    add("current","2026-11-25T12:30:00Z"); add("immediate-invalid","2026-11-25T12:05:00Z",invalid_previous)
    add("older-valid","2026-11-25T11:50:00Z")
    if tied_previous: add("tied-immediate","2026-11-25T12:05:00Z",False)
    return db

def load_adapter(monkeypatch):
    stub=types.ModuleType("options_filters")
    stub.RAW_COLUMNS=("snapshot_id","expiry_date","symbol","product_id","option_type","strike_price","oi_contracts","oi_value_usd","volume_24h","turnover_usd","mark_iv","taker_buy_volume","taker_sell_volume","spot_price","collected_at","exchange_timestamp","data_status")
    stub.RUN_COLUMNS=("snapshot_id","status","started_at","completed_at","underlying","expected_contracts","received_contracts","inserted_contracts")
    stub.DERIVED_COLUMNS=("snapshot_id","expiry_date","calculated_at","source_collected_at","underlying_spot_price","metric_status")
    class Missing(Exception):
        def __init__(self,source,details): self.source,self.details=source,details
    stub.MissingRequiredRecordKeys=Missing; stub._execute=lambda q:list(q.execute().data or [])
    stub._missing_record_keys=lambda rows,cols:{"missing_columns":sorted({c for r in rows for c in cols if c not in r}),"row_indexes":{}}
    stub._run_is_complete=lambda r:r.get("status")=="SUCCESS" and r.get("expected_contracts")==r.get("received_contracts")==r.get("inserted_contracts")
    from datetime import date as D
    stub._iso_date=lambda v:D.fromisoformat(str(v)[:10]) if v else None
    def validate(raw,sid,expiry):
        errors=[]
        if raw.empty or set(raw.snapshot_id.astype(str))!={sid} or set(raw.expiry_date.astype(str))!={expiry}: errors.append("IDENTITY")
        if not {"CALL","PUT"}.issubset(set(raw.option_type.astype(str).str.upper())): errors.append("SIDE")
        spots=pd.to_numeric(raw.spot_price,errors="coerce")
        if spots.isna().any() or (spots<=0).any(): errors.append("SPOT")
        return {"status":"OK" if not errors else "INVALID","errors":errors}
    stub.validate_raw_snapshot=validate
    class Snap:
        snapshot_id="current"; expiry_date=EXPIRIES[0]; completed_at="2026-11-25T12:30:00Z"; raw=pd.DataFrame()
        previous_snapshot_id="older-valid"
    stub.select_canonical_snapshot=lambda client,as_of=None:(Snap(),{"status":"OK","previous_snapshot_id":"older-valid"})
    monkeypatch.setitem(sys.modules,"options_filters",stub); sys.modules.pop("phase2_oi_migration_adapter",None)
    return importlib.import_module("phase2_oi_migration_adapter").load_phase2_oi_migration

def test_adapter_withholds_invalid_immediately_preceding_success_instead_of_skipping_to_older(monkeypatch):
    loader=load_adapter(monkeypatch); r=loader(Client(adapter_db()),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="PREVIOUS_EXPIRY_VALIDATION_FAILED"
    assert r["pair"]["previous_snapshot_id"]=="immediate-invalid" and r["pair"]["previous_snapshot_id"]!="older-valid"
    assert r["canonical_selector_previous_snapshot_id"]=="older-valid"
    assert r["canonical_selector_previous_snapshot_ignored"] is True

def test_adapter_accepts_valid_immediate_predecessor_without_fallback(monkeypatch):
    loader=load_adapter(monkeypatch); r=loader(Client(adapter_db(invalid_previous=False)),as_of=date(2026,11,25))
    assert r["status"]=="OK"
    assert r["pair"]["previous_snapshot_id"]=="immediate-invalid"
    assert r["pair_validation"]["previous_run_skipped"] is False

def test_adapter_withholds_ambiguous_equal_timestamp_predecessors(monkeypatch):
    loader=load_adapter(monkeypatch); r=loader(Client(adapter_db(invalid_previous=False,tied_previous=True)),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="AMBIGUOUS_IMMEDIATELY_PRECEDING_SUCCESS_RUN"
