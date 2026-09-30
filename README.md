# Titan Manual Scanner

Metadata-only inventory scanner for the authorized Service Alliance APPLIANCE manual library.

## Safety design
- Does **not** call `igd_download`.
- Does **not** fetch PDF content.
- Does **not** save PDFs locally or to cloud storage.
- Does **not** persist cookies, nonce values, or a manual catalog.
- Keeps only aggregate scan statistics in process memory: folders, unique PDF count, reported bytes, missing-size count, and failed-folder count.
- Uses a 750 ms delay and retries failed folder metadata requests up to 3 times.

## Endpoints
- `GET /` scanner mode and status
- `GET /status` aggregate scan status
- `POST /scan` starts a metadata scan using an authenticated cookie and current nonce supplied for that run only

Authentication values must never be committed to this repository.
