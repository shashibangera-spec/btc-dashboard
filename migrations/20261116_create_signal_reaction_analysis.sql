-- Market Reaction Analysis: versioned, non-directional outcome records.
-- The original system_signal_journal remains immutable.
CREATE TABLE public.signal_reaction_analysis (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id BIGINT NOT NULL REFERENCES public.system_signal_journal(id) ON DELETE RESTRICT,
    analysis_version TEXT NOT NULL DEFAULT 'reaction-v1.0',
    anchor_price NUMERIC(20, 8) NOT NULL CHECK (anchor_price > 0),
    window_start TIMESTAMPTZ,
    window_end TIMESTAMPTZ NOT NULL,
    price_after_30m NUMERIC(20, 8),
    price_after_1h NUMERIC(20, 8),
    price_after_2h NUMERIC(20, 8),
    price_after_4h NUMERIC(20, 8),
    window_high NUMERIC(20, 8),
    window_low NUMERIC(20, 8),
    max_up_excursion_usd NUMERIC(20, 8),
    max_down_excursion_usd NUMERIC(20, 8),
    max_up_excursion_pct NUMERIC(18, 8),
    max_down_excursion_pct NUMERIC(18, 8),
    candles_expected INTEGER NOT NULL DEFAULT 8 CHECK (candles_expected = 8),
    candles_received INTEGER NOT NULL DEFAULT 0 CHECK (candles_received BETWEEN 0 AND 8),
    analysis_status TEXT NOT NULL CHECK (analysis_status IN ('COMPLETE', 'INCOMPLETE', 'ERROR')),
    data_source TEXT NOT NULL DEFAULT 'BINANCE_SPOT_API:BTCUSDT:30m',
    error_message TEXT,
    analyzed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT signal_reaction_analysis_window_check CHECK (window_start IS NULL OR window_end > window_start),
    CONSTRAINT signal_reaction_analysis_range_check CHECK (window_high IS NULL OR window_low IS NULL OR window_high >= window_low),
    CONSTRAINT signal_reaction_analysis_complete_check CHECK (
        analysis_status <> 'COMPLETE' OR (
            candles_received = 8 AND window_start IS NOT NULL
            AND price_after_30m IS NOT NULL AND price_after_1h IS NOT NULL
            AND price_after_2h IS NOT NULL AND price_after_4h IS NOT NULL
            AND window_high IS NOT NULL AND window_low IS NOT NULL
            AND max_up_excursion_usd IS NOT NULL AND max_down_excursion_usd IS NOT NULL
            AND max_up_excursion_pct IS NOT NULL AND max_down_excursion_pct IS NOT NULL
        )
    ),
    CONSTRAINT signal_reaction_analysis_signal_version_key UNIQUE (signal_id, analysis_version)
);

CREATE INDEX signal_reaction_analysis_signal_id_idx ON public.signal_reaction_analysis(signal_id);
CREATE INDEX signal_reaction_analysis_status_idx ON public.signal_reaction_analysis(analysis_status);

ALTER TABLE public.signal_reaction_analysis ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.signal_reaction_analysis TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.signal_reaction_analysis TO service_role;

CREATE POLICY "Anon can read signal reaction analysis"
    ON public.signal_reaction_analysis FOR SELECT TO anon USING (true);
CREATE POLICY "Service role can manage signal reaction analysis"
    ON public.signal_reaction_analysis FOR ALL TO service_role USING (true) WITH CHECK (true);
