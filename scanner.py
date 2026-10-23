"""Evidence-first feed adapters: missing facts are never eligibility or availability."""
import json
import math
import re
import ssl
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import uuid
import storage as db

HEADERS = {'User-Agent': 'OpportunityRadar/1.0', 'Accept': 'application/json'}
CTX = ssl.create_default_context()
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SCAN_LOCK = threading.Lock()
KEYWORDS = {
    'Product Management': ['product manager', 'associate product manager', 'apm', 'product lead', 'technical product', 'product operations'],
    'Applied AI & LLMs': ['applied ai', 'ai engineer', 'ai coding', 'prompt engineer', 'llm', 'machine learning', 'data annotation', 'ai evaluation'],
    'Data & Analytics': ['risk analyst', 'data analyst', 'business analyst', 'analytics', 'growth analyst', 'operations analyst'],
    'Engineering & Python': ['python developer', 'full stack', 'full-stack', 'backend engineer', 'software engineer', 'automation engineer'],
}
COMPETITION_KEYWORDS = {
    'Hackathon & Tech Challenge': ['hackathon', 'coding', 'machine learning', 'ai', 'data challenge', 'tech challenge'],
    'Startup Pitch & Grant': ['b-plan', 'business plan', 'startup', 'pitch', 'venture'],
    'Core Civil & Infrastructure': ['civil', 'infrastructure', 'cad', 'sustainable'],
    'Case Competition / Strategy': ['case competition', 'case study', 'strategy', 'consulting'],
}

class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def _text(value):
    if not isinstance(value, str):
        return ''
    parser = _Text()
    parser.feed(value)
    return ' '.join(unescape(' '.join(parser.parts)).split())

def _fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=CTX) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('Feed exceeds response size limit')
    return json.loads(payload.decode('utf-8'))

def _valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False

def verify_url(url):
    """Compatibility API: never fetch arbitrary listing URLs or imply they are alive."""
    if not _valid_url(url):
        return False, 'Invalid URL'
    return None, 'Unknown: availability not verified'

def _match(title, keywords=KEYWORDS):
    for track, terms in keywords.items():
        for term in terms:
            if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', title, re.I):
                return track, term
    return '', ''

def _base(item_id, source, title, organization, link, description='', location=''):
    return dict(id=item_id, source=source, title=_text(title), organization=_text(organization),
                action_url=link, source_url=link, description=_text(description), location=_text(location),
                fetched_at=datetime.now(timezone.utc).isoformat(), evidence=[], availability_state='unknown',
                eligibility='', deadline='', days_left_str='', compensation_usd='', compensation_inr='',
                quadrant='', tags=[])

def _evidence(record, field, quote):
    if quote not in (None, '', [], {}):
        record['evidence'].append(dict(field=field, quote=str(quote), url=record['source_url']))

def _amount(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(',', '').strip())
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None

def _salary(record, minimum, maximum, currency, period=''):
    low, high = _amount(minimum), _amount(maximum)
    currency = currency.upper() if isinstance(currency, str) else ''
    if not (low or high) or not re.fullmatch('[A-Z]{3}', currency) or (low and high and low > high):
        return
    amounts = f'{low:,.0f} – {high:,.0f}' if low and high else (f'{low:,.0f}+' if low else f'Up to {high:,.0f}')
    value = f'{amounts} {currency}' + (f' / {period}' if period else '')
    record['compensation'] = value
    if currency in ('USD', 'INR'):
        record['compensation_usd' if currency == 'USD' else 'compensation_inr'] = value
    _evidence(record, 'compensation', f'min={minimum}; max={maximum}; currency={currency}; period={period}')

def fetch_remoteok():
    data = _fetch_json('https://remoteok.com/api')
    if not isinstance(data, list):
        raise ValueError('RemoteOK feed must be a list')
    jobs = []
    for item in data[:100]:
        if not isinstance(item, dict) or not item.get('id') or not item.get('position'):
            continue
        track, matched = _match(_text(item['position']))
        link = item.get('url')
        if not track or not _valid_url(link):
            continue
        record = _base(f"remoteok_{item['id']}", 'RemoteOK', item['position'], item.get('company', ''), link, item.get('description', ''), item.get('location', ''))
        record.update(type='remote_usd', track=track, matched_on=matched)
        record['tags'] = [str(tag) for tag in item.get('tags', [])] if isinstance(item.get('tags'), list) else []
        # RemoteOK documents its salary fields in USD. Never infer an FX equivalent.
        _salary(record, item.get('salary_min'), item.get('salary_max'), 'USD')
        for field in ('description', 'location'):
            _evidence(record, field, record[field])
        _evidence(record, 'track', item['position'])
        jobs.append(record)
    return jobs

