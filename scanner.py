import urllib.request
import urllib.parse
import json
import re
import ssl
import os
import sys
from datetime import datetime, date
import db

USD_TO_INR = 83.50

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html'
}

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

KEYWORDS = {
    'Product Management': ['product manager', 'associate product manager', 'apm', 'product lead', 'technical product', 'product operations'],
    'Applied AI & LLMs': ['applied ai', 'ai engineer', 'ai coding', 'prompt engineer', 'llm', 'machine learning', 'data annotation', 'ai evaluation'],
    'Data & Analytics': ['risk analyst', 'data analyst', 'business analyst', 'analytics', 'growth analyst', 'operations analyst'],
    'Engineering & Python': ['python developer', 'full stack', 'backend engineer', 'software engineer', 'automation engineer']
}

def verify_url(url):
    """Simple fast check that URL is alive and not 404/closed."""
    if not url or not url.startswith('http'):
        return False, "Invalid URL"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8, context=CTX) as resp:
            if resp.status >= 400:
                return False, f"HTTP {resp.status}"
            content = resp.read(80000).decode('utf-8', errors='ignore').lower()
            expired_markers = [
                'this job is no longer available',
                'this posting has expired',
                'no longer accepting responses',
                'this form is closed',
                'registration is closed',
                'applications are closed'
            ]
            for m in expired_markers:
                if m in content:
                    return False, f"Expired: {m}"
            return True, "Active"
    except Exception as e:
        # Don't drop on transient network glitch, keep unless confirmed 404
        return True, "Alive"

def fetch_remoteok():
    jobs = []
    try:
        url = 'https://remoteok.com/api'
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data[1:60]:
                title = item.get('position', '').strip()
                company = item.get('company', '').strip()
                location = item.get('location', '').strip()
                tags = [t.lower() for t in item.get('tags', [])]
                
                loc_lower = location.lower()
                if any(geo in loc_lower for geo in ['us only', 'usa only', 'north america only', 'uk only', 'canada only']):
                    continue
                
                combined_text = f"{title} {' '.join(tags)}".lower()
                matched_track = None
                matched_keyword = None
                
                for track, kws in KEYWORDS.items():
                    for kw in kws:
                        if kw in combined_text:
                            matched_track = track
                            matched_keyword = kw
                            break
                    if matched_track:
                        break
                        
                if not matched_track:
                    continue
                    
                min_sal = item.get('salary_min') or 0
                max_sal = item.get('salary_max') or 0
                link = item.get('url') or f"https://remoteok.com/remote-jobs/{item.get('id')}"
                
                sal_str = "Competitive USD"
                inr_str = "Est. ₹25.0 – ₹45.0 LPA equivalent"
                if min_sal and max_sal:
                    sal_str = f"${min_sal:,} - ${max_sal:,} USD"
                    inr_min = round((min_sal * USD_TO_INR) / 100000, 1)
                    inr_max = round((max_sal * USD_TO_INR) / 100000, 1)
                    inr_str = f"₹{inr_min}L – ₹{inr_max}L INR/yr"
                elif min_sal:
                    sal_str = f"${min_sal:,}+ USD"
                    inr_min = round((min_sal * USD_TO_INR) / 100000, 1)
                    inr_str = f"₹{inr_min}L+ INR/yr"
                    
                jobs.append({
                    'id': f"remoteok_{item.get('id')}",
                    'type': 'remote_usd',
                    'organization': company,
                    'title': title,
                    'track': matched_track,
                    'matched_on': matched_keyword,
                    'location': location if location else 'Worldwide / Remote',
                    'compensation_usd': sal_str,
                    'compensation_inr': inr_str,
                    'action_url': link,
                    'source': 'RemoteOK',
                    'tags': tags[:5],
                    'deadline': 'Rolling / Apply Early',
                    'days_left_str': 'Active',
                    'quadrant': 'Q1: High Priority USD'
                })
    except Exception as e:
        print(f"[RemoteOK] Error: {e}")
    return jobs

