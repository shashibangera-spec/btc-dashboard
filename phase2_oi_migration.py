"""Pure Phase 2 OI migration calculations; no I/O or persistence."""
from collections import Counter
from datetime import datetime
import math

VERSION = "phase2-oi-migration-v1.0"
REQUIRED = ("product_id", "expiry_date", "option_type", "strike_price", "oi_contracts")

def _num(value):
    if value is None or isinstance(value, bool): return None
    try: value = float(value)
    except (TypeError, ValueError, OverflowError): return None
    return value if math.isfinite(value) and value >= 0 else None

def _dt(value):
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError): return None

def _rows(value):
    if value is None: return []
    return value.to_dict("records") if hasattr(value, "to_dict") else [dict(x) for x in value]

def _index(value, endpoint):
    rows = _rows(value)
    missing = sorted({c for r in rows for c in REQUIRED if c not in r})
    ids = [str(r.get("product_id") or "") for r in rows]
    duplicate_ids = sorted(k for k, n in Counter(ids).items() if k and n > 1)
    identity = [(str(r.get("expiry_date") or ""), str(r.get("option_type") or "").upper(), str(r.get("strike_price"))) for r in rows]
    duplicate_identity = sorted(str(k) for k, n in Counter(identity).items() if n > 1)
    diag = {"endpoint": endpoint, "row_count": len(rows), "missing_columns": missing,
            "duplicate_product_ids": duplicate_ids, "blank_product_id_rows": sum(not x for x in ids),
            "duplicate_contract_identities": duplicate_identity}
    if missing or duplicate_ids or diag["blank_product_id_rows"] or duplicate_identity: return {}, diag
    return {str(r["product_id"]): r for r in rows}, diag

def _conc(values):
    positive = sorted((v for v in values.values() if v > 0), reverse=True)
    total = sum(positive)
    if total <= 0:
        return {"total_oi": total, "hhi": None, "hhi_0_10000": None, "top5_share_pct": None,
                "top10_share_pct": None, "strike_count": len(values)}
    shares = [x / total for x in positive]
    hhi = sum(x*x for x in shares)
    return {"total_oi": total, "hhi": hhi, "hhi_0_10000": hhi*10000,
            "top5_share_pct": sum(shares[:5])*100, "top10_share_pct": sum(shares[:10])*100,
            "strike_count": len(values)}

def _distribution(rows):
    sides = {"CALL": {}, "PUT": {}}
    for r in rows:
        side, strike = r["option_type"], r["strike_price"]
        sides[side][strike] = sides[side].get(strike, 0.0) + r["oi_contracts"]
    combined = {}
    for values in sides.values():
        for strike, oi in values.items(): combined[strike] = combined.get(strike, 0.0) + oi
    return {"CALL": _conc(sides["CALL"]), "PUT": _conc(sides["PUT"]),
            "COMBINED": _conc(combined), "strike_oi": sides}

def _expiry(cur, prev, expiry):
    a = {k:v for k,v in cur.items() if str(v.get("expiry_date")) == expiry}
    b = {k:v for k,v in prev.items() if str(v.get("expiry_date")) == expiry}
    common = sorted(set(a) & set(b)); valid=[]; invalid=[]
    for pid in common:
        x,y=a[pid],b[pid]; side=str(x.get("option_type","")).upper()
        sx,sy=_num(x.get("strike_price")),_num(y.get("strike_price"))
        ox,oy=_num(x.get("oi_contracts")),_num(y.get("oi_contracts"))
        if side not in ("CALL","PUT") or side != str(y.get("option_type","")).upper() or sx is None or sx != sy or ox is None or oy is None:
            invalid.append(pid); continue
        valid.append({"product_id":pid,"expiry_date":expiry,"option_type":side,"strike_price":sx,
                      "previous_oi":oy,"current_oi":ox,"oi_change":ox-oy})
    prev_rows=[{"option_type":r["option_type"],"strike_price":r["strike_price"],"oi_contracts":r["previous_oi"]} for r in valid]
    cur_rows=[{"option_type":r["option_type"],"strike_price":r["strike_price"],"oi_contracts":r["current_oi"]} for r in valid]
    pdist,cdist=_distribution(prev_rows),_distribution(cur_rows)
    delta={"CALL":{},"PUT":{}}
    for r in valid:
        s,k=r["option_type"],r["strike_price"]; delta[s][k]=delta[s].get(k,0)+r["oi_change"]
    ptotal=sum(r["previous_oi"] for r in valid); ctotal=sum(r["current_oi"] for r in valid)
    change=sum(r["oi_change"] for r in valid); diff=(ctotal-ptotal)-change
    return {"expiry_date":expiry,"status":"OK" if valid else "INSUFFICIENT_DATA",
        "coverage":{"current_product_ids":len(a),"previous_product_ids":len(b),"matched_product_ids":len(common),
            "valid_matched_product_ids":len(valid),"unmatched_current_product_ids":sorted(set(a)-set(b)),
            "unmatched_previous_product_ids":sorted(set(b)-set(a)),"invalid_matched_product_ids":invalid},
        "arithmetic_reconciliation":{"previous_matched_cohort_oi":ptotal,"current_matched_cohort_oi":ctotal,
            "net_oi_change":ctotal-ptotal,"sum_of_contract_changes":change,"difference":diff,
            "status":"PASS" if math.isclose(diff,0,rel_tol=1e-12,abs_tol=1e-9) else "FAIL"},
        "call_oi_change":sum(delta["CALL"].values()),"put_oi_change":sum(delta["PUT"].values()),
        "combined_oi_change":change,"previous_concentration":pdist,"current_concentration":cdist,
        "strike_oi_change":delta,"matched_contract_changes":valid}

