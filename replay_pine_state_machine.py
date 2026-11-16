#!/usr/bin/env python3
"""Read-only replay: legacy dashboard auction state vs Pine v7.2.2 port."""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from pine_state_machine import Candle, PineConfig, PineStateMachine, STATE_NAMES

UTC = timezone.utc
BAR = timedelta(minutes=30)
START = datetime(2026, 11, 11, tzinfo=UTC)
END = datetime(2026, 11, 17, tzinfo=UTC)


def dt(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def db_rows(start, end):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_ANON_KEY (read-only key preferred).")
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("Install repository dependencies: pip install -r requirements.txt") from exc
    client = create_client(url, key)
    response = (client.table("amt_snapshots")
        .select("id,candle_open_time,open_price,high_price,low_price,close_price,prior_vah,prior_val,prior_poc,state_output")
        .eq("exchange", "BINANCE").eq("symbol", "BTCUSDT").eq("timeframe", "30")
        .eq("data_status", "VALID").gte("candle_open_time", start.isoformat())
        .lt("candle_open_time", end.isoformat()).order("candle_open_time")
        .range(0, 999).execute())
    rows = []
    for row in response.data or []:
        meta = row.get("state_output") or {}
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except json.JSONDecodeError: meta = {}
        if meta.get("completed") is True:
            row["time"] = dt(row["candle_open_time"])
            rows.append(row)
    rows.sort(key=lambda x: x["time"])
    return rows


def binance_history(last_target):
    session = requests.Session()
    retry = Retry(total=4, connect=4, read=4, backoff_factor=0.8,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["GET"]))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    end_ms = int((last_target + BAR - timedelta(milliseconds=1)).timestamp() * 1000)
    params = {"symbol": "BTCUSDT", "interval": "30m", "endTime": end_ms, "limit": 1000}
    error = None
    for host in ("https://api.binance.com", "https://api1.binance.com",
                 "https://api2.binance.com", "https://data-api.binance.vision"):
        try:
            response = session.get(host + "/api/v3/klines", params=params, timeout=20)
            response.raise_for_status()
            raw = response.json()
            candles = [Candle(datetime.fromtimestamp(int(x[0])/1000, UTC),
                              float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in raw]
            candles.sort(key=lambda x: x.open_time)
            if len(candles) < 500: raise RuntimeError(f"Only {len(candles)} warm-up candles")
            return candles
        except Exception as exc:
            error = exc
    raise RuntimeError(f"Binance history fetch failed: {error}")


def verify_and_join(rows, history, expected_rows=282, expected_gap_fills=1):
    if len(rows) != expected_rows:
        raise RuntimeError(f"Expected {expected_rows} completed snapshots; found {len(rows)}. Reconcile extraction.")
    db = {r["time"]: r for r in rows}
    market = {c.open_time: c for c in history}
    if len(db) != len(rows) or len(market) != len(history):
        raise RuntimeError("Duplicate timestamps found.")
    for ts, row in db.items():
        c = market.get(ts)
        if c is None: raise RuntimeError(f"Snapshot candle absent from Binance: {ts}")
        values = [float(row[k]) for k in ("open_price", "high_price", "low_price", "close_price")]
        if any(abs(a-b) > 0.005 for a,b in zip(values, (c.open,c.high,c.low,c.close))):
            raise RuntimeError(f"Supabase/Binance OHLC mismatch at {ts}: {values}")
    first, last = rows[0]["time"], rows[-1]["time"]
    expected = []
    ts = first
    while ts <= last:
        expected.append(ts); ts += BAR
    target = [c for c in history if first <= c.open_time <= last]
    if [c.open_time for c in target] != expected:
        raise RuntimeError("Binance target history has a missing 30m interval.")
    gap_fills = len(set(expected) - set(db))
    if gap_fills != expected_gap_fills:
        raise RuntimeError(f"Expected {expected_gap_fills} Binance gap fills; got {gap_fills}.")
    if sum(c.open_time < first for c in history) < 500:
        raise RuntimeError("Insufficient warm-up history before the target window.")
    for a,b in zip(history, history[1:]):
        if b.open_time-a.open_time != BAR:
            raise RuntimeError(f"Warm-up gap: {a.open_time} -> {b.open_time}")
    return history, db, gap_fills


def profile(candles, row=50.0, value_pct=.70):
    if not candles: return None, None, None
    base=floor(min(c.low for c in candles)/row)
    top=floor(max(c.high for c in candles)/row)
    counts=[0]*(top-base+1)
    for c in candles:
        lo=max(0,floor(c.low/row)-base); hi=min(len(counts)-1,floor(c.high/row)-base)
        for i in range(lo,hi+1): counts[i]+=1
    maximum=max(counts); tied=[i for i,v in enumerate(counts) if v==maximum]
    mid=(len(counts)-1)/2
    poc=min(tied,key=lambda i:abs(i-mid))
    target=ceil(sum(counts)*value_pct); covered=counts[poc]; lo=hi=poc
    while covered<target:
        down=counts[lo-1] if lo>0 else -1
        up=counts[hi+1] if hi<len(counts)-1 else -1
        if down==-1 and up==-1: break
        if up>down: hi+=1; covered+=up
        elif down>up: lo-=1; covered+=down
        else:
            if lo>0: lo-=1; covered+=down
            if covered<target and hi<len(counts)-1: hi+=1; covered+=up
    return (base+hi)*row+row/2,(base+lo)*row+row/2,(base+poc)*row+row/2


def legacy_replay(history):
    # Reproduces generate_state_journal() from main commit
    # 2bde6aa4cd900761a1a91132d71368ece678bc2a, evaluated per UTC profile day.
    atrs=[]; prev=None; atr=None
    for c in history:
        tr=c.high-c.low if prev is None else max(c.high-c.low,abs(c.high-prev),abs(c.low-prev))
        atr=tr if atr is None else .95*atr+.05*tr
        atrs.append(atr); prev=c.close
    days=defaultdict(list)
    for i,c in enumerate(history): days[c.open_time.strftime("%Y-%m-%d")].append(i)
    result={}; day_names=sorted(days)
    for dpos,day in enumerate(day_names):
        if dpos==0: continue
        today=days[day]; prevday=days[day_names[dpos-1]]
        pvah,pval,ppoc=profile([history[i] for i in prevday])
        if pvah is None: continue
        state=0; disc=0; cycle=0
        above=below=inside=down_retests=0; down_accept=False
        poc_up=poc_down=reentry=0
        prev_vah=prev_val=prev_poc=None
        low_since=high_since=None; no_low=no_high=0
        for local,idx in enumerate(today):
            c=history[idx]; cycle+=1
            vah,val,poc=profile([history[j] for j in today[:local+1]])
            if c.close>pvah: above+=1; below=inside=0
            elif c.close<pval: below+=1; above=inside=0
            else: above=below=0; inside+=1
            if poc>pvah: poc_up+=1; poc_down=0
            elif poc<pval: poc_down+=1; poc_up=0
            else: poc_up=poc_down=0
            wide=(c.high-c.low)>atrs[idx]*1.5
            timeout=max(3,min(20,round(6*(atrs[idx]/250.0))))
            if below>=2:
                if c.high>=pval and c.close<pval and wide:
                    down_retests+=1
                    if down_retests>=2: down_accept=True
                elif c.close>pval: down_retests=0; down_accept=False
            else: down_retests=0; down_accept=False
            migration=(prev_vah is not None and vah<prev_vah and val<prev_val
                       and prev_poc is not None and prev_poc-poc>=50)
            new_down=poc_down>=2 and cycle>=3
            if state in (3,16):
                if low_since is None or c.low<low_since: low_since=c.low; no_low=0
                else: no_low+=1
            else: low_since=None; no_low=0
            if state in (2,15):
                if high_since is None or c.high>high_since: high_since=c.high; no_high=0
                else: no_high+=1
            else: high_since=None; no_high=0
            td=no_low>=timeout; tu=no_high>=timeout
            if state==0:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif cycle>=3: state=1
            elif state==1:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
            elif state==2:
                if c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif c.close<=pvah: state=17
                elif tu: state=15
            elif state==3:
                if down_accept: state=5
                elif c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close>=pval: state=18
                elif td: state=16
            elif state==5:
                if migration: state=7
                elif c.close>=pval: state=13
            elif state==7:
                if new_down: state=9
                elif c.close>=pval: state=13
            elif state==15:
                if c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif pval<=c.close<=pvah: state=17
                elif c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
            elif state==16:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif pval<=c.close<=pvah: state=11
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
            elif state==17:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif inside>=2: state=10
            elif state==18:
                if c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif inside>=2: state=11
            elif state==10:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif inside>=2: state=14
            elif state==11:
                if c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                elif c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif inside>=2: state=14
            elif state==14:
                if c.close>pvah: state=2; disc=local; high_since=c.high; no_high=0
                elif c.close<pval: state=3; disc=local; low_since=c.low; no_low=0
                else:
                    reentry+=1
                    if reentry>=3 and cycle>=3: state=1; reentry=0
            if state!=14: reentry=0
            prev_vah,prev_val,prev_poc=vah,val,poc
            result[c.open_time]={"legacy_state_code":state,"legacy_state_name":{
                0:"BUILDING",1:"DEVELOPMENT",2:"DISCOVERY ABOVE",3:"DISCOVERY BELOW",
                4:"ACCEPTANCE UP",5:"ACCEPTANCE DOWN",6:"POC BREAK ABOVE",7:"POC BREAK BELOW",
                8:"NEW AREA UP",9:"NEW AREA DOWN",10:"FAILED DISC UP",11:"FAILED DISC DOWN",
                12:"ACC FAILURE UP",13:"ACC FAILURE DOWN",14:"RE-ENTRY CONFIRMED",
                15:"STALLED ABOVE",16:"STALLED BELOW",17:"FAILED UP (CAND)",18:"FAILED DOWN (CAND)"
            }[state],"legacy_prior_vah":pvah,"legacy_prior_val":pval,"legacy_prior_poc":ppoc,
            "legacy_developing_vah":vah,"legacy_developing_val":val,"legacy_developing_poc":poc,
            "legacy_atr20_ewm":atrs[idx]}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start",default=START.isoformat())
    parser.add_argument("--end-exclusive",default=END.isoformat())
    parser.add_argument("--expected-db-rows",type=int,default=282)
    parser.add_argument("--expected-gap-fills",type=int,default=1)
    parser.add_argument("--output-dir",default="replay_output")
    args=parser.parse_args()
    start,end=dt(args.start),dt(args.end_exclusive)
    rows=db_rows(start,end)
    if len(rows)!=args.expected_db_rows:
        raise RuntimeError(f"Expected {args.expected_db_rows} rows, found {len(rows)}")
    history=binance_history(rows[-1]["time"])
    history,db,gap_fills=verify_and_join(rows,history,args.expected_db_rows,args.expected_gap_fills)
    old=legacy_replay(history)
    pine=PineStateMachine(PineConfig()).replay(history)
    pine_by_time={r.open_time:r for r in pine}
    target=[c for c in history if rows[0]["time"]<=c.open_time<=rows[-1]["time"]]
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    all_rows=[]; divergences=[]; matrix=defaultdict(Counter)
    for c in target:
        ts=c.open_time; a=old[ts]; b=pine_by_time[ts]
        match=a["legacy_state_code"]==b.state_code
        source="SUPABASE_SNAPSHOT" if ts in db else "BINANCE_GAP_FILL"
        row={"candle_open_time_utc":ts.isoformat(),"source":source,
             "amt_snapshot_id":db[ts]["id"] if ts in db else "",
             "open":c.open,"high":c.high,"low":c.low,"close":c.close,
             "db_prior_vah":db[ts].get("prior_vah","") if ts in db else "",
             "db_prior_val":db[ts].get("prior_val","") if ts in db else "",
             "db_prior_poc":db[ts].get("prior_poc","") if ts in db else "",
             **a,"pine_state_code":b.state_code,"pine_state_name":b.state_name,
             "pine_previous_state_code":b.previous_state_code,
             "pine_prior_vah":b.prior_vah,"pine_prior_val":b.prior_val,"pine_prior_poc":b.prior_poc,
             "pine_developing_vah":b.developing_vah,"pine_developing_val":b.developing_val,
             "pine_developing_poc":b.developing_poc,"pine_atr20_rma":b.atr20,
             "pine_row_size":b.row_size,"pine_cycle_bars":b.cycle_bars,
             "pine_transition_event":b.transition_event or "",
             "pine_development_stalled":b.development_stalled,"state_match":match}
        all_rows.append(row); matrix[a["legacy_state_name"]][b.state_name]+=1
        if not match: divergences.append(row)
    fields=list(all_rows[0])
    for name,data in (("state_machine_comparison.csv",all_rows),
                      ("state_machine_divergences.csv",divergences)):
        with (out/name).open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(data)
    matched=sum(r["state_match"] for r in all_rows)
    summary={"reference":"BTC_AMT_Observer_v7.2.2.pine",
             "legacy_source_commit":"2bde6aa4cd900761a1a91132d71368ece678bc2a",
             "database_snapshot_count":len(rows),"target_replay_count":len(all_rows),
             "binance_gap_fill_count":gap_fills,"warmup_candles":sum(c.open_time<rows[0]["time"] for c in history),
             "legacy_pine_port_matches":matched,"divergence_count":len(divergences),
             "agreement_pct":round(100*matched/len(all_rows),4),
             "confusion_matrix":{k:dict(v) for k,v in matrix.items()},
             "warning":"Python port vs legacy comparison is not TradingView runtime parity. Export Pine 'Auction State Code' from TradingView for final parity proof."}
    (out/"state_machine_comparison_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
