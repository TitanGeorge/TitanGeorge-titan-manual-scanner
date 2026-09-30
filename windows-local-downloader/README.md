# Titan Service Manuals — Windows test package

This package downloads authorized PDFs directly to `C:\Service Manuals` with SQLite checkpoints. It is permanently limited to **10 successful PDFs per preserved catalog**, shared by every test/resume run. It contains no account credentials, session cookies, active nonce, downloaded manuals, or cloud infrastructure configuration. Setup, status, export, and offline tests do not access Service Alliance.

## Install on Windows 10/11

1. Download the ZIP and choose **Extract All**. Copy the extracted `windows-local-downloader` folder to `C:\Users\<your Windows username>\Documents\Titan Downloader` (or another writable local folder).
2. Install Python 3.11 or newer from https://www.python.org/downloads/windows/ if necessary. Include the **Python launcher**. The setup script checks `py -3` and the Python version. Administrator access is not required by this program.
3. Double click `setup.bat`. It creates `.venv`, installs pinned dependencies from PyPI, initializes SQLite, and checks disk space. Internet is needed to install dependencies. It performs no Service Alliance login, discovery, or download.
4. Default PDF destination is `C:\Service Manuals`. Setup must be able to create/write this directory. If Windows denies access, have the directory created and grant your normal account Modify permission. Do not run the downloader as administrator solely to work around permissions.
5. Double click `status.bat` to confirm the catalog starts empty.

Keep program files separate from the PDF destination. Copying/updating program files does not reset SQLite. Back up the entire `_catalog` directory together with the PDFs while the program is closed.

## Perform the authorized 10-PDF test

1. Double click `test-download.bat`.
2. Enter your Service Alliance username locally, then your password at the hidden password prompt.
3. Leave the window open. The program lists the proven APPLIANCE bootstrap and uses returned child objects. It downloads available PDFs as it encounters them, with one transfer at a time and two seconds between download starts by default.
4. It stops automatically at `TEST_LIMIT_REACHED` after 10 verified successes. Failed attempts do not count. If it pauses at `METADATA_PAGE_LIMIT_PAUSED_RESUME`, use the resume launcher to continue from the saved page; each invocation is capped at 20 page jobs as an additional discovery safeguard.
5. Double click `status.bat` and `export-manifest.bat` afterward.

The initial run does not offer a full-library option. There is no command-line or configuration setting to increase/reset the PDF limit. Do not delete/edit `_catalog`, change the destination to obtain a fresh budget, or modify the code to bypass the limit. A later explicit approval is required for a full-run build. The local files/database are user-owned, so deliberate manual tampering is outside an accidental-launch safeguard.

## Resume and safe pauses

Double click `resume-download.bat` after program closure, Windows restart, loss of internet, or an individual failure. It uses the same catalog and **the same lifetime 10-success budget**. The launcher prompts for a new local session each time; it never remembers the password.

- Authentication failure/expiry: stop, then resume and sign in again. The program does not bypass CAPTCHA, MFA, subscription restrictions, or access controls. If direct login is no longer accepted, share sanitized results so the adapter can be revised.
- Low space: free space and resume. Default minimum is **5 GiB**; config requires at least 1 GiB.
- Long server Retry-After: stop and wait before resuming. Retry-After over five minutes pauses the whole run. Shorter Retry-After is respected alongside backoff.
- HTTP 429, 5xx, interrupted transfer: at most three attempts for the selected file/page operation per invocation by default, with exponential delay and jitter. Failed records remain eligible on the next resume. A file is processed once per invocation; individual permanent failures do not loop endlessly or reset traversal.
- Metadata failure: checkpoint the failed folder and continue other queued folders. Resume retries failed folders without starting again at APPLIANCE.
- Existing unrelated destination file: stop that file with a conflict; never overwrite it. Ask for review before moving/deleting anything.
- Verified local file removed/changed: mark `needs_review`, preserve its consumed success slot, and do not silently download it again.

Exit codes: 0 normal completion/pause report; 2 bad command; 3 authentication; 4 disk safety; 5 safe operational error; 6 local storage; 7 unexpected failure with details withheld; 8 server rate pause; 130 interruption.

## Authentication and request protocol

