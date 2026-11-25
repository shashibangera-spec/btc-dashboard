"""Read-only Phase 2 adapter. Canonical selector is used only for current-snapshot authority."""
from datetime import date, datetime, timezone
import pandas as pd
from options_filters import (DERIVED_COLUMNS, RAW_COLUMNS, RUN_COLUMNS, _execute,
    _missing_record_keys, _run_is_complete, _iso_date, validate_raw_snapshot,
    select_canonical_snapshot, MissingRequiredRecordKeys)
from phase2_oi_migration import calculate_oi_migration

def _validate_run(run):
    missing=_missing_record_keys([run],tuple(c for c in RUN_COLUMNS if c!="underlying"))
    if missing["missing_columns"]: return {"status":"INVALID","reason":"MISSING_RUN_COLUMNS",**missing}
    if not _run_is_complete(run): return {"status":"INVALID","reason":"RUN_COUNTER_MISMATCH_OR_NOT_SUCCESS"}
    if not run.get("snapshot_id") or not run.get("completed_at"): return {"status":"INVALID","reason":"INVALID_RUN_IDENTITY"}
    if pd.isna(pd.to_datetime(run.get("completed_at"),utc=True,errors="coerce")):
        return {"status":"INVALID","reason":"INVALID_COMPLETION_TIMESTAMP"}
    return {"status":"OK"}

def _read_all_raw(client,snapshot_id):
    query=client.table("options_oi_snapshots").select(",".join(RAW_COLUMNS)).eq("snapshot_id",snapshot_id)
    rows=[]; start=0; size=500
    while True:
        page=_execute(query.range(start,start+size-1)); rows.extend(page)
        if len(page)<size: break
        start+=size
    missing=_missing_record_keys(rows,RAW_COLUMNS)
    if missing["missing_columns"]: raise MissingRequiredRecordKeys("options_oi_snapshots",missing)
    return pd.DataFrame(rows,columns=list(RAW_COLUMNS))

