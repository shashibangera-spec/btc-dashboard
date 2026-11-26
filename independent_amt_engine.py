"""Independent read-only BTC AMT replay from raw Binance OHLC stored in Supabase.

No Pine outputs or amt_snapshots are inputs. TPO is an OHLC approximation:
each completed 30m candle contributes once to each floor-aligned price row
intersected by its high-low range. This is not tick-level Market Profile data.
"""
from __future__ import annotations
import argparse, json, math, os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

IST=ZoneInfo("Asia/Kolkata"); UTC=timezone.utc
ENGINE_VERSION="independent-amt-v1.0"; SESSION_RESET=time(5,30)

class AMTInputError(ValueError): pass

def parse_time(v:Any)->datetime:
    if isinstance(v,datetime): d=v
    elif isinstance(v,(int,float)) and not isinstance(v,bool): d=datetime.fromtimestamp(v/1000,tz=UTC)
    else:
        raw=str(v or "").strip()
        if not raw: raise AMTInputError("Missing timestamp")
        d=datetime.fromisoformat(raw.replace("Z","+00:00"))
    if d.tzinfo is None: raise AMTInputError("Timestamp must include timezone")
    return d.astimezone(UTC)

@dataclass(frozen=True)
class Candle:
    open_time:datetime; close_time:datetime
    open:float; high:float; low:float; close:float; volume:float=0.0
    @classmethod
    def from_record(cls,r:dict[str,Any])->"Candle":
        try:
            c=cls(parse_time(r["open_time"]),parse_time(r["close_time"]),
                  float(r["open_price"]),float(r["high_price"]),float(r["low_price"]),
                  float(r["close_price"]),float(r.get("base_volume") or 0))
        except (KeyError,TypeError,ValueError,OverflowError) as e: raise AMTInputError(f"Invalid OHLC record: {e}") from e
        if not all(math.isfinite(x) for x in (c.open,c.high,c.low,c.close,c.volume)): raise AMTInputError("Non-finite OHLCV")
        if c.volume<0 or not c.low<=min(c.open,c.close)<=max(c.open,c.close)<=c.high: raise AMTInputError("Invalid OHLCV range")
        if c.close_time<=c.open_time or c.close_time-c.open_time>timedelta(minutes=30,seconds=1): raise AMTInputError("Invalid candle duration")
        return c
    @property
    def session_date(self)->date:
        return (self.open_time.astimezone(IST)-timedelta(hours=5,minutes=30)).date()

@dataclass(frozen=True)
class Profile:
    vah:float; poc:float; val:float; total_tpo:int; row_size:float; value_area_percent:float
    rows:tuple[tuple[float,int],...]
    def as_dict(self):
        return {"vah":self.vah,"poc":self.poc,"val":self.val,"total_tpo":self.total_tpo,
                "row_size":self.row_size,"value_area_percent":self.value_area_percent,
                "rows":[{"price":p,"tpo":n} for p,n in self.rows]}

def normalize_candles(records:Iterable[dict[str,Any]],now:datetime|None=None)->list[Candle]:
    now=(now or datetime.now(UTC)).astimezone(UTC); found={}
    for r in records:
        c=Candle.from_record(r)
        if c.close_time>now: continue
        if c.open_time in found: raise AMTInputError(f"Duplicate candle {c.open_time.isoformat()}")
        found[c.open_time]=c
    return [found[k] for k in sorted(found)]

def calculate_tpo_profile(candles:Iterable[Candle],row_size:float=50.0,value_area_percent:float=.70)->Profile:
    bars=list(candles)
    if not bars: raise AMTInputError("No candles for profile")
    if not math.isfinite(row_size) or row_size<=0: raise AMTInputError("row_size must be positive")
    if not 0<value_area_percent<=1: raise AMTInputError("value_area_percent must be in (0,1]")
    counts={}
    for c in bars:
        for i in range(math.floor(c.low/row_size),math.floor(c.high/row_size)+1): counts[i]=counts.get(i,0)+1
    lo,hi=min(counts),max(counts); total=sum(counts.values()); maximum=max(counts.values())
    tied=[i for i in range(lo,hi+1) if counts.get(i,0)==maximum]
    # Match the documented Pine profile tie-break: midpoint of the bucket index span.
    profile_mid=(hi-lo)/2.0
    poc=min(tied,key=lambda i:(abs((i-lo)-profile_mid),i))
    target=math.ceil(total*value_area_percent); covered=counts[poc]; lower=upper=poc
    while covered<target and (lower>lo or upper<hi):
        dn=counts.get(lower-1,0) if lower>lo else -1
        up=counts.get(upper+1,0) if upper<hi else -1
        if dn>=0 and up>=0 and dn==up:
            lower-=1; covered+=dn
            if covered<target:
                upper+=1; covered+=up
        elif up>dn: upper+=1; covered+=up
        elif dn>=0: lower-=1; covered+=dn
        else: upper+=1; covered+=up
    px=lambda i:(i+.5)*row_size
    return Profile(px(upper),px(poc),px(lower),total,row_size,value_area_percent,
                   tuple((px(i),counts.get(i,0)) for i in range(lo,hi+1)))

