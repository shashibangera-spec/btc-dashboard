
"""
BTC AMT RESEARCH DASHBOARD
AMT + Price Action + Futures OI + Options Positioning

Primary venue: Delta Exchange India
Primary expiry cycle: 17:30 IST to 17:30 IST
Primary timeframe: 30 minutes
Timezone: Asia/Kolkata

Public endpoints only. No authentication required.

IMPORTANT:
Volume Profile is an OHLCV range-distributed approximation.
True trade-level volume-at-price requires trade-level data.
"""

import re
import time
import logging
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="BTC AMT Research",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="collapsed"
)

IST = ZoneInfo("Asia/Kolkata")

BINANCE_BASE = "https://api.binance.com"
DELTA_BASE = "https://api.india.delta.exchange"

BINANCE_KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"

DELTA_TICKERS_URL = f"{DELTA_BASE}/v2/tickers"
DELTA_PRODUCTS_URL = f"{DELTA_BASE}/v2/products"

TIMEFRAME = "30m"
CANDLE_LIMIT = 1000

TPO_ROW_SIZE = 50.0
VALUE_AREA_PERCENT = 0.70

REFRESH_INTERVAL_MS = 300000

REQUEST_TIMEOUT = 20

logging.basicConfig(level=logging.ERROR)


# ============================================================
# 2. CUSTOM STYLING
# ============================================================

