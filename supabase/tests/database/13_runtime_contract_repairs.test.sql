BEGIN;
SELECT plan(6);

SELECT has_column('public', 'generation_jobs', 'base_map_version', 'Regeneration stores its base version');
SELECT has_column('public', 'agent_memories', 'updated_at', 'Memory timestamps match the API contract');
SELECT has_index('public', 'agent_memories', 'uq_agent_memories_active_content', 'Active memory content is unique');
SELECT has_table('public', 'account_deletion_requests', 'Deletion requests survive the account');
SELECT ok(
    NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.admin_audit_log'::regclass
          AND confrelid = 'auth.users'::regclass
          AND contype = 'f'
    ),
    'Audit records cannot block account removal'
);
SELECT ok(
    NOT has_table_privilege('authenticated', 'public.account_deletion_requests', 'SELECT'),
    'Clients cannot read account deletion operations directly'
);

SELECT * FROM finish();
ROLLBACK;
