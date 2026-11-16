-- Restrict table privileges to match the RLS access contract.
REVOKE ALL PRIVILEGES ON TABLE public.signal_reaction_analysis FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.signal_reaction_analysis FROM anon;
REVOKE ALL PRIVILEGES ON TABLE public.signal_reaction_analysis FROM authenticated;

GRANT SELECT ON TABLE public.signal_reaction_analysis TO anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.signal_reaction_analysis TO service_role;