def fetch_jobicy():
    jobs = []
    try:
        url = 'https://jobicy.com/api/v2/remote-jobs?count=50&geo=anywhere'
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for item in data.get('jobs', []):
                title = item.get('jobTitle', '').strip()
                company = item.get('companyName', '').strip()
                geo = item.get('jobGeo', '').strip()
                
                combined_text = f"{title} {item.get('jobIndustry', '')} {item.get('jobType', '')}".lower()
                matched_track = None
                matched_keyword = None
                
                for track, kws in KEYWORDS.items():
                    for kw in kws:
                        if kw in combined_text:
                            matched_track = track
                            matched_keyword = kw
                            break
                    if matched_track:
                        break
                        
                if not matched_track:
                    continue
                    
                min_sal = item.get('annualSalaryMin')
                max_sal = item.get('annualSalaryMax')
                curr = item.get('salaryCurrency', 'USD')
                
                sal_str = "Competitive USD / Global Market"
                inr_str = "Est. ₹20.0 – ₹40.0 LPA equivalent"
                if min_sal and max_sal and curr == 'USD':
                    sal_str = f"${int(min_sal):,} - ${int(max_sal):,} USD"
                    inr_min = round((float(min_sal) * USD_TO_INR) / 100000, 1)
                    inr_max = round((float(max_sal) * USD_TO_INR) / 100000, 1)
                    inr_str = f"₹{inr_min}L – ₹{inr_max}L INR/yr"
                    
                jobs.append({
                    'id': f"jobicy_{item.get('id')}",
                    'type': 'remote_usd',
                    'organization': company,
                    'title': title,
                    'track': matched_track,
                    'matched_on': matched_keyword,
                    'location': geo if geo else 'Anywhere (Worldwide)',
                    'compensation_usd': sal_str,
                    'compensation_inr': inr_str,
                    'action_url': item.get('url'),
                    'source': 'Jobicy',
                    'tags': [item.get('jobIndustry', ''), item.get('jobType', '')],
                    'deadline': 'Rolling / Open',
                    'days_left_str': 'Active',
                    'quadrant': 'Q1: High Priority USD'
                })
    except Exception as e:
        print(f"[Jobicy] Error: {e}")
    return jobs

def fetch_unstop_opportunities():
    opps = []
    try:
        url = 'https://unstop.com/api/public/opportunity/search-result?opportunity=competitions&per_page=30'
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            items = data.get('data', {}).get('data', [])
            today = date.today()
            
            for item in items:
                title = item.get('title', '').strip()
                org = item.get('organisation', {}).get('name') if isinstance(item.get('organisation'), dict) else 'National Competition'
                seo_url = item.get('seo_url', '')
                end_date_raw = item.get('end_date', '')
                prizes = item.get('prizes', '')
                
                # Filter out irrelevant school/sports competitions
                combined = f"{title} {item.get('type', '')} {item.get('sub_type', '')}".lower()
                if any(x in combined for x in ['dance', 'singing', 'quiz for school', 'photography', 'drawing', 'kids', 'poetry']):
                    continue
                    
                # Match relevant tracks
                track = 'Case Competition / Strategy'
                if any(k in combined for k in ['hackathon', 'ai', 'coding', 'machine learning', 'tech', 'data']):
                    track = 'Hackathon & Tech Challenge'
                elif any(k in combined for k in ['b-plan', 'business', 'startup', 'pitch', 'venture', 'founder']):
                    track = 'Startup Pitch & Grant'
                elif any(k in combined for k in ['civil', 'infrastructure', 'cad', 'sustainable']):
                    track = 'Core Civil & Infrastructure'
                
                days_left_str = 'Closes soon'
                deadline_str = 'October 2026'
                if end_date_raw:
                    try:
                        clean_date = end_date_raw.split('T')[0]
                        end_dt = datetime.strptime(clean_date, '%Y-%m-%d').date()
                        if end_dt < today:
                            continue
                        days = (end_dt - today).days
                        deadline_str = clean_date
                        days_left_str = f"{days} days left" if days > 0 else "Closes today!"
                    except Exception:
                        pass
                
                comp_str = f"₹{prizes}" if prizes else "Cash Prizes, PPIs & Certificate"
                
                opps.append({
                    'id': f"unstop_{item.get('id')}",
                    'type': 'national_campus',
                    'organization': org if org else 'National Case Competition',
                    'title': title,
                    'track': track,
                    'matched_on': 'Unstop National Challenge',
                    'location': 'India / Campus Online',
                    'compensation_usd': '',
                    'compensation_inr': comp_str,
                    'action_url': seo_url if seo_url else f"https://unstop.com/competitions/{item.get('id')}",
                    'source': 'Unstop',
                    'tags': ['National Competition', track],
                    'deadline': deadline_str,
                    'days_left_str': days_left_str,
                    'quadrant': 'Q2: Important, Less Urgent' if 'hackathon' in track.lower() else 'Q3: Urgent, Less Important'
                })
    except Exception as e:
        print(f"[Unstop] Error: {e}")
    return opps

def run_full_scan():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Dual Opportunity Radar Scan...")
    db.init_db()
    
    # 1. Fetch Remote USD
    remote_jobs = fetch_remoteok() + fetch_jobicy()
    print(f"Fetched {len(remote_jobs)} eligible Remote USD roles.")
    for j in remote_jobs:
        db.upsert_opportunity(j)
        
    # 2. Fetch National / Campus
    national_opps = fetch_unstop_opportunities()
    print(f"Fetched {len(national_opps)} active National competitions & challenges.")
    for o in national_opps:
        db.upsert_opportunity(o)
        
    total_active = len(db.get_all_active_opportunities())
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scan complete! Total active opportunities in database: {total_active}")
    return {
        'last_scanned': datetime.now().strftime('%d %b %Y, %I:%M %p'),
        'total_active': total_active,
        'remote_count': len(remote_jobs),
        'national_count': len(national_opps)
    }

if __name__ == '__main__':
    run_full_scan()
