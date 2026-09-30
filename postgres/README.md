# PostgreSQL recovery preparation

This directory is isolated from the deployed Node application. `server.js`, the root package, `render.yaml`, and production commands are unchanged. There is no crawler loop, source HTTP client, PDF downloader, automatic import, or production deployment in this package.

## Recovery inspection

At recovery start on September 30, 2026, remote HEAD/default branch was `main`. Both `main` and `postgres-checkpoint-migration` pointed to `8d87ebd592c74f5514e830484068ede56cb70331`. Their histories and trees were identical. The reachable history and repository tree contained no PostgreSQL schema, importer, repository layer, or PostgreSQL tests. Existing legacy crawler implementation was preserved. No detailed checkpoint was in the branch tree; aggregate results cannot reconstruct it. GitHub does not expose lost uncommitted Work workspace contents. This inspection does not claim recovery of unreachable or deleted objects.

## Schema and identity

`migrations/001_catalog.sql` creates standard PostgreSQL tables:

| Table | Purpose |
| --- | --- |
| scan_runs | Root, source account, paused status, explicit crawl gate, import hash, timestamps |
| folders | Per-scan account/Drive identity, nullable metadata/path/parent/timestamps, crawl status, page, attempts, errors, provenance |
| files | Catalog identity unique on `(account_id, drive_file_id)`, nullable size/name/MIME, sanitized metadata and provenance |
| scan_files | Distinct file membership per scan; controls scan reconciliation |
| folder_files | Many-to-many known relationships, distinct by scan/folder/file, provenance and unknown historical completeness |
| crawl_jobs | Durable page work, lease owner/token/expiration, retry availability, attempts, sanitized errors |
| processed_pages | Committed page fingerprint for safe repeated delivery |
| scan_status view | Database-derived scan counts and totals |

Legacy `objectKey` is `(file.accountId || ACCOUNT) + ':' + (file.shortcutDetails?.targetId || file.id)`. `recordPdf` retains the first metadata/parent and only enriches a missing size. Its stored record account can fall back to the parent account, while the dedup key falls back to the root account. Import preserves the saved key as authoritative and reports record-account differences instead of changing identities. Future page processing also uses the scan's root account fallback and shortcut target ID. Stored IDs must agree with the ID part of the key. Duplicate saved keys are rejected rather than silently collapsed.

Files are globally unique; scan_files makes reconciliation scan-specific. Extra folder references cannot create new unique files. Conflicting non-null sizes fail rather than overwrite a previously known total.

## Provenance and missing data

Completed identity-only folders have NULL name/path/source_metadata/parent/completed_at and `metadata_completeness=not_captured`. No full IGD object or missing timestamp is invented. Retained unresolved folder metadata is allowlisted and partial. Unknown source dates remain NULL; database creation/update timestamps describe persistence activity only.

Legacy files retain sanitized record metadata, including retained parentId and shortcutId. A first-parent link is added only if that ID resolves unambiguously among retained folder identities. Unmatched/ambiguous references remain in source_metadata and are reported. Missing secondary historical parents are not failures. Both files' relationship completeness and imported relationship completeness remain unknown. A future directly observed link is marked observed_partial; observing another parent never asserts exhaustive parent discovery. Rediscovery can enrich folder metadata without changing completed status.

Sanitization uses an explicit primitive metadata allowlist and only target ID/MIME from shortcut details. Credentials, sessions, nonces, URLs, resource keys and arbitrary nested fields are excluded. Legacy errors are reduced to a fixed code; HTTP 500 is retained only when explicitly present in the saved error. This intentionally does not retain a complete request-ready IGD object. A future authorized adapter must validate the exact sanitized fields IGD requires before making any request.

## Offline importer and dry-run

Install dependencies from this directory with `npm ci --ignore-scripts`.

First use:

```sh
node cli.js dry-run /secure/offline/checkpoint.json
```

Dry-run has no database connection and no network requests. It validates the version/header, known production root/account, arrays, unique identities, done/seen membership, unresolved jobs, pagination, sizes and exact acceptance totals. It reports logical folder inserts, file upserts, relationship inserts, unresolved blocked jobs, missing sizes, account differences and unmatched parents. It does not compare an existing database, so it cannot claim how many upserts would actually update existing records. There are no database changes.

Production acceptance requires all four exact values:

* 217 completed folders
* 92,266 unique PDFs
* 146,394,392,927 bytes
* 1 unresolved folder, named UNSORTED MISC PDF FILES, retained in failed state; no queued folders

The CLI never accepts alternative counts. Tests supply small synthetic expectations through the internal API only. An invalid checkpoint or count/byte mismatch stops before any writes; mismatch diagnostics report the expected and actual values.

For a separately authorized isolated database, apply the SQL once using an offline administrative connection:

```sh
psql "$TITAN_IMPORT_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/001_catalog.sql
TITAN_OFFLINE_IMPORT_APPROVED=yes node cli.js import /secure/offline/checkpoint.json titan-legacy-import
```

Do not point these commands at an existing production database. Migration SQL is an atomic, one-time baseline; it is not a general versioned migration runner. The schema must already exist for import. The database URL is required in `TITAN_IMPORT_DATABASE_URL`; the package never reads the production server's environment variables.

Actual import parses and reconciles first, then writes one transaction using one dedicated connection. It acquires a transaction advisory catalog lock, creates the paused scan, folders, distinct files, memberships, known relationships and blocked unresolved jobs, then reconciles authoritative SQL totals and job counts before commit. Any failure rolls back all scan/catalog writes. Identical hash/scan re-import validates existing totals and blocked jobs without duplicating state; changed snapshots or enabled scans are refused. Root/record identities and import hash are preserved, while arbitrary state fields are not copied.

The real checkpoint has NOT been recovered or dry-run in this session. The synthetic acceptance test with 92,266 records demonstrates validation logic, not verification of production data.

## Page transactions, leasing and restart

Repository functions accept a dedicated `pg.Client`; never pass a pool directly to multi-statement transactions. The caller must release a client after use and must not run overlapping transactions on the same connection. A global advisory lock serializes catalog writers for conservative correctness. This can later be optimized after concurrency validation.

`claimJob` refuses disabled scans, blocked jobs and completed folders. It claims eligible pending/retry jobs or expired processing leases, increments a monotonically increasing token, and persists owner/expiration/attempts. Current persisted page must agree with the job page. `heartbeat` renews only a valid fenced lease. `failJob` fences the worker, clears the lease, records a fixed sanitized error and schedules retry or blocks the job. There is no API here to enable a scan or unblock the imported unresolved job.

`processPage` consumes metadata already provided by a future adapter, without fetching anything. In one transaction it checks the scan gate and lease token with the database clock; upserts unique PDFs; records memberships/relationships; upserts children; schedules only noncompleted children; inserts the next page job; advances pagination or completes the folder; completes the page job; records a page fingerprint; and updates persistent scan status/time. Lease validity is checked immediately before commit. A repeated committed payload under the original token is acknowledged; changed payloads or stale tokens are rejected.

A death before commit leaves page mutations absent and the lease eventually reclaimable. A death after commit leaves the next page durable. A new process needs only its database connection and scan ID to claim the next job, not JSON or RAM history. Repeat/self-returning page numbers are rejected. Pagination adapter logic must use explicit nextPageNumber/end-of-list information and validate IGD's cache/order/count behavior before integration; the legacy per-folder in-memory seenFiles early-stop heuristic is not reused.

## Future /status

`status.js` exports an Express handler but does not register it. The view supplies scan status/gate, discovered/completed/pending/failed folders, distinct PDF count, total bytes as text, missing sizes, live leased/retry/blocked jobs and persistent update time. The handler adds unresolved job details and identifies PostgreSQL as authority. Database failure returns 503, with no fallback to misleading RAM counts. For integration, use a dedicated client or obtain/release a pool client per request. Persistent status must be wired only during a future approved code cutover.

## Validation

Run `npm test`. Tests use PGlite's embedded PostgreSQL engine and synthetic metadata only, including an on-disk database close/reopen test. They cover identity and shortcut deduplication, duplicates, incomplete parents, malformed checkpoint rejection, atomic/repeat imports, size conflicts and final reconciliation rollback, synthetic full acceptance totals, page idempotence/rollback, pagination, expired leases, stale worker fencing, heartbeat/retry, disabled scan protection, completed child protection and extra parents, and persistent restart.

This is not a live Render, Service Alliance, or real checkpoint test. Before deployment, run the same SQL/repository against a standalone supported PostgreSQL server, plus two independent connections/processes to validate simultaneous claims, locks, lease races and crash injection. PGlite alone does not establish multi-session production behavior. Validate 92,266-row import time and transaction size using the recovered sanitized checkpoint in an isolated database.

## Remaining work and exact future cutover order

These steps require future authorization; none was performed here.

1. Preserve the running instance. Obtain explicit approval for a read-only extraction of `/tmp/titan-manual-inventory/checkpoint.json` from the existing running instance using an already available access mechanism. No restart, deploy, configuration change or source request. If access is unavailable without changing the service, STOP. Aggregate JSON is insufficient.
2. Save a byte-for-byte original securely outside ephemeral storage, record its checksum, then create a sanitized offline copy and checksum. Never commit a sensitive source catalog/checkpoint to the repository. Verify sanitization did not alter saved identity keys, size values, done/seen or job state. Preserve the original separately.
3. Run strict offline dry-run. STOP on any structural, root/account, count, byte, unresolved-state or identity discrepancy; never fill gaps using source requests. Review unmatched first-parent references separately; absent secondary relationships are allowed.
4. With separate approval, provision/select an isolated standard PostgreSQL database, configure roles/backups and encrypted connectivity, apply the baseline, import atomically, and verify all four totals plus exactly one blocked job and zero live leases. Keep crawling_enabled=false.
5. Back up PostgreSQL and test restore. Test new independent processes, lease expiry, stale-worker rejection and restart using synthetic scans. Verify the imported 217 completed folders never acquire jobs. Perform database-only status and reconciliation checks after every test. Do not unblock UNSORTED MISC PDF FILES.
6. Complete a separately reviewed Node runtime adapter: integrate this package's database client lifecycle and status handler; remove automatic legacy JSON crawler startup; make PostgreSQL the sole state authority; default all source activity off; persist an explicit scan ID; validate request-ready sanitized IGD object needs and pagination semantics. There is no runnable production worker in this branch yet. Runtime integration must not trigger automatic rescans or missing-size lookups.
7. Prepare a specific reviewed commit, deployment configuration and rollback plan. Obtain explicit approval to merge/change production configuration/deploy; approval to this recovery branch is not deployment approval. Only after the original checkpoint and reconciled database are durably preserved may a deployment replace the running instance.
8. Deploy the approved version in paused/database-only mode. Check /status against exact acceptance totals. Restart only if separately approved and confirm identical database state, one blocked unresolved job and no source activity. Rollback must stay paused and database-backed; do not blindly redeploy the old auto-start JSON crawler, which could rescan after loss of /tmp.
9. Investigate only the unresolved folder in a later explicitly authorized task. Keep the 217 completed folders protected. PDF contents, R2 and Supabase remain outside this cutover.

## Production safety record

Only incremental GitHub commits/ref advances on `postgres-checkpoint-migration` were performed remotely. Default branch, production application files, Render service/configuration/environment/build/start settings and production endpoints were untouched. No Render tools, production POST requests, crawler starts, Service Alliance requests, unresolved-folder attempts, PDF requests, R2 operations or Supabase operations were performed.

## Fresh rebuild preparation (2026-09-30)

The no-checkpoint fresh path now has a separate paused initializer, guarded runtime, real PostgreSQL tests and change-aware comparison tooling. See [FRESH_REBUILD_RUNBOOK.md](FRESH_REBUILD_RUNBOOK.md) for the authoritative future deployment procedure and [PREPARATION_REPORT.md](PREPARATION_REPORT.md) for evidence and limitations. The earlier recovery/import instructions remain applicable only to an actual recovered checkpoint. Do not apply strict historical aggregate acceptance to a fresh scan. Neither legacy source nor Render configuration is modified by this preparation.
