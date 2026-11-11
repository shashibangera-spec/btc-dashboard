# 🔬 BTC Combined Research Terminal

An institutional-grade, automated, multi-exchange research terminal combining Auction Market Theory (AMT), Futures Open Interest, and Options Forensics. 

## 🌐 Live Architecture
The system is fully automated and runs 24/7 in the cloud. No local machine needs to be open. Data is collected via GitHub Actions, stored in Supabase (PostgreSQL), and visualized on Streamlit Cloud.

## 🏗️ System Layers

### Layer 1: Auction Market Theory (AMT)
*   **Files:** `research_engine.py` & `amt_core.py`
*   **Action:** `amt_collector.yml` (Runs at :10 and :40)
*   Calculates the certified AMT state (IN_VALUE, DISCOVERY, BALANCE) using a strict ATR penetration buffer to prevent fakeouts.

### Layer 2: Futures Positioning
*   **Files:** `futures_positioning.py` & `collector.py`
*   **Action:** `collector.yml` (Runs at :07 and :37)
*   Fetches Open Interest, Basis, and Funding Rates from 4 exchanges (Binance, Bybit, OKX, Delta). Calculates a strict 30-minute baseline delta. 

### Layer 3: Options Forensics
*   **Files:** `options_collector.py` & `btc_live_research_dashboard.py`
*   **Action:** `options_collector.yml` (Runs at :08 and :38)
*   Fetches the Delta Exchange India BTC Options chain and Trade Tape. Runs 5 institutional filters:
    1.  **Neighboring Strikes:** Detects OI Walls.
    2.  **IV Skew:** Detects Call vs Put urgency.
    3.  **Volume vs OI:** Detects fresh money entering.
    4.  **OI Migration:** Tracks if the OI walls are shifting up or down.
    5.  **Aggressor Trades:** Sums Taker Buys vs Taker Sells.

## 🛡️ Data Integrity Guardrails
*   **Missing Data:** Strictly saved as `NULL` in Supabase (never `0`).
*   **Baselines:** 30-minute deltas are rejected if snapshots are not between 25-35 minutes apart.
*   **Classifier:** Defaults to "Unclassified Concentration" unless the math proves a signal.

## 🚀 Setup & Secrets
The following secrets are configured in **GitHub Secrets** and **Streamlit Cloud Secrets**:
*   `SUPABASE_URL`
*   `SUPABASE_SERVICE_ROLE_KEY`
