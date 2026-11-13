-- Migration: Add chat_turn job_type and support S2-BE7 queue contracts
ALTER TABLE public.generation_jobs
DROP CONSTRAINT IF EXISTS generation_jobs_job_type_check;

ALTER TABLE public.generation_jobs
ADD CONSTRAINT generation_jobs_job_type_check
CHECK (job_type in ('generate', 'regenerate_full', 'regenerate_node', 'chat_turn'));
