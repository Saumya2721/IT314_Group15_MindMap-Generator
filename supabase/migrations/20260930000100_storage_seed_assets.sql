-- Private bucket for uploaded seed files (US-02). Objects are stored under
-- "<user_id>/<job_id>/<file>", and each user may only touch their own folder.
-- The size limit and MIME list are starting values; adjust to the real
-- upload rules once they are agreed.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'seed-assets',
    'seed-assets',
    false,
    20971520,
    array['application/pdf', 'image/png', 'image/jpeg', 'image/webp']
)
on conflict (id) do nothing;

create policy seed_assets_storage_owner on storage.objects
    for all to authenticated
    using (
        bucket_id = 'seed-assets'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    )
    with check (
        bucket_id = 'seed-assets'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );
