# Isolated fresh metadata rebuild and future cutover

Preparation date: 2026-09-30. This document authorizes no infrastructure changes or source requests.

## Protected historical service

Never execute these instructions against `srv-dau5ob893c1s73cr7000` (`titan-manual-scanner`, My Workspace `tea-dau3cbg93c1s73cijg6g`, Virginia, Free). Do not visit its URL to check health: even GET can wake a Free instance. Do not change its database, environment, source, commands, plan, disk, deploy settings or lifecycle. Legacy `server.js`, root `package.json`, `render.yaml`, and `main` remain unchanged. All commands below use **a new database and a new service**.

## Architecture and commands

Run from the repository's `postgres/` directory. Install with `npm ci`. Use a dedicated direct PostgreSQL connection for administration and each worker, with TLS verified when connecting remotely. Supply `TITAN_CATALOG_DATABASE_URL` securely; never put the URL/password in git, command arguments, output, or a shared environment group used by the protected service.

- `node admin.js migrate`: apply tracked standard PostgreSQL schema migrations. No source transport is invoked.
- `node admin.js init FRESH_SCAN_ID root.json`: create a paused run, a root folder and one page-1 job in one transaction. Root JSON must contain the reviewed root ID, account ID and required structural folder fields. This adds a new empty scan catalog; it never deletes existing scans or the shared source-identity table. Default initialization rejects any nonterminal run for the same account/root. Explicit library API `allowParallel:true` is required for intentional overlap.
- `node admin.js status FRESH_SCAN_ID`: persistent status.
- `node app.js`: new health/status-only application; startup cannot invoke metadata crawling. It requires `TITAN_SCAN_ID=FRESH_SCAN_ID`. Migrations and initialization are deliberate separate commands.
- `node admin.js pause FRESH_SCAN_ID`: persistent pause, serialized with claims/page commits. Future claims stop; a page already requested may drain and commit. Pause is not a retroactive cancellation of an HTTP request. Restart preserves this state.
- After approval only: `TITAN_METADATA_ENABLE_APPROVED=yes node admin.js validate-one FRESH_SCAN_ID`: enable exactly one additional successful page, with persistent page budget and concurrency one. Requires paused state and no live lease. Retries are bounded. Page commit atomically turns crawling off when the budget is consumed.
- After approval only: `TITAN_METADATA_WORKER_APPROVED=yes node run-worker.js`: deliberate worker launch. Configure `SAG_USERNAME`, `SAG_PASSWORD` in the new service's secret store only. It obtains session cookies/nonce in RAM when an eligible enabled job exists. A worker started against a paused run makes no source requests.
- Full rebuild, after separate approval only: `TITAN_METADATA_ENABLE_APPROVED=yes node admin.js enable FRESH_SCAN_ID`: clear the validation budget and enable/resume the same scan. Do not initialize another root or recrawl completed folders.
- `node admin.js compare FRESH_SCAN_ID [BASELINE_SCAN_ID]`: read-only repeatable-read reconciliation report. Without a detailed retained baseline it reports aggregate historical deltas and explicitly labels individual historical changes unavailable.

The worker is a standalone process with no HTTP listener, so it can be launched explicitly from an approved new-service administrative shell without conflicting with the status server. It is not automatically supervised by the health-only start command; after a service restart, an operator deliberately relaunches it. A future separate supervised background-worker service would need additional provisioning/deployment approval and compute budget.

A health-only deployment must use `node postgres/app.js` from repository root, with build command `npm ci --prefix postgres`. Do not use the legacy root start command. Future explicitly approved worker launch uses the separate `run-worker.js` invocation and guard above. A newly launched guarded worker resumes an enabled scan from PostgreSQL; an ordinary health/status app startup never starts it. Do not put the worker launch command in the initial deployment.

## Transaction and recovery behavior

Claim transaction locks the scan and eligible job, enforces persistent pause/rate/concurrency/page budget, advances fencing token, records owner/lease/attempts, then commits. No source request occurs inside a database transaction. The worker reads the retained folder, checks pause immediately before transport, and requests only that job's page.

The page transaction validates lease ownership/token/page/expiry; upserts unique source files; writes per-scan metadata snapshots and many-to-many folder/file relationships; retains nested child folder data and creates future jobs; writes next pagination; updates folder/job status; records page fingerprint; updates run progress and optional validation pause; checks lease expiry again; commits. Only then does the worker report success. Credentials, session cookies, authentication headers and nonce are never persisted. Secret-like fields and capability URLs are removed recursively from folder objects; other structural fields are preserved. If an omitted capability is required by a future folder, stop and design a secure ephemeral request mechanism rather than persisting it.

