-- S2 Remediation Migration: Fix Snapshot Reason Length and Trigger Nulling Exception
-- 1. Snapshot Reason: Change VARCHAR(50) to TEXT to avoid truncation errors (PostgreSQL 22001)
-- 2. Snapshot Immutability: Allow ON DELETE SET NULL cascades on foreign keys (target_node_id, job_id, trigger_job_id)
--    while strictly rejecting any mutation to snapshot graph content, version, or creation timestamp.

BEGIN;

-- 1. Snapshot Reason length expansion
ALTER TABLE public.map_snapshots ALTER COLUMN reason TYPE text;

-- 2. Refined Snapshot Immutability Trigger Function
CREATE OR REPLACE FUNCTION public.reject_map_snapshot_update()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  -- Allow updates caused by foreign key ON DELETE SET NULL cascades:
  -- The core snapshot payload, version, map, reason, and creation timestamp must be strictly identical.
  IF (OLD.id = NEW.id AND
      OLD.map_id = NEW.map_id AND
      OLD.version_no = NEW.version_no AND
      OLD.nodes_snapshot = NEW.nodes_snapshot AND
      OLD.edges_snapshot = NEW.edges_snapshot AND
      OLD.graph = NEW.graph AND
      OLD.reason = NEW.reason AND
      OLD.created_at = NEW.created_at AND
      (NEW.target_node_id IS NULL OR NEW.target_node_id = OLD.target_node_id) AND
      (NEW.job_id IS NULL OR NEW.job_id = OLD.job_id) AND
      (NEW.trigger_job_id IS NULL OR NEW.trigger_job_id = OLD.trigger_job_id)) THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION 'map snapshots are immutable audit records' USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_reject_map_snapshot_update ON public.map_snapshots;
CREATE TRIGGER trg_reject_map_snapshot_update
  BEFORE UPDATE ON public.map_snapshots
  FOR EACH ROW EXECUTE FUNCTION public.reject_map_snapshot_update();

COMMIT;

