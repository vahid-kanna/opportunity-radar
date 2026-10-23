"""Bounded HTTPS JSON retrieval with conditional, never stale-on-error caching.

Public APIs: fetch_json(url, *, opener=None, cache_path=None, sleeper=time.sleep)
and fetch_json_with_metadata(...) -> (JSON value, metadata dict). Injected openers
are trusted test/transport adapters (callable or object with .open); production
callers should leave opener unset to retain pre-follow redirect enforcement.
"""
import http.client
import json
import math
import os
import socket
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ALLOWED_DOMAINS = frozenset({
    'remoteok.com', 'jobicy.com', 'unstop.com', 'boards-api.greenhouse.io',
    'api.lever.co', 'api.eu.lever.co',
})
TIMEOUT = 15
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RETRIES = 2
MAX_RETRY_AFTER = 5
RETRY_STATUSES = frozenset({429, 502, 503, 504})
HEADERS = {'User-Agent': 'OpportunityRadar/1.0', 'Accept': 'application/json',
           'Accept-Encoding': 'identity'}


def validate_url(url):
    """Require an exact allowlisted HTTPS hostname, without userinfo or ports."""
    if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('Feed URL must not contain whitespace or control characters')
    try:
        parsed = urllib.parse.urlsplit(url)
        safe = (parsed.scheme == 'https' and parsed.hostname in ALLOWED_DOMAINS
                and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and not parsed.fragment
                and '\\' not in url)
    except ValueError as exc:
        raise ValueError('Invalid feed URL') from exc
    if not safe:
        raise ValueError('Feed URL must use HTTPS on an allowed feed domain')
    return url


# Official urllib redirect/TLS API documentation:
# https://docs.python.org/3/library/urllib.request.html#urllib.request.HTTPRedirectHandler
class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate *before* urllib can open the redirect target (also 307/308)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_url(newurl)
        except ValueError:
            if fp is not None:
                fp.close()
            raise
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            for name in ('If-none-match', 'If-modified-since'):
                redirected.remove_header(name)
        return redirected


def build_secure_opener():
    """No global opener installation; CA verification and hostname checks stay on."""
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        SafeRedirectHandler(),
    )


def _cache_path(cache_path):
    if cache_path is not None:
        return os.fspath(cache_path)
    return os.environ.get('RADAR_HTTP_CACHE') or str(Path(__file__).with_name('radar-http-cache.sqlite3'))


