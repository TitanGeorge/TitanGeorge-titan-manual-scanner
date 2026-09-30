PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS folders(
 key TEXT PRIMARY KEY, object_json TEXT NOT NULL, source_path TEXT NOT NULL,
 page INTEGER NOT NULL DEFAULT 1, warmed INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS folder_pages(
 folder_key TEXT NOT NULL REFERENCES folders(key), page INTEGER NOT NULL,
 PRIMARY KEY(folder_key,page));
CREATE TABLE IF NOT EXISTS folder_items(
 folder_key TEXT NOT NULL REFERENCES folders(key), item_key TEXT NOT NULL,
 PRIMARY KEY(folder_key,item_key));
CREATE TABLE IF NOT EXISTS manuals(
 key TEXT PRIMARY KEY, file_id TEXT NOT NULL, account_id TEXT NOT NULL,
 request_id TEXT NOT NULL, source_filename TEXT NOT NULL, local_filename TEXT NOT NULL,
 folder_key TEXT NOT NULL, source_path TEXT NOT NULL, mime TEXT NOT NULL,
 expected_size INTEGER, local_path TEXT NOT NULL UNIQUE COLLATE NOCASE,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 http_status INTEGER, actual_size INTEGER, sha256 TEXT,
 signature_ok INTEGER NOT NULL DEFAULT 0, verification TEXT NOT NULL DEFAULT 'unverified',
 last_error TEXT, discovered TEXT NOT NULL, started TEXT, finished TEXT, UNIQUE(account_id,file_id));
CREATE TABLE IF NOT EXISTS file_refs(
 file_key TEXT NOT NULL REFERENCES manuals(key), folder_key TEXT NOT NULL REFERENCES folders(key),
 source_id TEXT NOT NULL, source_filename TEXT NOT NULL,
 PRIMARY KEY(file_key,folder_key,source_id));
-- Permanent successes plus in-flight reservations must never exceed ten.
CREATE TABLE IF NOT EXISTS budget(
 file_key TEXT PRIMARY KEY REFERENCES manuals(key),
 state TEXT NOT NULL CHECK(state IN ('reserved','success')), created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS budget_limit BEFORE INSERT ON budget
 WHEN (SELECT COUNT(*) FROM budget)>=10
 BEGIN SELECT RAISE(ABORT,'TEST_LIMIT_REACHED'); END;
CREATE TRIGGER IF NOT EXISTS keep_success BEFORE DELETE ON budget
 WHEN OLD.state='success'
 BEGIN SELECT RAISE(ABORT,'SUCCESS_BUDGET_IS_PERMANENT'); END;
CREATE TRIGGER IF NOT EXISTS keep_success_state BEFORE UPDATE OF state ON budget
 WHEN OLD.state='success' AND NEW.state!='success'
 BEGIN SELECT RAISE(ABORT,'SUCCESS_BUDGET_IS_PERMANENT'); END;
