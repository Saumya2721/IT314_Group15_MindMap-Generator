-- S3-BE8 / Issue #171: persistent, backend-only pipeline settings.
BEGIN;

CREATE TABLE public.pipeline_config (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object')
);

INSERT INTO public.pipeline_config DEFAULT VALUES;

ALTER TABLE public.pipeline_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pipeline_config FROM PUBLIC, anon, authenticated, service_role;

COMMIT;