def wilder_atr(candles:list[Candle],period:int=21)->list[float|None]:
    if period<1: raise AMTInputError("ATR period must be positive")
    out=[]; trs=[]; prev=None; atr=None
    for c in candles:
        tr=c.high-c.low
        if prev is not None: tr=max(tr,abs(c.high-prev),abs(c.low-prev))
        trs.append(tr)
        if len(trs)<period: out.append(None)
        elif len(trs)==period: atr=sum(trs[-period:])/period; out.append(atr)
        else: atr=((period-1)*atr+tr)/period; out.append(atr)
        prev=c.close
    return out

def classify_value(prior:Profile,dev:Profile)->str:
    r=prior.row_size
    if dev.val>prior.vah:return "VALUE_SEPARATED_ABOVE"
    if dev.vah<prior.val:return "VALUE_SEPARATED_BELOW"
    dp,dh,dl=dev.poc-prior.poc,dev.vah-prior.vah,dev.val-prior.val
    if dp>=r and dh>=r and dl>=r:return "BROAD_MIGRATION_UP"
    if dp<=-r and dh<=-r and dl<=-r:return "BROAD_MIGRATION_DOWN"
    if dp>=r:return "POC_MIGRATION_UP"
    if dp<=-r:return "POC_MIGRATION_DOWN"
    if dh>=r or dl>=r:return "PARTIAL_MIGRATION_UP"
    if dh<=-r or dl<=-r:return "PARTIAL_MIGRATION_DOWN"
    return "VALUE_OVERLAPPING"

def _price_state(close,prior,buffer,confirmed,candidate,count,reentry):
    side="BELOW" if close<prior.val-buffer else "ABOVE" if close>prior.vah+buffer else None
    if confirmed:
        if prior.val<=close<=prior.vah:
            reentry+=1
            return ("REENTRY_CONFIRMED",None,None,0,0) if reentry>=2 else ("REENTRY_CANDIDATE",confirmed,None,0,reentry)
        if side==confirmed:return f"DISCOVERY_CONFIRMED_{confirmed}",confirmed,None,0,0
        if side and side!=confirmed:return f"DISCOVERY_CANDIDATE_{side}",None,side,1,0
        return "VALUE_EXCURSION",confirmed,None,0,0
    if side:
        n=count+1 if candidate==side else 1
        return (f"DISCOVERY_CONFIRMED_{side}",side,None,0,0) if n>=2 else (f"DISCOVERY_CANDIDATE_{side}",None,side,n,0)
    if prior.val<=close<=prior.vah:return "IN_VALUE",None,None,0,0
    # A close that does not clear the buffered boundary breaks consecutive confirmation.
    return "VALUE_EXCURSION",None,None,0,0

