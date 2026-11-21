# Mind Map Generator — Architecture & Data Access Notes

## 1. Search Maps Architecture (M3 / S2-DB4 / S2-FE5)

The application maintains a deliberate, two-tier architecture for searching user mind maps:

### Layer A: Direct Database RPC (`public.search_maps`)
- **Location:** `supabase/migrations/20261114000400_s2_db4_search_maps.sql`
- **Purpose:** PostgREST / Supabase Client direct access.
- **Security:** Security Definer with explicit ownership enforcement (`WHERE m.user_id = p_user_id AND (m.user_id = auth.uid() OR m.is_public = true)`).
- **Permissions:** Execution is granted to `authenticated` users only, with execution revoked from `anon` and `PUBLIC`.
- **Use Case:** Client-side dashboards querying Supabase directly using supabase-js client libraries.

### Layer B: FastAPI Backend Repository (`search_maps`)
- **Location:** `backend/db/repository.py`
- **Purpose:** Authoritative backend API search endpoint (`GET /api/maps?q=...&sort=...&offset=...`).
- **Features:**
  - High-performance direct asyncpg parameterized queries.
  - Comprehensive sorting options (`updated_at_desc`, `updated_at_asc`, `created_at_desc`, `created_at_asc`, `title_asc`, `title_desc`).
  - Full offset/cursor pagination with node count and generation job subquery aggregations.
  - Strict SQL injection protection via parameter binding and `replace()` escaping for `%` and `_` wildcards.
- **Use Case:** All REST API consumers querying through the FastAPI application server.
