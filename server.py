import os
import sys
import threading
import time
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
import db
import scanner

app = Flask(__name__)
_manual_guard = threading.Lock()
_last_manual_scan = 0.0

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response

# Configurable scan interval (in seconds): 10 minutes = 600s
SCAN_INTERVAL_SECONDS = max(600, int(os.environ.get('SCAN_INTERVAL_SECONDS', '3600')))

def background_radar_worker():
    """Autonomous 24/7 scanning worker running every 10 minutes."""
    print("[Radar Worker] 24/7 Background Scheduler initialized.")
    # Run an initial scan on startup
    try:
        scanner.run_full_scan()
    except Exception as e:
        print(f"[Radar Worker] Initial scan error: {e}")

    while True:
        try:
            time.sleep(SCAN_INTERVAL_SECONDS)
            print(f"[Radar Worker] Triggering scheduled 10-minute scan at {datetime.now()}...")
            scanner.run_full_scan()
        except Exception as e:
            print(f"[Radar Worker] Scheduled scan error: {e}")

@app.route("/favicon.ico")
def favicon():
    svg_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="#FAF8F5" stroke="#D97706" stroke-width="8"/><circle cx="50" cy="50" r="28" fill="none" stroke="#D97706" stroke-width="4" stroke-dasharray="6 6"/><circle cx="50" cy="50" r="8" fill="#15803D"/></svg>"""
    return svg_icon, 200, {"Content-Type": "image/svg+xml"}

@app.route("/")
def index():
    db.init_db()
    opps = db.get_all_active_opportunities()
    
    total_count = len(opps)
    usd_count = sum(1 for o in opps if o['type'] == 'remote_usd')
    nat_count = sum(1 for o in opps if o['type'] == 'national_campus')
    q1_count = 0  # Legacy priority labels are not evidence-backed.
    
    return render_template(
        "index.html",
        opportunities=opps,
        total_count=total_count,
        usd_count=usd_count,
        nat_count=nat_count,
        q1_count=0, scanner_health=db.get_scanner_health(), scan_enabled=False
    )

@app.route("/api/opportunities")
def get_opportunities():
    db.init_db()
    opps = db.get_all_active_opportunities()
    return jsonify(opps)

@app.route("/api/scan", methods=["POST"])
def manual_scan():
    global _last_manual_scan
    import hmac
    token = os.environ.get('RADAR_SCAN_TOKEN', '')
    if not token or not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
        return jsonify(error='Operator authentication required'), 401
    with _manual_guard:
        if time.monotonic() - _last_manual_scan < 60:
            return jsonify(error='Scan rate limited; retry after 60 seconds'), 429
        _last_manual_scan = time.monotonic()
    result = scanner.run_full_scan()
    return jsonify(result), 409 if result.get('status') == 'busy' else 200

@app.route("/health")
def health():
    db.init_db()
    sources = db.get_scanner_health()
    now = datetime.now(timezone.utc)
    from quality import parse_date
    stale_after = SCAN_INTERVAL_SECONDS * 3
    source_state = 'current'
    for source in sources:
        seen = parse_date(source.get('last_success'))
        if source.get('status') in ('error', 'partial'):
            source_state = 'degraded'
            break
        if not seen or (now - seen).total_seconds() > stale_after:
            source_state = 'stale'
    # Liveness stays 200; discovery health is separate and never implied by HTTP uptime.
    return jsonify(status='healthy', discovery_status=source_state, version='2.1', service='opportunity-radar', sources=sources, timestamp=now.isoformat())

@app.route("/ready")
def readiness():
    # External monitors can distinguish process uptime from usable discovery.
    db.init_db()
    from quality import parse_date
    now = datetime.now(timezone.utc)
    sources = db.get_scanner_health()
    recent = [s for s in sources if s.get('status') == 'success' and parse_date(s.get('last_success')) and
              0 <= (now - parse_date(s['last_success'])).total_seconds() <= SCAN_INTERVAL_SECONDS * 3]
    ready = bool(recent)
    complete = len(recent) == len(sources)
    return jsonify(status='ready' if complete else 'degraded' if ready else 'unready',
                   recent_sources=len(recent), configured_sources=len(sources)), 200 if ready else 503

if __name__ == "__main__":
    db.init_db()
    # Start background scanner thread (daemon)
    worker_thread = threading.Thread(target=background_radar_worker, daemon=True)
    worker_thread.start()
    
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 8080)))
    print(f"Starting Opportunity Radar Web Service on 0.0.0.0:{port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
