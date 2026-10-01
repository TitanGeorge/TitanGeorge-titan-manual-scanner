# Windows downloader promotion report

Scope: code, synthetic migration, offline regression tests, Windows packaging and documentation. No live crawl or actual Windows catalog migration was performed.

## GitHub changes

Repository: `TitanGeorge/TitanGeorge-titan-manual-scanner`. Branch: `windows-local-downloader`. Starting commit: `9b1b4a1199bffbab9427a98ffae89cd90cefde80`.

| New commit | Purpose |
|---|---|
| `4e40ace20daf994b512281d9d3484c9a32b0087c` | Verified SQLite backup; transactional v1→v2 promotion; preserved success ledger; explicit full mode; offline migration/verify launchers; safe pre-run restoration |
| `eb76cbbf122c24444ea5111f1eced4fb4536df70` | 27 additional offline tests; preserve original 62; stored-size verification; hold process lock through final reporting and database closure |

Release documentation/evidence is committed separately. The delivered release summary gives its exact final SHA and ZIP SHA-256. No merge or change to main/PostgreSQL migration was made. No files outside `windows-local-downloader/` changed. Local intermediate commits were published through equivalent GitHub API commits with the same trees; the SHAs above are the authoritative GitHub commits.

## Code and schema

| File | Final behavior |
|---|---|
| `migration.py` | Recognized-v1 validation; WAL-aware backup API; reopened backup validation; all-value digests; transaction; remove only cap trigger; version/mode/history; idempotence |
| `core.py` | Read existing schema before initialization; v1 ceiling/v2 unlimited; preserved immutable success ledger; folder counts/mode/activity; changed source metadata review; verified hash and stored size check |
| `downloader.py` | Offline migrate/verify commands; only explicit full-resume can authenticate; retired test/resume refuse; bounded SQL batches/temp attempted set; full traversal without test page cap; Ctrl+C stop before worker shutdown; lock held through closure; reserve minimum 5 GiB |
| `restore_catalog.py` | Validated local v1 backup restore; current-catalog safety backup; refuse rollback if preserved data changed since promotion |
| launchers | `migrate-catalog.bat`, `verify-local.bat` offline; `full-download.bat` explicitly starts/resumes source work; legacy network launchers retired |
| `tests/test_promotion.py` | 27 new offline tests; original 62 tests unchanged |

Version 2 keeps all original tables and records. SQL changes: `DROP TRIGGER budget_limit`; update `meta.schema_version` to `2`; insert `mode=full`, `migration_backup`, `test_successes_at_promotion`, and `promoted_at`. `keep_success`/`keep_success_state` remain unchanged. Version 1 schema initialization remains in `schema.sql`; v2 is created only by explicit migration.

## Preservation proof

Synthetic fixture matches supplied live Windows totals. Hashes and paths are synthetic fixture values, not claims about unseen real Windows data. `migration-evidence.json` records the equality of streaming before/after digests over **every value** in all six preserved data tables, including identities, filenames, paths, relationships, hashes, sizes, statuses, timestamps, retries/errors and pagination.

| Property | Before | After |
|---|---:|---:|
| Discovered manuals | 24 | 24 |
| Verified records | 10 | 10 |
| Pending records | 14 | 14 |
| Verified bytes | 43,253,290 | 43,253,290 |
| Known remaining bytes | 496,683,343 | 496,683,343 |
| Permanent success slots | 10 | 10 |
| Hard ceiling | 10 | none |
| Limit reached | true | false |
| Mode/schema | test / 1 | full / 2 |
| Saved source folder | APPLIANCE/Whirlpool Brands - ALL | unchanged |
| Saved page/status | 1 / pending | unchanged |

The migration itself does not open any PDF. A second fixture with physically valid synthetic PDFs proves ten existing successes are locally verified, 16 new synthetic pending files complete, total reaches 26, transport receives exactly 16 requests, and another resume receives zero further PDF requests. A separate test proves saved page 2 resumes after committed page 1. Missing/changed verified files become review work without consuming a new request.

## Backup, rollback, recovery

