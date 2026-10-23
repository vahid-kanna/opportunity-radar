import os
from server import app, background_radar_worker, db
import threading

if __name__ == "__main__":
    db.init_db()
    # Start 24/7 background radar worker thread
    t = threading.Thread(target=background_radar_worker, daemon=True)
    t.start()
    
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 8080)))
    print(f"[Botkeep] Launching Opportunity Radar on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