The proven legacy member login form is submitted over HTTPS using runtime `input`/`getpass`; the Requests Session keeps correctly scoped cookies in memory. No credential environment variables, browser cookie export, cookie file, or browser profile is used. The application clears the session at shutdown. Credentials are absent from the database, logs, CSV, config, and source. Python cannot guarantee forensic erasure of process memory or OS swap; no application persistence of authentication material is performed.

The Tech Manuals page is fetched after login and refreshed before each PDF request. The program reads **current** `igd.nonce` and `igd.ajaxUrl` from the IGD configuration and checks the Service Alliance HTTPS origin. Download URL queries remain in memory and are never reported. Redirects are limited to HTTPS Service Alliance and the approved Google Drive/content hosts. Credential POST bodies are never forwarded to a different host.

Metadata uses `igd_get_files`, shortcode **7**, recursive jQuery-compatible POST encoding, sort by name descending, the proven one-item cache warmup, and a 500-item page size. Only the already-proven APPLIANCE bootstrap object is defined in source. Every nested folder comes from its parent response; folder IDs are never used to reconstruct child payloads. Structural metadata is preserved recursively, excluding cookies, nonces, tokens, resource keys, and remote links. If a folder requires an excluded transient field, fail safely and revise the adapter rather than persisting a secret.

Folder pages, returned objects, file records, file references, and next-page position commit together. Already visited pages and item identities detect repeated pagination and lack of progress. Traversal is deduplicated by account plus target ID, including shortcut targets. Discovery and downloading are interleaved; this build does not pre-enumerate the entire library.

## Download, verification, and recovery

`metadata → igd_download(id, accountId, shortcodeId=7, current nonce) → validated redirect → streaming .part → verify → atomic publication → SQLite verified`

Validation requires nonempty `%PDF-` bytes; an acceptable HTTP content type when present; complete Content-Length where reliable; exact source-size match when supplied; a trailing PDF EOF marker; strict PDF parsing; at least one page; readable page dictionaries and content streams; and a SHA-256 digest. Encrypted PDFs and malformed/questionable files are withheld for review. Successful status is never based on HTTP 200 alone. Strict parsing can reject a usable but nonconforming PDF, which is preferable to a false success in this test.

Temporary files live beside their final file on the same volume. Data is flushed/fsynced, validation metadata is persisted as `staged`, the file is published without overwriting an existing destination, then SQLite records success. Windows uses atomic, no-overwrite `os.rename`; the Linux development path uses a no-clobber hard-link publication. A persistent budget reservation protects every in-flight/staged file.

Recovery adopts a staged final/part only when its parsed bytes match the saved SHA-256. Incomplete or unstaged parts remain retryable; PDF-looking rejects move to quarantine and non-PDF/HTML bodies are deleted. HTTP byte-range continuation is deliberately not assumed for nonce-protected/redirected links: an interrupted unverified transfer restarts that PDF from byte zero. Completed files and discovery pages still resume from SQLite. This is job/checkpoint resumability, not guaranteed byte-range resume.

SQLite uses WAL, foreign keys, and FULL synchronization. The kernel process lock is released automatically at process/Windows termination. Transfer concurrency is configurable between 1 and 2. SQL independently enforces at most ten permanent successes plus active reservations, and prevents deleting/downgrading success slots through the normal program. No reset command is provided.

## Catalog and filenames

Tables in `schema.sql`:

| Table | Purpose |
|---|---|
| `meta` | Schema version 1; reject unknown versions |
| `folders` | Complete sanitized returned object, source path, page, warmup, status, attempts, safe error |
| `folder_pages` | Committed visited pages |
| `folder_items` | Seen source identities per folder for pagination progress |
| `manuals` | Unique account/target identity, request identity, source/local filenames, folder/path, MIME, expected/actual sizes, status, attempts, HTTP code, signature, verification, SHA-256, safe error, timestamps |
| `file_refs` | Every distinct folder/source reference to a deduplicated PDF |
| `budget` | Permanent success ledger and crash-safe in-flight reservations |

Filename identity is the source ID plus account, not the name. A shortcut to the same target is another reference. Different accounts remain separate authorization identities. One file appearing in multiple folders downloads once and keeps all references in SQLite.

