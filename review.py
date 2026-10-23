"""Private operator review registry; no scraping, models, or automatic trust upgrades.

Set RADAR_REVIEW_FILE to an absolute path OUTSIDE this repository (and its ZIP).
`python review.py put review-input.json` validates and stores one reviewed record.
`python review.py validate` validates the registry. Only exact ID/source URL/cycle
matches apply. A new cycle needs a new opportunity ID. This is a local trust
boundary: an operator must read the cited authoritative posting and transcribe
exact excerpts; this module cannot establish whether a human's quote is truthful.

Operator workflow (bash examples; keep all inputs outside the repository):
  export RADAR_REVIEW_FILE="$HOME/.radar-private/reviews.json"
  python review.py fingerprint "$HOME/.radar-private/listing.json"
  python review.py put "$HOME/.radar-private/review-input.json"
  python review.py validate

listing.json must contain the EXACT raw record before quality.normalize (including
ID, title, organization, source/apply URLs, location, description and any deadline
or compensation). If exporting from SQLite, merge payload with the stored identity
and timestamps as storage does, not the public API's normalized projection. Copy
the printed SHA-256 into content_fingerprint. Changing relevant listing content
invalidates review automatically; fetched_at/last_seen_at changes do not.

review-input.json shape (fill every placeholder after reading the original source):
  {"id":"exact-id", "source_url":"https://employer.example/jobs/123",
   "cycle_id":null, "content_fingerprint":"<printed SHA-256>",
   "authoritative":true, "checked_at":"<UTC ISO timestamp>",
   "expires_at":"<UTC ISO timestamp within 90 days>",
   "facts":{"country":{"value":["India"], "quote":"<exact public source excerpt>",
                        "url":"https://employer.example/jobs/123",
                        "observed_at":"<UTC ISO timestamp>"}}}

Add evidence objects for experience, graduation_year, branch, conditions and
availability to qualify fully. "any" is only valid when the source explicitly says
no graduation/branch restriction, never because those criteria were omitted.
Country must specifically include the profile country: "remote" is insufficient.
No additional conditions requires explicit source review, not absent scraped data.
Private notes must not go in quote: excerpts are intentionally public evidence.
Keep registry and input JSON files outside git, ZIP staging, and public directories.
For Windows use a private user directory with restrictive inherited ACLs; POSIX
writes use owner-only permissions. There is no server upload/review endpoint.

Entry fields: id, source_url, cycle_id (string or null), content_fingerprint,
authoritative=true, checked_at, expires_at, facts. Every fact has value, quote, url, observed_at.
Supported facts: country (list), experience (minimum years, number),
graduation_year (list of integers or "any"), branch (list or "any"),
availability ("open"/"closed"), conditions ("satisfied"/"not_applicable"/"unmet").
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parent
_CACHE = {}
_LOCK = threading.Lock()
_MAX_BYTES = 2_000_000
FACTS = {'country', 'experience', 'graduation_year', 'branch', 'availability', 'conditions'}


def aware_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def registry_path():
    configured = os.environ.get('RADAR_REVIEW_FILE')
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ValueError('RADAR_REVIEW_FILE must be an absolute private path outside the repository')
    path = path.resolve()
    if path == _REPO or _REPO in path.parents:
        raise ValueError('Review files must be outside the repository and distributable ZIP')
    return path


# Only consequential listing content binds a review. Observation/collection metadata
# is deliberately absent; a refresh alone must not revoke an unchanged review.
FINGERPRINT_FIELDS = ('id', 'cycle_id', 'title', 'organization', 'location', 'description',
                      'source_url', 'action_url', 'apply_url', 'deadline_at', 'deadline_at_raw',
                      'deadline', 'deadline_timezone', 'compensation', 'compensation_usd',
                      'compensation_inr', 'prizes', 'kind', 'type', 'track')


def content_fingerprint(raw):
    canonical = json.dumps({key: raw.get(key) for key in FINGERPRINT_FIELDS},
                           sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def validate_entry(entry):
    """Validate a review's structure, typed facts, and bounded evidence lifetime."""
    from quality import safe_url
    if not isinstance(entry, dict):
        raise ValueError('A reviewed entry must be an object')
    expected = {'id', 'source_url', 'cycle_id', 'content_fingerprint', 'authoritative', 'checked_at', 'expires_at', 'facts'}
    fingerprint = entry.get('content_fingerprint')
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('Review needs the exact listing content_fingerprint (SHA-256)')
    if set(entry) != expected:
        raise ValueError('Review fields must be exactly: ' + ', '.join(sorted(expected)))
    if not isinstance(entry['id'], str) or not entry['id'].strip():
        raise ValueError('Review requires an exact opportunity ID')
    if entry['cycle_id'] is not None and (not isinstance(entry['cycle_id'], str) or not entry['cycle_id'].strip()):
        raise ValueError('cycle_id must be nonempty text or null')
    source = safe_url(entry['source_url'])
    if not source or not source.startswith('https://') or entry['authoritative'] is not True:
        raise ValueError('Operator must confirm an authoritative HTTPS source_url')
    checked, expiry = aware_time(entry['checked_at']), aware_time(entry['expires_at'])
    if not checked or not expiry or not checked < expiry <= checked + timedelta(days=90):
        raise ValueError('Review needs timezone-aware checked_at and expiry within 90 days')
    facts = entry['facts']
    if not isinstance(facts, dict) or not facts or set(facts) - FACTS:
        raise ValueError('Review facts are missing or unsupported')
    for field, fact in facts.items():
        if not isinstance(fact, dict) or set(fact) != {'value', 'quote', 'url', 'observed_at'}:
            raise ValueError(f'{field}: requires value, exact quote, url, observed_at')
        if not isinstance(fact['quote'], str) or not fact['quote'].strip() or len(fact['quote']) > 4000:
            raise ValueError(f'{field}: requires an exact, bounded source excerpt')
        url = safe_url(fact['url'])
        if not url or urlsplit(url).scheme != 'https' or urlsplit(url).hostname != urlsplit(source).hostname:
            raise ValueError(f'{field}: evidence must be on the reviewed authoritative HTTPS host')
        observed = aware_time(fact['observed_at'])
        if not observed or observed > checked or observed < checked - timedelta(days=90):
            raise ValueError(f'{field}: observed_at must be timezone-aware and no later than checked_at')
        value = fact['value']
        valid = False
        if field == 'experience':
            valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 50
        elif field == 'graduation_year':
            valid = value == 'any' or (isinstance(value, list) and bool(value) and all(type(x) is int and 2000 <= x <= 2100 for x in value))
        elif field in ('country', 'branch'):
            valid = (field == 'branch' and value == 'any') or (isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value))
        elif field == 'availability':
            valid = value in ('open', 'closed')
        elif field == 'conditions':
            valid = value in ('satisfied', 'not_applicable', 'unmet')
        if not valid:
            raise ValueError(f'{field}: invalid factual value')
    return deepcopy(entry)