def build_session_snapshots(candles:list[Candle],row_size=50.0,value_area_percent=.70,
                            atr_period=21,minimum_buffer=50.0,atr_multiplier=.50):
    if not candles:return []
    atrs=wilder_atr(candles,atr_period); sessions={}
    for i,c in enumerate(candles):sessions.setdefault(c.session_date,[]).append((i,c))
    days=sorted(sessions); out=[]
    for pos in range(1,len(days)):
        day,prevday=days[pos],days[pos-1]; prev=[c for _,c in sessions[prevday]]
        complete=((day-prevday).days==1 and len(prev)==48
          and prev[0].open_time.astimezone(IST).time().replace(tzinfo=None)==SESSION_RESET
          and prev[-1].open_time.astimezone(IST).time().replace(tzinfo=None)==time(5,0)
          and all(b.open_time-a.open_time==timedelta(minutes=30) for a,b in zip(prev,prev[1:])))
        prior=calculate_tpo_profile(prev,row_size,value_area_percent) if complete else None
        cur=sessions[day]; confirmed=candidate=None; count=reentry=0; last_open=None; last_close=None
        last_poc_side=None; poc_crossings=[]; outside_side=None; outside_streak=0
        for j,(gi,c) in enumerate(cur):
            gap=last_open is not None and c.open_time-last_open>timedelta(minutes=30,seconds=1)
            if gap:
                candidate,count,reentry=None,0,0
                last_poc_side=None; poc_crossings=[]; outside_side=None; outside_streak=0
            dev=calculate_tpo_profile([x for _,x in cur[:j+1]],row_size,value_area_percent)
            atr=atrs[gi]; buf=max(minimum_buffer,atr*atr_multiplier) if atr is not None else None
            raw_side=None; boundary_retest=None; balance_evidence=False; discovery_event_start=False
            if prior is None: ps,vs,read="PRIOR_PROFILE_INCOMPLETE","NOT_CALCULATED","NO_VALID_PRIOR_SESSION_PROFILE"
            elif buf is None: ps,vs,read="WARMUP",classify_value(prior,dev),"INSUFFICIENT_ATR_WARMUP"
            else:
                ps,confirmed,candidate,count,reentry=previously_confirmed=confirmed
                ps,confirmed,candidate,count,reentry=_price_state(c.close,prior,buf,confirmed,candidate,count,reentry)
                discovery_event_start=previously_confirmed is None and confirmed is not None
                vs=classify_value(prior,dev)
                raw_side="ABOVE" if c.close>prior.vah else "BELOW" if c.close<prior.val else None
                if raw_side is None: outside_side,outside_streak=None,0
                elif raw_side==outside_side: outside_streak+=1
                else: outside_side,outside_streak=raw_side,1
                boundary_retest=(
                    "RETEST_RECLAIM_ABOVE" if (not gap and last_close is not None
                        and last_close>prior.vah and c.low<=prior.vah and c.close>prior.vah) else
                    "RETEST_REJECTION_BELOW" if (not gap and last_close is not None
                        and last_close<prior.val and c.high>=prior.val and c.close<prior.val) else None
                )
                poc_side="ABOVE" if dev.poc>prior.poc else "BELOW" if dev.poc<prior.poc else None
                if poc_side is not None:
                    if last_poc_side is not None and poc_side!=last_poc_side: poc_crossings.append(j)
                    last_poc_side=poc_side
                poc_crossings=[k for k in poc_crossings if j-k<5]
                balance_evidence=(len(poc_crossings)>=2 and dev.val<=prior.vah and dev.vah>=prior.val)
                if ps.startswith("DISCOVERY_CONFIRMED_"):
                    direction=ps.rsplit("_",1)[-1]
                    read=f"DISCOVERY_WITH_VALUE_SEPARATION_{direction}" if vs==f"VALUE_SEPARATED_{direction}" else f"PRICE_DISCOVERY_VALUE_NOT_YET_SEPARATED_{direction}"
                elif ps=="REENTRY_CONFIRMED":read="REENTRY_TO_PRIOR_VALUE"
                elif balance_evidence and ps in {"IN_VALUE","VALUE_EXCURSION"}:read="BALANCE_EVIDENCE_WITH_VALUE_OVERLAP"
                elif ps=="IN_VALUE":read="IN_VALUE_ROTATION"
                else:read=ps
            out.append({"engine_version":ENGINE_VERSION,"session_date_ist":day.isoformat(),
              "prior_session_date_ist":prevday.isoformat(),"candle_open_time_utc":c.open_time.isoformat(),
              "candle_close_time_utc":c.close_time.isoformat(),"open":c.open,"high":c.high,
              "low":c.low,"close":c.close,"volume":c.volume,"atr":atr,"discovery_buffer":buf,
              "prior_profile_quality":"COMPLETE" if complete else "INCOMPLETE",
              "prior_value":prior.as_dict() if prior else None,"developing_value":dev.as_dict(),
              "price_state":ps,"discovery_event_start":discovery_event_start,"value_state":vs,"auction_read":read,"gap_before":gap,
              "outside_prior_value_side":raw_side if prior else None,
              "outside_value_close_streak":outside_streak if prior else 0,
              "boundary_retest":boundary_retest if prior else None,
              "poc_side_crossings_in_5":len(poc_crossings) if prior else 0,
              "balance_evidence":balance_evidence if prior else False})
            last_open=c.open_time; last_close=c.close
    return out

def fetch_supabase_candles(page_size:int=1000):
    if page_size<1:raise AMTInputError("page_size must be positive")
    from supabase import create_client
    url=os.getenv("SUPABASE_URL")
    key=os.getenv("SUPABASE_READ_ONLY_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:raise RuntimeError("SUPABASE_URL and a read-only SUPABASE_READ_ONLY_KEY (or permitted SUPABASE_ANON_KEY) are required")
    client=create_client(url,key); cols="exchange,market_type,symbol,interval,open_time,close_time,open_price,high_price,low_price,close_price,base_volume"
    rows=[]; start=0
    while True:
        res=(client.table("binance_ohlc_history").select(cols).eq("exchange","BINANCE")
          .eq("market_type","SPOT").eq("symbol","BTCUSDT").eq("interval","30m")
          .order("open_time").range(start,start+page_size-1).execute())
        batch=res.data or []; rows.extend(batch)
        if len(batch)<page_size:break
        start+=page_size
    return rows

def main():
    p=argparse.ArgumentParser(description="Read-only independent BTC AMT replay")
    p.add_argument("--row-size",type=float,default=50.0);p.add_argument("--value-area",type=float,default=.70)
    p.add_argument("--atr-period",type=int,default=21);a=p.parse_args()
    candles=normalize_candles(fetch_supabase_candles())
    if len(candles)<a.atr_period+2:raise RuntimeError("Insufficient completed candles")
    snaps=build_session_snapshots(candles,a.row_size,a.value_area,a.atr_period)
    if not snaps:raise RuntimeError("Need at least two sessions for replay")
    day=snaps[-1]["session_date_ist"]; latest=[x for x in snaps if x["session_date_ist"]==day]
    print(json.dumps({"engine_version":ENGINE_VERSION,"read_only":True,
      "source":"Supabase raw Binance spot BTCUSDT 30m OHLC",
      "completed_candles_loaded":len(candles),"snapshot_count":len(snaps),
      "latest_session_ist":day,"latest":latest[-1]},indent=2,default=str))
    return 0
if __name__=="__main__":raise SystemExit(main())