def load_phase2_oi_migration(client,*,as_of:date|None=None):
    """Withhold unless the immediately preceding SUCCESS run validates; never skip it."""
    snap,meta=select_canonical_snapshot(client,as_of=as_of)
    if snap is None: return {"status":"WITHHELD","reason":"CURRENT_SNAPSHOT_INVALID","current_selection":meta}
    current_rows=_execute(client.table("options_collection_runs").select(",".join(RUN_COLUMNS)).eq("snapshot_id",snap.snapshot_id).limit(1))
    if not current_rows: return {"status":"WITHHELD","reason":"CURRENT_RUN_NOT_FOUND"}
    current=current_rows[0]; current_valid=_validate_run(current)
    if current_valid["status"]!="OK": return {"status":"WITHHELD","reason":"CURRENT_RUN_INVALID","current_validation":current_valid}
    underlying=str(current.get("underlying") or "BTCUSD")
    # One statement snapshot plus exact count: never page mutable history by offset.
    try:
        history_query=(client.table("options_collection_runs")
            .select(",".join(RUN_COLUMNS),count="exact")
            .eq("status","SUCCESS").eq("underlying",underlying)
            .order("completed_at",desc=True).order("snapshot_id"))
        history_response=history_query.execute()
        history=getattr(history_response,"data",None)
        exact_count=getattr(history_response,"count",None)
    except Exception:
        return {"status":"WITHHELD","reason":"RUN_HISTORY_QUERY_FAILED"}
    if not isinstance(history,list):
        return {"status":"WITHHELD","reason":"RUN_HISTORY_RESPONSE_INVALID"}
    if isinstance(exact_count,bool) or not isinstance(exact_count,int) or exact_count<0:
        return {"status":"WITHHELD","reason":"RUN_HISTORY_EXACT_COUNT_UNAVAILABLE",
            "received_rows":len(history)}
    if exact_count!=len(history):
        return {"status":"WITHHELD","reason":"RUN_HISTORY_INCOMPLETE",
            "exact_count":exact_count,"received_rows":len(history)}
    missing=_missing_record_keys(history,tuple(c for c in RUN_COLUMNS if c!="underlying"))
    if missing["missing_columns"]: return {"status":"WITHHELD","reason":"RUN_HISTORY_INVALID",**missing}
    parsed=[]
    for run in history:
        stamp=pd.to_datetime(run.get("completed_at"),utc=True,errors="coerce")
        if pd.isna(stamp):
            return {"status":"WITHHELD","reason":"RUN_HISTORY_TIMESTAMP_INVALID",
                "invalid_snapshot_id":run.get("snapshot_id")}
        parsed.append((stamp,run))
    current_time=pd.to_datetime(current["completed_at"],utc=True,errors="coerce")
    earlier=[(stamp,run) for stamp,run in parsed if stamp<current_time]
    if not earlier: return {"status":"WITHHELD","reason":"NO_IMMEDIATELY_PRECEDING_SUCCESS_RUN",
        "current_snapshot_id":snap.snapshot_id,"current_completed_at":current["completed_at"]}
    latest=max(x[0] for x in earlier); candidates=[r for t,r in earlier if t==latest]
    if len(candidates)!=1: return {"status":"WITHHELD","reason":"AMBIGUOUS_IMMEDIATELY_PRECEDING_SUCCESS_RUN",
        "ambiguous_previous_snapshot_ids":sorted(str(r.get("snapshot_id") or "") for r in candidates)}
    previous=candidates[0]; previous_id=str(previous.get("snapshot_id") or "")
    pair={"current_snapshot_id":snap.snapshot_id,"current_completed_at":current["completed_at"],
          "previous_snapshot_id":previous_id,"previous_completed_at":previous.get("completed_at")}
    previous_valid=_validate_run(previous)
    if previous_valid["status"]!="OK": return {"status":"WITHHELD","reason":"IMMEDIATELY_PRECEDING_SUCCESS_RUN_INVALID",
        "pair":pair,"previous_validation":previous_valid,"current_selection":meta,
        "canonical_selector_previous_snapshot_ignored":meta.get("previous_snapshot_id") != previous_id,
        "canonical_selector_previous_snapshot_id":meta.get("previous_snapshot_id")}
    today=as_of or datetime.now(timezone.utc).date()
    def eligible(sid):
        rows=_execute(client.table("options_derived_snapshots").select(",".join(DERIVED_COLUMNS)).eq("snapshot_id",sid).order("expiry_date"))
        missing=_missing_record_keys(rows,DERIVED_COLUMNS)
        if missing["missing_columns"]: return None,missing
        values=sorted({str(r["expiry_date"]) for r in rows if _iso_date(r.get("expiry_date")) is not None
            and _iso_date(r.get("expiry_date"))>=today and isinstance(r.get("metric_status"),dict)
            and r["metric_status"].get("chain")=="OK"})
        return values,None
    current_exp,err=eligible(snap.snapshot_id)
    if err: return {"status":"WITHHELD","reason":"CURRENT_DERIVED_ROWS_INVALID","pair":pair,"validation":err}
    previous_exp,err=eligible(previous_id)
    if err: return {"status":"WITHHELD","reason":"PREVIOUS_DERIVED_ROWS_INVALID","pair":pair,"validation":err}
    shared=sorted(set(current_exp)&set(previous_exp))
    if snap.expiry_date not in shared: return {"status":"WITHHELD","reason":"PREVIOUS_RUN_SELECTED_EXPIRY_NOT_VALID",
        "pair":pair,"previous_validation":{"status":"INVALID","eligible_expiries":previous_exp}}
    try: cur_raw,prev_raw=_read_all_raw(client,snap.snapshot_id),_read_all_raw(client,previous_id)
    except MissingRequiredRecordKeys as exc:
        return {"status":"WITHHELD","reason":"RAW_SNAPSHOT_MISSING_COLUMNS","pair":pair,
            "validation":{"source":exc.source,**exc.details}}
    for label,raw,sid in (("CURRENT",cur_raw,snap.snapshot_id),("PREVIOUS",prev_raw,previous_id)):
        if raw.empty or set(raw.snapshot_id.dropna().astype(str))!={sid}:
            return {"status":"WITHHELD","reason":f"{label}_RAW_SNAPSHOT_INVALID","pair":pair}
        for expiry in shared:
            validation=validate_raw_snapshot(raw[raw.expiry_date.astype(str)==expiry],sid,expiry)
            if validation["status"]!="OK": return {"status":"WITHHELD","reason":f"{label}_EXPIRY_VALIDATION_FAILED",
                "pair":pair,"expiry_date":expiry,"validation":validation,"current_selection":meta,
                "canonical_selector_previous_snapshot_ignored":meta.get("previous_snapshot_id") != previous_id,
                "canonical_selector_previous_snapshot_id":meta.get("previous_snapshot_id")}
    cur_raw=cur_raw[cur_raw.expiry_date.astype(str).isin(shared)]
    prev_raw=prev_raw[prev_raw.expiry_date.astype(str).isin(shared)]
    result=calculate_oi_migration(cur_raw,prev_raw,current_snapshot_id=snap.snapshot_id,previous_snapshot_id=previous_id,
        current_completed_at=current["completed_at"],previous_completed_at=previous["completed_at"],
        selected_expiry=snap.expiry_date,eligible_expiries=shared)
    result.update({"pair":pair,"current_selection":meta,"eligible_expiries":shared,
        "expiry_eligibility":{"current_eligible_expiries":current_exp,"previous_eligible_expiries":previous_exp,
            "shared_eligible_expiries":shared,"current_only_expiries":sorted(set(current_exp)-set(previous_exp)),
            "previous_only_expiries":sorted(set(previous_exp)-set(current_exp))},
        "pair_validation":{"status":"OK","current_run_validation":current_valid,
            "previous_run_validation":previous_valid,"strict_adjacency":"IMMEDIATELY_PRECEDING_SUCCESS_RUN",
            "previous_run_skipped":False,
            "canonical_selector_previous_snapshot_ignored":meta.get("previous_snapshot_id") != previous_id,
            "canonical_selector_previous_snapshot_id":meta.get("previous_snapshot_id")}})
    return result
