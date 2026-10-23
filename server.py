import os
import sys
import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import db
import scanner

app = Flask(__name__)

# Configurable scan interval (in seconds): 10 minutes = 600s
SCAN_INTERVAL_SECONDS = 600

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
    q1_count = sum(1 for o in opps if o.get('quadrant', '').startswith('Q1'))
    
    return render_template(
        "index.html",
        opportunities=opps,
        total_count=total_count,
        usd_count=usd_count,
        nat_count=nat_count,
        q1_count=q1_count
    )

@app.route("/api/opportunities")
def get_opportunities():
    opps = db.get_all_active_opportunities()
    return jsonify(opps)

@app.route("/api/scan", methods=["POST"])
def manual_scan():
    result = scanner.run_full_scan()
    return jsonify({"status": "success", "result": result})

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "opportunity-radar", "timestamp": datetime.now().isoformat()})

if __name__ == "__main__":
    db.init_db()
    # Start background scanner thread (daemon)
    worker_thread = threading.Thread(target=background_radar_worker, daemon=True)
    worker_thread.start()
    
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 8080)))
    print(f"Starting Opportunity Radar Web Service on 0.0.0.0:{port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