def _regions(distribution, boundaries):
    if boundaries is None: return {"status":"NOT_CALCULATED","reason":"FIXED_REGION_BOUNDARIES_NOT_SUPPLIED"}
    checked=[]; last=-math.inf
    for label,lo,hi in boundaries:
        lo,hi=float(lo),float(hi)
        if not label or not math.isfinite(lo) or not math.isfinite(hi) or lo >= hi or lo < last:
            raise ValueError("Region boundaries must be finite, ordered and non-overlapping")
        checked.append((str(label),lo,hi)); last=hi
    return {"status":"OK","boundaries":[{"label":n,"lower_inclusive":lo,"upper_exclusive":hi} for n,lo,hi in checked],
        "oi_by_region":{side:{n:sum(v for k,v in strikes.items() if lo<=k<hi) for n,lo,hi in checked}
                        for side,strikes in distribution["strike_oi"].items()}}

def calculate_oi_migration(current_rows, previous_rows, *, current_snapshot_id, previous_snapshot_id,
    current_completed_at, previous_completed_at, selected_expiry, eligible_expiries, region_boundaries=None):
    """Use only common valid Product IDs; ambiguous duplicate endpoints fail closed."""
    cur,cd=_index(current_rows,"current"); prev,pd=_index(previous_rows,"previous")
    provenance={"current_snapshot_id":current_snapshot_id,"previous_snapshot_id":previous_snapshot_id,
        "current_completed_at":current_completed_at,"previous_completed_at":previous_completed_at}
    if not cur or not prev:
        return {"status":"WITHHELD","reason":"ENDPOINT_IDENTITY_VALIDATION_FAILED","provenance":provenance,
                "endpoint_validation":[cd,pd],"exchange_wide_completeness":"UNVERIFIED"}
    expiries=sorted(set(map(str,eligible_expiries)))
    if selected_expiry not in expiries: return {"status":"WITHHELD","reason":"SELECTED_EXPIRY_NOT_ELIGIBLE","provenance":provenance}
    by_expiry={e:_expiry(cur,prev,e) for e in expiries}; nearest=by_expiry[selected_expiry]
    cohort=[r for result in by_expiry.values() for r in result["matched_contract_changes"]]
    p_rows=[{"option_type":r["option_type"],"strike_price":r["strike_price"],"oi_contracts":r["previous_oi"]} for r in cohort]
    c_rows=[{"option_type":r["option_type"],"strike_price":r["strike_price"],"oi_contracts":r["current_oi"]} for r in cohort]
    p_dist,c_dist=_distribution(p_rows),_distribution(c_rows); delta={"CALL":{},"PUT":{}}
    for r in cohort:
        s,k=r["option_type"],r["strike_price"]; delta[s][k]=delta[s].get(k,0)+r["oi_change"]
    pt=sum(r["previous_oi"] for r in cohort); ct=sum(r["current_oi"] for r in cohort); change=sum(r["oi_change"] for r in cohort)
    diff=(ct-pt)-change; recon={"previous_matched_cohort_oi":pt,"current_matched_cohort_oi":ct,
        "net_oi_change":ct-pt,"sum_of_contract_changes":change,"difference":diff,
        "status":"PASS" if math.isclose(diff,0,rel_tol=1e-12,abs_tol=1e-9) else "FAIL"}
    now,then=_dt(current_completed_at),_dt(previous_completed_at)
    elapsed=(now-then).total_seconds() if now and then else None
    return {"status":"OK" if nearest["status"]=="OK" else "INSUFFICIENT_DATA","methodology_version":VERSION,
        "provenance":{**provenance,"elapsed_seconds":elapsed,"selected_nearest_expiry":selected_expiry},
        "validity":{"snapshot_pair_validation":"ADAPTER_VALIDATED","matched_cohort_validity":"VALID" if cohort else "NO_VALID_MATCHED_COHORT",
            "arithmetic_reconciliation":recon["status"],"exchange_wide_completeness":"UNVERIFIED"},
        "cross_expiry_arithmetic_reconciliation":recon,"nearest_expiry":nearest,"expiry_specific":by_expiry,
        "cross_expiry_same_numerical_strike":{"status":"OK" if cohort else "INSUFFICIENT_DATA",
            "aggregation_basis":"COMMON_VALID_MATCHED_PRODUCT_IDS_ACROSS_ELIGIBLE_EXPIRIES","expiry_breakdown_retained":True,
            "matched_contract_count":len(cohort),"previous_concentration":p_dist,"current_concentration":c_dist,
            "strike_oi_change":delta},
        "strike_region_concentration":{"nearest_expiry":_regions(nearest["current_concentration"],region_boundaries),
            "cross_expiry_same_numerical_strike":_regions(c_dist,region_boundaries)},"endpoint_validation":[cd,pd]}
