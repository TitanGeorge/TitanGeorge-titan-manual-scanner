# Titan Windows downloader: offline promotion and future full-library operation

This upgrade preserves `C:\Service Manuals`, its ten verified PDFs, and `_catalog`. It adds an explicit, backed-up SQLite version 1 to version 2 promotion. Full mode has no numerical PDF ceiling. **Do not launch the full download yet.** This release was tested with synthetic catalogs, fake HTTP and synthetic PDFs only; your actual Windows catalog has not been accessed or migrated.

## Exact safe Windows upgrade

1. Close every old downloader window. Leave `C:\Service Manuals\`, `C:\Service Manuals\Wolf\` and `C:\Service Manuals\_catalog\` exactly where they are. Never extract this ZIP over those directories.
2. Extract the new ZIP into a **new program folder**, for example `C:\Users\<you>\Documents\Titan Downloader Full`. Retain the old program folder for rollback. If replacing program files instead, only replace files in the separate old program folder: Python sources, batch launchers, schema, requirements, documentation and tests. Never copy a packaged `_catalog` or destination directory; this ZIP contains neither.
3. Check `config.json`. Keep `destination` as `C:\Service Manuals` (or your previously configured destination). Do not point at a new destination to reset state. Keep concurrency 1, spacing 2 seconds and reserve at least 5 GiB.
4. Run `setup.bat` from the new program folder. It creates its own `.venv`, installs pinned Python dependencies, opens the existing catalog without promotion and checks free space. Dependency installation uses PyPI; it makes **no Service Alliance requests**. Existing tables/rows are not reset. Setup does not start traversal or downloads.
5. Run `status.bat` before migration. It should show schema 1, test mode, 24 discovered, 10 verified, 14 pending, 43,253,290 verified bytes, the saved Whirlpool folder/page and the exhausted test budget. If your current state differs, preserve it and inspect the reports rather than resetting it.
6. Run **`migrate-catalog.bat`**. This operation is entirely local. It validates version 1, automatically backs up SQLite using its backup API, verifies the backup and promotes the schema transactionally. It does not parse, modify, move or download any PDF. The versioned backup is under `_catalog\backups\manuals-v1-before-v2-<UTC timestamp>.db`. The output names the backup and reports a digest of all preserved catalog values.
7. Run `status.bat` again. Expect schema 2, `mode: full`, `hard_limit: null`, `limit_reached: false`, paused/inactive crawling, and **the same ten verified/fourteen pending records, bytes and saved page**. Run `export-manifest.bat` to retain a CSV. Repeating `migrate-catalog.bat` validates version 2 and reports `already_promoted: true`; it does not migrate again.
8. Optionally run `verify-local.bat`. It parses existing verified local files and checks stored size and SHA-256 locally. Missing/changed files become `needs_review` and are not silently redownloaded. Thus this operation can legitimately change review counts if the local files differ; migration itself does not.
9. **Stop here for review/approval.** No launcher automatically starts the full run.
10. Only when you deliberately choose to start the future run, launch **`full-download.bat`**. This is also the future resume launcher. Enter credentials locally; they remain in memory. Only `full-resume` may instantiate/login to Service Alliance. Legacy `test-download.bat` and `resume-download.bat` are retired and do no network work; their CLI equivalents also refuse to run.

From Command Prompt inside the new program directory, the exact offline commands are:

```bat
.venv\Scripts\python.exe downloader.py migrate
.venv\Scripts\python.exe downloader.py status
.venv\Scripts\python.exe downloader.py export
.venv\Scripts\python.exe downloader.py verify
```

The future explicitly network-enabled start/resume command is:

```bat
.venv\Scripts\python.exe downloader.py full-resume
```

Running `downloader.py` with no command only prints usage. `init`, `migrate`, `status`, `export`, `verify`, retired test/resume commands and restore never instantiate the source adapter.

## Migration and backup guarantees

`schema.sql` remains the exact version 1 initializer so existing tests and fresh setup remain compatible. `migration.py` owns the forward version 2 migration; `Catalog` reads the existing version before initialization and **never reruns version 1 initialization on an existing catalog**. Consequently reopening version 2 does not recreate the cap trigger.

The migration recognizes the original tables/columns/constraints and exact three SQL triggers, performs SQLite integrity and foreign-key checks, validates verified-record success-ledger entries, and checks the ten-slot bound in version 1. Status values `hard_limit`, `limit_reached` and `success_budget_used` are derived from this schema/ledger; they are not separately stored flags in the original database.

The source is opened without schema writes. Python `sqlite3.Connection.backup()` captures committed SQLite state including WAL; no active `.db` file is blindly copied. The standalone backup is closed, fsynced, reopened read-only, validated, and compared to the source using a streaming SHA-256 over every value in `folders`, `folder_pages`, `folder_items`, `manuals`, `file_refs`, and `budget`, ordered by rowid. Backup failure stops before schema/row changes. Incomplete backup files, if any, are not used for migration.

Under `BEGIN IMMEDIATE`, validation and the source digest are rechecked against the verified backup. The migration drops **only `budget_limit`**, changes `meta.schema_version` from 1 to 2, and adds `mode=full`, backup path, promotion timestamp and historical test-success count. `budget` and all of its records remain. `keep_success` and `keep_success_state` remain, protecting permanent successes from deletion/downgrade. No data table is recreated and no PDF is touched. Every preserved value is compared again before commit. Foreign keys and FULL synchronization are enabled.

A validation exception or Ctrl+C before commit rolls back. An abruptly terminated transaction is recovered by SQLite on next open. A termination after commit leaves a complete version 2 catalog; rerunning promotion is idempotent. The process lock prevents simultaneous old/new program operations. Unexpected external writers detected between backup and transaction cause a stop. Do not manually edit SQLite or its triggers.

## Offline rollback before a full run

Retain the old program folder and the generated version 1 backup. Close every downloader window. From the **new** program folder:

```bat
.venv\Scripts\python.exe restore_catalog.py "C:\Service Manuals\_catalog\backups\manuals-v1-before-v2-<actual timestamp>.db"
```

Use the exact backup path printed by migration. This local tool validates the backup, holds the same kernel lock, compares every preserved data value with the current catalog, safely backs up the current version into `manuals-before-restore-<timestamp>.db`, verifies that safety copy, then restores via SQLite's backup API and validates version 1. It does not move/delete PDFs and does not require manual database edits. After success, use the retained old program with the restored version 1 catalog. Never run the old program against version 2: its old initializer can recreate the cap before rejecting that version.

Automatic transaction rollback requires no restore command. The restore tool intentionally refuses if new discovery, downloads, verification changes or traversal updates would be lost. After a real full run begins, recovery must reconcile newer catalog/files with backups; do not blindly replace the database or delete WAL/SHM files. Keep all backups for review. Migration backups protect SQLite, not the PDF collection; maintain normal PC/file backups separately.

## Full operation, pause and resume

Full operation selects the next pending/failed file first, then the saved folder/page. Verified files are never selected for download. Startup checks their local size/hash/structure; removed or altered files become `needs_review`, retaining the success ledger. Source identities, names, paths and references remain the basis of deduplication. Completed metadata pages retain their committed checkpoints. Returned source metadata with a changed known size or changed primary-reference name raises an explicit review error without replacing stored values; shortcut/alternate-reference names can legitimately differ.

The full-mode scheduler has neither a PDF cap nor the test mode's per-invocation metadata page cap. SQL fetches only a concurrency-sized batch; a temporary SQLite attempted table avoids repeatedly retrying one failed item within the same invocation. Failures are retried on a later explicit resume. CSV export streams rows instead of building an in-memory manifest. Existing recovery still scans local verified files on each resume; that can take substantial time at full-library scale.

Defaults: one transfer, two seconds between starts, at most two concurrent transfers, three bounded attempts, exponential backoff/jitter, respected Retry-After (over 300 seconds pauses), auth-expiration pause, and minimum free reserve **5 GiB**. Free space is checked before start, metadata work, transfers and streamed chunks. No automatic PDF deletion. The Service Alliance/Google authorization and redirect checks are unchanged. No access/rate-limit bypass is implemented.

Ctrl+C sets the shared stop event **before** waiting for worker shutdown. No new work is scheduled; workers abandon temporary transfers at their next checked boundary or finish valid publication. Completed SQLite state remains committed; reserved/staged files are reconciled on resume. Crash/reboot releases the kernel lock, and SQLite/part-file recovery continues existing jobs. Byte-range continuation is not assumed; an incomplete PDF can restart from byte zero. No completed PDF or committed metadata page needs to restart.

A currently blocked HTTP call may take its configured timeout to return before shutdown; backoff/pacer sleeps can also delay interruption. No background process or automatic reboot/startup resume is installed. Current mode/activity/paused state is reported in status; while running, status.json is refreshed after each batch/page. A second live status process is excluded by the lock. A report left by an abrupt crash may temporarily say active until a new local status command refreshes it; no crawling automatically resumes.

Verification remains: authorized source record → IGD request → approved HTTPS redirect → streamed `.part` → content type/length, exact known size, `%PDF-`, EOF and strict PDF structural parsing → SHA-256 → fsynced atomic no-overwrite publication → durable SQLite success. Deterministic Windows sanitization, case-insensitive path uniqueness, identity suffixes and manufacturer organization are unchanged.

## Local reports and tests

`status.bat` writes `_catalog\reports\status.json`: discovered/verified/pending/failed/retry/review PDFs, duplicate references, verified/known remaining bytes, missing sizes, last success, saved folder/page, discovered/completed/pending/failed folders, disk space, schema, mode, cap and current runtime activity. `success_budget_used` retains historical ledger count but is no longer a ceiling in full mode. Export writes `_catalog\reports\manifest.csv` with all ten original records preserved. Credentials, cookies, nonce values, auth headers and downloaded bodies are never persisted/logged.

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

**89 offline tests pass on Python 3.12/Linux: original 62 unchanged plus 27 promotion/full-operation tests.** See `test-results.txt`, `migration-evidence.json` and `UPGRADE-REPORT.md`. New tests cover precise 24/10/14 state and byte totals; every preserved value; WAL backup; backup write/verification failure; transaction rollback; actual process termination before/during/after commit; retry/idempotence; permanent success protections; explicit-only full start; offline commands; saved page resume; no completed-file requests; Ctrl+C; full-mode disk/auth/429/5xx behavior; concurrency two; restore safety; source change detection and a 2,024-row streaming export. Synthetic PDFs are temporary fixtures, not bundled manuals.

The previous ten-PDF Windows test passed according to the supplied results. The new migration and launchers have **not** been executed against your actual Windows PC/catalog. Windows-specific behavior still needs your local offline migration/status test. Long-run source markup, authentication changes, Google confirmation pages, strict-parser rejects, missing source sizes and large-scale performance remain unknowns. Encrypted/invalid PDFs fail safely; no verification is weakened.

No Service Alliance requests, real PDF downloads, Render operations or cloud-resource creation occurred during development. Main and the PostgreSQL migration branch are untouched. **Do not start full downloading until you deliberately approve that next step.**
