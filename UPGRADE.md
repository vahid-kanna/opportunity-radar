# Evidence-first radar upgrade

## Verification
- `python -m unittest test_boundaries test_quality test_scanner test_lease test`: 48 tests passed.
- `uv run --with flask --with playwright python test_ui.py`: 6 real-browser tests passed, covering responsive layout, keyboard controls, safe rendering, sorting, taxonomy filters, URL restoration, device-only actions and storage failure.
- At 390x844, first card begins at 349.1875px.
- Live local scan succeeded: RemoteOK 18, Jobicy 8, Unstop 5 records received. These are discovery records, not proven eligible jobs.

## Operation and migration
Run `python main.py` or `python server.py` as one process. Each entrypoint starts one scheduler; importing server starts none. Default interval is now one hour (SCAN_INTERVAL_SECONDS, minimum ten minutes).
Storage supports RADAR_DB for a persistent database path. Existing SQLite is backed up to radar.db.pre-v2.bak before additive migration. Imported seeds do not overwrite existing IDs. Raw records remain in storage; normalized public output excludes unsupported claims and private campus-mail sources.
POST /api/scan requires Authorization: Bearer with a configured RADAR_SCAN_TOKEN. No token is shipped or exposed in the UI; unauthorized requests receive 401. Scans coalesce across processes sharing the database through an atomic owner-scoped SQLite lease, with a bounded scan deadline and lease expiry. Keep one scheduler owner to avoid redundant scheduling.
Save/Applied/Hide are browser-only state, not synced accounts or actual application submission.

## Deployment precautions
Back up production database and download it before host synchronization. Never overwrite production SQLite with local test data or upload database binaries in the source ZIP. The existing repository tracks radar.db; remove that path from source synchronization or deploy a text-only ZIP excluding it. Do not use destructive Replace sync until persistent database behavior is verified.
Do not include pre-existing app.py, preview.html, .hermes config or unrelated requirements changes in this upgrade.
After deployment read /health and /api/opportunities, load mobile and desktop pages, verify static files, verify scan authentication, reconcile source health and inspect corrected entries.

## Deliberately incomplete
This is a conservative discovery/review release, not full qualification automation. All imports remain unverified until explicit field-level qualification is implemented; Best matches can correctly be empty. Current adapters cover RemoteOK, Jobicy and Unstop only. Automatic official employer/ATS enrichment, live incubator call discovery, configurable candidate rules, conditional fetch caching/retry, freshness expiry and authenticated cross-device tracking remain follow-up work. Audited SISFS/L&T/PRAYAS conflicts are dated evidence, not a substitute for future source rechecks. Public campus mail ingestion is disabled pending proper authentication/privacy design.

Do not call the project fully fixed or deployed until remaining acceptance criteria are met and production is read back.