@contextmanager
def _database(path):
    connection = sqlite3.connect(path, timeout=5)
    try:
        with connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY, body BLOB NOT NULL, etag TEXT,
                last_modified TEXT, content_fetched_at TEXT NOT NULL,
                checked_at TEXT NOT NULL)''')
            yield connection
    finally:
        connection.close()


def _load_cache(path, url):
    with _database(path) as connection:
        row = connection.execute('SELECT body, etag, last_modified, content_fetched_at '
                                 'FROM http_cache WHERE url = ?', (url,)).fetchone()
    if row is None:
        return None
    body, etag, modified, fetched_at = row
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('Cached feed exceeds response size limit')
    # A damaged cache is not evidence and must never be sent back as valid JSON.
    json.loads(body)
    return {'body': body, 'etag': etag, 'last_modified': modified,
            'content_fetched_at': fetched_at}


def _delete_cache(path, url):
    with _database(path) as connection:
        connection.execute('DELETE FROM http_cache WHERE url = ?', (url,))


def _no_store(headers):
    return any(part.strip().split('=', 1)[0].lower() == 'no-store'
               for part in headers.get('Cache-Control', '').split(','))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _retry_delay(headers, attempt):
    value = headers.get('Retry-After') if headers is not None else None
    if value:
        try:
            delay = float(value)
            if math.isfinite(delay):
                return min(MAX_RETRY_AFTER, max(0, delay))
        except (TypeError, ValueError):
            try:
                stamp = parsedate_to_datetime(value)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                return min(MAX_RETRY_AFTER, max(0, (stamp - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(MAX_RETRY_AFTER, 2 ** attempt)


def _transient(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return False
    return isinstance(reason, (TimeoutError, socket.timeout, ConnectionError,
                               http.client.IncompleteRead, OSError))


def fetch_json(url, *, opener=None, cache_path=None, sleeper=time.sleep):
    """Return JSON only. Two retries maximum; failures never return stale cache."""
    return fetch_json_with_metadata(url, opener=opener, cache_path=cache_path,
                                    sleeper=sleeper)[0]


def fetch_json_with_metadata(url, *, opener=None, cache_path=None, sleeper=time.sleep):
    """Return (JSON, metadata), retaining original content time on a valid 304.

    Metadata keys: status, from_cache, content_fetched_at, checked_at, attempts,
    final_url. A 304 is successful revalidation, NOT newly downloaded content.
    Cache errors and exhausted HTTP/transport errors propagate to scan health.
    """
    validate_url(url)
    path = _cache_path(cache_path)
    cached = _load_cache(path, url)
    transport = build_secure_opener() if opener is None else opener
    open_request = transport.open if hasattr(transport, 'open') else transport
    headers = dict(HEADERS)
    if cached:
        if cached['etag']:
            headers['If-None-Match'] = cached['etag']
        if cached['last_modified']:
            headers['If-Modified-Since'] = cached['last_modified']

    for attempt in range(MAX_RETRIES + 1):
        response = None
        try:
            request = urllib.request.Request(url, headers=headers)
            try:
                response = open_request(request, timeout=TIMEOUT)
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    response = exc
                else:
                    raise
            with response:
                final_url = response.geturl() or url
                validate_url(final_url)
                status = getattr(response, 'status', None) or response.getcode()
                response_headers = response.headers
                no_store = _no_store(response_headers)
                if no_store:
                    _delete_cache(path, url)
                checked_at = _now()
                if status == 304:
                    if (cached is None
                            or not (headers.get('If-None-Match') or headers.get('If-Modified-Since'))
                            or urllib.parse.urlsplit(final_url).netloc != urllib.parse.urlsplit(url).netloc):
                        raise ValueError('304 response without a conditional cached representation')
                    body = cached['body']
                    value = json.loads(body)
                    fetched_at = cached['content_fetched_at']
                    etag = response_headers.get('ETag') or cached['etag']
                    modified = response_headers.get('Last-Modified') or cached['last_modified']
                elif status == 200:
                    length = response_headers.get('Content-Length')
                    if length is not None and int(length) > MAX_RESPONSE_BYTES:
                        raise ValueError('Feed exceeds response size limit')
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError('Feed exceeds response size limit')
                    value = json.loads(body)
                    fetched_at = checked_at
                    etag = response_headers.get('ETag')
                    modified = response_headers.get('Last-Modified')
                else:
                    raise urllib.error.HTTPError(final_url, status, 'Unexpected feed status', response_headers, None)
                if not no_store:
                    with _database(path) as connection:
                        connection.execute('INSERT OR REPLACE INTO http_cache '
                                           '(url, body, etag, last_modified, content_fetched_at, checked_at) '
                                           'VALUES (?, ?, ?, ?, ?, ?)',
                                           (url, body, etag, modified, fetched_at, checked_at))
                return value, {'status': status, 'from_cache': status == 304,
                               'content_fetched_at': fetched_at, 'checked_at': checked_at,
                               'attempts': attempt + 1, 'final_url': final_url}
        except urllib.error.HTTPError as exc:
            try:
                if _no_store(exc.headers or {}):
                    _delete_cache(path, url)
                    cached = None
                    headers.pop('If-None-Match', None)
                    headers.pop('If-Modified-Since', None)
                if exc.code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                    raise
                delay = _retry_delay(exc.headers, attempt)
            finally:
                exc.close()
            sleeper(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.IncompleteRead, OSError) as exc:
            if not _transient(exc) or attempt == MAX_RETRIES:
                raise
            sleeper(_retry_delay(None, attempt))
    raise AssertionError('Unreachable retry state')
