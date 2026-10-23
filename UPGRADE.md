# Evidence-First Opportunity Radar Upgrade

## Verification & Test Results
- **Backend Test Suites**: `python -m unittest test_feedclient test_discovery test_boundaries test_quality test_scanner test_lease test_qualification test`
  - **108 unit & regression tests passed** in ~13s.
- **Browser Playwright Suites**: `uv run --offline --with flask --with playwright python -m unittest test_ui test_ui_additions`
  - **13 real-browser UI tests passed** across mobile & desktop viewports.
  - Viewport verification: 320px first result at 417.5px; 390px at 389.2px; 449px at 389.2px (all within compact <450px mobile standard).
- **Canonical Verification**: `hermes verify --json` -> `ok: true`.
- **Live Discovery Verified**: Live scan against Greenhouse (Razorpay) confirmed matching roles without false eligibility claims.

## Key Upgrades & Architecture

### 1. Robust Feed Client (`feedclient.py`)
- Strict domain allowlist (`remoteok.com`, `jobicy.com`, `unstop.com`, `boards-api.greenhouse.io`, `api.lever.co`, `api.eu.lever.co`).
- HTTPS-only with pre-follow redirect validation via `SafeRedirectHandler`.
- Exponential backoff with `Retry-After` header support for 429/5xx errors (max 2 retries, max 5s wait).
- Conditional HTTP caching with ETag and Last-Modified in SQLite (`radar-http-cache.sqlite3`).
- 304 revalidation avoids re-downloading unchanged feeds while tracking exact last-seen timestamps; no stale fallback on hard error.

### 2. Official Employer ATS Integration (`ats.py`)
- Opt-in configuration via `RADAR_BOARDS_FILE` or `boards.example.json`.
- Direct Greenhouse and Lever API adapters with token and destination host validation.
- Official listings tagged as `source_authority: employer_ats` while maintaining conservative eligibility verification.

### 3. Evidence-Backed Qualification & Review (`review.py`, `radar_profile.py`, `quality.py`)
- Explicit tri-state eligibility (`eligible`, `unknown`, `ineligible`) and availability (`open`, `unknown`, `stale`, `closed`).
- Configurable candidate profile (`RADAR_PROFILE_JSON`) covering graduation year, branch, country, and experience.
- Private operator review registry (`RADAR_REVIEW_FILE`) with content fingerprinting (SHA-256), cycle ID safety, and 90-day evidence expiry.
- Scraped assertions are never trusted for automated promotion; promotion to Best requires verified evidence.

### 4. Canonical Deduplication & Storage Safety (`storage.py`)
- Deduplication merges duplicate listings across aggregators and direct boards while preserving all source attributions.
- SQLite cross-process scan lease prevents race conditions across multi-worker environments.
- Additive database schema migrations with automated backup (`radar.db.pre-v2.bak`) on upgrade.
- Future schema version protection rejects `user_version > 2` before write attempts.
- Content hash excludes fetch provenance timestamps to avoid false mutation detection.
- Database file `radar.db` untracked from Git to protect production data from being overwritten on repository sync.

### 5. Server Health & Monitoring (`server.py`)
- Separate `/health` (process liveness) and `/ready` (discovery freshness and source health).
- Returns 503 from `/ready` if all discovery sources are failed or stale.
- Manual scan endpoint (`POST /api/scan`) protected with Bearer token authentication (`RADAR_SCAN_TOKEN`) and 60-second rate limiting.
- Security headers enabled: `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, `Cache-Control: no-store`.

### 6. User Interface Polish (`index.html`, `radar.js`, `radar.css`)
- Safe DOM construction avoiding `innerHTML` vulnerabilities.
- CSV export for visible filtered results with spreadsheet formula injection escaping (`=`, `+`, `-`, `@`, `\t`, `\r`).
- Shareable filtered URLs with fallback copy dialog when clipboard API is restricted.
- Clear disclosure of compensation status ("Not disclosed" vs raw amounts).
- Real-time source health breakdown with status and attempt ages.
- Device-only local actions (Save, Applied, Hide) using `localStorage` with explicit disclaimer.
