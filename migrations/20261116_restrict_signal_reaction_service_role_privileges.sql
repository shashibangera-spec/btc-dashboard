-- Restrict the backend role to required CRUD operations only.
REVOKE ALL PRIVILEGES ON TABLE public.signal_reaction_analysis FROM service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.signal_reaction_analysis TO service_role;
