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
    cur=[dict(r,snapshot_id=("c1" if r.get("snapshot_id")=="current" else r.get("snapshot_id"))) for r in (cur if cur is not None else chain("current"))]
    prev=[dict(r,snapshot_id=("p1" if r.get("snapshot_id")=="previous" else r.get("snapshot_id"))) for r in (prev if prev is not None else chain("previous"))]
    return calculate_oi_migration(cur,prev,current_snapshot_id="c1",previous_snapshot_id="p1",
        current_completed_at="2026-11-25T12:30:00Z",previous_completed_at="2026-11-25T12:00:00Z",
        selected_expiry=EXPIRIES[0],eligible_expiries=EXPIRIES,**kw)
def test_calculates_pair_provenance_and_elapsed_seconds():
    r=calc(); assert r["status"]=="OK" and r["provenance"]["elapsed_seconds"]==1800
    assert (r["provenance"]["current_snapshot_id"],r["provenance"]["previous_snapshot_id"])==("c1","p1")
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
def test_rows_with_snapshot_id_mismatch_are_rejected():
    cur=chain("current"); cur[0]["snapshot_id"]="wrong-snapshot"; r=calc(cur)
    assert r["status"]=="WITHHELD" and r["endpoint_validation"][0]["snapshot_id_mismatch"] is True
def test_duplicate_contract_identity_withholds_comparison():
    cur=chain("current"); cur.append(dict(cur[0],product_id="different")); r=calc(cur)
    assert r["status"]=="WITHHELD" and r["endpoint_validation"][0]["duplicate_contract_identities"]
def test_duplicate_contract_identity_normalizes_numeric_strike_representation():
    cur=chain("current"); duplicate=dict(cur[0],product_id="different",strike_price=float(cur[0]["strike_price"]))
    cur[0]["strike_price"]=str(cur[0]["strike_price"]); cur.append(duplicate); r=calc(cur)
    assert r["status"]=="WITHHELD" and r["endpoint_validation"][0]["duplicate_contract_identities"]
def test_unmatched_products_are_reported_and_excluded():
    cur=chain("current"); cur=cur[1:]; x=calc(cur)["nearest_expiry"]["coverage"]
    assert x["unmatched_previous_product_ids"] and x["valid_matched_product_ids"]==9
def test_hhi_top5_top10_reported_separately_for_call_and_put_only():
    metrics=calc()["nearest_expiry"]["current_concentration"]
    assert set(metrics)=={"CALL","PUT","strike_oi"}
    for side in ("CALL","PUT"):
        assert metrics[side]["hhi"] is not None and metrics[side]["top5_share_pct"] is not None and metrics[side]["top10_share_pct"] is not None
def test_region_functionality_and_parameters_are_absent(monkeypatch):
    import inspect, phase2_oi_migration as module
    load_adapter(monkeypatch)
    import phase2_oi_migration_adapter as adapter_module
    assert not hasattr(module,"_region_migration")
    assert "region_boundaries" not in inspect.signature(module.calculate_oi_migration).parameters
    assert "region_boundaries" not in inspect.signature(adapter_module.load_phase2_oi_migration).parameters
    assert "strike_region_concentration" not in calc()
def test_combined_hhi_and_combined_concentration_are_absent():
    result=calc()
    assert "COMBINED" not in result["nearest_expiry"]["current_concentration"]
    assert "COMBINED" not in result["nearest_expiry"]["previous_concentration"]
    assert "COMBINED" not in result["cross_expiry_same_numerical_strike"]["current_concentration"]
    assert "COMBINED" not in result["cross_expiry_same_numerical_strike"]["previous_concentration"]
def test_matched_cohort_arithmetic_reconciles_but_exchange_completeness_is_unverified():
    r=calc(); assert r["validity"]["arithmetic_reconciliation"]=="PASS"
    assert r["cross_expiry_arithmetic_reconciliation"]["difference"]==pytest.approx(0)
    assert r["validity"]["exchange_wide_completeness"]=="UNVERIFIED"
class Response:
    def __init__(self,data,count=None): self.data,self.count=data,count