Local organization uses the first encountered manufacturer/source folder. Filename stems are bounded, Windows-invalid characters replaced, reserved device names prefixed, trailing dots/spaces removed, and the full SHA-256 of source identity appended. This deterministic suffix also handles identical names, case-only differences, and truncation collisions. Original filenames remain unchanged in SQLite. Local paths are uniquely constrained case-insensitively; paths over 240 characters fail safely. Destination roots over 65 characters and UNC paths are rejected by configuration. No file is silently overwritten.

## Configuration and disk protection

`config.json` has no authentication material and no download-limit setting:

| Setting | Default | Allowed |
|---|---:|---|
| destination | `C:\Service Manuals` | Absolute local path, max 65 characters |
| concurrency | 1 | 1–2 |
| delay_seconds | 2 | 1–60 |
| minimum_free_gib | 5 | 1–10000 |
| retries | 3 | 1–5 |
| metadata_pages_per_run | 20 | 1–20 |

Free space is checked before the run, before page discovery, before each transfer, against expected/HTTP size when available, and before every streamed chunk. Falling below the reserve causes a clean pause. Creating the catalog and the initial state file verifies write access. No disk quota can defend against another application simultaneously exhausting the drive; local I/O failures are also handled safely.

## Reports to share

`status.bat` writes `C:\Service Manuals\_catalog\reports\status.json`. `export-manifest.bat` writes `manifest.csv` there. Events are in `_catalog\logs\events.jsonl`.

Upload **status.json and events.jsonl** back to Work first. They contain counts, safe event codes, opaque hashed identities, timestamps, disk free space, and folder progress; no login values, session cookies, nonces, redirect URLs, or raw exception text. You may also upload the manifest; it contains source filenames, source account/file identities, source folder paths and your local destination paths, so review those ordinary metadata fields first. CSV values beginning with Excel formula characters are escaped. Do not send passwords, browser session exports, raw network traces, `_catalog\quarantine` contents, or the downloaded PDFs.

## Package and tests

| File | Purpose |
|---|---|
| `downloader.py` | Commands, config validation, bounded scheduler |
| `core.py` | Catalog, safety ledger, verification, recovery, disk checks |
| `service.py` | Runtime authentication, IGD request encoding, safe redirects |
| `inventory.py` | Transactional page traversal |
| `schema.sql` | Idempotent schema version 1 initialization |
| `requirements.txt`, `config.json` | Pinned dependencies and conservative settings |
| `setup.bat` | Python check, virtual environment, dependencies, catalog setup |
| `test-download.bat`, `resume-download.bat` | Shared 10-success run/resume |
| `status.bat`, `export-manifest.bat` | Local reporting |
| `tests/` | Synthetic PDFs/fake HTTP, no real network |
| `test-results.txt` | Offline verification results |
| `README.md` | Installation, protocol, safety and limitations |

Run offline tests on Windows from the package directory:

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Development validation: **62 offline tests passed** on Python 3.12/Linux using the pinned dependencies. They cover the required SQLite, restart, deduplication, references, names, collisions, parts, HTML, invalid/truncated PDFs, size mismatch, hash, atomic publication, retries, 429/500, auth expiry, disk stop, ten-success limit, CSV, pagination transactions, redirect restrictions, exact protocol fields, sanitized exceptions, and long rate-limit pauses. CLI init/status/export and ZIP integrity are checked separately. Synthetic PDFs are created temporarily by the tests and are not shipped as manuals.

## Release status and remaining unknowns

This is an implementation validated with fixtures, ready for your limited Windows test; it has not been executed on a real Windows PC or validated against a new live Service Alliance session. Windows batch launchers, `msvcrt` locking, and Windows atomic rename still need your local test. The previous one-PDF proof establishes the authorized flow, but the new adapter's live behavior remains to be confirmed. Account-specific MFA/CAPTCHA, plugin markup changes, unusual shared folders, Google download-confirmation/virus-scan pages, unsupported content types, and strict-PDF false negatives stop or fail safely; no bypass is implemented.

No Render production action or request occurred. No PostgreSQL, R2, or Supabase resource was created. No Service Alliance metadata crawl or PDF download occurred during this assignment. Main and the PostgreSQL branch were not changed or merged. All work is isolated under `windows-local-downloader/` on the `windows-local-downloader` GitHub branch. The hard ten-success limit remains enforced. The full-library run requires a later explicitly approved change.
