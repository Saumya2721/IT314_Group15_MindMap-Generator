-- S3-DB3 / Issue #164: support document uploads and URL seeds.
BEGIN;

ALTER TABLE public.seed_assets
    DROP CONSTRAINT seed_assets_asset_type_check,
    DROP CONSTRAINT chk_seed_asset_storage,
    ADD CONSTRAINT seed_assets_asset_type_check
        CHECK (asset_type IN ('pdf', 'image', 'text', 'docx', 'pptx', 'url')),
    ADD CONSTRAINT chk_seed_asset_storage CHECK (
        (asset_type IN ('text', 'url') AND storage_key IS NULL)
        OR (asset_type IN ('pdf', 'image', 'docx', 'pptx')
            AND storage_key IS NOT NULL AND file_name IS NOT NULL)
    );

COMMIT;