class Query:
    def __init__(self,db,table): self.db,self.table,self.filters,self.sort=db,table,[],None
    def select(self,*_,**kwargs): self.count_requested=kwargs.get("count"); return self
    def eq(self,k,v): self.filters.append((k,v)); return self
    def order(self,k,desc=False):
        if self.sort is None: self.sort=[]
        self.sort.append((k,desc)); return self
    def limit(self,n): self.maximum=n; return self
    def range(self,a,b):
        if self.table=="options_collection_runs": self.db["history_range_calls"]=self.db.get("history_range_calls",0)+1
        self.bounds=(a,b); return self
    def execute(self):
        if self.table=="options_collection_runs" and self.count_requested=="exact":
            self.db["history_query_calls"]=self.db.get("history_query_calls",0)+1
        if self.table=="options_collection_runs" and self.count_requested=="exact" and self.db.get("history_query_raises"):
            raise RuntimeError("synthetic history query failure")
        rows=list(self.db.get(self.table,[]))
        for k,v in self.filters: rows=[r for r in rows if r.get(k)==v]
        if self.sort:
            for key,desc in reversed(self.sort):
                rows.sort(key=lambda r:r.get(key) or "",reverse=desc)
        exact_count=len(rows) if self.count_requested=="exact" else None
        if self.table=="options_collection_runs" and self.count_requested=="exact":
            if "history_count" in self.db: exact_count=self.db["history_count"]
            if self.db.get("history_response_limit") is not None:
                rows=rows[:self.db["history_response_limit"]]
        if hasattr(self,"maximum"): rows=rows[:self.maximum]
        if hasattr(self,"bounds"): rows=rows[self.bounds[0]:self.bounds[1]+1]
        return Response(rows,exact_count)

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
                        "taker_sell_volume":1,"spot_price":0 if bad else 100,"collected_at":stamp,"exchange_timestamp":stamp,"data_status":"OK"})
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
    assert r["canonical_selector_previous_snapshot_id"]=="older-valid" and r["canonical_selector_previous_snapshot_ignored"] is True
def test_adapter_accepts_valid_immediate_predecessor_without_fallback(monkeypatch):
    loader=load_adapter(monkeypatch); r=loader(Client(adapter_db(invalid_previous=False)),as_of=date(2026,11,25))
    assert r["status"]=="OK" and r["pair"]["previous_snapshot_id"]=="immediate-invalid"
    assert r["pair_validation"]["previous_run_skipped"] is False
