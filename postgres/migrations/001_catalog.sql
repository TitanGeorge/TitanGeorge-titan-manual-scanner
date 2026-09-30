BEGIN;
CREATE TABLE scan_runs (
 id text PRIMARY KEY, root_identity text NOT NULL, account_id text NOT NULL,
 status text NOT NULL DEFAULT 'paused', crawling_enabled boolean NOT NULL DEFAULT false,
 import_sha256 text UNIQUE, provenance text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz, finished_at timestamptz
);
CREATE TABLE folders (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 scan_id text NOT NULL REFERENCES scan_runs(id), account_id text NOT NULL, drive_folder_id text NOT NULL,
 parent_identity text, name text, path text, source_metadata jsonb,
 provenance text NOT NULL, metadata_completeness text NOT NULL CHECK(metadata_completeness IN ('not_captured','partial','observed')),
 crawl_status text NOT NULL CHECK(crawl_status IN ('pending','processing','completed','failed')),
 next_page bigint CHECK(next_page > 0), attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0), last_error jsonb,
 discovered_at timestamptz, completed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(scan_id,account_id,drive_folder_id), UNIQUE(scan_id,id)
);
CREATE TABLE files (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 account_id text NOT NULL, drive_file_id text NOT NULL, filename text, size_bytes bigint CHECK(size_bytes >= 0), mime_type text,
 source_metadata jsonb, provenance text NOT NULL, metadata_completeness text NOT NULL,
 relationships_completeness text NOT NULL DEFAULT 'unknown' CHECK(relationships_completeness IN ('unknown','observed_partial')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(account_id,drive_file_id)
);
CREATE TABLE scan_files (
 scan_id text NOT NULL REFERENCES scan_runs(id), file_id bigint NOT NULL REFERENCES files(id),
 PRIMARY KEY(scan_id,file_id)
);
CREATE TABLE folder_files (
 scan_id text NOT NULL, folder_id bigint NOT NULL, file_id bigint NOT NULL REFERENCES files(id),
 provenance text NOT NULL, relationship_completeness text NOT NULL DEFAULT 'unknown',
 observed_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(scan_id,folder_id) REFERENCES folders(scan_id,id), PRIMARY KEY(scan_id,folder_id,file_id)
);
CREATE TABLE crawl_jobs (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, scan_id text NOT NULL, folder_id bigint NOT NULL,
 page bigint NOT NULL CHECK(page > 0), status text NOT NULL CHECK(status IN ('pending','processing','retry','completed','blocked')),
 attempts integer NOT NULL DEFAULT 0, lease_owner text, lease_token bigint NOT NULL DEFAULT 0,
 lease_expires_at timestamptz, available_after timestamptz NOT NULL DEFAULT now(), last_error jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(scan_id,folder_id) REFERENCES folders(scan_id,id), UNIQUE(scan_id,folder_id,page),
 CHECK((status='processing')=(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL))
);
CREATE INDEX crawl_jobs_claim ON crawl_jobs(scan_id,status,available_after,lease_expires_at);
CREATE TABLE processed_pages (
 job_id bigint PRIMARY KEY REFERENCES crawl_jobs(id), result_sha256 text NOT NULL,
 committed_at timestamptz NOT NULL DEFAULT now()
);
CREATE VIEW scan_status AS
 SELECT r.id AS scan_id,r.status,r.crawling_enabled,r.updated_at AS last_persistent_update,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id) AS discovered_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status='completed') AS completed_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status IN ('pending','processing')) AS pending_folders,
 (SELECT count(*) FROM folders f WHERE f.scan_id=r.id AND crawl_status='failed') AS failed_folders,
 (SELECT count(*) FROM scan_files sf WHERE sf.scan_id=r.id) AS unique_pdfs,
 (SELECT coalesce(sum(f.size_bytes),0)::text FROM scan_files sf JOIN files f ON f.id=sf.file_id WHERE sf.scan_id=r.id) AS total_unique_bytes,
 (SELECT count(*) FROM scan_files sf JOIN files f ON f.id=sf.file_id WHERE sf.scan_id=r.id AND f.size_bytes IS NULL) AS missing_size_pdfs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='processing' AND lease_expires_at>now()) AS leased_jobs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='retry') AS retry_jobs,
 (SELECT count(*) FROM crawl_jobs j WHERE j.scan_id=r.id AND status='blocked') AS blocked_jobs
 FROM scan_runs r;
COMMIT;
