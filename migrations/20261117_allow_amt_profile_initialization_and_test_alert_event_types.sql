-- Keep this allowlist synchronized with ALLOWED_EVENT_TYPES in telegram_alerts.py.
-- Adds the two event types emitted by the notifier but previously rejected by the DB.
ALTER TABLE public.telegram_alert_events
  DROP CONSTRAINT IF EXISTS telegram_alert_events_event_type_check;

ALTER TABLE public.telegram_alert_events
  ADD CONSTRAINT telegram_alert_events_event_type_check
  CHECK (
    event_type = ANY (
      ARRAY[
        'AMT_STATE_CHANGE',
        'AMT_PROFILE_INITIALIZATION',
        'GEX_WALL_BREACH',
        'OI_SHOCK',
        'TEST_ALERT'
      ]::text[]
    )
  );
