BEGIN;
ALTER TABLE scan_runs ADD COLUMN max_concurrency integer NOT NULL DEFAULT 1 CHECK(max_concurrency BETWEEN 1 AND 4), ADD COLUMN min_delay_ms integer NOT NULL DEFAULT 1000 CHECK(min_delay_ms BETWEEN 250 AND 600000), ADD COLUMN max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 20), ADD COLUMN next_request_at timestamptz NOT NULL DEFAULT now(), ADD COLUMN allow_parallel_scan boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX one_active_root ON scan_runs(account_id,root_identity) WHERE status NOT IN ('completed','cancelled') AND NOT allow_parallel_scan;
ALTER TABLE scan_files ADD COLUMN observed_name text, ADD COLUMN observed_size bigint, ADD COLUMN observed_metadata jsonb;
CREATE OR REPLACE VIEW scan_status AS
 SELECT r.id AS scan_id,r.status,r.crawling_enabled,greatest(r.updated_at,(SELECT max(updated_at) FROM crawl_jobs WHERE scan_id=r.id),(SELECT max(updated_at) FROM folders WHERE scan_id=r.id)) AS last_persistent_update,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id) AS discovered_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status='completed') AS completed_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status IN ('pending','processing')) AS pending_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status='failed') AS failed_folders,
 (SELECT count(*) FROM scan_files sf WHERE sf.scan_id=r.id) AS unique_pdfs,
 (SELECT coalesce(sum(CASE WHEN sf.observed_metadata IS NOT NULL THEN sf.observed_size ELSE f.size_bytes END),0)::text FROM scan_files sf JOIN files f ON f.id=sf.file_id WHERE sf.scan_id=r.id) AS total_unique_bytes,
 (SELECT count(*) FROM scan_files sf JOIN files f ON f.id=sf.file_id WHERE sf.scan_id=r.id AND CASE WHEN sf.observed_metadata IS NOT NULL THEN sf.observed_size ELSE f.size_bytes END IS NULL) AS missing_size_pdfs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='processing' AND lease_expires_at>now()) AS leased_jobs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='retry') AS retry_jobs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='blocked') AS blocked_jobs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='pending') AS queued_jobs,
 (SELECT max(p.committed_at) FROM processed_pages p JOIN crawl_jobs j ON j.id=p.job_id WHERE j.scan_id=r.id) AS last_committed_page_time,
 (SELECT p.job_id FROM processed_pages p JOIN crawl_jobs j ON j.id=p.job_id WHERE j.scan_id=r.id ORDER BY p.committed_at DESC,p.job_id DESC LIMIT 1) AS last_committed_page_id,
 NOT r.crawling_enabled AS crawling_paused,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='blocked') AS terminal_failures
 FROM scan_runs r;
COMMIT;
