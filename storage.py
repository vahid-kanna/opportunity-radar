"""Authoritative storage with additive migration and normalized public output."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
import quality

DB_FILE = os.environ.get('RADAR_DB', str(Path(__file__).with_name('radar.db')))

@contextmanager
def connection():
    c = sqlite3.connect(DB_FILE, timeout=20)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()

def init_db():
    with connection() as c:
        version = c.execute('PRAGMA user_version').fetchone()[0]
        if version > 2:
            raise RuntimeError(f'Unsupported database schema version {version}; maximum supported is 2')
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='opportunities'").fetchone()
        if exists and version < 2:
            path = str(DB_FILE) + '.pre-v2.bak'
            if not Path(path).exists():
                backup = sqlite3.connect(path)
                try:
                    c.backup(backup)
                finally:
                    backup.close()
        c.execute('BEGIN IMMEDIATE')
        c.execute('''CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY, type TEXT, title TEXT NOT NULL, organization TEXT NOT NULL,
            track TEXT, action_url TEXT NOT NULL, source TEXT, location TEXT,
            is_active INTEGER DEFAULT 1, discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_verified_at TEXT)''')
        cols = {r['name'] for r in c.execute('PRAGMA table_info(opportunities)')}
        for name in ('payload', 'last_seen_at', 'content_hash'):
            if name not in cols:
                c.execute(f'ALTER TABLE opportunities ADD COLUMN {name} TEXT')
        c.execute('''CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY, source TEXT, status TEXT, count INTEGER, error TEXT, attempted_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS scan_leases (
            name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at REAL NOT NULL)''')
        c.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('PRAGMA user_version=2')
        seeded = c.execute("SELECT value FROM metadata WHERE key='seeded'").fetchone()
    if not seeded:
        seed_existing_data()
        with connection() as c:
            c.execute("INSERT OR IGNORE INTO metadata VALUES ('seeded','1')")

def seed_existing_data():
    path = Path(__file__).with_name('seed_data.json')
    if not path.exists():
        return
    with connection() as c:
        ids = {r[0] for r in c.execute('SELECT id FROM opportunities')}
    for item in json.loads(path.read_text(encoding='utf-8')):
        if item['id'] not in ids:
            upsert_opportunity(item, imported=True)

def upsert_opportunity(opp, imported=False):
    if not all(opp.get(k) for k in ('id', 'title', 'organization')):
        raise ValueError('Missing identity, title or organization')
    payload = json.dumps(opp, ensure_ascii=False, sort_keys=True)
    with connection() as c:
        c.execute('''INSERT INTO opportunities
            (id,type,title,organization,track,action_url,source,location,payload,last_seen_at,content_hash,last_verified_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)
            ON CONFLICT(id) DO UPDATE SET type=excluded.type,title=excluded.title,
            organization=excluded.organization,track=excluded.track,action_url=excluded.action_url,
            source=excluded.source,location=excluded.location,payload=excluded.payload,
            last_seen_at=excluded.last_seen_at,content_hash=excluded.content_hash,last_verified_at=NULL''',
            (opp['id'],opp.get('type'),opp['title'],opp['organization'],opp.get('track'),opp.get('action_url') or '',
             opp.get('source'),opp.get('location'),payload,None if imported else quality.utcnow(),
             hashlib.sha256(payload.encode()).hexdigest()))

def get_all_active_opportunities():
    with connection() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM opportunities WHERE is_active=1')]
    result=[]
    for row in rows:
        raw=dict(row)
        if row.get('payload'):
            try:
                payload = json.loads(row['payload'])
            except (ValueError, TypeError):
                continue  # Unreadable provenance must fail closed.
            if not isinstance(payload, dict):
                continue
            raw.update(payload)
        # Check both persisted provenance and final payload: neither can launder the other.
        if not quality.is_public_record(row) or not quality.is_public_record(raw):
            continue
        raw['last_seen_at']=row.get('last_seen_at')
        raw['discovered_at']=row['discovered_at']
        result.append(quality.normalize(raw))
    return sorted(result,key=lambda r:(-r['fit_score'],r['title'].casefold()))

def acquire_scan_lease(owner, lease_seconds=600):
    """Atomically acquire the singleton lease; stale owners cannot release successors."""
    if not owner or lease_seconds <= 0:
        raise ValueError('A lease requires an owner and positive duration')
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        now = time.time()
        cursor = c.execute('''INSERT INTO scan_leases(name,owner,expires_at)
            VALUES ('full_scan',?,?) ON CONFLICT(name) DO UPDATE SET
            owner=excluded.owner,expires_at=excluded.expires_at
            WHERE scan_leases.expires_at<=?''', (owner, now + lease_seconds, now))
        return cursor.rowcount == 1


def release_scan_lease(owner):
    with connection() as c:
        c.execute("DELETE FROM scan_leases WHERE name='full_scan' AND owner=?", (owner,))


def record_scan(source,status,count=0,error=None):
    with connection() as c:
        c.execute('INSERT INTO scan_runs(source,status,count,error,attempted_at) VALUES (?,?,?,?,?)',
                  (source,status,count,str(error)[:500] if error else None,quality.utcnow()))

def get_scanner_health():
    with connection() as c:
        rows=[dict(r) for r in c.execute('SELECT * FROM scan_runs s WHERE id=(SELECT MAX(id) FROM scan_runs WHERE source=s.source)')]
        successes={r['source']:r['at'] for r in c.execute("SELECT source,MAX(attempted_at) AS at FROM scan_runs WHERE status='success' GROUP BY source")}
    result={r['source']:dict(source=r['source'],status=r['status'],count=r['count'],error=r['error'],last_attempt=r['attempted_at'],last_success=successes.get(r['source'])) for r in rows}
    for name in ('RemoteOK','Jobicy','Unstop'):
        result.setdefault(name,dict(source=name,status='not_run',count=0,error=None,last_attempt=None,last_success=None))
    return list(result.values())
