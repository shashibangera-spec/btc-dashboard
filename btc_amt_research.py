        hide_index=True
    )

    st.caption(
        "The dashboard stores a reading about every 5 minutes. "
        "30-minute, 1-hour and 4-hour comparisons become available after enough "
        "readings have accumulated."
    )

    report_text = build_copy_report(
        bias_snapshot,
        bias_assessment,
        options_metrics,
        initial_balance,
        single_prints,
        cycle_df,
        now
    )

    st.divider()
    st.markdown("#### Copy-ready report")
    st.caption("Use the copy icon on the report block, then paste it into ChatGPT.")
    st.code(report_text, language="text")

    with st.expander("History and data-quality notes"):
        st.write(
            "History is held in Streamlit session state. It accumulates automatically "
            "while this browser session remains connected, but it is not durable storage "
            "and may reset after a server restart, session expiry, or a new browser session."
        )
        st.write(
            "The bias is a structured interpretation, not a prediction. Developing TPO "
            "uses 30-minute candle ranges; the volume profile is an OHLCV approximation. "
            "Options OI walls are concentrations, not confirmed support/resistance. "
            "Gamma is not dealer net gamma exposure."
        )
        st.write(
            "The current code uses Binance's latest candle close, which may be an "
            "in-progress 30-minute candle. The automatic history therefore records the "
            "dashboard's current displayed reading at each refresh."
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
