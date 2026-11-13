-- S2-DB2 / Issue #76: additive snapshot/version contract.
-- Preserve the existing node-comments/snapshots backend contract.
BEGIN;

ALTER TABLE public.nodes
  ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE public.nodes
  ADD CONSTRAINT chk_nodes_version_positive CHECK (version >= 1);

CREATE OR REPLACE FUNCTION public.bump_node_version()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF (to_jsonb(NEW) - 'version' - 'updated_at')
     IS DISTINCT FROM (to_jsonb(OLD) - 'version' - 'updated_at') THEN
    NEW.version := OLD.version + 1;
  ELSE
    NEW.version := OLD.version;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_nodes_bump_version ON public.nodes;
CREATE TRIGGER trg_nodes_bump_version
  BEFORE UPDATE ON public.nodes
  FOR EACH ROW EXECUTE FUNCTION public.bump_node_version();

-- Existing snapshots already have nodes_snapshot, edges_snapshot and
-- trigger_job_id. Add the requested interface without renaming those fields.
ALTER TABLE public.map_snapshots
  ADD COLUMN IF NOT EXISTS version_no integer,
  ADD COLUMN IF NOT EXISTS job_id uuid REFERENCES public.generation_jobs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS graph jsonb;

-- Backfill rows created before this migration, keeping the old representation.
WITH ordered AS (
  SELECT id, row_number() OVER (PARTITION BY map_id ORDER BY created_at, id)::integer AS n
  FROM public.map_snapshots
)
UPDATE public.map_snapshots s
SET version_no = ordered.n,
    job_id = s.trigger_job_id,
    graph = jsonb_build_object('nodes', s.nodes_snapshot, 'edges', s.edges_snapshot)
FROM ordered WHERE ordered.id = s.id;

ALTER TABLE public.map_snapshots
  ALTER COLUMN version_no SET NOT NULL,
  ALTER COLUMN graph SET NOT NULL,
  ADD CONSTRAINT chk_map_snapshots_version_positive CHECK (version_no >= 1),
  ADD CONSTRAINT chk_map_snapshots_graph_object CHECK (jsonb_typeof(graph) = 'object');

CREATE UNIQUE INDEX IF NOT EXISTS uq_map_snapshots_map_version_no
  ON public.map_snapshots(map_id, version_no);

-- Preserve backend INSERTs, which supply only legacy snapshot columns.
CREATE OR REPLACE FUNCTION public.populate_map_snapshot_version()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  -- Prevent duplicate version numbers when concurrent snapshots are inserted.
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(NEW.map_id::text, 0));

  IF NEW.version_no IS NULL THEN
    SELECT COALESCE(MAX(version_no), 0) + 1 INTO NEW.version_no
    FROM public.map_snapshots WHERE map_id = NEW.map_id;
  END IF;

  IF NEW.job_id IS NOT NULL AND NEW.trigger_job_id IS NOT NULL
     AND NEW.job_id IS DISTINCT FROM NEW.trigger_job_id THEN
    RAISE EXCEPTION 'job_id and trigger_job_id must match' USING ERRCODE = '23514';
  END IF;
  NEW.job_id := COALESCE(NEW.job_id, NEW.trigger_job_id);
  NEW.trigger_job_id := COALESCE(NEW.trigger_job_id, NEW.job_id);

  IF NEW.job_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.generation_jobs j
    WHERE j.id = NEW.job_id AND j.map_id = NEW.map_id
  ) THEN
    RAISE EXCEPTION 'snapshot job must belong to its map' USING ERRCODE = '23514';
  END IF;

  IF NEW.graph IS NULL THEN
    NEW.graph := jsonb_build_object('nodes', NEW.nodes_snapshot, 'edges', NEW.edges_snapshot);
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_populate_map_snapshot_version ON public.map_snapshots;
CREATE TRIGGER trg_populate_map_snapshot_version
  BEFORE INSERT ON public.map_snapshots
  FOR EACH ROW EXECUTE FUNCTION public.populate_map_snapshot_version();

-- Snapshots cannot be edited after capture. Explicit deletion is retained
-- because the existing backend exposes a snapshot delete action.
CREATE OR REPLACE FUNCTION public.reject_map_snapshot_update()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  RAISE EXCEPTION 'map snapshots are immutable' USING ERRCODE = '55000';
END;
$$;
DROP TRIGGER IF EXISTS trg_reject_map_snapshot_update ON public.map_snapshots;
CREATE TRIGGER trg_reject_map_snapshot_update
  BEFORE UPDATE ON public.map_snapshots
  FOR EACH ROW EXECUTE FUNCTION public.reject_map_snapshot_update();

-- Backend-callable snapshot helper; capture the graph in a single transaction
-- before a full-map or branch regeneration. SECURITY INVOKER by default.
CREATE OR REPLACE FUNCTION public.record_map_snapshot(
  p_map_id uuid,
  p_job_id uuid DEFAULT NULL,
  p_target_node_id uuid DEFAULT NULL,
  p_reason text DEFAULT 'pre_regeneration'
) RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
  v_nodes jsonb;
  v_edges jsonb;
  v_id uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.mind_maps WHERE id = p_map_id) THEN
    RAISE EXCEPTION 'map not found' USING ERRCODE = '23503';
  END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(n) ORDER BY n.id), '[]'::jsonb)
    INTO v_nodes FROM public.nodes n WHERE n.map_id = p_map_id;
  SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.id), '[]'::jsonb)
    INTO v_edges FROM public.edges e WHERE e.map_id = p_map_id;
  INSERT INTO public.map_snapshots
    (map_id, trigger_job_id, target_node_id, reason, nodes_snapshot, edges_snapshot)
  VALUES (p_map_id, p_job_id, p_target_node_id, p_reason, v_nodes, v_edges)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

-- RLS on map_snapshots already exists. The helper is backend-only, not
-- available via the authenticated/anon Data API roles.
REVOKE ALL ON FUNCTION public.record_map_snapshot(uuid, uuid, uuid, text) FROM PUBLIC, anon, authenticated;

COMMIT;
