import sqlite3
import os
import json
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "radar.db")

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            type TEXT, -- 'remote_usd' or 'national_campus'
            title TEXT NOT NULL,
            organization TEXT NOT NULL,
            track TEXT,
            compensation_usd TEXT,
            compensation_inr TEXT,
            eligibility TEXT,
            deadline TEXT,
            days_left_str TEXT,
            quadrant TEXT,
            action_url TEXT NOT NULL,
            source TEXT,
            matched_on TEXT,
            tags TEXT,
            location TEXT,
            is_active INTEGER DEFAULT 1,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    
    # Check if empty, then seed
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM opportunities WHERE type='national_campus'")
    c = cur.fetchone()[0]
    conn.close()
    if c <= 2:
        seed_existing_data()

def seed_existing_data():
    conn = get_connection()
    cur = conn.cursor()
    
    # 1. Seed from seed_data.json if present
    seed_json = os.path.join(os.path.dirname(__file__), "seed_data.json")
    if os.path.exists(seed_json):
        try:
            with open(seed_json, 'r', encoding='utf-8') as f:
                items = json.load(f)
                for item in items:
                    upsert_opportunity(item)
            return
        except Exception as e:
            print(f"[Seed] Error loading seed_data.json: {e}")

    # 2. Seed from national opps.db if present
    local_opps_db = r"C:\Users\vahid\AI_Brain\career\opportunities\opps.db"
    if os.path.exists(local_opps_db):
        try:
            src_conn = sqlite3.connect(local_opps_db)
            src_cur = src_conn.cursor()
            src_cur.execute("""
                SELECT id, title, organization, category, compensation_or_grant, 
                       eligibility, deadline, days_left_str, quadrant, action_url, source, match_reason, track
                FROM tracked_opportunities WHERE is_active = 1
            """)
            for row in src_cur.fetchall():
                cur.execute("""
                    INSERT OR REPLACE INTO opportunities 
                    (id, type, title, organization, track, compensation_usd, compensation_inr, 
                     eligibility, deadline, days_left_str, quadrant, action_url, source, matched_on, location, is_active)
                    VALUES (?, 'national_campus', ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, 'India / Campus', 1)
                """, (
                    f"nat_{row[0]}",
                    row[1],
                    row[2],
                    row[12] if row[12] else row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7] if row[7] else 'Active',
                    row[8] if row[8] else 'Q2: Important, Less Urgent',
                    row[9],
                    row[10],
                    row[11]
                ))
            src_conn.close()
        except Exception as e:
            print(f"[Seed] Warning reading local opps.db: {e}")

    # 2. Seed from remote_usd_jobs.json if present
    local_remote_json = r"C:\Users\vahid\AI_Brain\career\opportunities\remote_usd_jobs.json"
    if os.path.exists(local_remote_json):
        try:
            with open(local_remote_json, 'r', encoding='utf-8') as f:
                jobs = json.load(f)
                for j in jobs:
                    cur.execute("""
                        INSERT OR REPLACE INTO opportunities
                        (id, type, title, organization, track, compensation_usd, compensation_inr,
                         eligibility, deadline, days_left_str, quadrant, action_url, source, matched_on, tags, location, is_active)
                        VALUES (?, 'remote_usd', ?, ?, ?, ?, ?, 'Global Applicants (Worldwide / Anywhere)', 
                                'Rolling / Apply ASAP', 'Active', 'Q1: High Priority USD', ?, ?, ?, ?, ?, 1)
                    """, (
                        j.get('id'),
                        j.get('title'),
                        j.get('company'),
                        j.get('track'),
                        j.get('salary_usd'),
                        j.get('salary_inr'),
                        j.get('link'),
                        j.get('source'),
                        j.get('matched_on'),
                        json.dumps(j.get('tags', [])),
                        j.get('location', 'Worldwide / Anywhere')
                    ))
        except Exception as e:
            print(f"[Seed] Warning reading local remote_usd_jobs.json: {e}")

    conn.commit()
    conn.close()

def get_all_active_opportunities():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM opportunities 
        WHERE is_active = 1
        ORDER BY 
            CASE 
                WHEN quadrant LIKE 'Q1%' THEN 1
                WHEN quadrant LIKE 'Q2%' THEN 2
                WHEN quadrant LIKE 'Q3%' THEN 3
                ELSE 4
            END,
            discovered_at DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def upsert_opportunity(opp):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO opportunities 
        (id, type, title, organization, track, compensation_usd, compensation_inr, 
         eligibility, deadline, days_left_str, quadrant, action_url, source, matched_on, tags, location, is_active, last_verified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
    """, (
        opp['id'],
        opp['type'],
        opp['title'],
        opp['organization'],
        opp.get('track', 'General'),
        opp.get('compensation_usd', ''),
        opp.get('compensation_inr', ''),
        opp.get('eligibility', 'Open / Eligible'),
        opp.get('deadline', 'Rolling 2026 Cohort'),
        opp.get('days_left_str', 'Active'),
        opp.get('quadrant', 'Q2: Important, Less Urgent'),
        opp['action_url'],
        opp.get('source', 'Radar Scan'),
        opp.get('matched_on', ''),
        json.dumps(opp.get('tags', [])),
        opp.get('location', 'Global')
    ))
    conn.commit()
    conn.close()
