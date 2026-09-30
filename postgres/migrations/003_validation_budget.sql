BEGIN;
ALTER TABLE scan_runs ADD COLUMN page_budget bigint CHECK(page_budget > 0);
COMMIT;