Before-commit death rolls back all page changes; the lease becomes eligible after expiry. After-commit death leaves the complete page, next page and children durable. Another process needs only database configuration, scan ID and its own authenticated session. Same-page/same-fingerprint replay succeeds without duplicate rows; different replay metadata is rejected. Completed folders are terminal. Stale workers cannot commit or mark a newer lease failed. Pausing never discards committed catalog data.

`(account_id, drive_file_id)` is the shared file identity; shortcuts use target ID for dedup while their returned metadata remains available for requests. Relationships use `(scan_id, folder_id, file_id)`. Per-scan snapshots preserve filenames/sizes across later scans, so legitimate library changes do not rewrite historical scan totals or trigger forced reconciliation. Conflicting known sizes within one scan cause a rolled-back page requiring investigation. Missing sizes stay explicitly unknown; no PDF-content fallback exists.

## Rate and failure controls

Database defaults: concurrency 1 (allowed 1–4), minimum delay 1000 ms between page claims (allowed 250–600000), maximum 5 attempts (allowed 1–20). Operator configuration updates only the new scan row. All HTTP transport calls additionally wait `TITAN_SOURCE_DELAY_MS` (default/minimum 1000 ms, maximum 60000); authentication traffic is subject to this per-call delay. Session initialization may involve several auth/metadata requests, not additional inventory pages. Ordinary requests have 15-second timeouts; established IGD metadata requests retain 90-second timeouts. Default worker lease is 600 seconds. If rates/timeouts are increased, ensure the bounded request duration fits the lease or add a dedicated-connection heartbeat before enabling.

Transient transport failures, HTTP 408/429/5xx retry with exponential delay 5,10,20,40… seconds capped at 3600. Other HTTP failures and invalid/conflicting page responses are terminal. Stored errors are fixed codes, never raw responses/errors/secrets. Repeated worker crashes also count against the same attempt ceiling. A blocked folder does not reset other committed folders. Retrying a terminal folder requires a separate reviewed administrative change to that specific job; no blanket retry/rescan command is provided. Investigate `UNSORTED MISC PDF FILES` separately only after explicit authorization.

## Persistent status

The new `/status` reads PostgreSQL only: scan ID/status, enabled/paused, discovered/completed/pending/failed folders, unique PDFs/bytes/missing sizes, queued/leased/retry/blocked jobs and terminal failures, last committed page ID/time and last database progress update. Database failure returns 503, never cached memory totals. `/health` verifies database accessibility and configured scan existence. Neither route invokes the source. No administrative POST endpoints are exposed.

## Historical comparison

Historical benchmark: 217 completed folders, 92,266 unique PDFs, 146,394,392,927 bytes, one unresolved folder named `UNSORTED MISC PDF FILES`. These are observations, not an acceptance constraint for a new crawl. Positive or negative deltas are reported without altering the catalog. The offline checkpoint importer retains its strict import-validation rules; fresh scans never call that importer or strict benchmark reconciliation.

The old detailed checkpoint is unavailable. Aggregate values cannot establish individual new/missing PDFs, renamed files, changed sizes or new/missing folders. The comparison tooling implements those comparisons when a future retained baseline scan exists. Absence means not observed; incomplete scans cannot prove deletion. Reports also list unresolved folders, total relationships and extra folder references. Identical duplicate relationship rows are prevented by primary keys; multiple valid folder references to one PDF are counted separately.

## Isolated database options and sizing

No remote database was created. A separate Free Render Postgres database is an option to review first, if the workspace's single-Free-database allowance is available. It has 1 GB storage, expires after 30 days, and lacks managed backups. It must have its own credentials, and must not be linked to the protected service or its environment groups. This is suitable only for disposable synthetic tests. Do not create it automatically. Sources: https://render.com/docs/free and https://render.com/docs/postgresql-backups (checked 2026-09-30).

For roughly 100,000 PDF metadata records, start with **1 GB RAM and 10 GB database disk**. This is an engineering estimate, not a measurement of the live library: filenames, JSON snapshots, relationship indexes, extra scan history, WAL and maintenance require headroom. Metadata itself will likely fit well below the historical 146 GB PDF-content volume. Measure `pg_total_relation_size` and database disk/WAL usage after validation; preserve space for indexes and vacuum, and alert at 70% usage. No PDF bodies belong in this database.