An SQLite backup API snapshot captures committed WAL state. Backups are timestamped/versioned under `_catalog\backups`, closed/fsynced, reopened read-only, integrity/FK/schema checked and compared before migration. Failure prevents original schema/row modification.

The transaction rechecks unchanged source state, applies schema/meta changes, validates and compares all preserved values before committing. Exceptions roll back; real subprocess exits before changes, during trigger removal and after commit prove restart behavior. Retry after a successful commit validates v2 and makes no additional migration.

`restore_catalog.py` provides an offline restore for the pre-full-run case, retaining a validated rescue backup. It refuses if newer data would be lost. Do not manually delete/alter tables, triggers, database, WAL or SHM files. The old program must only be used after a validated restore to v1. All exact steps/commands are in README.md.

## Explicit start, pause and verification

Offline upgrade: setup → status → migrate-catalog → status/export → optional verify-local. Setup installs dependencies from PyPI but never accesses Service Alliance. Startup alone does not crawl. Full start/resume is only `full-download.bat` / `.venv\Scripts\python.exe downloader.py full-resume`, after explicit migration. The assignment did not invoke it against the source.

Persistent file/folder/page state survives Ctrl+C/reboot. Ctrl+C sets stop before executor shutdown, checks streamed chunks, preserves completed publication and leaves interrupted reservations recoverable. A blocked request can take its configured timeout to return. Local status refreshes activity after batches/pages; kernel lock excludes simultaneous commands. A crash can leave the last status report stale until refreshed. No automatic/background resume.

Concurrency 1 by default, maximum 2, spacing 2 seconds, bounded retries/backoff/jitter/Retry-After and auth-expiration handling remain. Minimum reserve is at least 5 GiB. Disk checks precede start/pages/transfers/chunks. Download verification, approved redirects, atomic no-overwrite publication, SHA-256 and identity filenames remain. No automatic deletion or access-control bypass. The CSV writer streams its query.

## Validation

89 tests pass on Python 3.12/Linux: all original 62 plus 27 new tests. Tests use mocks/synthetic metadata/PDFs and network-blocked Requests. The subprocess crash tests import only local migration code. Test output is included. Tests cover WAL state, backup failure, validation rollback, actual abrupt process exit, idempotence, every preserved value, full-mode no-redownload, pagination, offline-only commands, Ctrl+C, restart/resume, disk, auth, HTTP 429/5xx, two-worker operation, source-change review and 2,024-row export. Original tests cover duplicate references, collisions, `.part` recovery, structural PDF checks and source protocol. No new tests contact the real library.

Package checks: explicit file allowlist; no `.venv`, cache, PDFs, SQLite databases, credentials, cookies, nonce/auth values or downloaded source bodies. ZIP entries are CRC-checked and Python sources compile after extraction. See delivered SHA-256 sidecar for artifact integrity.

## Remaining unknowns and boundaries

Actual Windows migration, new batch execution, Windows locking and no-overwrite rename need the user's local test. No access to the authoritative Windows catalog was assumed. The previous Windows test passed according to supplied results, while new promotion evidence is entirely synthetic. Full-library duration/scale/source variation has not been measured. Resume parses all verified local PDFs, which can take time at full scale. Strict/encrypted PDF rejection, changed source metadata, unknown sizes, MFA/CAPTCHA and Google confirmation pages may pause/fail safely. Current retry/error categorization is preserved rather than redesigned. Backups cover SQLite; normal PDF/PC backups remain separate.

## Confirmations

- No Service Alliance authentication or requests occurred.
- No real PDF or additional source PDF was downloaded; only temporary synthetic test PDFs were created.
- The user's actual Windows files/catalog were neither accessed nor migrated.
- Render production `srv-dau5ob893c1s73cr7000` was untouched; no Render calls were made.
- No PostgreSQL, Cloudflare R2 or Supabase resources were created.
- Main was unchanged and the preserved PostgreSQL migration branch was neither modified nor merged.
- The full download was not started. Work stops for the user's requested review/approval after delivering the package and report.
