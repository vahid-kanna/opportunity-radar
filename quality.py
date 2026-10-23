"""Conservative presentation policy. Imported assertions are not verification."""
import html
import ipaddress
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from radar_profile import get_profile
from review import reviewed_facts, aware_time

AUDIT_AT = '2026-10-23T18:00:00+00:00'
PUBLIC_FIELDS = (
    'id', 'type', 'title', 'organization', 'track', 'track_group', 'kind',
    'source', 'source_url', 'action_url', 'apply_url', 'destination_host', 'action_label',
    'location', 'compensation', 'compensation_usd', 'compensation_inr', 'eligibility',
    'eligibility_state', 'availability_state', 'verification_state', 'evidence',
    'fit_reason', 'fit_score', 'queue', 'deadline_at', 'days_left_str',
    'discovered_at', 'last_seen_at', 'last_verified_at', 'freshness_note',
)

def is_public_record(raw):
    if raw.get('is_private') or raw.get('visibility') in ('private', 'internal', 'restricted'):
        return False
    source = str(raw.get('source') or '').lower()
    provenance = str(raw.get('provenance') or '').lower()
    if any(word in source + ' ' + provenance for word in ('campus', 'placement portal', 'smail', 'imap', 'email', 'mailbox')):
        return raw.get('visibility') == 'public' and raw.get('publication_approved') is True
    return True

def utcnow():
    return datetime.now(timezone.utc).isoformat()


def safe_url(value):
    try:
        p = urlsplit(value or '')
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            return ''
        host = p.hostname.lower().rstrip('.')
        if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            pass
        return value
    except (ValueError, TypeError):
        return ''


def clean_text(value):
    if not isinstance(value, str):
        return ''
    value = html.unescape(value)
    for _ in range(2):
        if not any(c in value for c in ('â', 'Ã', 'Ù', 'Ø')):
            break
        try:
            repaired = value.encode('latin1').decode('utf-8')
        except (UnicodeError, ValueError):
            break
        value = repaired
    return re.sub(r'<[^>]*>', ' ', value).strip()


def parse_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def classify(title, track=''):
    t = title.lower()
    if re.search(r'\b(business development|sales|account executive)\b', t):
        return 'other'
    if any(x in t for x in ('software', 'backend', 'full stack', 'full-stack', 'devops', 'sre', 'systems engineer', 'engineer ai coding')):
        return 'engineering'
    if 'product' in t:
        return 'product'
    if any(x in t for x in ('annotator', 'ai trainer', 'transcription')):
        return 'other'
    text = (title + ' ' + track).lower()
    for group, words in (
        ('funding', ('grant', 'accelerator', 'residency', 'railway pilot', 'entrepreneur awards', 'seed fund', 'y combinator', 'prayas')),
        ('civil', ('civil', 'infrastructure', 'build india', 'construction')),
        ('product', ('product', 'apm')),
        ('analytics', ('analyst', 'analytics')),
        ('consulting', ('consult', 'strategy associate')),
        ('competitions', ('competition', 'hackathon', 'challenge', 'call for article')),
        ('ai', ('ai ', 'llm', 'prompt', 'automation')),
        ('engineering', ('engineer', 'developer', 'technology')),
    ):
        if any(w in text for w in words):
            return group
    return 'other'