Render currently lists 1 GB Postgres compute at $19/month and storage at $0.30/GB/month: budget approximately **$22/month for 10 GB database storage**, excluding taxes, workspace plan, application compute, bandwidth and other charges. A lower $6/month 256 MB tier can be considered for tiny synthetic tests, but 1 GB RAM provides more margin for reconciliation/index operations. Verify the exact dashboard quote before provisioning. Sources: https://render.com/pricing and https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses (checked 2026-09-30).

Supabase is a later compatible alternative, not a migration performed here. Pro starts at $25/month with one Micro instance covered by compute credits, 1 GB RAM, 8 GB disk and seven-day daily-backup retention; storage above 8 GB is listed at $0.125/GB/month. PITR is an additional paid option. Source: https://supabase.com/pricing (checked 2026-09-30). Standard SQL, `pg` and direct connection transactions are used; no provider-specific storage API is required.

For a new paid 512 MB web service, Render lists $7/month. Database plus this separate paused status/application service would therefore start around **$29/month**, excluding the charges listed above; a separately provisioned supervised worker would add its own compute charge. Source: https://render.com/pricing.

For the real catalog require managed backups/PITR where available, plus encrypted off-platform daily logical backups retained at least 30 days, a dump before schema changes, and a tested restore into another isolated database. Render paid databases provide PITR with 3-day Hobby / 7-day Pro-or-higher windows; Free does not. Test restored counts, relationships and pagination before relying on backups. Keep backup credentials separate from source login. No backup bucket or paid resource is provisioned in this task.

## Future cutover procedure — DO NOT EXECUTE NOW

1. Obtain approval for the exact separate database/new-service names, plan, storage and monthly budget. Reject any action whose target is the protected service ID.
2. Provision the separate persistent database with unique credentials and restricted access; retain a normal PostgreSQL direct connection path. Record new database ID.
3. Apply schema with `node admin.js migrate`; initialize reviewed root with a unique scan ID. Verify paused, zero PDFs/bytes and one queued root job.
4. Verify backup policy and perform dump/restore into an isolated restore-test database; validate schema/status. No source requests.
5. Deploy a **new service**, for example `titan-manual-scanner-postgres`, pinned to the reviewed migration-branch commit. Use the health-only build/start commands above. Keep auto-deploy disabled initially. Record its distinct new service ID; never alter the legacy service.
6. Configure only new-service database URL/scan ID and verify `/health` and PostgreSQL `/status`. Do not launch a worker or configure source credentials yet.
7. Restart the **new service only**, still paused; verify persisted zero state and no leased jobs/source requests. Exercise synthetic worker recovery against a separate synthetic scan/database, not the live root.
8. Configure source secrets securely only in the new service, still without launching the worker. No authentication or source contact occurs merely from configuring secrets.
9. Obtain explicit approval for the one-page metadata-only validation. Specify expected root, page budget one, concurrency one, timing, and the possible auth requests.
10. Execute `validate-one` and deliberately launch the guarded worker on the new service. It stops additional claims automatically after one successful committed page. On failure, pause and inspect sanitized database state before another approval.
11. Verify persistent metadata, unique identities, complete structural child-folder objects, relationships, pagination, page fingerprint, missing sizes, pause and absence of PDF-content requests. Restart new process while paused and inspect again. Do not use the historical totals as a forced pass criterion.
12. Obtain separate explicit approval for the full fresh metadata rebuild, including rate/retry controls and the unresolved-folder policy.
13. Execute `enable` for the existing fresh scan ID and launch the guarded worker deliberately. No automatic broad rescan of completed folders; run a single worker initially.
14. Monitor database status, backup health, disk/connection usage, retry/terminal failures, successful page times and fencing. To stop future work use `pause`, then allow the in-flight page to drain or stop the new worker; PostgreSQL owns recovery.
15. Generate `compare` report against the aggregate historical benchmark. Explain differences and incompleteness; preserve report and catalog backup. Compare detailed identities only against an actually retained detailed baseline.
16. Separately obtain authorization to investigate problematic/unresolved folders. Any retirement/change of the legacy service requires its own future approval. PDF download, preview, R2 or Supabase Storage migration remains a separate future phase and is not included in any metadata approval.

**Next approval required:** approve creation of `titan-manual-catalog-postgres` in Virginia (1 GB RAM, 10 GB disk, approximately $22/month) and deployment of the separate `titan-manual-scanner-postgres` status/application service in paused state (512 MB, approximately $7/month), for an estimated $29/month base total and the backup policy above. Verify the final dashboard quote before creation. No source validation or full rebuild is authorized by that approval; each requires its own later explicit approval. Stop after preparation report.
