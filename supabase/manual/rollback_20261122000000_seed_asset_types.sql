-- Manual rollback for 20261122000000_s3_db3_seed_asset_types.sql.
-- Run with: psql -v ON_ERROR_STOP=1 "$DATABASE_URL" -f supabase/manual/rollback_20261122000000_seed_asset_types.sql
-- PostgreSQL rejects the rollback if DOCX, PPTX, or URL rows still exist.
-- The transaction preserves the rows and the widened constraints in that case.
BEGIN;

ALTER TABLE public.seed_assets
    DROP CONSTRAINT seed_assets_asset_type_check,
    DROP CONSTRAINT chk_seed_asset_storage,
    ADD CONSTRAINT seed_assets_asset_type_check
        CHECK (asset_type IN ('pdf', 'image', 'text')),
    ADD CONSTRAINT chk_seed_asset_storage CHECK (
        (asset_type = 'text' AND storage_key IS NULL)
        OR (asset_type IN ('pdf', 'image') AND storage_key IS NOT NULL AND file_name IS NOT NULL)
    );

COMMIT;