st.markdown(
    """
    <style>

    .main {
        background-color: #0e1117;
    }

    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }

    div[data-testid="stMetric"] {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 15px;
        border-radius: 10px;
    }

    div[data-testid="stMetricLabel"] {
        color: #aeb8c4;
    }

    div[data-testid="stMetricValue"] {
        color: #ffffff;
    }

    .section-header {
        font-size: 19px;
        font-weight: 600;
        margin-top: 12px;
        margin-bottom: 12px;
    }

    .info-text {
        color: #9da7b3;
        font-size: 12px;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# 3. HTTP SESSION
# ============================================================

@st.cache_resource
def get_session():

    session = requests.Session()

    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "BTC-AMT-Research-Dashboard/1.0"
    })

    return session


SESSION = get_session()


def api_get(url, params=None):

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):

        if data.get("success") is False:

            raise RuntimeError(
                str(data.get("error", "API returned unsuccessful response"))
            )

    return data


def to_float(value, default=np.nan):

    try:

        if value is None:
            return default

        result = float(value)

        if not np.isfinite(result):
            return default

        return result

    except (ValueError, TypeError):

        return default


def get_result(data):

    if isinstance(data, dict):

        return data.get("result", [])

    return data


# ============================================================
# 4. BINANCE OHLC DATA
# ============================================================

def fetch_binance_klines():

    params = {
        "symbol": "BTCUSDT",
        "interval": TIMEFRAME,
        "limit": CANDLE_LIMIT
    }

    data = api_get(
        BINANCE_KLINES_URL,
        params=params
    )

    if not isinstance(data, list) or not data:

        raise RuntimeError("No Binance candle data returned.")

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore"
    ]

    df = pd.DataFrame(data, columns=columns)

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    ).dt.tz_convert(IST)

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    ).dt.tz_convert(IST)

    df = df.dropna(
        subset=["open", "high", "low", "close", "volume"]
    )

    df = df.sort_values("open_time")

    return df.reset_index(drop=True)


# ============================================================
# 5. DELTA PAGINATED DATA RETRIEVAL
# ============================================================

def fetch_paginated_delta(
    url,
    params,
    page_size=1000,
    max_pages=30
):
    """
    Fetches Delta API pages using meta.after.

    Some Delta endpoints, including tickers, may return
    all matching records in one response without pagination
    metadata. The loop stops when meta.after is absent.
    """

    results = []

    current_params = params.copy()

    current_params["page_size"] = page_size

    cursor_history = set()

    for page in range(max_pages):

        data = api_get(
            url,
            params=current_params
        )

        page_results = get_result(data)

        if isinstance(page_results, dict):

            page_results = [page_results]

        if not isinstance(page_results, list):

            break

        results.extend(page_results)

        meta = data.get("meta", {}) if isinstance(data, dict) else {}

        next_cursor = meta.get("after")

        if not next_cursor:

            break

        if next_cursor in cursor_history:

            break

        cursor_history.add(next_cursor)

        current_params["after"] = next_cursor

        time.sleep(0.15)

    return results


# ============================================================
# 6. DELTA PRODUCTS
# ============================================================

def fetch_delta_products():

    params = {
        "states": "live",
        "page_size": 1000
    }

    try:

        return fetch_paginated_delta(
            DELTA_PRODUCTS_URL,
            params,
            page_size=1000
        )

    except requests.HTTPError:

        # Some API configurations may reject page_size=1000.
        # Retry using a smaller page size and continue pagination.

        return fetch_paginated_delta(
            DELTA_PRODUCTS_URL,
            {
                "states": "live",
                "page_size": 500
            },
            page_size=500
        )


# ============================================================
# 7. DELTA FUTURES DATA
# ============================================================

def fetch_delta_futures():

    params = {
        "contract_types": "perpetual_futures"
    }

    data = api_get(
        DELTA_TICKERS_URL,
        params=params
    )

    tickers = get_result(data)

    if isinstance(tickers, dict):

        tickers = [tickers]

    btc_ticker = None

    for ticker in tickers:

        if ticker.get("symbol") == "BTCUSD":

            btc_ticker = ticker

            break

    if btc_ticker is None:

        raise RuntimeError(
            "BTCUSD perpetual ticker not found on Delta."
        )

    return {
        "symbol": btc_ticker.get("symbol"),

        "funding_rate": to_float(
            btc_ticker.get("funding_rate")
        ),

        "mark_price": to_float(
            btc_ticker.get("mark_price")
        ),

        "spot_price": to_float(
            btc_ticker.get("spot_price")
        ),

        "oi_value_usd": to_float(
            btc_ticker.get("oi_value_usd")
        ),

        "timestamp": btc_ticker.get("timestamp")
    }


# ============================================================
# 8. OPTION EXPIRY PARSER
# ============================================================

def parse_option_symbol(symbol):

    """
    Expected symbol structure:

    C-BTC-90000-091126
    P-BTC-90000-091126

    Expiry format: DDMMYY
    """

    if not symbol:

        return None

    pattern = (
        r"^(C|P)-BTC-"
        r"([0-9]+(?:\.[0-9]+)?)-"
        r"(\d{6})$"
    )

    match = re.match(pattern, symbol)

    if not match:

        return None

    option_type = match.group(1)

    strike = float(match.group(2))

    expiry_text = match.group(3)

    try:

        expiry_date = datetime.strptime(
            expiry_text,
            "%d%m%y"
        ).date()

    except ValueError:

        return None

    return {
        "symbol": symbol,
        "option_type": option_type,
        "strike": strike,
        "expiry_date": expiry_date
    }


def get_nearest_expiry(products, tickers, now_ist):

    """
    Finds the nearest non-expired BTC options expiry.

    Product symbols and ticker symbols are both inspected.
    """

    expiry_dates = set()

    for product in products:

        symbol = product.get("symbol", "")

        parsed = parse_option_symbol(symbol)

        if parsed:

            expiry_dates.add(parsed["expiry_date"])

    for ticker in tickers:

        symbol = ticker.get("symbol", "")

        parsed = parse_option_symbol(symbol)

        if parsed:

            expiry_dates.add(parsed["expiry_date"])

    valid_expiries = []

    for expiry in expiry_dates:

        expiry_datetime = datetime.combine(
            expiry,
            dt_time(17, 30),
            tzinfo=IST
        )

        if expiry_datetime > now_ist:

            valid_expiries.append(expiry)

    if not valid_expiries:

        return None

    return min(valid_expiries)


# ============================================================
# 9. DELTA OPTIONS CHAIN
# ============================================================

def fetch_delta_options():

    products = fetch_delta_products()

    params = {
        "contract_types": "call_options,put_options",
        "page_size": 1000
    }

    tickers = fetch_paginated_delta(
        DELTA_TICKERS_URL,
        params,
        page_size=1000
    )

    now_ist = datetime.now(IST)

    nearest_expiry = get_nearest_expiry(
        products,
        tickers,
        now_ist
    )

    if nearest_expiry is None:

        raise RuntimeError(
            "No upcoming BTC options expiry found."
        )

    option_rows = []

    for ticker in tickers:

        parsed = parse_option_symbol(
            ticker.get("symbol", "")
        )

        if not parsed:

            continue

        if parsed["expiry_date"] != nearest_expiry:

            continue

        quotes = ticker.get("quotes") or {}

        greeks = ticker.get("greeks") or {}

        bid = to_float(
            quotes.get("best_bid")
        )

        ask = to_float(
            quotes.get("best_ask")
        )

        mark = to_float(
            ticker.get("mark_price")
        )

        last = to_float(
            ticker.get("close")
        )

        if np.isfinite(bid) and np.isfinite(ask) and bid > 0 and ask >= bid:

            mid_price = (bid + ask) / 2

        elif np.isfinite(mark) and mark > 0:

            mid_price = mark

        elif np.isfinite(last) and last > 0:

            mid_price = last

        else:

            mid_price = np.nan

        option_rows.append({

            "symbol": parsed["symbol"],

            "option_type": parsed["option_type"],

            "strike": parsed["strike"],

            "expiry_date": parsed["expiry_date"],

            "oi_usd": to_float(
                ticker.get("oi_value_usd"),
                default=0.0
            ),

            "gamma": to_float(
                greeks.get("gamma")
            ),

            "bid": bid,

            "ask": ask,

            "mark_price": mark,

            "option_price": mid_price,

            "spot_price": to_float(
                ticker.get("spot_price")
            )

        })

    options_df = pd.DataFrame(option_rows)

    if options_df.empty:

        raise RuntimeError(
            "No option tickers found for nearest expiry."
        )

    return nearest_expiry, options_df


# ============================================================
# 10. PRIMARY 17:30 IST DAILY CYCLE
# ============================================================

def get_current_cycle_start(now=None):

    if now is None:

        now = datetime.now(IST)

    boundary = now.replace(
        hour=17,
        minute=30,
        second=0,
        microsecond=0
    )

    if now < boundary:

        boundary -= timedelta(days=1)

    return boundary


def get_cycle_candles(df, cycle_start):

    cycle_end = cycle_start + timedelta(days=1)

    cycle_df = df[
        (df["open_time"] >= cycle_start) &
        (df["open_time"] < cycle_end)
    ].copy()

    return cycle_df.reset_index(drop=True)


# ============================================================
# 11. TPO PRICE ROWS
# ============================================================

def get_price_rows(low, high, row_size=TPO_ROW_SIZE):

    lower = int(np.floor(low / row_size))

    upper = int(np.floor(high / row_size))

    return list(range(lower, upper + 1))


def row_price(row, row_size=TPO_ROW_SIZE):

    return row * row_size + row_size / 2


# ============================================================
# 12. TPO PROFILE AND 70% VALUE AREA
# ============================================================

def calculate_tpo_profile(
    cycle_df,
    row_size=TPO_ROW_SIZE,
    value_area_percent=VALUE_AREA_PERCENT
):

    if cycle_df.empty:

        return None

    tpo_counts = {}

    profile_low = cycle_df["low"].min()

    profile_high = cycle_df["high"].max()

    for _, candle in cycle_df.iterrows():

        rows = get_price_rows(
            candle["low"],
            candle["high"],
            row_size
        )

        for row in rows:

            tpo_counts[row] = (
                tpo_counts.get(row, 0) + 1
            )

    if not tpo_counts:

        return None

    rows = sorted(tpo_counts.keys())

    max_tpo = max(tpo_counts.values())

    candidates = [
        row
        for row, count in tpo_counts.items()
        if count == max_tpo
    ]

    # Midpoint tie-breaker for POC.

    profile_midpoint = (
        profile_low + profile_high
    ) / 2

    midpoint_row = profile_midpoint / row_size

    poc_row = min(
        candidates,
        key=lambda row: (
            abs(row - midpoint_row),
            row
        )
    )

    total_tpo = sum(tpo_counts.values())

    target_tpo = total_tpo * value_area_percent

    included_rows = {poc_row}

    accumulated = tpo_counts[poc_row]

    lower_row = poc_row - 1

    upper_row = poc_row + 1

    while accumulated < target_tpo:

        lower_count = tpo_counts.get(
            lower_row,
            0
        )

        upper_count = tpo_counts.get(
            upper_row,
            0
        )

        if lower_count == 0 and upper_count == 0:

            break

        if lower_count > upper_count:

            included_rows.add(lower_row)

            accumulated += lower_count

            lower_row -= 1

        elif upper_count > lower_count:

            included_rows.add(upper_row)

            accumulated += upper_count

            upper_row += 1

        else:

            # Equal-count tie-breaker:
            # Choose the row closer to the overall
            # profile midpoint.

            lower_distance = abs(
                row_price(lower_row, row_size) -
                profile_midpoint
            )

            upper_distance = abs(
                row_price(upper_row, row_size) -
                profile_midpoint
            )

            if lower_distance <= upper_distance:

                included_rows.add(lower_row)

                accumulated += lower_count

                lower_row -= 1

            else:

                included_rows.add(upper_row)

                accumulated += upper_count

                upper_row += 1

    value_area_low_row = min(included_rows)

    value_area_high_row = max(included_rows)

    profile_table = []

    for row in rows:

        profile_table.append({

            "Price Level": row_price(row, row_size),

            "TPO Count": tpo_counts[row],

            "In Value Area": row in included_rows,

            "POC": row == poc_row

        })

    return {

        "poc": row_price(poc_row, row_size),

        "vah": row_price(
            value_area_high_row,
            row_size
        ),

        "val": row_price(
            value_area_low_row,
            row_size
        ),

        "tpo_counts": tpo_counts,

        "table": pd.DataFrame(profile_table),

        "total_tpo": total_tpo

    }


# ============================================================
# 13. OHLCV VOLUME PROFILE APPROXIMATION
# ============================================================

def calculate_volume_profile(
    cycle_df,
    row_size=TPO_ROW_SIZE
):

    """
    Distributes each 30m candle's volume equally
    among all $50 price rows touched by its range.

    This is an OHLCV approximation, not actual
    trade-level volume-at-price.
    """

    if cycle_df.empty:

        return None

    volume_distribution = {}

    for _, candle in cycle_df.iterrows():

        rows = get_price_rows(
            candle["low"],
            candle["high"],
            row_size
        )

        if not rows:

            continue

        allocated_volume = (
            candle["volume"] / len(rows)
        )

        for row in rows:

            volume_distribution[row] = (
                volume_distribution.get(row, 0.0)
                + allocated_volume
            )

    if not volume_distribution:

        return None

    poc_row = max(
        volume_distribution,
        key=volume_distribution.get
    )

    result = pd.DataFrame([

        {
            "Price Level": row_price(row, row_size),

            "Estimated Volume": volume,

            "POC": row == poc_row

        }

        for row, volume in sorted(
            volume_distribution.items()
        )

    ])

    return {

        "poc": row_price(poc_row, row_size),

        "table": result

    }


# ============================================================
# 14. INITIAL BALANCE
# ============================================================

def calculate_initial_balance(
    cycle_df,
    cycle_start
):

    """
    Initial Balance uses:

    17:30 candle
    18:00 candle

    IST.
    """

    ib_start = cycle_start

    ib_end = cycle_start + timedelta(hours=1)

    ib_df = cycle_df[
        (cycle_df["open_time"] >= ib_start) &
        (cycle_df["open_time"] < ib_end)
    ]

    if len(ib_df) < 2:

        return {

            "high": np.nan,

            "low": np.nan,

            "complete": False

        }

    return {

        "high": ib_df["high"].max(),

        "low": ib_df["low"].min(),

        "complete": True

    }


# ============================================================
# 15. SINGLE PRINTS
# ============================================================

def calculate_single_prints(
    cycle_df,
    row_size=TPO_ROW_SIZE
):

    """
    A single-print price row is touched by
    exactly one 30-minute candle in the cycle.
    """

    if cycle_df.empty:

        return pd.DataFrame(
            columns=[
                "Price Level",
                "Candle Count"
            ]
        )

    candle_counts = {}

    for _, candle in cycle_df.iterrows():

        rows = set(
            get_price_rows(
                candle["low"],
                candle["high"],
                row_size
            )
        )

        for row in rows:

            candle_counts[row] = (
                candle_counts.get(row, 0) + 1
            )

    single_rows = [

        row

        for row, count in candle_counts.items()

        if count == 1

    ]

    result = pd.DataFrame([

        {
            "Price Level": row_price(row, row_size),

            "Candle Count": candle_counts[row]

        }

        for row in sorted(single_rows)

    ])

    return result


# ============================================================
# 16. OPTIONS ANALYTICS
# ============================================================

def calculate_options_metrics(
    options_df,
    current_price
):

    if options_df.empty:

        return None

    calls = options_df[
        options_df["option_type"] == "C"
    ].copy()

    puts = options_df[
        options_df["option_type"] == "P"
    ].copy()

    all_strikes = sorted(
        options_df["strike"].unique()
    )

    if not all_strikes:

        return None

    atm_strike = min(
        all_strikes,
        key=lambda strike: abs(
            strike - current_price
        )
    )

    atm_calls = calls[
        calls["strike"] == atm_strike
    ]

    atm_puts = puts[
        puts["strike"] == atm_strike
    ]

    call_price = np.nan

    put_price = np.nan

    if not atm_calls.empty:

        call_price = atm_calls.iloc[0]["option_price"]

    if not atm_puts.empty:

        put_price = atm_puts.iloc[0]["option_price"]

    straddle = np.nan

    if np.isfinite(call_price) and np.isfinite(put_price):

        straddle = call_price + put_price

    call_wall = None

    put_wall = None

    if not calls.empty:

        call_wall = calls.loc[
            calls["oi_usd"].idxmax()
        ]

    if not puts.empty:

        put_wall = puts.loc[
            puts["oi_usd"].idxmax()
        ]

    return {

        "atm_strike": atm_strike,

        "call_price": call_price,

        "put_price": put_price,

        "straddle": straddle,

        "call_wall": call_wall,

        "put_wall": put_wall,

        "calls": calls,

        "puts": puts

    }


# ============================================================
# 17. EXPIRY COUNTDOWN
# ============================================================

def expiry_countdown(expiry_date, now=None):

    if now is None:

        now = datetime.now(IST)

    expiry_datetime = datetime.combine(
        expiry_date,
        dt_time(17, 30),
        tzinfo=IST
    )

    remaining = expiry_datetime - now

    if remaining.total_seconds() <= 0:

        return "Expired"

    total_seconds = int(
        remaining.total_seconds()
    )

    days, remainder = divmod(
        total_seconds,
        86400
    )

    hours, remainder = divmod(
        remainder,
        3600
    )

    minutes, seconds = divmod(
        remainder,
        60
    )

    return (
        f"{days}d {hours}h "
        f"{minutes}m {seconds}s"
    )


# ============================================================
# 18. DATA FETCH CONTROLLER
# ============================================================

def fetch_all_data():

    results = {}

    errors = []

    successful_fetch_times = []

    # Binance candles

    try:

        results["candles"] = fetch_binance_klines()

        successful_fetch_times.append(
            datetime.now(IST)
        )

    except Exception as exc:

        errors.append(
            f"Binance OHLC: {exc}"
        )

        results["candles"] = None

    # Delta perpetual futures

    try:

        results["futures"] = fetch_delta_futures()

        successful_fetch_times.append(
            datetime.now(IST)
        )

    except Exception as exc:

        errors.append(
            f"Delta Futures: {exc}"
        )

        results["futures"] = None

    # Delta options

    try:

        expiry, options = fetch_delta_options()

        results["expiry"] = expiry

        results["options"] = options

        successful_fetch_times.append(
            datetime.now(IST)
        )

    except Exception as exc:

        errors.append(
            f"Delta Options: {exc}"
        )

        results["expiry"] = None

        results["options"] = None

    results["errors"] = errors

    results["last_successful_fetch"] = (
        max(successful_fetch_times)
        if successful_fetch_times
        else None
    )

    return results


# ============================================================
# 19. DASHBOARD HEADER
# ============================================================

st.title("₿ BTC Auction Market Observer")

st.caption(
    "AMT | Price Action | Futures Participation | Options Positioning"
)

now = datetime.now(IST)

cycle_start = get_current_cycle_start(now)

cycle_end = cycle_start + timedelta(days=1)

col1, col2, col3 = st.columns(3)

with col1:

    st.metric(
        "Primary Cycle",
        "17:30 → 17:30 IST"
    )

with col2:

    st.metric(
        "Primary Timeframe",
        "30 Minutes"
    )

with col3:

    st.metric(
        "TPO Row Size",
        "$50"
    )

st.caption(
    f"Current Cycle: "
    f"{cycle_start.strftime('%d %b %Y %H:%M')} "
    f"to "
    f"{cycle_end.strftime('%d %b %Y %H:%M')} IST"
)

st.divider()


# ============================================================
# 20. FETCH MARKET DATA
# ============================================================

with st.spinner("Fetching public market data..."):

    market_data = fetch_all_data()

candles = market_data["candles"]

futures = market_data["futures"]

options_df = market_data["options"]

expiry_date = market_data["expiry"]

# Show API errors without terminating the application.

for error in market_data["errors"]:

    st.warning(error)

if candles is None:

    st.error(
        "Binance candle data is unavailable. "
        "AMT calculations cannot be completed."
    )

    st.stop()


# ============================================================
# 21. CALCULATE CURRENT AMT CYCLE
# ============================================================

cycle_df = get_cycle_candles(
    candles,
    cycle_start
)

current_price = float(
    candles.iloc[-1]["close"]
)

tpo_profile = calculate_tpo_profile(
    cycle_df
)

volume_profile = calculate_volume_profile(
    cycle_df
)

initial_balance = calculate_initial_balance(
    cycle_df,
    cycle_start
)

single_prints = calculate_single_prints(
    cycle_df
)


# ============================================================
# 22. OPTIONS CALCULATIONS
# ============================================================

options_metrics = None

if options_df is not None:

    options_metrics = calculate_options_metrics(
        options_df,
        current_price
    )


# ============================================================
# 23. DASHBOARD TABS
# ============================================================

tab_amt, tab_futures, tab_options = st.tabs([

    "AMT Structure",

    "Futures Participation",

    "Options Positioning"

])


# ============================================================
# TAB 1: AMT STRUCTURE
# ============================================================

with tab_amt:

    st.subheader("BTC Auction Structure")

    # Current Price

    st.metric(
        "Current BTC Price (Binance BTCUSDT)",
        f"${current_price:,.2f}"
    )

    st.caption(
        f"30-minute candles available in current cycle: "
        f"{len(cycle_df)}"
    )

    st.divider()

    st.markdown(
        '<div class="section-header">Developing TPO Profile</div>',
        unsafe_allow_html=True
    )

    if tpo_profile is not None:

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Developing POC",
            f"${tpo_profile['poc']:,.2f}"
        )

        c2.metric(
            "VAH",
            f"${tpo_profile['vah']:,.2f}"
        )

        c3.metric(
            "VAL",
            f"${tpo_profile['val']:,.2f}"
        )

        st.caption(
            f"70% TPO Value Area | "
            f"Total TPO observations: "
            f"{tpo_profile['total_tpo']}"
        )

        with st.expander(
            "View TPO Price Distribution"
        ):

            tpo_table = tpo_profile["table"].copy()

            tpo_table = tpo_table.sort_values(
                "Price Level",
                ascending=False
            )

            st.dataframe(
                tpo_table,
                use_container_width=True,
                hide_index=True
            )

    else:

        st.warning(
            "Insufficient candles for TPO calculation."
        )

    st.divider()

    # Initial Balance

    st.markdown(
        '<div class="section-header">Initial Balance</div>',
        unsafe_allow_html=True
    )

    ib1, ib2 = st.columns(2)

    if initial_balance["complete"]:

        ib1.metric(
            "Initial Balance High",
            f"${initial_balance['high']:,.2f}"
        )

        ib2.metric(
            "Initial Balance Low",
            f"${initial_balance['low']:,.2f}"
        )

    else:

        ib1.metric(
            "Initial Balance High",
            "Pending"
        )

        ib2.metric(
            "Initial Balance Low",
            "Pending"
        )

        st.info(
            "IB requires both the 17:30 and 18:00 "
            "IST candles of the current cycle."
        )

    st.divider()

    # Single Prints

    st.markdown(
        '<div class="section-header">Single Prints</div>',
        unsafe_allow_html=True
    )

    if not single_prints.empty:

        st.dataframe(
            single_prints.sort_values(
                "Price Level",
                ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "Price rows touched by exactly one "
            "30-minute candle in the current cycle."
        )

    else:

        st.info(
            "No single-print levels identified."
        )

    st.divider()

    # Volume Profile

    st.markdown(
        '<div class="section-header">'
        'Volume Profile (OHLCV Approximation)'
        '</div>',
        unsafe_allow_html=True
    )

    if volume_profile is not None:

        st.metric(
            "Estimated Volume POC",
            f"${volume_profile['poc']:,.2f}"
        )

        st.dataframe(
            volume_profile["table"].sort_values(
                "Price Level",
                ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "Candle volume is equally allocated across "
            "the $50 rows touched by each candle. "
            "This is not trade-level volume-at-price."
        )

    else:

        st.info(
            "Volume Profile unavailable."
        )


# ============================================================
# TAB 2: FUTURES PARTICIPATION
# ============================================================

with tab_futures:

    st.subheader("Delta BTC Perpetual Futures")

    if futures is not None:

        oi = futures["oi_value_usd"]

        funding = futures["funding_rate"]

        mark_price = futures["mark_price"]

        spot_price = futures["spot_price"]

        col1, col2 = st.columns(2)

        col1.metric(
            "Total OI (USD)",
            (
                f"${oi:,.2f}"
                if np.isfinite(oi)
                else "Unavailable"
            )
        )

        col2.metric(
            "Current Funding Rate",
            (
                f"{funding * 100:.6f}%"
                if np.isfinite(funding)
                else "Unavailable"
            )
        )

        st.divider()

        st.markdown(
            '<div class="section-header">'
            'Spot / Perpetual Premium'
            '</div>',
            unsafe_allow_html=True
        )

        if (
            np.isfinite(mark_price)
            and np.isfinite(spot_price)
            and spot_price != 0
        ):

            premium = (
                (mark_price - spot_price)
                / spot_price
            ) * 100

            p1, p2, p3 = st.columns(3)

            p1.metric(
                "Perpetual Mark Price",
                f"${mark_price:,.2f}"
            )

            p2.metric(
                "Spot Price",
                f"${spot_price:,.2f}"
            )

            p3.metric(
                "Spot / Perp Premium",
                f"{premium:+.5f}%"
            )

        else:

            st.warning(
                "Spot or perpetual price unavailable."
            )

        st.divider()

        st.markdown(
            '<div class="section-header">'
            'Participation Interpretation Reference'
            '</div>',
            unsafe_allow_html=True
        )

        interpretation = pd.DataFrame([

            {
                "Price": "Rising",
                "OI": "Rising",
                "Context": "New participation with upward movement"
            },

            {
                "Price": "Rising",
                "OI": "Falling",
                "Context": "Possible short covering / position reduction"
            },

            {
                "Price": "Falling",
                "OI": "Rising",
                "Context": "New participation with downward movement"
            },

            {
                "Price": "Falling",
                "OI": "Falling",
                "Context": "Possible long liquidation / position reduction"
            },

            {
                "Price": "Sideways",
                "OI": "Rising",
                "Context": "Position build-up during balance"
            },

            {
                "Price": "Sideways",
                "OI": "Falling",
                "Context": "Position reduction during balance"
            }

        ])

        st.dataframe(
            interpretation,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "OI is aggregate open contract value. "
            "It does not independently identify "
            "whether buyers or sellers initiated positions."
        )

    else:

        st.warning(
            "Delta perpetual futures data unavailable."
        )


# ============================================================
# TAB 3: OPTIONS POSITIONING
# ============================================================

with tab_options:

    st.subheader("Delta BTC Options Positioning")

    if (
        options_metrics is not None
        and expiry_date is not None
    ):

        st.markdown(
            '<div class="section-header">'
            'Nearest Options Expiry'
            '</div>',
            unsafe_allow_html=True
        )

        now = datetime.now(IST)

        expiry_datetime = datetime.combine(
            expiry_date,
            dt_time(17, 30),
            tzinfo=IST
        )

        countdown = expiry_countdown(
            expiry_date,
            now
        )

        c1, c2 = st.columns(2)

        c1.metric(
            "Nearest Expiry Date",
            expiry_date.strftime("%d %B %Y")
        )

        c2.metric(
            "Time Remaining to 17:30 IST",
            countdown
        )

        st.caption(
            "Expiry reference: 17:30 IST. "
            "Settlement price follows Delta's "
            "published settlement methodology."
        )

        st.divider()

        # ATM STRADDLE

        st.markdown(
            '<div class="section-header">'
            'ATM Strike and Straddle'
            '</div>',
            unsafe_allow_html=True
        )

        atm1, atm2 = st.columns(2)

        atm1.metric(
            "ATM Strike",
            f"${options_metrics['atm_strike']:,.2f}"
        )

        straddle = options_metrics["straddle"]

        atm2.metric(
            "ATM Straddle Price",
            (
                f"${straddle:,.2f}"
                if np.isfinite(straddle)
                else "Unavailable"
            )
        )

        st.caption(
            "Straddle = ATM call premium + ATM put premium. "
            "Premium uses bid/ask midpoint where available, "
            "otherwise mark price or last close."
        )

        st.divider()

        # CALL OI WALL

        st.markdown(
            '<div class="section-header">'
            'Largest Call OI Wall'
            '</div>',
            unsafe_allow_html=True
        )

        call_wall = options_metrics["call_wall"]

        if call_wall is not None:

            call1, call2 = st.columns(2)

            call1.metric(
                "Call Strike",
                f"${call_wall['strike']:,.2f}"
            )

            call2.metric(
                "Call OI (USD)",
                f"${call_wall['oi_usd']:,.2f}"
            )

        else:

            st.info(
                "Call OI data unavailable."
            )

        st.divider()

        # PUT OI WALL

        st.markdown(
            '<div class="section-header">'
            'Largest Put OI Wall'
            '</div>',
            unsafe_allow_html=True
        )

        put_wall = options_metrics["put_wall"]

        if put_wall is not None:

            put1, put2 = st.columns(2)

            put1.metric(
                "Put Strike",
                f"${put_wall['strike']:,.2f}"
            )

            put2.metric(
                "Put OI (USD)",
                f"${put_wall['oi_usd']:,.2f}"
            )

        else:

            st.info(
                "Put OI data unavailable."
            )

        st.divider()

        # OPTIONS CHAIN

        st.markdown(
            '<div class="section-header">'
            'Nearest Expiry Options Chain'
            '</div>',
            unsafe_allow_html=True
        )

        display_columns = [
            "symbol",
            "option_type",
            "strike",
            "oi_usd",
            "gamma",
            "bid",
            "ask",
            "option_price"
        ]

        display_df = options_df[
            display_columns
        ].copy()

        display_df.columns = [
            "Symbol",
            "Type",
            "Strike",
            "OI USD",
            "Gamma",
            "Bid",
            "Ask",
            "Premium"
        ]

        display_df = display_df.sort_values(
            ["Strike", "Type"]
        )

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "Gamma is displayed as supplied by Delta. "
            "It is not an estimate of dealer net gamma exposure."
        )

    else:

        st.warning(
            "Options positioning data is currently unavailable."
        )


# ============================================================
# 24. DATA STATUS AND LAST FETCH TIME
# ============================================================

st.divider()

st.markdown(
    "### Data Status"
)

status1, status2, status3 = st.columns(3)

with status1:

    st.metric(
        "Binance OHLC",
        "Available" if candles is not None else "Unavailable"
    )

with status2:

    st.metric(
        "Delta Futures",
        "Available" if futures is not None else "Unavailable"
    )

with status3:

    st.metric(
        "Delta Options",
        "Available" if options_df is not None else "Unavailable"
    )

last_fetch = market_data["last_successful_fetch"]

if last_fetch is not None:

    st.caption(
        "Last successful data fetch: "
        + last_fetch.strftime(
            "%d-%m-%Y %H:%M:%S %Z"
        )
    )

else:

    st.warning(
        "No successful API fetch was completed."
    )

st.caption(
    "BTC AMT Research | Public market data | "
    "Visual research only | Not an execution system"
)


# ============================================================
# 25. AUTOMATIC REFRESH - EVERY 5 MINUTES
# ============================================================

components.html(
    f"""
    <script>
        const refreshInterval = {REFRESH_INTERVAL_MS};

        setTimeout(function() {{

            window.parent.location.reload();

        }}, refreshInterval);
    </script>
    """,
    height=0
)
