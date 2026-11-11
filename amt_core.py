import pandas as pd
import numpy as np
import requests
import math
from datetime import timezone
from research_engine import ResearchStateEngine, IN_VALUE

def fetch_binance_data():
    url = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=30m&limit=500"
    res = requests.get(url, timeout=10).json()
    
    df = pd.DataFrame(res, columns=['Date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
    df['Date'] = pd.to_datetime(df['Date'], unit='ms', utc=True).dt.tz_convert('Asia/Kolkata')
    
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
        
    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['prev_close'])
    df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/21, adjust=False).mean()
    
    df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
    df = df.dropna(subset=['atr'])
    return df

def calculate_tpo(session_df):
    if session_df.empty: return np.nan, np.nan, np.nan
    row_size = 50.0
    value_pct = 0.70
    high = session_df['high'].max()
    low = session_df['low'].min()
    if high - low <= 0: return np.nan, np.nan, np.nan
    
    base_bucket = math.floor(low / row_size)
    num_buckets = math.floor(high / row_size) - base_bucket + 1
    tpo = np.zeros(num_buckets)
    
    for _, row in session_df.iterrows():
        lo_idx = max(0, int(math.floor(row['low'] / row_size) - base_bucket))
        hi_idx = min(num_buckets - 1, int(math.floor(row['high'] / row_size) - base_bucket))
        for i in range(lo_idx, hi_idx + 1): tpo[i] += 1
            
    poc_idx = np.argmax(tpo)
    max_val = np.max(tpo)
    tied_indices = np.where(tpo == max_val)[0]
    if len(tied_indices) > 1:
        profile_mid = (num_buckets - 1) / 2.0
        distances = np.abs(tied_indices - profile_mid)
        poc_idx = tied_indices[np.argmin(distances)]
        
    target = math.ceil(np.sum(tpo) * value_pct)
    covered = tpo[poc_idx]
    up, dn = poc_idx, poc_idx
    
    while covered < target:
        can_down, can_up = dn > 0, up < num_buckets - 1
        if not can_down and not can_up: break
        d = tpo[dn - 1] if can_down else -1
        u = tpo[up + 1] if can_up else -1
        if u > d: up += 1; covered += u
        elif d > u: dn -= 1; covered += d
        else:
            if can_down: dn -= 1; covered += d
            if covered < target and can_up: up += 1; covered += u
                
    return (base_bucket + up) * row_size + row_size/2, (base_bucket + dn) * row_size + row_size/2, (base_bucket + poc_idx) * row_size + row_size/2