def _read_registry(path):
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError('Review registry exceeds size limit')
    registry = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(registry, dict) or set(registry) != {'version', 'reviews'} or registry['version'] != 1 or not isinstance(registry['reviews'], dict):
        raise ValueError('Invalid review registry envelope')
    for identity, entry in registry['reviews'].items():
        validate_entry(entry)
        if identity != entry['id']:
            raise ValueError('Registry key must equal review ID')
    return registry


def load_reviews():
    """Cache parsed registry by file metadata; invalid files fail closed, never stale."""
    path = registry_path()
    if not path:
        return {}
    try:
        stat = path.stat()
        signature = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
        with _LOCK:
            if signature not in _CACHE:
                registry = _read_registry(path)
                _CACHE.clear()
                _CACHE[signature] = registry['reviews']
            return _CACHE[signature]
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise ValueError(f'Cannot load private review registry: {exc}') from exc


def reviewed_facts(raw, now, max_age_days):
    """Only local registry data is trusted; raw qualification objects are ignored."""
    entry = load_reviews().get(raw.get('id'))
    if not entry or entry['source_url'] != (raw.get('source_url') or raw.get('action_url')) or entry['cycle_id'] != raw.get('cycle_id'):
        return {}, None
    if entry['content_fingerprint'] != content_fingerprint(raw):
        return {}, None
    checked, expiry = aware_time(entry['checked_at']), aware_time(entry['expires_at'])
    if not checked <= now < expiry:
        return {}, None
    cutoff = now - timedelta(days=max_age_days)
    facts = {k: deepcopy(v) for k, v in entry['facts'].items() if cutoff <= aware_time(v['observed_at']) <= now}
    return facts, checked.isoformat() if facts else None


def store_review(entry):
    entry = validate_entry(entry)
    now = datetime.now(timezone.utc)
    if aware_time(entry['checked_at']) > now or aware_time(entry['expires_at']) <= now:
        raise ValueError('Cannot store a future-dated or expired review')
    path = registry_path()
    if not path:
        raise ValueError('Set RADAR_REVIEW_FILE to a private path outside the repository')
    registry = _read_registry(path) if path.exists() else {'version': 1, 'reviews': {}}
    old = registry['reviews'].get(entry['id'])
    if old and old['cycle_id'] != entry['cycle_id']:
        raise ValueError('A different cycle requires a new opportunity ID')
    registry['reviews'][entry['id']] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement and owner-only mode on POSIX. Windows inherits directory ACLs;
    # operators must choose a private directory, not a public/synced share.
    fd, temporary = tempfile.mkstemp(prefix='.radar-review-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(registry, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    with _LOCK:
        _CACHE.clear()
    return entry['id']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    put = commands.add_parser('put', help='Validate and atomically store one operator-reviewed JSON entry')
    put.add_argument('input', type=Path)
    fingerprint = commands.add_parser('fingerprint', help='Fingerprint one exact raw listing JSON object, before normalization')
    fingerprint.add_argument('input', type=Path)
    commands.add_parser('validate', help='Validate the configured private review registry')
    args = parser.parse_args(argv)
    try:
        if args.command == 'fingerprint':
            raw = json.loads(args.input.read_text(encoding='utf-8'))
            if not isinstance(raw, dict):
                raise ValueError('Listing must be a raw JSON object')
            print(content_fingerprint(raw))
        elif args.command == 'put':
            identity = store_review(json.loads(args.input.read_text(encoding='utf-8')))
            # Read the exact target back before reporting success.
            if identity not in _read_registry(registry_path())['reviews']:
                raise ValueError('Stored review could not be read back')
            print(f'Stored local review for {identity}')
        else:
            path = registry_path()
            if not path or not path.exists():
                raise ValueError('Configured review registry does not exist')
            print(f"Valid registry: {len(_read_registry(path)['reviews'])} reviews")
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        parser.exit(2, f'Review error: {exc}\n')


if __name__ == '__main__':
    main()