def fetch_jobicy():
    data = _fetch_json('https://jobicy.com/api/v2/remote-jobs?count=50&geo=anywhere')
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('Jobicy feed missing jobs list')
    jobs = []
    for item in data['jobs'][:100]:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        title = _text(item.get('jobTitle', ''))
        track, matched = _match(title)
        link = item.get('url')
        if not track or not _valid_url(link):
            continue
        record = _base(f"jobicy_{item['id']}", 'Jobicy', title, item.get('companyName', ''), link, item.get('jobDescription', ''), item.get('jobGeo', ''))
        record.update(type='remote_usd', track=track, matched_on=matched)
        record['tags'] = [_text(value) for key in ('jobIndustry', 'jobType') for value in (item.get(key) if isinstance(item.get(key), list) else [item.get(key, '')]) if isinstance(value, str)]
        _salary(record, item.get('annualSalaryMin'), item.get('annualSalaryMax'), item.get('salaryCurrency'), 'year')
        for field in ('description', 'location'):
            _evidence(record, field, record[field])
        _evidence(record, 'track', title)
        jobs.append(record)
    return jobs

def _prizes(value):
    """Keep explicit prize facts; never stringify unknown objects as rupees."""
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, list):
        return '; '.join(filter(None, (_prizes(part) for part in value)))
    if isinstance(value, dict):
        label = _text(value.get('name') or value.get('title') or '')
        amount = _amount(value.get('amount') or value.get('cash') or value.get('value'))
        currency = value.get('currency')
        money = f'{amount:,.0f} {currency}' if amount and isinstance(currency, str) and re.fullmatch('[A-Za-z]{3}', currency) else ''
        return ': '.join(filter(None, (label, money)))
    return ''

def _deadline(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = raw.strip()
        end = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Unstop offset-free timestamps refer to India; date-only closes end of local day.
        if end.tzinfo is None:
            if len(value) == 10:
                end = end.replace(hour=23, minute=59, second=59)
            end = end.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return end.astimezone(timezone.utc)
    except ValueError:
        return None

def fetch_unstop_opportunities():
    data = _fetch_json('https://unstop.com/api/public/opportunity/search-result?opportunity=competitions&per_page=30')
    container = data.get('data') if isinstance(data, dict) else None
    if not isinstance(container, dict) or not isinstance(container.get('data'), list):
        raise ValueError('Unstop feed missing data list')
    opportunities = []
    for item in container['data'][:100]:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        title = _text(item.get('title', ''))
        track, matched = _match(title, COMPETITION_KEYWORDS)
        link = item.get('seo_url', '')
        if isinstance(link, str) and link and not urllib.parse.urlsplit(link).scheme:
            # Feed-provided relative paths are resolved, never invented from IDs.
            link = urllib.parse.urljoin('https://unstop.com/', link)
        if not track or not _valid_url(link):
            continue
        organization = item.get('organisation')
        organization = organization.get('name', '') if isinstance(organization, dict) else organization
        record = _base(f"unstop_{item['id']}", 'Unstop', title, organization, link, item.get('description') or item.get('details') or '', item.get('location', ''))
        record.update(type='national_campus', track=track, matched_on=matched)
        prizes = _prizes(item.get('prizes'))
        record['compensation'] = prizes
        if 'INR' in prizes.upper() or '₹' in prizes:
            record['compensation_inr'] = prizes
        _evidence(record, 'prizes', json.dumps(item['prizes'], ensure_ascii=False) if item.get('prizes') else '')
        raw_deadline = item.get('end_date')
        end = _deadline(raw_deadline)
        if end:
            record['deadline'] = end.isoformat()
            _evidence(record, 'deadline', raw_deadline)
            if end <= datetime.now(timezone.utc):
                record['availability_state'] = 'closed'
                record['days_left_str'] = 'Deadline passed'
        _evidence(record, 'track', title)
        _evidence(record, 'description', record['description'])
        opportunities.append(record)
    return opportunities

def run_full_scan():
    if not SCAN_LOCK.acquire(blocking=False):
        return dict(status='busy', counts={}, remote_count=0, national_count=0)
    counts, errors = {}, {}
    owner, leased = uuid.uuid4().hex, False
    try:
        db.init_db()
        # Three bounded feed requests (15s each); ten minutes permits DB/parse overhead.
        leased = db.acquire_scan_lease(owner, lease_seconds=600)
        if not leased:
            return dict(status='busy', counts={}, remote_count=0, national_count=0)
        for source, adapter in [('RemoteOK', fetch_remoteok), ('Jobicy', fetch_jobicy), ('Unstop', fetch_unstop_opportunities)]:
            count = 0
            try:
                for opportunity in adapter():
                    db.upsert_opportunity(opportunity)
                    count += 1
            except Exception as exc:
                errors[source] = f'{type(exc).__name__}: {exc}'
            counts[source] = count
            db.record_scan(source, 'error' if source in errors else 'success', count, errors.get(source))
        status = 'complete' if not errors else ('error' if len(errors) == 3 else 'partial')
        return dict(status=status, counts=counts, errors=errors,
                    last_scanned=datetime.now(timezone.utc).isoformat(),
                    total_active=len(db.get_all_active_opportunities()),
                    remote_count=counts.get('RemoteOK', 0) + counts.get('Jobicy', 0),
                    national_count=counts.get('Unstop', 0))
    finally:
        try:
            if leased:
                db.release_scan_lease(owner)
        finally:
            SCAN_LOCK.release()

if __name__ == '__main__':
    print(json.dumps(run_full_scan(), indent=2))