def test_adapter_withholds_ambiguous_equal_timestamp_predecessors(monkeypatch):
    loader=load_adapter(monkeypatch); r=loader(Client(adapter_db(invalid_previous=False,tied_previous=True)),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="AMBIGUOUS_IMMEDIATELY_PRECEDING_SUCCESS_RUN"
def test_adapter_finds_actual_predecessor_beyond_500_runs(monkeypatch):
    db=adapter_db(invalid_previous=False)
    current=db["options_collection_runs"][0]
    db["options_collection_runs"]=[current]
    for i in range(505):
        minute=31+(i//60); second=i%60
        stamp=f"2026-11-25T12:{minute:02d}:{second:02d}Z"
        db["options_collection_runs"].append({**current,"snapshot_id":f"between-{i}",
            "completed_at":stamp,"started_at":stamp})
    predecessor={**current,"snapshot_id":"true-predecessor","completed_at":"2026-11-24T23:59:59Z",
        "started_at":"2026-11-24T23:59:59Z"}
    db["options_collection_runs"].append(predecessor)
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD"
    assert r["pair"]["previous_snapshot_id"]=="true-predecessor"
def test_adapter_withholds_invalid_immediate_success_run_without_older_fallback(monkeypatch):
    db=adapter_db(invalid_previous=False)
    immediate=next(r for r in db["options_collection_runs"] if r["snapshot_id"]=="immediate-invalid")
    immediate["inserted_contracts"]-=1
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD"
    assert r["reason"]=="IMMEDIATELY_PRECEDING_SUCCESS_RUN_INVALID"
    assert r["pair"]["previous_snapshot_id"]=="immediate-invalid"
    assert r["pair"]["previous_snapshot_id"]!="older-valid"
def test_adapter_withholds_when_success_run_timestamp_is_unresolvable(monkeypatch):
    db=adapter_db(invalid_previous=False)
    db["options_collection_runs"].append({**db["options_collection_runs"][-1],
        "snapshot_id":"bad-time","completed_at":"not-a-timestamp"})
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="RUN_HISTORY_TIMESTAMP_INVALID"

def test_adapter_accepts_exact_count_matching_complete_history(monkeypatch):
    db=adapter_db(invalid_previous=False)
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="OK" and r["pair"]["previous_snapshot_id"]=="immediate-invalid"
    assert db["history_query_calls"]==1
    assert db.get("history_range_calls",0)==0

def test_adapter_withholds_when_exact_history_count_is_missing(monkeypatch):
    db=adapter_db(invalid_previous=False); db["history_count"]=None
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="RUN_HISTORY_EXACT_COUNT_UNAVAILABLE"

def test_adapter_withholds_when_exact_history_count_mismatches_rows(monkeypatch):
    db=adapter_db(invalid_previous=False); db["history_count"]=999
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="RUN_HISTORY_INCOMPLETE"
    assert r["exact_count"]==999 and r["received_rows"]==len(db["options_collection_runs"])

def test_adapter_withholds_when_history_response_is_truncated(monkeypatch):
    db=adapter_db(invalid_previous=False); db["history_response_limit"]=1
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="RUN_HISTORY_INCOMPLETE"
    assert r["exact_count"]>r["received_rows"]

def test_adapter_withholds_when_history_query_fails(monkeypatch):
    db=adapter_db(invalid_previous=False); db["history_query_raises"]=True
    loader=load_adapter(monkeypatch); r=loader(Client(db),as_of=date(2026,11,25))
    assert r["status"]=="WITHHELD" and r["reason"]=="RUN_HISTORY_QUERY_FAILED"


def _run_with_counter_change(db,snapshot_id,change):
    run=next(r for r in db["options_collection_runs"] if r["snapshot_id"]==snapshot_id)
    change(run)
    return run

@pytest.mark.parametrize("counter",("expected_contracts","received_contracts","inserted_contracts"))
def test_adapter_withholds_when_all_run_counters_are_null(monkeypatch,counter):
    db=adapter_db(invalid_previous=False)
    run=_run_with_counter_change(db,"current",lambda r:r.update(
        expected_contracts=None,received_contracts=None,inserted_contracts=None))
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD" and result["reason"]=="CURRENT_RUN_INVALID"
    assert result["current_validation"]["reason"]=="RUN_COUNTERS_MISSING"

@pytest.mark.parametrize("counter",("expected_contracts","received_contracts","inserted_contracts"))
@pytest.mark.parametrize("mode",("missing","null"))
def test_adapter_withholds_when_each_required_counter_is_missing_or_null(monkeypatch,counter,mode):
    db=adapter_db(invalid_previous=False)
    def change(run):
        if mode=="missing": run.pop(counter)
        else: run[counter]=None
    _run_with_counter_change(db,"current",change)
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD" and result["reason"]=="CURRENT_RUN_INVALID"
    assert result["current_validation"]["reason"] in ("MISSING_RUN_COLUMNS","RUN_COUNTERS_MISSING")

@pytest.mark.parametrize("counter",("expected_contracts","received_contracts","inserted_contracts"))
def test_adapter_withholds_when_a_run_counter_is_negative(monkeypatch,counter):
    db=adapter_db(invalid_previous=False)
    _run_with_counter_change(db,"current",lambda r:r.update({counter:-1}))
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD"
    assert result["current_validation"]["reason"]=="RUN_COUNTERS_INVALID"
    assert result["current_validation"]["invalid_counters"][counter]=="NEGATIVE"

@pytest.mark.parametrize("counter",("expected_contracts","received_contracts","inserted_contracts"))
@pytest.mark.parametrize("value",[1.5,"12.5","not-a-count",True,False])
def test_adapter_withholds_when_a_run_counter_is_fractional_malformed_or_boolean(monkeypatch,counter,value):
    db=adapter_db(invalid_previous=False)
    _run_with_counter_change(db,"current",lambda r:r.update({counter:value}))
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD"
    assert result["current_validation"]["reason"]=="RUN_COUNTERS_INVALID"
    assert result["current_validation"]["invalid_counters"][counter]=="NOT_INTEGER"

def test_adapter_withholds_when_run_counters_are_unequal(monkeypatch):
    db=adapter_db(invalid_previous=False)
    _run_with_counter_change(db,"current",lambda r:r.update(inserted_contracts=r["inserted_contracts"]-1))
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD"
    assert result["current_validation"]["reason"]=="RUN_COUNTERS_MISMATCH"

def test_adapter_accepts_valid_equal_nonnegative_integer_run_counters(monkeypatch):
    db=adapter_db(invalid_previous=False)
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="OK"
    current_run=next(r for r in db["options_collection_runs"] if r["snapshot_id"]=="current")
    assert result["pair_validation"]["current_run_validation"]["counters"]=={
        "expected_contracts":current_run["expected_contracts"],
        "received_contracts":current_run["received_contracts"],
        "inserted_contracts":current_run["inserted_contracts"]}

@pytest.mark.parametrize("snapshot_id,counter,value,expected_reason",[
    ("current","received_contracts",None,"CURRENT_RUN_INVALID"),
    ("immediate-invalid","inserted_contracts",-1,"IMMEDIATELY_PRECEDING_SUCCESS_RUN_INVALID"),
])
def test_adapter_withholds_invalid_current_or_immediate_predecessor_without_older_fallback(
        monkeypatch,snapshot_id,counter,value,expected_reason):
    db=adapter_db(invalid_previous=False)
    _run_with_counter_change(db,snapshot_id,lambda r:r.update({counter:value}))
    loader=load_adapter(monkeypatch); result=loader(Client(db),as_of=date(2026,11,25))
    assert result["status"]=="WITHHELD" and result["reason"]==expected_reason
    if snapshot_id=="current":
        assert result["current_validation"]["reason"]=="RUN_COUNTERS_MISSING"
    else:
        assert result["pair"]["previous_snapshot_id"]=="immediate-invalid"
        assert result["pair"]["previous_snapshot_id"]!="older-valid"
        assert result["previous_validation"]["reason"]=="RUN_COUNTERS_INVALID"