def normalize(raw, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('normalize now must be timezone-aware')
    profile = get_profile()
    facts, checked_at = reviewed_facts(raw, now, profile['evidence_max_age_days'])
    o = dict(raw)
    for k in ('title', 'organization', 'location', 'track', 'description'):
        o[k] = clean_text(o.get(k))
    o['source_url'] = safe_url(o.get('source_url') or o.get('action_url'))
    o['action_url'] = o['source_url']
    o['apply_url'] = safe_url(o.get('apply_url'))
    o['destination_host'] = urlsplit(o['apply_url'] or o['source_url']).hostname or ''
    evidence = o.get('evidence') or []
    o['evidence'] = [dict(field=str(e.get('field', '')), quote=clean_text(e.get('quote', '')), url=safe_url(e.get('url'))) for e in evidence if isinstance(e, dict)]
    o['track_group'] = classify(o['title'], o['track'])
    o['kind'] = o.get('kind') or ('job' if o.get('type') == 'remote_usd' or 'coderound' in o['source_url'].lower() else 'competition' if o.get('source') == 'Unstop' else 'program')
    o['eligibility_state'] = 'unknown'
    o['availability_state'] = 'unknown'
    o['verification_state'] = 'unverified'
    o['last_verified_at'] = None
    o['fit_reason'] = 'Check experience, graduation year and India eligibility against the original posting.'
    o['eligibility'] = 'Eligibility not confirmed'
    raw_deadline = o.get('deadline_at') or o.get('deadline_at_raw') or o.get('deadline')
    o['deadline_at'] = None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(raw_deadline or '')) and parse_date(raw_deadline):
        # A date without timezone does not identify a closing instant.
        zone = o.get('deadline_timezone') or ('Asia/Kolkata' if o.get('source') == 'Unstop' else None)
        if zone == 'Asia/Kolkata':
            deadline = datetime.fromisoformat(raw_deadline).replace(hour=23, minute=59, second=59, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        elif zone == 'UTC':
            deadline = datetime.fromisoformat(raw_deadline).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        else:
            deadline = None
    else:
        deadline = parse_date(raw_deadline)
    if deadline:
        o['deadline_at'] = deadline.isoformat()
        o['availability_state'] = 'closed' if deadline < now else 'unknown'
        o['days_left_str'] = 'Closed' if deadline < now else 'Deadline: ' + deadline.strftime('%d %b %Y %H:%M %Z')
    else:
        o['days_left_str'] = 'Deadline not verified'
    # Legacy money was inferred or truncated; only newly evidenced amounts survive.
    salary_evidence = any(e['field'] in ('compensation', 'prizes') and e['quote'] for e in o['evidence'])
    o['compensation'] = clean_text(o.get('compensation')) if salary_evidence else None
    for k in ('compensation_usd', 'compensation_inr'):
        value = clean_text(o.get(k))
        o[k] = value if salary_evidence and not any(x in value.lower() for x in ('est.', 'competitive', "[{'", 'equivalent')) else None
    text = o['title'] + ' ' + o.get('description', '')
    senior = bool(re.search(r'\b(senior|principal|staff|director|head of|lead developer|product lead)\b', o['title'], re.I))
    experience = re.search(r'(\d+)\s*\+?\s*years?\s+(?:of\s+)?(?:professional\s+)?experience', text, re.I)
    if experience and int(experience.group(1)) > profile['max_experience']:
        o['eligibility_state'] = 'ineligible'
        o['fit_reason'] = f"Requires {experience.group(0)}; outside the configured 0–{profile['max_experience']} year profile."
    elif senior:
        o['fit_reason'] = 'Seniority mismatch for an entry-level shortlist; explore only, not a confirmed eligible match.'
    restricted = re.search(r'\b(us|usa|united states|uk|canada)\s+only\b', text, re.I)
    if restricted:
        aliases = {'us': 'united states', 'usa': 'united states', 'uk': 'united kingdom'}
        country = aliases.get(profile['country'].casefold(), profile['country'].casefold())
        required = aliases.get(restricted.group(1).casefold(), restricted.group(1).casefold())
        if country != required:
            o['eligibility_state'] = 'ineligible'
            o['fit_reason'] = f"Explicit country restriction conflicts with the {profile['country']}-based profile."
    title = o['title'].lower()
    # Audited source conflicts are explicit, dated evidence, not blanket current verification.
    audit = None
    if 'seed fund scheme' in title or 'sisfs' in title:
        o.update(availability_state='closed', kind='grant', fit_reason='Official final notice: startup applications closed 31 May 2026. Watch for a verified reopening.', deadline_at='2026-05-31T23:59:59+05:30', days_left_str='Closed · 31 May 2026')
        audit = ('availability', 'Final Notice (Extended): Last date for startups to apply under SISFS – 31 May 2026', 'https://seedfund.startupindia.gov.in/')
    elif 'build india scholarship' in title:
        o.update(eligibility_state='ineligible' if profile['graduation_year'] != 2026 else 'unknown', kind='scholarship', fit_reason=f"Linked cycle requires graduation in 2026; configured graduation year is {profile['graduation_year']}. Includes a ₹12 lakh undertaking and five-year service obligation; verify all conditions.")
        audit = ('eligibility', 'Expected to graduate in 2026; undertaking valued at Rs. 12 lakhs; serving the company for five years.', 'https://www.lntecc.com/scholarship-for-engineering-students')
    elif 'prayas' in title:
        o.update(kind='grant', fit_reason='Physical-product requirement; pure software/app projects excluded by the linked older guidelines. Verify the current centre call before applying.')
        audit = ('eligibility', 'The product prototype has to be a physical product. Pure Software development and App based solutions will not be considered (2020 document; current call required).', 'https://www.nidhi-prayas.org/letters/Eligibility%20_%20Broad%20Terms%20_v4_16-09-2020.pdf')
    elif 'problem statements catalog' in title or 'product folks' in title:
        o.update(kind='resource', fit_reason='Discovery resource, not a verified specific opening or contract.')
    if audit:
        o['evidence'].append(dict(zip(('field', 'quote', 'url'), audit), observed_at=AUDIT_AT))
        o['verification_state'] = 'conflicting'
    # Reviewed facts remain separate from raw import claims. A source observation
    # newer than the historical audit can supersede that exact criterion only.
    audited_field = ('availability' if 'seed fund scheme' in title or 'sisfs' in title else
                     'graduation_year' if 'build india scholarship' in title else
                     'conditions' if 'prayas' in title else None)
    superseded = (audited_field in facts and
                  aware_time(facts[audited_field]['observed_at']) > aware_time(AUDIT_AT))
    if superseded:
        o['verification_state'] = 'unverified'
        if audited_field != 'availability':
            o['eligibility_state'] = 'unknown'
        o['fit_reason'] = 'Newer operator-reviewed source evidence supersedes the historical restriction for this exact ID and cycle.'
    # An older review never overrides a newer explicit historical audit.
    if audited_field in facts and not superseded:
        facts.pop(audited_field)
    reasons, missing = [], []
    evaluations = {}
    for field in ('country', 'experience', 'graduation_year', 'branch', 'conditions'):
        if field not in facts:
            missing.append(field.replace('_', ' '))
            continue
        value = facts[field]['value']
        if field == 'country':
            match = profile['country'].casefold() in [x.casefold() for x in value]
            reason = f"Country: {profile['country']} {'included' if match else 'excluded'}."
        elif field == 'experience':
            match = value <= profile['max_experience']
            reason = f"Experience: minimum {value:g} years; profile limit {profile['max_experience']}."
        elif field == 'graduation_year':
            match = value == 'any' or profile['graduation_year'] in value
            reason = f"Graduation: {profile['graduation_year']} {'accepted' if match else 'excluded'}."
        elif field == 'branch':
            match = value == 'any' or profile['branch'].casefold() in [x.casefold() for x in value]
            reason = f"Branch: {profile['branch']} {'accepted' if match else 'excluded'}."
        else:
            match = value != 'unmet'
            reason = 'Other program/employment conditions: ' + value.replace('_', ' ') + '.'
        evaluations[field] = match
        reasons.append(reason)
    blockers = []
    if experience and int(experience.group(1)) > profile['max_experience'] and 'experience' not in evaluations:
        blockers.append('Imported posting reports experience above the profile limit.')
    if restricted and country != required and 'country' not in evaluations:
        blockers.append('Imported posting reports a conflicting country restriction.')
    if audit and not superseded and audited_field == 'graduation_year' and profile['graduation_year'] != 2026:
        blockers.append('Historical linked cycle excludes the configured graduation year.')
    if any(not match for match in evaluations.values()) or blockers:
        o['eligibility_state'] = 'ineligible'
    elif not missing:
        o['eligibility_state'] = 'eligible'
    elif facts:
        o['eligibility_state'] = 'unknown'
    if facts:
        o['last_verified_at'] = checked_at
        o['verification_state'] = 'reviewed' if not audit or superseded else 'conflicting'
        o['evidence'].extend(dict(field=field, quote=clean_text(fact['quote']),
                                  url=safe_url(fact['url']), observed_at=fact['observed_at'])
                             for field, fact in sorted(facts.items()))
        o['fit_reason'] = ' '.join(reasons + blockers)
        if missing:
            o['fit_reason'] += ' Missing reviewed evidence: ' + ', '.join(missing) + '.'
    if o['eligibility_state'] == 'eligible':
        o['eligibility'] = 'Reviewed fit for configured profile'
    elif o['eligibility_state'] == 'ineligible':
        o['eligibility'] = 'Does not match configured profile'
    availability = facts.get('availability')
    if availability:
        observed = aware_time(availability['observed_at'])
        # A later closing instant still wins over an earlier "open" observation.
        expired_after_check = deadline and observed <= deadline < now
        if availability['value'] == 'closed' or not expired_after_check:
            o['availability_state'] = availability['value']
            if o['availability_state'] == 'open' and deadline and deadline < observed:
                o['deadline_at'] = None
                o['days_left_str'] = 'Open when reviewed; closing date not confirmed'
            elif o['availability_state'] == 'closed':
                o['days_left_str'] = 'Closed · operator-reviewed source'
        if superseded and audited_field == 'availability' and not raw_deadline:
            o['deadline_at'] = None
            o['days_left_str'] = 'Open when reviewed; closing date not confirmed' if availability['value'] == 'open' else 'Closed · operator-reviewed source'
    # Fresh collection is not proof of eligibility/open applications. Missing
    # timestamps are explicitly unconfirmed, not silently replaced by import time.
    seen = aware_time(o.get('last_seen_at'))
    latest_observation = aware_time(facts['availability']['observed_at']) if 'availability' in facts else None
    fresh_at = max((t for t in (seen, latest_observation) if t and t <= now), default=None)
    stale = bool(fresh_at and fresh_at < now - timedelta(days=profile['stale_days']))
    if stale:
        o['availability_state'] = 'stale' if o['availability_state'] != 'closed' else 'closed'
        o['freshness_note'] = 'Stale listing: no recent source observation.'
    elif not fresh_at:
        o['freshness_note'] = 'Freshness not confirmed: no dated source observation.'
    else:
        o['freshness_note'] = 'Last dated source observation: ' + fresh_at.isoformat() + '; not proof applications remain open.'
    o['fit_reason'] += ' ' + o['freshness_note']
    o['fit_score'] = 50 if o['track_group'] in ('product', 'analytics', 'consulting', 'civil', 'ai', 'funding') else 10
    if senior or o['eligibility_state'] == 'ineligible':
        o['fit_score'] = 0
    if o['kind'] == 'resource':
        o['queue'] = 'resources'
    elif o['availability_state'] in ('closed', 'stale') or o['eligibility_state'] == 'ineligible':
        o['queue'] = 'watch'
    elif senior or o['track_group'] == 'other' or (o['track_group'] == 'engineering' and not profile['include_engineering']):
        o['queue'] = 'explore'
    elif o['eligibility_state'] == 'eligible' and o['availability_state'] == 'open' and o['verification_state'] == 'reviewed' and fresh_at:
        o['queue'] = 'best'
        o['fit_score'] = 90
    else:
        o['queue'] = 'review'
    # Best matches require complete, current, local operator-reviewed qualification.
    o['action_label'] = 'View resource' if o['kind'] == 'resource' else ('View listing' if o['kind'] in ('job', 'internship', 'competition') else 'View program')
    if not o['location']:
        o['location'] = 'Location not specified'
    # Public output is a projection, never a copy of arbitrary source fields.
    return {key: o.get(key) for key in PUBLIC_FIELDS}
